#!/usr/bin/env python3
"""gui_server.py — local web console for the JEV worker fleet (stdlib only).

Serves one page on 127.0.0.1 that lets the operator:

  - see which worker CLIs are installed on this machine (agy, opencode, kilo,
    dsh) and whether their delegate wrapper exists,
  - see the configured providers/models and which `.env` keys exist,
  - compose a brief and run it through the matching `tools/*_delegate.py`.

Security stance (a local page that can spawn processes needs one):

  - loopback-only bind;
  - a per-run random token required on every `/api/*` call (the token is
    injected into the served HTML, so a same-origin page can call the API);
  - a fixed worker allowlist plus a model allowlist read from the catalog, so
    the browser cannot make the server run arbitrary commands or models;
  - secret VALUES are never read out of `.env` — only key NAMES are reported.

Usage:
    python tools/gui_server.py                 # http://127.0.0.1:8765
    python tools/gui_server.py --port 9000 --open
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import secrets
import shutil
import subprocess  # noqa: S404 — fixed argv, allowlisted worker + model
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DOC = Path(__file__).resolve().parent / "gui" / "index.html"
OPENCODE_JSON = ROOT / ".opencode" / "opencode.json"
ENV_FILE = ROOT / ".env"

MAX_BODY = 64 * 1024
MAX_BRIEF = 20_000

# (id, label, cli, wrapper, note). `wrapper` is relative to the repo root.
_WORKER_SPEC: list[tuple[str, str, str, str, str | None]] = [
    (
        "gemini",
        "Gemini (Antigravity CLI)",
        "agy",
        "tools/antigravity_delegate.py",
        None,
    ),
    ("opencode", "OpenCode / DeepSeek", "opencode", "tools/opencode_delegate.py", None),
    ("kilo", "Kilo CLI", "kilo", "tools/kilo_cli_delegate.py", None),
    ("dsh", "DeepSeek Harness (dsh)", "dsh", "tools/dsh_delegate.py", None),
    (
        "jev",
        "JEV orchestrator (OpenCode agent)",
        "opencode",
        "tools/opencode_delegate.py",
        None,
    ),
]


def env_key_names(env_file: Path = ENV_FILE) -> list[str]:
    """Return the KEY names in a .env file. Values are never returned."""
    names: list[str] = []
    if not env_file.is_file():
        return names
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip()
        if name and name not in names:
            names.append(name)
    return names


def providers(opencode_json: Path = OPENCODE_JSON) -> dict[str, list[str]]:
    """provider name -> sorted model keys, from the OpenCode v2 catalog."""
    try:
        data = json.loads(opencode_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, list[str]] = {}
    for name, prov in (data.get("providers") or {}).items():
        models = (prov or {}).get("models") or {}
        result[name] = sorted(models.keys())
    return result


def workers(root: Path = ROOT) -> list[dict[str, Any]]:
    """Discovery: CLI on PATH + wrapper present + a stable runnable flag."""
    found: list[dict[str, Any]] = []
    for wid, label, cli, wrapper, note in _WORKER_SPEC:
        cli_path = shutil.which(cli)
        wrapper_ready = (root / wrapper).is_file()
        found.append(
            {
                "id": wid,
                "label": label,
                "cli": cli,
                "cli_path": cli_path,
                "wrapper": wrapper,
                "wrapper_ready": wrapper_ready,
                "note": note,
                "ready": bool(cli_path and wrapper_ready and note is None),
            }
        )
    return found


def known_models(catalog: dict[str, list[str]]) -> list[dict[str, str]]:
    """Flat, browser-safe model list: {provider, key, ref}."""
    out: list[dict[str, str]] = []
    for provider, keys in sorted(catalog.items()):
        for key in keys:
            out.append({"provider": provider, "key": key, "ref": f"{provider}/{key}"})
    return out


def build_argv(
    worker: str, brief: str, model: str | None, flags: dict[str, Any]
) -> list[str]:
    """Build the delegate argv. Pure — the security-relevant part, so it is tested."""
    py = sys.executable
    if worker == "gemini":
        argv = [py, "tools/antigravity_delegate.py", brief]
        if model:
            argv += ["--model", model]
        if flags.get("allow_tools"):
            argv.append("--allow-tools")
        return argv
    if worker == "opencode":
        argv = [py, "tools/opencode_delegate.py", brief]
        if model:
            argv += ["--model", model]
        if flags.get("free"):
            argv.append("--free")
        return argv
    if worker == "jev":
        argv = [py, "tools/opencode_delegate.py", brief, "--agent", "jev"]
        if model:
            argv += ["--model", model]
        if flags.get("session"):
            argv += ["--session", str(flags["session"])]
        return argv
    if worker == "kilo":
        argv = [py, "tools/kilo_cli_delegate.py", brief]
        if model:
            argv += ["--model", model]
        return argv
    if worker == "dsh":
        return [py, "tools/dsh_delegate.py", brief]

    raise ValueError(f"worker not runnable: {worker!r}")


def validate_run(
    worker: str, brief: str, model: str | None, allowed_refs: set[str]
) -> None:
    runnable = {w[0] for w in _WORKER_SPEC if w[4] is None}
    if worker not in runnable:
        raise ValueError(f"worker {worker!r} is not runnable")
    if not brief.strip():
        raise ValueError("brief is empty")
    if len(brief) > MAX_BRIEF:
        raise ValueError(f"brief longer than {MAX_BRIEF} chars")
    if model and model not in allowed_refs:
        raise ValueError(f"model {model!r} is not in the catalog")
    if worker == "gemini" and model and not re.fullmatch(r"[A-Za-z0-9._/-]+", model):
        raise ValueError("bad model string")


def run_task(
    worker: str, brief: str, model: str | None, flags: dict[str, Any], timeout_s: int
) -> dict[str, Any]:
    argv = build_argv(worker, brief, model, flags)
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, browser cannot inject
            argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "argv": argv,
            "returncode": None,
            "stdout": "",
            "stderr": f"timeout after {timeout_s}s",
        }
    return {
        "argv": argv,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def change_summary(root: Path = ROOT) -> dict[str, str]:
    """Post-run `git status` / `git diff --stat`, so any write is visible."""

    def _git(*args: str) -> str:
        try:
            proc = subprocess.run(  # noqa: S603 — fixed argv (git + constants)
                ["git", "-C", str(root), *args],  # noqa: S607 — fixed argv
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return proc.stdout.strip()

    return {"status": _git("status", "--porcelain"), "diffstat": _git("diff", "--stat")}


# ── executor mode (mirrors .claude/hooks/guard.py) ───────────────────────────
_EXECUTOR_OFF = frozenset({"off", "0", "disabled", "false", "no"})

# Per-worker write fence, for the console's status panel. "read-only" means the
# wrapper refuses writes without an explicit flag; "unfenced" means the delegate
# wrapper enforces no write boundary of its own.
_SANDBOX = {
    "gemini": "read-only (writes need --allow-dir + --auto-approve)",
    "opencode": "unfenced (wrapper can write)",
    "kilo": "unfenced (wrapper can write)",
    "dsh": "profile-defined",
}


def executor_mode_enabled(root: Path = ROOT) -> bool:
    """True when the orchestrator write gate is ON. Absent file = ON (fail closed)."""
    try:
        raw = (root / ".solocode" / "executor-mode").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True
    return raw.strip().split("#", 1)[0].strip().lower() not in _EXECUTOR_OFF


def executor_mode_set(on: bool, root: Path = ROOT) -> None:
    """Write the toggle: ON writes 'on', OFF writes the literal 'off'."""
    directory = root / ".solocode"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "executor-mode").write_text(
        "on\n" if on else "off\n", encoding="utf-8"
    )


# ── running streams, so the console can Stop one ─────────────────────────────
_RUNNING: dict[str, Any] = {}
_RUNNING_LOCK = threading.Lock()


def stop_run(run_id: str) -> bool:
    """Kill a streamed run by id. False when the id is unknown or already done."""
    with _RUNNING_LOCK:
        proc = _RUNNING.pop(run_id, None)
    if proc is None:
        return False
    proc.kill()
    return True


def stop_all() -> int:
    """Kill every streamed run. Returns how many were running."""
    with _RUNNING_LOCK:
        procs = list(_RUNNING.values())
        _RUNNING.clear()
    for proc in procs:
        proc.kill()
    return len(procs)


def _sse(event: str, data: Any) -> bytes:
    """Frame one Server-Sent Event. Pure — tested directly."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


# Indirection so tests can stream a trivial command instead of a real worker.
_stream_argv = build_argv


def stream_task(
    argv: list[str], timeout_s: int, run_id: str | None = None
) -> Iterator[tuple[str, Any]]:
    """Yield (event, data) frames while running argv, merging stderr into stdout.

    Events: ``start`` (argv + run_id), ``chunk`` (one output line), ``error``
    (launch failure) and ``done`` (exit code, stop reason, change summary). While
    the child runs it is registered under ``run_id``, so the console can Stop it.
    """
    yield ("start", {"argv": argv, "run_id": run_id})
    try:
        proc = subprocess.Popen(  # noqa: S603 — fixed argv, allowlisted worker
            argv,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        yield ("error", {"error": str(exc)})
        return
    if run_id is not None:
        with _RUNNING_LOCK:
            _RUNNING[run_id] = proc
    lines: queue.Queue[str | None] = queue.Queue()

    def _reader() -> None:
        try:
            for line in proc.stdout or []:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=_reader, daemon=True).start()
    deadline = time.monotonic() + timeout_s
    timed_out = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                proc.kill()
                break
            try:
                item = lines.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if proc.poll() is not None and lines.empty():
                    break
                continue
            if item is None:
                break
            yield ("chunk", {"line": item.rstrip("\n")})
        code = proc.wait()
    finally:
        if run_id is not None:
            with _RUNNING_LOCK:
                _RUNNING.pop(run_id, None)
    yield (
        "done",
        {
            "exit": None if timed_out else code,
            "stop": "timeout" if timed_out else "completed",
            "changes": change_summary(),
        },
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "jevy-gui/1.0"
    token: str = ""
    timeout_default: int = 300

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the console quiet
        return

    # ── helpers ──────────────────────────────────────────────────────────────
    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    def _authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Token", ""), self.token)

    # ── routes ───────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        if self.path in ("/", "/index.html"):
            html = DOC.read_text(encoding="utf-8").replace("__TOKEN__", self.token)
            self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))
            return
        if self.path == "/api/state":
            if not self._authorized():
                self._json({"error": "forbidden"}, 403)
                return
            catalog = providers()
            self._json(
                {
                    "root": str(ROOT),
                    "workers": workers(),
                    "providers": catalog,
                    "models": known_models(catalog),
                    "env_keys": env_key_names(),
                    "executor_mode": executor_mode_enabled(),
                    "sandboxes": _SANDBOX,
                    "default_timeout": self.timeout_default,
                }
            )
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        if self.path not in (
            "/api/run",
            "/api/run/stream",
            "/api/stop",
            "/api/executor-mode",
        ):
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return
        if not self._authorized():
            self._json({"error": "forbidden"}, 403)
            return
        payload = self._read_payload()
        if payload is None:
            return

        if self.path == "/api/stop":
            if payload.get("all"):
                stopped = stop_all()
            else:
                stopped = 1 if stop_run(str(payload.get("run_id", ""))) else 0
            self._json({"stopped": stopped})
            return
        if self.path == "/api/executor-mode":
            executor_mode_set(bool(payload.get("on")))
            self._json({"executor_mode": executor_mode_enabled()})
            return

        catalog = providers()
        allowed = {
            f"{provider}/{key}" for provider, keys in catalog.items() for key in keys
        }
        worker = str(payload.get("worker", ""))
        brief = str(payload.get("brief", ""))
        model = payload.get("model") or None
        flags = payload.get("flags") or {}
        try:
            validate_run(worker, brief, model, allowed)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
            return
        timeout_s = int(
            payload.get("timeout", self.timeout_default) or self.timeout_default
        )
        timeout_s = max(10, min(timeout_s, 1800))
        if self.path == "/api/run/stream":
            self._stream(worker, brief, model, flags, timeout_s)
        else:
            self._json(run_task(worker, brief, model, flags, timeout_s))

    def _read_payload(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > MAX_BODY:
            self._json({"error": "bad body size"}, 400)
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json({"error": "invalid JSON"}, 400)
            return None

    def _stream(
        self,
        worker: str,
        brief: str,
        model: str | None,
        flags: dict[str, Any],
        timeout_s: int,
    ) -> None:
        argv = _stream_argv(worker, brief, model, flags)
        run_id = secrets.token_hex(6)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            for event, data in stream_task(argv, timeout_s, run_id):
                self.wfile.write(_sse(event, data))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser went away; the worker keeps running to completion


def build_server(
    host: str, port: int, token: str, timeout_default: int
) -> ThreadingHTTPServer:
    handler = type(
        "BoundHandler", (Handler,), {"token": token, "timeout_default": timeout_default}
    )
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="JEV worker console (local, stdlib-only)"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--timeout", type=int, default=300, help="default run timeout (s)"
    )
    parser.add_argument("--open", action="store_true", help="open the browser")
    parser.add_argument(
        "--print-token", action="store_true", help="print the token and exit"
    )
    args = parser.parse_args(argv)

    token = secrets.token_urlsafe(18)
    if args.print_token:
        print(token)
        return 0

    httpd = build_server(args.host, args.port, token, args.timeout)
    url = f"http://{args.host}:{httpd.server_address[1]}/"
    print(f"JEV worker console: {url}")
    print(f"token (header X-Token): {token}")
    print(
        f"workers: {', '.join(w['id'] for w in workers() if w['ready']) or '(none ready)'}"
    )
    if args.open:
        import webbrowser

        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
