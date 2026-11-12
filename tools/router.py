#!/usr/bin/env python3
"""Pick a brain, a worker and a budget for a task string -- no network, no LLM call.

The orchestrator classified a task by hand, and nothing recorded which worker it
picked or why, so a delegation that quietly fell back to the executor looked the
same as a routed one. This tool writes that decision down:

    route(task) -> {
      "task_type": question | simple_edit | complex | review | survey | destructive,
      "brain":     the decision source actually consulted, "rules" today,
      "worker":    one id from DECLARED_WORKERS, or "none",
      "model":     "<provider>/<model-id>" or None,
      "reason":    one line, written from the task's own trigger word,
      "budget":    {"max_seconds": int, "max_prompt_chars": int},
      "evidence":  the rule-table version plus a hash of model-roles.json
    }

Classification is a rule table (regex triggers, first match wins), so the same
task always routes the same way and the decision costs nothing to reproduce. The
brain field names the decision source that was actually consulted, and every route
names `rules` today; `jev-router` and `gemini-decision` are reserved for when
`--probe` is wired, and only `--probe` may call an endpoint.

`--shadow` asks the decision model the frozen question set and records its answer
in the ledger, without letting that answer change the route: the printed decision
stays the rule table's, and a failed brain call lands in the row as an error
rather than a lost decision.

Usage:
    python tools/router.py "explain how auth works"
    python tools/router.py "delete the users table" --dry-run
    python tools/router.py "explain how auth works" --shadow
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: The one place every agent's and role's model id is declared.
MODEL_ROLES_PATH = ROOT / "tools" / "config" / "model-roles.json"
#: Append-only ledger, written by `--dry-run` and `--shadow`. Mirrors dsh-routing.jsonl.
LEDGER_PATH = ROOT / ".solocode" / "route-decisions.jsonl"

#: Bumped whenever _CLASSIFIERS or _ROUTES changes, so a ledger row can be traced.
RULES_VERSION = "v2"

#: The only worker ids a brain may choose. An id outside this set is an error.
DECLARED_WORKERS = frozenset(
    {
        "subagent:explore",
        "subagent:code-reviewer",
        "cli:opencode",
        "cli:antigravity",
        "cli:kilo",
        "in_process:solo-code-engineer",
        "none",
    }
)

#: Ordered classification rules; the first match wins. The verbs that stop work
#: come first, then the ones that read the whole repo, then the rest -- so
#: "explain where else this appears" surveys instead of answering in place.
_CLASSIFIERS: list[tuple[str, re.Pattern[str]]] = [
    (
        "destructive",
        re.compile(
            r"\b(?:delete|drop|destroy|truncate|erase|rm -rf|force[ -]push"
            r"|reset --hard|drop table)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "survey",
        re.compile(
            r"\b(?:survey|where else|everywhere|repo-wide|across the (?:repo|codebase)"
            r"|find all|all callers)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "review",
        re.compile(
            r"\b(?:review|audit|second opinion|check this|red team)\b", re.IGNORECASE
        ),
    ),
    (
        "question",
        re.compile(
            r"\b(?:what is|what are|what's|explain|how does|how do|why does|why is"
            r"|document)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "complex",
        re.compile(
            r"\b(?:build|create|refactor|implement|migrate|design|rewrite|add support)\b",
            re.IGNORECASE,
        ),
    ),
]
#: A task matching no trigger is a small mechanical change (jev.md:50-55).
DEFAULT_TASK_TYPE = "simple_edit"

#: task_type -> (brain, worker, model role in model-roles.json). The brain field
#: names the decision source that was actually consulted; until probe mode is
#: wired, every decision comes from the rule table.
_ROUTES: dict[str, tuple[str, str, str | None]] = {
    "question": ("rules", "none", None),
    "simple_edit": ("rules", "cli:opencode", "default-executor"),
    "complex": ("rules", "cli:opencode", "default-executor"),
    "review": ("rules", "subagent:code-reviewer", "independent-review"),
    "survey": ("rules", "cli:antigravity", "fast-broad"),
    "destructive": ("rules", "none", None),
}

#: task_type -> (max_seconds, max_prompt_chars). Destructive is blocked: a zero
#: budget is the "stop and ask" signal, not a short timeout.
_BUDGETS: dict[str, tuple[int, int]] = {
    "question": (30, 4000),
    "simple_edit": (120, 12000),
    "complex": (900, 60000),
    "review": (300, 30000),
    "survey": (600, 40000),
    "destructive": (0, 0),
}

#: Evidence string used when model-roles.json cannot be read.
EVIDENCE_UNREADABLE = "model-roles:unreadable"

#: Bumped whenever SHADOW_QUESTIONS changes, so a shadow row can be traced.
SHADOW_QUESTIONS_VERSION = "v1"

#: Frozen question set for `--shadow`. The choice options are the six task types
#: the rule table emits, so a shadow label compares to the rule's answer directly.
SHADOW_QUESTIONS = {
    "task_type": {
        "type": "choice",
        "instructions": "Which work shape is this task?",
        "criteria": {
            "question": "asks what, how or why something works; changes nothing",
            "simple_edit": "one small mechanical change to one file; no plan needed",
            "complex": "builds, creates, refactors or migrates; needs a plan first",
            "review": "judges work that already exists: review, audit, second opinion",
            "survey": "reads many files to find every place a pattern appears",
            "destructive": "deletes, drops or force-pushes something that cannot be undone",
        },
    },
    "needs_second_opinion": {
        "type": "noul",
        "instructions": "Is this classification genuinely ambiguous?",
    },
}


def _roles() -> dict[str, str]:
    """The role -> model-id map from model-roles.json; empty when unreadable."""
    with contextlib.suppress(OSError, json.JSONDecodeError):
        config = json.loads(MODEL_ROLES_PATH.read_text(encoding="utf-8"))
        roles = config.get("roles")
        if isinstance(roles, dict):
            return {k: v for k, v in roles.items() if isinstance(v, str)}
    return {}


def _model_for(role: str | None) -> str | None:
    """The declared model id for a role, or None when no role is named."""
    return _roles().get(role) if role else None


def _evidence() -> str:
    """The rule-table version plus a hash of model-roles.json, or the sentinel."""
    with contextlib.suppress(OSError):
        digest = hashlib.sha256(MODEL_ROLES_PATH.read_bytes()).hexdigest()[:8]
        return f"rules:{RULES_VERSION} model-roles:{digest}"
    return EVIDENCE_UNREADABLE


def _classify(task: str) -> tuple[str, re.Match[str] | None]:
    """The first rule that matches, or the default type with no match."""
    for task_type, pattern in _CLASSIFIERS:
        match = pattern.search(task)
        if match:
            return task_type, match
    return DEFAULT_TASK_TYPE, None


def _reason(task: str, task_type: str, match: re.Match[str] | None) -> str:
    """One line built from this task's trigger word and length, never a template."""
    if match is None:
        return (
            f"{task_type}: no trigger in a {len(task)}-char task, so the "
            "mechanical default applies"
        )
    return (
        f"{task_type}: the task says {match.group(0).lower()!r} at char "
        f"{match.start()} of {len(task)}"
    )


def _check_worker(worker: str) -> str:
    """Return the worker when it is declared, else raise: a made-up id is a bug."""
    if worker not in DECLARED_WORKERS:
        raise ValueError(
            f"{worker!r} is not a declared worker; use one of "
            f"{', '.join(sorted(DECLARED_WORKERS))}"
        )
    return worker


def route(task: str, *, force_worker: str | None = None) -> dict:
    """Classify `task` and return the routing decision. No network, no model call.

    `force_worker` is the internal hook for tests and callers that name a worker
    themselves; it still has to be a declared id.
    """
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")
    task_type, match = _classify(task)
    brain, worker, role = _ROUTES[task_type]
    if force_worker is not None:
        worker = force_worker
    max_seconds, max_prompt_chars = _BUDGETS[task_type]
    return {
        "task_type": task_type,
        "brain": brain,
        "worker": _check_worker(worker),
        "model": _model_for(role),
        "reason": _reason(task, task_type, match),
        "budget": {"max_seconds": max_seconds, "max_prompt_chars": max_prompt_chars},
        "evidence": _evidence(),
    }


def probe(decision: dict) -> None:
    """Ask the chosen brain endpoint. Not wired: no caller may reach the network."""
    raise NotImplementedError(
        f"probe mode is not wired: brain {decision.get('brain')!r} was not called"
    )


def shadow_decision(task: str, decision: dict) -> dict:
    """What the decision model would answer, recorded without changing the route.

    `decision` is accepted so a caller can log the pair, and never read: the
    brain's answer is kept verbatim even when it disagrees with the rule table.
    The import sits inside the function so importing this module stays offline,
    and every failure comes back as `error` instead of an exception.
    """
    shadow: dict = {
        "version": SHADOW_QUESTIONS_VERSION,
        "label": None,
        "confidence": None,
        "noul": None,
        "error": None,
    }
    try:
        import jev_decide

        response = jev_decide.decide(task, SHADOW_QUESTIONS, timeout=5)
        answers = response.get("answers") or {}
        choice = answers.get("task_type") or {}
        second = answers.get("needs_second_opinion") or {}
        shadow["label"] = choice.get("choice")
        with contextlib.suppress(TypeError, ValueError):
            shadow["confidence"] = float(choice.get("confidence"))
        with contextlib.suppress(TypeError, ValueError):
            shadow["noul"] = float(second.get("noul"))
    except Exception as exc:  # noqa: BLE001 - a failed brain must not lose the decision
        shadow["error"] = f"{type(exc).__name__}: {exc}"
    return shadow


def _ledger_row(task: str, decision: dict, shadow: dict | None = None) -> dict:
    """The dsh-routing.jsonl-shaped row for one decision (`spawned` is always false).

    `shadow` is added only when it was collected, so a plain `--dry-run` row keeps
    the nine keys dsh-routing.jsonl already has.
    """
    row = {
        "task_type": decision["task_type"],
        "brain": decision["brain"],
        "worker": decision["worker"],
        "model": decision["model"],
        "reason": decision["reason"],
        "prompt_chars": len(task),
        "prompt_hash": hashlib.sha256(task.encode("utf-8")).hexdigest()[:12],
        "at": int(time.time()),
        "spawned": False,
    }
    if shadow is not None:
        row["shadow"] = shadow
    return row


def append_decision(
    task: str, decision: dict, path: Path | None = None, shadow: dict | None = None
) -> None:
    """Append one ledger row; an unwritable ledger must not lose the printed decision."""
    target = path or LEDGER_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(_ledger_row(task, decision, shadow), ensure_ascii=False)
            )
            handle.write("\n")
    except OSError:  # a full disk is not a routing failure
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pick a brain, worker and budget for a task string"
    )
    parser.add_argument("task", help="the task string to classify")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the decision and append it to .solocode/route-decisions.jsonl",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="ask the chosen brain endpoint (not wired yet)",
    )
    parser.add_argument(
        "--shadow",
        action="store_true",
        help="also record what the decision model would answer, without routing by it",
    )
    args = parser.parse_args(argv)

    try:
        decision = route(args.task)
        if args.probe:
            probe(decision)
    except ValueError as exc:
        print(f"[router] {exc}", file=sys.stderr)
        return 2
    except NotImplementedError as exc:
        print(f"[router] {exc}", file=sys.stderr)
        return 2

    print(json.dumps(decision, ensure_ascii=False))
    shadow = shadow_decision(args.task, decision) if args.shadow else None
    if args.dry_run or args.shadow:
        append_decision(args.task, decision, shadow=shadow)
    return 0


if __name__ == "__main__":
    sys.exit(main())
