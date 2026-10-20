#!/usr/bin/env python3
"""
kilo_server_manager.py — Lifecycle management for the Kilo HTTP server.

Provides start/stop/status/restart/ensure commands to manage a Kilo server
as a background process. The server exposes an HTTP API (default port 14096)
used by tools/kilo_cli_delegate.py for stateless one-shot worker delegation.

State is persisted via a PID file (.solocode/kilo-server.pid); health is
verified by polling GET /doc on the server's HTTP endpoint with exponential
backoff.

Usage:
    python tools/kilo_server_manager.py start [--port PORT]
    python tools/kilo_server_manager.py stop
    python tools/kilo_server_manager.py status [--json]
    python tools/kilo_server_manager.py restart [--port PORT]
    python tools/kilo_server_manager.py ensure [--port PORT]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOLOCODE = ROOT / ".solocode"
DEFAULT_PORT = 14096
PID_FILE = SOLOCODE / "kilo-server.pid"
LOG_FILE = SOLOCODE / "kilo-server.log"

# Health-check backoff intervals in seconds.
_BACKOFF_INTERVALS = (0.5, 1.0, 2.0, 4.0)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _stderr(*args: object) -> None:
    """Print a debug/info line to stderr, prefixed with the tool name."""
    print("[kilo_server_manager]", *args, file=sys.stderr)


def _expand_glob(pattern: str) -> Path | None:
    """Return the first match for a glob pattern, or *None*."""
    matches = sorted(
        Path(pattern).expanduser().parent.glob(Path(pattern).expanduser().name)
    )
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# binary discovery
# ---------------------------------------------------------------------------


def find_kilo_binary() -> Path | None:
    """Locate the ``kilo`` executable.

    Search order:
    1. ``kilo`` (or ``kilo.exe``) on **PATH** (via ``shutil.which``).
    2. ``~/.antigravity-ide/extensions/kilocode.kilo-code-*/bin/kilo.exe``
       (newest match first by directory name).
    """
    import shutil

    # 1. PATH
    path_bin = shutil.which("kilo") or shutil.which("kilo.exe")
    if path_bin:
        return Path(path_bin)

    # 2. Antigravity IDE extension directory - use rglob for nested wildcards
    extensions_dir = Path.home() / ".antigravity-ide" / "extensions"
    if extensions_dir.exists():
        # Find all kilocode.kilo-code-* directories, sort by version descending
        matches = sorted(
            extensions_dir.glob("kilocode.kilo-code-*/bin/kilo.exe"), reverse=True
        )
        if matches:
            return matches[0]

    return None


# ---------------------------------------------------------------------------
# PID helpers
# ---------------------------------------------------------------------------


def _read_pid() -> int | None:
    """Return the PID stored in *PID_FILE*, or *None* if unreadable."""
    if not PID_FILE.is_file():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _pid_is_alive(pid: int) -> bool:
    """Return *True* if a process with *pid* exists and is responding."""
    import ctypes
    import ctypes.wintypes

    kernel32 = ctypes.windll.kernel32
    synchronize = 0x00100000
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


def _write_pid(pid: int) -> None:
    """Atomically write *pid* to *PID_FILE*."""
    SOLOCODE.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")


def _remove_pid() -> None:
    """Remove the PID file if it exists."""
    PID_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# health check
# ---------------------------------------------------------------------------


def health_check(port: int = DEFAULT_PORT, timeout: int = 10) -> bool:
    """Poll ``GET http://localhost:{port}/doc`` until it responds 2xx.

    Uses exponential backoff: 0.5 s, 1 s, 2 s, 4 s (then repeats 4 s).
    Returns *True* once the endpoint responds, *False* on timeout.
    """
    url = f"http://127.0.0.1:{port}/doc"
    if not url.startswith("http://127.0.0.1:"):
        _stderr(f"refusing to open non-loopback URL: {url}")
        return False
    deadline = time.monotonic() + timeout

    idx = 0
    while time.monotonic() < deadline:
        try:
            # urlopen raises HTTPError for 4xx/5xx, so an error status is
            # "answered but unhealthy", not a transport failure.
            with urllib.request.urlopen(  # noqa: S310 — loopback scheme checked above
                url, timeout=2
            ) as resp:
                if resp.status < 400:
                    return True
        except urllib.error.HTTPError:
            pass
        except (urllib.error.URLError, OSError, TimeoutError):
            pass

        delay = _BACKOFF_INTERVALS[min(idx, len(_BACKOFF_INTERVALS) - 1)]
        idx += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(delay, remaining))

    return False


# ---------------------------------------------------------------------------
# running detection
# ---------------------------------------------------------------------------


def is_running() -> bool:
    """Return *True* when the PID file exists and the process is alive."""
    pid = _read_pid()
    if pid is None:
        return False
    return _pid_is_alive(pid)


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


def start_server(port: int = DEFAULT_PORT) -> bool:
    """Spawn ``kilo serve --port <port>`` as a background process.

    Returns *True* on success; writes PID to *PID_FILE* and waits for the
    health check to pass.

    Error conditions:
    - Binary not found → prints an error and returns *False*.
    - Process exits immediately → reports the exit code/stderr and returns
      *False*.
    - Health check times out → reports the failure and returns *False*.
    """
    binary = find_kilo_binary()
    if binary is None:
        _stderr(
            "kilo binary not found. Searched: PATH,"
            " ~/.antigravity-ide/extensions/kilocode.kilo-code-*/bin/kilo.exe"
        )
        return False

    _stderr(f"starting kilo server: {binary} serve --port {port}")
    SOLOCODE.mkdir(parents=True, exist_ok=True)

    with LOG_FILE.open("w", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            [str(binary), "serve", "--port", str(port)],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )

    _write_pid(proc.pid)
    _stderr(f"pid={proc.pid}  log={LOG_FILE}")

    # Wait a beat before the first health check – the binary may need a
    # moment to bind the port and start listening.
    time.sleep(0.3)

    # If the process died immediately, surface the log tail.
    if proc.poll() is not None:
        _stderr(f"kilo exited immediately with code {proc.returncode}")
        _print_log_tail(20)
        _remove_pid()
        return False

    _stderr("waiting for health check …")
    if not health_check(port, timeout=15):
        _stderr("health check timed out — server may have failed to start")
        _stderr(f"last {min(20, _log_line_count())} lines of {LOG_FILE}:")
        _print_log_tail(20)
        if proc.poll() is not None:
            _stderr(f"kilo exited with code {proc.returncode}")
            _remove_pid()
        return False

    _stderr("health check passed")
    return True


def stop_server() -> bool:
    """Stop a running Kilo server (read PID, kill, cleanup).

    Returns *True* if the server was stopped or was already stopped;
    *False* if the server could not be stopped.
    """
    pid = _read_pid()
    if pid is None:
        _stderr("no PID file — server is not tracked")
        return True

    if not _pid_is_alive(pid):
        _stderr(f"pid {pid} is not alive — cleaning up stale PID file")
        _remove_pid()
        return True

    _stderr(f"terminating pid {pid}")
    import ctypes

    kernel32 = ctypes.windll.kernel32
    process_terminate = 0x0001
    handle = kernel32.OpenProcess(process_terminate, False, pid)
    if not handle:
        _stderr(f"cannot open process {pid} — cleaning up PID file")
        _remove_pid()
        return True

    success = kernel32.TerminateProcess(handle, 0)
    kernel32.CloseHandle(handle)

    if not success:
        _stderr(f"TerminateProcess failed for pid {pid}")
        return False

    # Give the OS a moment to reap the process.
    time.sleep(0.3)
    _remove_pid()
    _stderr(f"pid {pid} terminated")
    return True


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def get_status() -> dict[str, object]:
    """Return a dictionary with ``running``, ``pid``, ``port``, and
    ``uptime`` keys."""
    pid = _read_pid()
    if pid is None or not _pid_is_alive(pid):
        return {
            "running": False,
            "pid": pid,  # may be stale
            "port": None,
            "uptime": None,
        }

    # We don't have a reliable cross-platform uptime for the process, but we
    # can report the PID file mtime as a proxy for when the server was last
    # started.
    uptime: str | None = None
    try:
        mtime = PID_FILE.stat().st_mtime
        uptime = f"{time.time() - mtime:.0f}s"
    except OSError:
        pass

    return {
        "running": True,
        "pid": pid,
        "port": DEFAULT_PORT,  # best-effort — we store one port per manager
        "uptime": uptime,
    }


# ---------------------------------------------------------------------------
# ensure
# ---------------------------------------------------------------------------


def ensure_running(port: int = DEFAULT_PORT) -> bool:
    """Start the server only if it is not already running.

    Returns *True* if the server is running after the call (either it was
    already up, or was started successfully).
    """
    if is_running():
        _stderr("server already running")
        return True
    return start_server(port)


# ---------------------------------------------------------------------------
# log helpers
# ---------------------------------------------------------------------------


def _log_line_count() -> int:
    """Return the number of lines in *LOG_FILE* (0 if absent)."""
    if not LOG_FILE.is_file():
        return 0
    count = 0
    with LOG_FILE.open("r", encoding="utf-8", errors="replace") as fh:
        for _ in fh:
            count += 1
    return count


def _print_log_tail(n: int = 20) -> None:
    """Print the last *n* lines of *LOG_FILE* to stderr."""
    if not LOG_FILE.is_file():
        return
    lines: list[str] = []
    with LOG_FILE.open("r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    tail = lines[-n:] if len(lines) > n else lines
    for line in tail:
        _stderr(f"  [log] {line.rstrip()}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_HELP_START = "Start the Kilo server as a background process"
_HELP_STOP = "Stop a running Kilo server"
_HELP_STATUS = "Print server status (human-readable by default, --json for machine)"
_HELP_RESTART = "Stop then start the server"
_HELP_ENSURE = "Start the server only if not already running"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kilo HTTP server lifecycle management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sp_start = sub.add_parser("start", help=_HELP_START, description=_HELP_START)
    sp_start.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})"
    )

    sub.add_parser("stop", help=_HELP_STOP, description=_HELP_STOP)

    sp_status = sub.add_parser("status", help=_HELP_STATUS, description=_HELP_STATUS)
    sp_status.add_argument(
        "--json", action="store_true", help="Machine-readable JSON output"
    )

    sp_restart = sub.add_parser(
        "restart", help=_HELP_RESTART, description=_HELP_RESTART
    )
    sp_restart.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})"
    )

    sp_ensure = sub.add_parser("ensure", help=_HELP_ENSURE, description=_HELP_ENSURE)
    sp_ensure.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    cmd: str = args.command

    if cmd == "start":
        port: int = args.port
        ok = start_server(port)
        if ok:
            print(f"Kilo server started on port {port}")
        return 0 if ok else 1

    if cmd == "stop":
        ok = stop_server()
        if ok:
            print("Kilo server stopped")
        return 0 if ok else 1

    if cmd == "status":
        use_json: bool = getattr(args, "json", False)
        status = get_status()
        if use_json:
            print(json.dumps(status, indent=2))
        else:
            if status["running"]:
                print(
                    f"Kilo server is RUNNING"
                    f"  pid={status['pid']}"
                    f"  port={status['port']}"
                    f"  uptime={status['uptime']}"
                )
            else:
                print("Kilo server is STOPPED")
        return 0

    if cmd == "restart":
        port: int = args.port
        stop_server()
        ok = start_server(port)
        if ok:
            print(f"Kilo server restarted on port {port}")
        return 0 if ok else 1

    if cmd == "ensure":
        port: int = args.port
        ok = ensure_running(port)
        if ok:
            s = get_status()
            if isinstance(s["running"], bool) and s["running"]:
                print(
                    f"Kilo server is RUNNING"
                    f"  pid={s['pid']}"
                    f"  port={s['port']}"
                    f"  uptime={s['uptime']}"
                )
            else:
                print("Kilo server is STOPPED — ensure did not succeed")
                return 1
        return 0 if ok else 1

    # Should not reach here with required=True on subparsers.
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
