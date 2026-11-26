#!/usr/bin/env python3
"""session_control.py — list / send / interrupt the harness's own child sessions.

The control plane half of gap G7 (spec in `plan.md` §4.1). `session_registry.py`
records the sessions; this module acts on them:

    python tools/session_control.py list
    python tools/session_control.py send <run_id> "extra instruction"
    python tools/session_control.py interrupt <run_id>

Two rules shape the design:

* **The process table is the truth.** `interrupt` verifies the pid is gone after
  the kill instead of reporting success from the kill call alone.
* **No pretending.** `send_message` only works for a session that declared a
  channel the controller can actually write to. Every other session gets
  `unsupported` — a worker that never reads its inbox would otherwise look like it
  received the instruction.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # noqa: S404 — fixed argv, no shell
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import session_registry  # noqa: E402 — needs the path fix above

_KILL_TIMEOUT_S = 10.0
_POLL_INTERVAL_S = 0.1
FILE_CHANNEL = "file"
UNSUPPORTED = "unsupported"
_CREATE_NO_WINDOW = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
)


def _descendant_pids_windows_free(pid: int) -> list[int]:
    """Pids whose parent is ``pid`` (POSIX); the deepest-first order is built here."""
    try:
        proc = subprocess.run(  # noqa: S603, S607 — fixed argv
            ["ps", "-o", "pid=", "--ppid", str(pid)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line) for line in proc.stdout.split() if line.strip().isdigit()]


def kill_tree(pid: int) -> list[int]:
    """Terminate ``pid`` and its descendants; return every pid we signalled.

    Windows uses ``taskkill /T`` (one call, whole tree). POSIX walks the process
    tree through ``ps --ppid`` and kills children first: sending SIGKILL to a
    process group would be shorter, but the sessions are not started in their own
    group, so a group kill could take the controller down with them.
    """
    if pid <= 1 or pid == os.getpid():
        # pid 1 is init on POSIX, 0 is "all processes" to many APIs, and the
        # controller must never interrupt itself.
        raise ValueError(f"refusing to kill pid {pid}")
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603, S607 — fixed argv
            ["taskkill", "/T", "/F", "/PID", str(pid)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            creationflags=_CREATE_NO_WINDOW,
        )
        return [pid]
    killed: list[int] = []
    for child in _descendant_pids_windows_free(pid):
        killed.extend(kill_tree(child))
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        return killed
    except PermissionError:
        return killed
    killed.append(pid)
    return killed


def wait_for_exit(pid: int, timeout_s: float = _KILL_TIMEOUT_S) -> bool:
    """Whether ``pid`` is gone, polling until the timeout: kill is not instant."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not session_registry.pid_alive(pid):
            return True
        time.sleep(_POLL_INTERVAL_S)
    return not session_registry.pid_alive(pid)


def interrupt(
    run_id: str, *, session_dir: str | Path | None = None
) -> dict[str, object]:
    """Kill a session's process tree and settle its record.

    Returns a result dict; `killed` is only True once the pid is verifiably gone,
    and an already-exited session is reported as such rather than as a failure.
    """
    session = find(run_id, session_dir=session_dir)
    if session is None:
        return {"run_id": run_id, "error": "unknown run id", "killed": False}
    pid = session.get("pid")
    if not isinstance(pid, int):
        return {"run_id": run_id, "error": "record carries no pid", "killed": False}
    if not session_registry.pid_alive(pid):
        session_registry.record_finish(
            run_id, session.get("exit_code"), state="exited", session_dir=session_dir
        )
        return {
            "run_id": run_id,
            "pid": pid,
            "killed": False,
            "reason": "already exited",
        }

    # Claim the session before killing it. The delegate's communicate() returns the
    # moment the worker dies, so both writers would otherwise race to settle one
    # record; whichever wrote first would win, and the delegate's `exited` would
    # hide that an operator interrupted the run.
    session_registry.record_finish(
        run_id, None, state=session_registry.INTERRUPTING, session_dir=session_dir
    )

    signalled = kill_tree(pid)
    gone = wait_for_exit(pid)
    session_registry.record_finish(
        run_id,
        None,
        state="interrupted" if gone else "interrupt-failed",
        only_if_running=False,  # the record is `interrupting`, claimed just above
        session_dir=session_dir,
    )
    return {
        "run_id": run_id,
        "pid": pid,
        "signalled": signalled,
        "killed": gone,
        "reason": "tree terminated" if gone else "pid still alive after the kill",
    }


def send_message(
    run_id: str, text: str, *, session_dir: str | Path | None = None
) -> dict[str, object]:
    """Append an instruction to a session's channel, or refuse loudly.

    Only the `file` channel is implemented: the worker polls the inbox the
    delegate gave it. A session that declared no channel returns `unsupported`
    instead of a success that no worker would ever see.
    """
    session = find(run_id, session_dir=session_dir)
    if session is None:
        return {"run_id": run_id, "status": "unknown run id", "delivered": False}
    if session.get("state") != session_registry.RUNNING:
        return {
            "run_id": run_id,
            "status": f"session is {session.get('state')}",
            "delivered": False,
        }
    channel = session.get("channel")
    inbox = session.get("inbox_path")
    if channel != FILE_CHANNEL or not inbox:
        return {
            "run_id": run_id,
            "status": UNSUPPORTED,
            "delivered": False,
            "reason": "this worker declared no channel it reads mid-run",
        }
    path = Path(str(inbox))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text.rstrip("\n") + "\n")
    except OSError as exc:
        return {"run_id": run_id, "status": f"write failed: {exc}", "delivered": False}
    return {
        "run_id": run_id,
        "status": "delivered",
        "delivered": True,
        "channel": FILE_CHANNEL,
        "inbox": str(path),
    }


def find(run_id: str, *, session_dir: str | Path | None = None) -> dict | None:
    for session in session_registry.list_sessions(session_dir=session_dir):
        if session.get("run_id") == run_id:
            return session
    return None


def prune(
    *,
    older_than_days: float,
    session_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Delete settled records older than the cutoff; never a live session.

    Records accumulate on their own — every keyed e2e run that drives a delegate
    for real writes one — and nothing else trims them. A record is only prunable
    once it is settled: `running` means the pid is alive, so pruning it would drop
    the only handle the control plane has on a live run.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - older_than_days * 86400
    removed: list[str] = []
    kept: list[str] = []
    live: list[str] = []
    for session in session_registry.list_sessions(session_dir=session_dir):
        run_id = str(session.get("run_id"))
        if session.get("state") in (
            session_registry.RUNNING,
            session_registry.INTERRUPTING,
        ):
            live.append(run_id)
            continue
        stamp = session.get("finished_at") or session.get("started_at")
        if not _older_than(stamp, cutoff):
            kept.append(run_id)
            continue
        if not dry_run:
            session_registry.forget(run_id, session_dir=session_dir)
        removed.append(run_id)
    return {
        "dry_run": dry_run,
        "removed": removed,
        "kept": kept,
        "live": live,
    }


def _older_than(stamp: object, cutoff: float) -> bool:
    """Whether an ISO-8601 stamp predates the cutoff. An unreadable one is kept."""
    if not isinstance(stamp, str) or not stamp:
        return False
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() < cutoff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="control the harness's child sessions")
    parser.add_argument("--session-dir", default=None, help="override the registry dir")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="every recorded session, newest first")
    send = sub.add_parser("send", help="send an instruction to a session")
    send.add_argument("run_id")
    send.add_argument("text")
    stop = sub.add_parser("interrupt", help="kill a session's process tree")
    stop.add_argument("run_id")
    trim = sub.add_parser("prune", help="delete settled records older than a cutoff")
    trim.add_argument("--older-than-days", type=float, required=True)
    trim.add_argument("--dry-run", action="store_true", help="report without deleting")

    args = parser.parse_args(argv)
    if args.command == "list":
        print(
            json.dumps(
                session_registry.list_sessions(session_dir=args.session_dir), indent=2
            )
        )
        return 0
    if args.command == "send":
        result = send_message(args.run_id, args.text, session_dir=args.session_dir)
        print(json.dumps(result, indent=2))
        return 0 if result.get("delivered") else 2
    if args.command == "interrupt":
        result = interrupt(args.run_id, session_dir=args.session_dir)
        print(json.dumps(result, indent=2))
        return (
            0 if result.get("killed") or result.get("reason") == "already exited" else 1
        )
    if args.command == "prune":
        result = prune(
            older_than_days=args.older_than_days,
            session_dir=args.session_dir,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
