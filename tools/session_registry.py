#!/usr/bin/env python3
"""session_registry.py — the harness's own record of its child sessions.

Why this exists (gap G7): every delegate runs a worker as a blocking subprocess,
so nothing could answer "which child sessions are alive right now?". This module
is that answer — one JSON file per run under `.solocode/sessions/`, with the state
settled against the process table instead of trusted from the file.

The file is a cache; the process is the truth. A record whose pid is gone reports
`stale`, never `running`.

Usage:
    from session_registry import record_start, record_finish, list_sessions

    run_id = record_start("opencode", model="commandcode/deepseek-v4.1-flash", pid=proc.pid)
    record_finish(run_id, proc.returncode)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSION_DIR = ROOT / ".solocode" / "sessions"

RUNNING = "running"
EXITED = "exited"
STALE = "stale"
INTERRUPTING = "interrupting"
# Keeps .solocode/sessions bounded: records accumulate on their own, one per run
# that drives a delegate, and nothing else trims them. See _enforce_retention.
MAX_SETTLED_RECORDS = 200
_ERROR_ACCESS_DENIED = 5
_STILL_ACTIVE = 259
_QUERY_LIMITED_INFORMATION = 0x1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pid_alive(pid: int) -> bool:
    """Whether a process with this pid exists.

    Deliberately not ``os.kill(pid, 0)``: on Windows os.kill calls
    TerminateProcess for every signal it does not special-case, which would kill
    the very session being asked about.

    On POSIX an unreaped SIGKILLed child stays a zombie, which os.kill(pid, 0)
    still reports as present, so a zombie is settled as dead here by asking ps.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return not _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    """Whether ps reports this pid as a zombie (state whose first letter is Z)."""
    try:
        proc = subprocess.run(  # noqa: S603, S607 - fixed argv
            ["ps", "-o", "stat=", "-p", str(pid)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.stdout.strip().startswith("Z")


def _pid_alive_windows(pid: int) -> bool:
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    k32.GetExitCodeProcess.restype = ctypes.c_int
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = k32.OpenProcess(_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Access denied means the process is there, just not ours to query.
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_ulong()
        if k32.GetExitCodeProcess(handle, ctypes.byref(code)) == 0:
            return False
        return code.value == _STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def _session_dir(session_dir: str | Path | None) -> Path:
    return Path(session_dir) if session_dir else SESSION_DIR


def session_path(run_id: str, *, session_dir: str | Path | None = None) -> Path:
    return _session_dir(session_dir) / f"{run_id}.json"


def _write_atomically(path: Path, payload: dict[str, object]) -> None:
    """Write via a temp file and replace, so a reader never sees half a JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def _enforce_retention(directory: Path) -> int:
    """Drop the oldest settled records once their count exceeds the cap.

    The bound lives here rather than in a cleanup step somebody must remember: the
    files appear on their own, one per run that drives a delegate. Live records are
    never candidates — a `running` file is the only handle the control plane has on
    a session that is still going.
    """
    if MAX_SETTLED_RECORDS <= 0 or not directory.is_dir():
        return 0
    settled = [
        session
        for session in list_sessions(session_dir=directory)
        if session.get("state") not in (RUNNING, INTERRUPTING)
    ]
    excess = len(settled) - MAX_SETTLED_RECORDS
    if excess <= 0:
        return 0
    # list_sessions is newest first, so the tail holds the oldest records.
    for session in settled[len(settled) - excess :]:
        forget(str(session.get("run_id")), session_dir=directory)
    return excess


def record_start(
    worker: str,
    *,
    model: str | None = None,
    pid: int | None = None,
    argv: list[str] | None = None,
    log_path: str | Path | None = None,
    channel: str | None = None,
    inbox_path: str | Path | None = None,
    session_dir: str | Path | None = None,
) -> str:
    """Write the opening record, defaulting the pid to this process, and return its id.

    ``channel``/``inbox_path`` describe how the session can receive mid-run
    instructions; a session that declares neither is reported as `unsupported`
    by the controller rather than looking reachable.
    """
    run_id = uuid.uuid4().hex[:12]
    _enforce_retention(_session_dir(session_dir))
    _write_atomically(
        session_path(run_id, session_dir=session_dir),
        {
            "run_id": run_id,
            "worker": worker,
            "model": model,
            "pid": os.getpid() if pid is None else pid,
            "argv": list(argv or []),
            "log_path": str(log_path) if log_path else None,
            "channel": channel,
            "inbox_path": str(inbox_path) if inbox_path else None,
            "started_at": _now(),
            "finished_at": None,
            "exit_code": None,
            "state": RUNNING,
        },
    )
    return run_id


def record_finish(
    run_id: str,
    exit_code: int | None,
    *,
    state: str = EXITED,
    only_if_running: bool = True,
    session_dir: str | Path | None = None,
) -> bool:
    """Settle a record. False when there is nothing to settle, or it is settled.

    ``only_if_running`` defaults on because two writers race on the same file: the
    controller may already have marked the session `interrupted`, and a delegate
    finishing afterwards must not replace that verdict with `exited`.
    """
    path = session_path(run_id, session_dir=session_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if only_if_running and payload.get("state") != RUNNING:
        return False
    payload["exit_code"] = exit_code
    payload["finished_at"] = _now()
    payload["state"] = state
    _write_atomically(path, payload)
    return True


def forget(run_id: str, *, session_dir: str | Path | None = None) -> bool:
    """Delete a record. False when there is no such file to delete."""
    try:
        session_path(run_id, session_dir=session_dir).unlink()
    except OSError:
        return False
    return True


def list_sessions(*, session_dir: str | Path | None = None) -> list[dict[str, object]]:
    """Every recorded session, newest first, each state settled against its pid."""
    directory = _session_dir(session_dir)
    if not directory.is_dir():
        return []
    sessions: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # a torn or foreign file is not a session
        if not isinstance(payload, dict) or "run_id" not in payload:
            continue
        sessions.append(_settle(payload))
    sessions.sort(key=lambda entry: str(entry.get("started_at") or ""), reverse=True)
    return sessions


def _settle(payload: dict[str, object]) -> dict[str, object]:
    """Report `stale` for a record that claims to be live under a dead pid."""
    pid = payload.get("pid")
    state = payload.get("state")
    if state in (RUNNING, INTERRUPTING) and isinstance(pid, int) and not pid_alive(pid):
        return {**payload, "state": STALE}
    return payload
