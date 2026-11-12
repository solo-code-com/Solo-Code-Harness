#!/usr/bin/env python3
"""UNVERIFIED CAPABILITY. Probe `gemini-decision-1`, which is a decision endpoint, not a chat model.

Google reports `outputTokenLimit: 1` and `supportedGenerationMethods: ["createDecision"]` for
`gemini-decision-1`, and the only route is `POST .../v1beta/decisions` -- a route the public
discovery documents do not describe. The current key answers HTTP 402 RESOURCE_EXHAUSTED for
every well-formed call, so the request schema below the field types is unknown: the billing wall
rejects the request before semantic validation runs. Only the field-type rejections (HTTP 400)
are measured, so they are encoded here as a fixed probe matrix and nothing here may be read as
a complete schema.

Three modes, all printing the same four-column table:

    python tools/decision_probe.py --matrix                  # the measured bodies, one call each
    python tools/decision_probe.py --record --label baseline # one live call, saved as a fixture
    python tools/decision_probe.py --replay --label baseline # newest fixture, zero network

A fixture is `{"ts", "url", "request", "status", "body"}` under `.solocode/decision-probes/`;
`request` is the exact text of the bytes sent, and the credential travels in the `x-goog-api-key`
header so it never appears in the recorded body. `--matrix` and `--record` exit 4 when the
provider answers 402, because a probe that is billed out has measured the wall and not the route.

This is a recording harness, not a routing brain: `tools/router.py` must never import it, and no
caller may treat its verdict as a model choice.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))
import env_file  # noqa: E402

ROOT = TOOLS_DIR.parent
#: The route Google serves for `createDecision`; undocumented in the public discovery documents.
URL = "https://generativelanguage.googleapis.com/v1beta/decisions"
MODEL = "gemini-decision-1"
#: Where `--record` writes fixtures. Local-only, never committed, never carries the key.
PROBE_DIR = ROOT / ".solocode" / "decision-probes"
#: The credential, in preference order. `.env` is read only through env_file.
KEY_NAMES = ("GOOGLE_GEMINI_KEY", "GEMINI_API_KEY")
UA = "opencode/1.0"
TIMEOUT = 30
#: The table column is "message (first 80 chars)", so nothing longer is kept.
MESSAGE_LIMIT = 80
DEFAULT_LABEL = "probe"

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_TRANSPORT = 3
EXIT_BILLING = 4

HTTP_OK = 200
HTTP_INVALID = 400
HTTP_BILLING = 402

BILLING_BLOCKED = "BILLING_BLOCKED"
REACHABLE = "REACHABLE"
SCHEMA_ONLY = "SCHEMA_ONLY"

#: Short code per HTTP status, so a row reads without the body.
_CODES = {
    HTTP_OK: "ok",
    HTTP_INVALID: "invalid_request",
    HTTP_BILLING: "billing_blocked",
}

#: Fixed table header; `--matrix` and `--replay` print the same four columns.
TABLE_HEADER = (
    "| case | status | code | message (first 80 chars) |",
    "| --- | --- | --- | --- |",
)

#: The fixed probe matrix in the order the failures were measured. `well_formed` marks a body
#: whose field types the schema can accept, so a 402 on it is the billing wall and not a shape
#: rejection. The malformed bodies cost a 400 and never reach billing.
MATRIX: tuple[tuple[str, dict, bool], ...] = (
    ("state", {"state": "probe"}, False),
    ("input-number", {"input": 1}, False),
    ("input-item-number", {"input": [1]}, False),
    ("input-item-text", {"input": [{"text": "t"}]}, False),
    ("model-number", {"model": 1}, False),
    ("questions-number", {"questions": 1}, False),
    ("empty", {}, True),
    ("model-only", {"model": MODEL}, True),
    ("input-empty", {"input": []}, True),
    ("questions-empty", {"questions": {}}, True),
)

#: The body `--record` sends: the most complete well-formed request this probe can build, so the
#: fixture captures what semantic validation would have seen had billing allowed it.
RECORD_BODY: dict = {"model": MODEL, "input": []}


class ProbeError(Exception):
    """A transport failure, never an HTTP status: the CLI exits 3 for it."""


def credential() -> str:
    """The first present credential from `.env` or the process environment; "" when none.

    The value is only ever put in a header: it is never printed and never written to a fixture.
    """
    values = env_file.parse_env_file(ROOT / ".env")
    for name in KEY_NAMES:
        value = values.get(name) or os.environ.get(name, "")
        if value:
            return value
    return ""


def _encode(payload: dict) -> bytes:
    """The exact bytes for one JSON request body."""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def post(
    data: bytes, key: str, *, url: str = URL, timeout: int = TIMEOUT
) -> tuple[int, str]:
    """POST `data` and return (status, body text); an HTTP error body is a result, not a failure.

    Only a transport failure (DNS, socket, timeout) raises `ProbeError`: a 400 or a 402 is the
    measurement this probe exists to take.
    """
    request = urllib.request.Request(  # noqa: S310 - the endpoint is a fixed https constant
        url,
        data=data,
        headers={
            "x-goog-api-key": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except OSError as exc:  # URLError, DNS failures and socket timeouts are all OSError
        raise ProbeError(f"{type(exc).__name__}: {exc}") from exc


def _message(body: str) -> str:
    """The provider's `error.message`, else the body with its whitespace collapsed."""
    with contextlib.suppress(json.JSONDecodeError, AttributeError):
        error = json.loads(body).get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return " ".join(error["message"].split())
    return " ".join(body.split())


def _code(status: int) -> str:
    """The short code for one HTTP status; an unmeasured status keeps its number."""
    return _CODES.get(status, f"http_{status}")


def normalize(status: int, body: str) -> tuple[int, str, str]:
    """(status, code, message) for one response, the message cut to 80 characters."""
    return status, _code(status), _message(body)[:MESSAGE_LIMIT]


def _row(case: str, status: int, body: str) -> dict:
    """One table row: the case name plus its normalized status, code and message."""
    status_code, code, message = normalize(status, body)
    return {"case": case, "status": status_code, "code": code, "message": message}


def _print_table(rows: list[dict]) -> None:
    """Print the fixed four-column table, one line per row."""
    for line in TABLE_HEADER:
        print(line)
    for row in rows:
        print(f"| {row['case']} | {row['status']} | {row['code']} | {row['message']} |")


def verdict(rows: list[dict]) -> str:
    """`REACHABLE` on any 200, `BILLING_BLOCKED` when every well-formed case is 402."""
    if any(row["status"] == HTTP_OK for row in rows):
        return REACHABLE
    well_formed = [row for row in rows if row.get("well_formed")]
    if well_formed and all(row["status"] == HTTP_BILLING for row in well_formed):
        return BILLING_BLOCKED
    return SCHEMA_ONLY


def run_matrix(key: str) -> list[dict]:
    """Send every MATRIX body in order and return one row per body."""
    rows: list[dict] = []
    for case, body, well_formed in MATRIX:
        status, text = post(_encode(body), key)
        row = _row(case, status, text)
        row["well_formed"] = well_formed
        rows.append(row)
    return rows


def _safe_label(label: str) -> str:
    """The label reduced to characters a fixture filename can carry."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-.") or DEFAULT_LABEL


def fixture_path(label: str) -> Path:
    """`PROBE_DIR/<UTC timestamp>-<label>.json`; the stamp sorts lexicographically."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return PROBE_DIR / f"{stamp}-{_safe_label(label)}.json"


def record_fixture(key: str, label: str) -> tuple[Path, int]:
    """Send RECORD_BODY once, write the fixture, and return (path, status)."""
    data = _encode(RECORD_BODY)
    status, text = post(data, key)
    fixture = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "url": URL,
        "request": data.decode("utf-8"),
        "status": status,
        "body": text,
    }
    path = fixture_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path, status


def newest_fixture(label: str | None = None) -> Path | None:
    """The newest fixture, optionally only those named `<stamp>-<label>.json`."""
    if not PROBE_DIR.is_dir():
        return None
    pattern = f"*-{_safe_label(label)}.json" if label else "*.json"
    matches = sorted(PROBE_DIR.glob(pattern))
    return matches[-1] if matches else None


def replay(label: str | None) -> int:
    """Print the newest fixture's row without touching the network."""
    path = newest_fixture(label)
    if path is None:
        scope = f"label {label!r}" if label else "any label"
        print(f"[decision_probe] no fixture recorded for {scope}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[decision_probe] unreadable fixture {path}: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    row = _row(
        path.stem, int(fixture.get("status") or 0), str(fixture.get("body") or "")
    )
    _print_table([row])
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe the gemini-decision-1 decision endpoint (matrix, record, replay)"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--matrix", action="store_true", help="run the fixed probe matrix"
    )
    mode.add_argument(
        "--record", action="store_true", help="one live call, saved as a fixture"
    )
    mode.add_argument(
        "--replay", action="store_true", help="print the newest fixture, no network"
    )
    parser.add_argument(
        "--label", help=f"fixture name suffix (default {DEFAULT_LABEL})"
    )
    args = parser.parse_args(argv)

    if args.replay:
        return replay(args.label)

    key = credential()
    if not key:
        print(
            f"[decision_probe] missing {' or '.join(KEY_NAMES)} in .env",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    try:
        if args.matrix:
            rows = run_matrix(key)
            _print_table(rows)
            result = verdict(rows)
            print(f"verdict: {result}")
            return EXIT_BILLING if result == BILLING_BLOCKED else EXIT_OK
        path, status = record_fixture(key, args.label or DEFAULT_LABEL)
    except ProbeError as exc:
        print(f"[decision_probe] {exc}", file=sys.stderr)
        return EXIT_TRANSPORT

    print(path)
    return EXIT_BILLING if status == HTTP_BILLING else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
