#!/usr/bin/env python3
"""Ask Jev for probabilities over a typed question set: no text, no streaming.

Jev is the decision model, and the usual discovery path does not see it: it is absent
from `GET /provider/v1/models`, and `GET /systemone` answers 404 ("not a registered API route"),
because the only route is `POST <base>/systemone` (measured 2026-10-20). Pointing `typesafe/jev`
at `/chat/completions` is refused with `unsupported_model`; the release-pinned
`typesafe/jev-1.14.0` answers the same shape at the same price, and the `typesafe/jev-router`
alias is rejected with a message naming those two ids.

The reply is probabilities, so the request has to carry the questions:

    python tools/jev_decide.py --state "My payments have failed for three days." \
        --questions-json questions.json
    python tools/jev_decide.py --state "..." --ask "urgent:noul:Does this need urgent attention?"

`--ask` cannot express `criteria` (a `choice`'s options, a `score`'s levels), so
`--questions-json` is the full-fidelity form and `--ask` is the one-liner.

Invalid question sets are caught locally instead of at the server, which rejects the same cases
with 400 `invalid_request_error` and a `param` pointer. The server's limits are 100 questions per
request, 64K tokens for state plus all questions, and 32K for state plus the longest question;
only the question count is checkable without a tokeniser.

Every request carries `opencode/1.0`, because the provider answers 403 to a default Python
User-Agent (measured 2026-10-16; the same reason `check_model_catalog.py` sets it).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))
import env_file  # noqa: E402

ROOT = TOOLS_DIR.parent
ENDPOINT_PATH = "/systemone"
DEFAULT_MODEL = "typesafe/jev"
PINNED_MODEL = "typesafe/jev-1.14.0"
QUESTION_TYPES = ("noul", "choice", "score")
MAX_QUESTIONS = 100
#: Append-only ledger, one line per call. The state text and the key never land here.
USAGE_LOG = ROOT / ".solocode" / "jev-decide-usage.jsonl"
UA = "opencode/1.0"


class JevDecideError(Exception):
    """A transport or provider failure, never a validation failure.

    `status` is the HTTP status when there was one (0 otherwise), and `config` marks the
    missing-url-or-key case so the CLI can exit 2 for configuration and 3 for the network.
    """

    def __init__(self, message: str, *, status: int = 0, config: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.config = config


def build_request(model: str, state: str, questions: dict) -> dict:
    """Validate the question set and return the payload.

    The checks mirror what the server answers with 400 `invalid_request_error` plus a `param`
    pointer, so a malformed question costs nothing instead of a round trip.
    """
    if not isinstance(state, str) or not state.strip():
        raise ValueError("state must be a non-empty string")
    if not isinstance(questions, dict) or not questions:
        raise ValueError("questions must be a non-empty object")
    if len(questions) > MAX_QUESTIONS:
        raise ValueError(
            f"{len(questions)} questions exceeds the server limit of {MAX_QUESTIONS}"
        )
    for name, question in questions.items():
        if not isinstance(question, dict):
            raise ValueError(f"question {name!r} must be an object")
        extra = set(question) - {"type", "instructions", "criteria"}
        if extra:
            raise ValueError(
                f"question {name!r} has unsupported keys: {', '.join(sorted(extra))}"
            )
        kind = question.get("type")
        if kind not in QUESTION_TYPES:
            raise ValueError(
                f"question {name!r} has type {kind!r}; "
                f"use one of {', '.join(QUESTION_TYPES)}"
            )
        instructions = question.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError(f"question {name!r} needs non-empty instructions")
        criteria = question.get("criteria")
        if kind == "choice" and (not isinstance(criteria, dict) or not criteria):
            raise ValueError(
                f"question {name!r} is a choice and needs criteria mapping options to descriptions"
            )
        if kind == "score" and (not isinstance(criteria, list) or len(criteria) < 2):
            raise ValueError(
                f"question {name!r} is a score and needs at least two criteria levels"
            )
    return {"model": model, "state": state, "questions": questions}


def decide(
    state: str, questions: dict, *, model: str = DEFAULT_MODEL, timeout: int = 60
) -> dict:
    """POST one decision request and return the parsed response body."""
    payload = build_request(model, state, questions)
    # The chat path is the only place that knows the base url; `/systemone` replaces its suffix.
    record: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "questions": len(questions),
        "state_chars": len(state),
        "input_tokens": 0,
        "output_tokens": 0,
        "latency_s": 0.0,
        "error": None,
    }
    started = time.monotonic()
    try:
        url, key = env_file.provider_endpoint("deepseek", "chat")
        if not url or not key:
            raise JevDecideError(
                "missing base url or deepseek credential in .env", config=True
            )
        endpoint = url.removesuffix("/chat/completions") + ENDPOINT_PATH
        request = urllib.request.Request(  # noqa: S310 - operator-configured https endpoint
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": UA,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = (
                exc.read().decode("utf-8", errors="replace")[:300].replace("\n", " ")
            )
            raise JevDecideError(f"HTTP {exc.code}: {detail}", status=exc.code) from exc
        except (
            OSError
        ) as exc:  # URLError, DNS failures and socket timeouts are all OSError
            raise JevDecideError(f"{type(exc).__name__}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise JevDecideError(f"unexpected body: {exc}") from exc
        usage = body.get("usage") or {}
        record["input_tokens"] = usage.get("input_tokens") or 0
        record["output_tokens"] = usage.get("output_tokens") or 0
        return body
    except JevDecideError as exc:
        record["error"] = str(exc)
        raise
    finally:
        record["latency_s"] = round(time.monotonic() - started, 2)
        try:
            USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with USAGE_LOG.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:  # a full disk must not lose an answer that already arrived
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ask Jev for probabilities on a typed question set"
    )
    state_source = parser.add_mutually_exclusive_group(required=True)
    state_source.add_argument("--state", metavar="TEXT", help="the text to decide on")
    state_source.add_argument(
        "--state-file", metavar="PATH", help="read the text from a file instead"
    )
    question_source = parser.add_mutually_exclusive_group(required=True)
    question_source.add_argument(
        "--questions-json",
        metavar="PATH|-",
        help="full-fidelity question object (reads stdin when '-')",
    )
    question_source.add_argument(
        "--ask",
        action="append",
        default=[],
        metavar="NAME:TYPE:INSTRUCTIONS",
        help="one question, repeatable (e.g. urgent:noul:Is this urgent?); cannot express "
        "criteria, so use --questions-json for choice or score questions",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"typesafe/jev or typesafe/jev-1.14.0 (default {DEFAULT_MODEL})",
    )
    parser.add_argument("--timeout", type=int, default=60, help="per-request timeout")
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the raw response JSON instead of the summary",
    )
    args = parser.parse_args(argv)

    if args.state_file:
        path = Path(args.state_file)
        if not path.is_file():
            print(
                f"[jev_decide] state file not found: {args.state_file}", file=sys.stderr
            )
            return 2
        state = path.read_text(encoding="utf-8")
    else:
        state = args.state

    if args.questions_json:
        if args.questions_json == "-":
            raw = sys.stdin.read()
        else:
            path = Path(args.questions_json)
            if not path.is_file():
                print(
                    f"[jev_decide] questions file not found: {args.questions_json}",
                    file=sys.stderr,
                )
                return 2
            raw = path.read_text(encoding="utf-8")
        try:
            questions = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(
                f"[jev_decide] --questions-json is not valid JSON: {exc}",
                file=sys.stderr,
            )
            return 2
    else:
        questions = {}
        for item in args.ask:
            name, _, remainder = item.partition(":")
            kind, separator, instructions = remainder.partition(":")
            if not name or not separator or not kind:
                print(
                    f"[jev_decide] --ask needs NAME:TYPE:INSTRUCTIONS, got {item!r}",
                    file=sys.stderr,
                )
                return 2
            questions[name] = {"type": kind, "instructions": instructions}

    try:
        response = decide(state, questions, model=args.model, timeout=args.timeout)
    except ValueError as exc:
        print(f"[jev_decide] {exc}", file=sys.stderr)
        return 2
    except JevDecideError as exc:
        print(f"[jev_decide] {exc}", file=sys.stderr)
        return 2 if exc.config else 3

    if args.json:
        print(json.dumps(response, ensure_ascii=False))
        return 0

    answers = response.get("answers") or {}
    width = max((len(name) for name in answers), default=0)
    for name, answer in answers.items():
        kind = str(answer.get("type") or "")
        prefix = f"{name:<{width}} {kind:<6}"
        if kind == "noul":
            print(f"{prefix} {float(answer.get('noul') or 0):.2f}")
            continue
        probabilities = answer.get("probabilities") or {}
        if kind == "choice":
            head = str(answer.get("choice") or "")
            detail = ", ".join(f"{k} {float(v):.2f}" for k, v in probabilities.items())
        elif kind == "score":
            head = f"{float(answer.get('score') or 0):.2f}"
            # The legend carries the level order; the probabilities object is not ordered by
            # contract, so reading it alone could print the levels back to front.
            order = list((answer.get("legend") or {}).keys()) or list(probabilities)
            detail = ", ".join(
                f"{float(probabilities[k]):.2f}" for k in order if k in probabilities
            )
        else:
            print(f"{prefix} {json.dumps(answer, ensure_ascii=False)}")
            continue
        confidence = float(answer.get("confidence") or 0)
        print(f"{prefix} {head} (confidence {confidence:.2f}; {detail})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
