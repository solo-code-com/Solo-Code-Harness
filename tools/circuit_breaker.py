#!/usr/bin/env python3
"""circuit_breaker.py — stop rule and loop budget enforcement for agent workflows.

Why this exists: AI agents can get trapped in repetitive error loops (e.g. failing
the same test or linter rule repeatedly, modifying adjacent files, and burning
tokens). This module enforces a deterministic Stop Rule:
  - Tracks consecutive failures per task/session
  - When the consecutive failure budget is exhausted (default: 3 iterations),
    it TRIPS the circuit breaker
  - Automatically emits a TASK_FAILURE_REPORT.md containing the failure evidence
  - DOES NOT destructively rollback or delete the user's uncommitted work
  - Exits with code 5 (CIRCUIT_BREAKER_TRIPPED), stopping further agent writes
    and escalating to the human engineer

State is kept in `.solocode/circuit-breaker-state.json`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_FILE = ROOT / ".solocode" / "circuit-breaker-state.json"
DEFAULT_MAX_ATTEMPTS = 3
CIRCUIT_BREAKER_EXIT_CODE = 5


def _normalize_error(error_text: str) -> str:
    """Normalize error text by stripping timestamps, memory addresses, and file line numbers."""
    text = error_text.lower().strip()
    # Strip memory addresses: 0x7fff...
    text = re.sub(r"0x[0-9a-fA-F]+", "0x...", text)
    # Strip ISO timestamps
    text = re.sub(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}(\.\d+)?", "<time>", text)
    # Collapse multiple whitespaces
    return re.sub(r"\s+", " ", text)


def _error_fingerprint(error_text: str) -> str:
    """Compute an MD5 fingerprint of the normalized error text."""
    normalized = _normalize_error(error_text)
    return hashlib.md5(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()[
        :12
    ]  # noqa: S324


def _load_state(state_file: Path = DEFAULT_STATE_FILE) -> dict[str, Any]:
    if not state_file.is_file():
        return {}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any], state_file: Path = DEFAULT_STATE_FILE) -> None:
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        print(
            f"[circuit_breaker] Warning: could not write state: {exc}", file=sys.stderr
        )


def emit_failure_report(
    task_id: str,
    attempts: int,
    last_error: str,
    output_path: Path | None = None,
) -> Path:
    """Generate a clean TASK_FAILURE_REPORT.md for human review."""
    target = output_path or (ROOT / "TASK_FAILURE_REPORT.md")
    report = [
        "# TASK FAILURE REPORT (Circuit Breaker Tripped)",
        f"- **Task ID**: `{task_id}`",
        f"- **Timestamp**: {datetime.now(timezone.utc).isoformat()}",
        f"- **Consecutive Failures**: {attempts} (threshold reached)",
        "- **Action Taken**: Stopped agent iteration loop immediately. Workspace preserved as-is.",
        "",
        "## 1. Last Recorded Error",
        "```text",
        last_error.strip(),
        "```",
        "",
        "## 2. Escalation & Recommended Next Steps",
        "1. **Do not force-retry** without changing strategy.",
        "2. Inspect the working tree (`git diff` / `git status`) to review what was attempted.",
        "3. Provide guidance or adjust requirements, then reset the breaker with:",
        f"   `python tools/circuit_breaker.py reset --task {task_id}`",
        "",
    ]
    target.write_text("\n".join(report), encoding="utf-8")
    return target


def check_and_record_failure(
    task_id: str,
    error_text: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    state_file: Path = DEFAULT_STATE_FILE,
    report_path: Path | None = None,
) -> tuple[bool, int, str]:
    """Record a failure and check whether the circuit breaker trips.

    Returns:
        (can_continue, current_attempt_count, message)
    """
    state = _load_state(state_file)
    task_data = state.get(
        task_id,
        {
            "consecutive_count": 0,
            "last_fingerprint": None,
            "last_error": None,
            "tripped": False,
        },
    )

    if task_data.get("tripped"):
        return (
            False,
            task_data["consecutive_count"],
            "Circuit breaker is already TRIPPED for this task.",
        )

    current_fp = _error_fingerprint(error_text)
    last_fp = task_data.get("last_fingerprint")

    # If same error fingerprint, increment consecutive counter; otherwise start counter at 1
    if last_fp == current_fp:
        consecutive = task_data.get("consecutive_count", 0) + 1
    else:
        consecutive = 1

    task_data["consecutive_count"] = consecutive
    task_data["last_fingerprint"] = current_fp
    task_data["last_error"] = error_text[:1000]
    task_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    if consecutive >= max_attempts:
        task_data["tripped"] = True
        state[task_id] = task_data
        _save_state(state, state_file)
        report_path = emit_failure_report(
            task_id, consecutive, error_text, output_path=report_path
        )
        msg = f"CIRCUIT BREAKER TRIPPED after {consecutive} failures. Report written to {report_path.name}."
        return False, consecutive, msg

    state[task_id] = task_data
    _save_state(state, state_file)
    msg = f"Failure recorded (attempt {consecutive} of {max_attempts})."
    return True, consecutive, msg


def record_success(task_id: str, state_file: Path = DEFAULT_STATE_FILE) -> None:
    """Reset the failure budget upon successful completion."""
    state = _load_state(state_file)
    if task_id in state:
        state.pop(task_id, None)
        _save_state(state, state_file)


def reset_circuit_breaker(task_id: str, state_file: Path = DEFAULT_STATE_FILE) -> bool:
    """Explicitly reset a tripped circuit breaker."""
    state = _load_state(state_file)
    if task_id in state:
        state.pop(task_id, None)
        _save_state(state, state_file)
        return True
    return False


def is_tripped(task_id: str, state_file: Path = DEFAULT_STATE_FILE) -> bool:
    """Check if the circuit breaker is currently tripped for a task."""
    state = _load_state(state_file)
    return bool(state.get(task_id, {}).get("tripped", False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Circuit breaker & loop budget manager"
    )
    sub = parser.add_subparsers(dest="command")

    check_cmd = sub.add_parser(
        "check", help="Record a failure and check breaker status"
    )
    check_cmd.add_argument("--task", required=True, help="Task or session ID")
    check_cmd.add_argument(
        "--error", required=True, help="Error message or failure text"
    )
    check_cmd.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help="Max failure attempts before trip",
    )
    check_cmd.add_argument(
        "--report-path", default=None, help="Custom output path for failure report"
    )

    reset_cmd = sub.add_parser("reset", help="Reset circuit breaker for a task")
    reset_cmd.add_argument("--task", required=True, help="Task or session ID")

    status_cmd = sub.add_parser("status", help="Check breaker status for a task")
    status_cmd.add_argument("--task", required=True, help="Task or session ID")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "check":
        report_target = Path(args.report_path) if args.report_path else None
        can_continue, count, msg = check_and_record_failure(
            args.task,
            args.error,
            max_attempts=args.max_attempts,
            report_path=report_target,
        )
        print(f"[{'OK' if can_continue else 'STOP'}] {msg}")
        return 0 if can_continue else CIRCUIT_BREAKER_EXIT_CODE

    if args.command == "reset":
        reset_circuit_breaker(args.task)
        print(f"Circuit breaker reset for task '{args.task}'.")
        return 0

    if args.command == "status":
        tripped = is_tripped(args.task)
        print(
            f"Task '{args.task}' circuit breaker status: {'TRIPPED' if tripped else 'CLOSED (normal)'}"
        )
        return CIRCUIT_BREAKER_EXIT_CODE if tripped else 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
