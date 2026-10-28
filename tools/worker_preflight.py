#!/usr/bin/env python3
"""Worker preflight - which of the harness's workers can run right now.

The repo has seven ways to run a worker (opencode, kilo, codex, antigravity, model_api, dsh)
and no single command that says which of them is usable. In practice a session finds out one
at a time, by watching a delegate fail: the Kilo arm timed out in ~40% of runs and Codex
returned empty replies, and both facts were discovered by trial.

This turns that discovery into one command:

    python tools/worker_preflight.py            # table, exit 0
    python tools/worker_preflight.py --strict   # exit 1 when any worker is unusable
    python tools/worker_preflight.py --json     # machine-readable

Read-only by construction: it never starts a server, never calls a model and never writes
anything. Every probe is capped at `PROBE_TIMEOUT` seconds, and the binary/server resolution
reuses the wrappers' own helpers instead of re-implementing them, so a path that works here
is the path the delegate will take.

An optional worker being absent is not a failure -- the dsh checkout is optional -- so the
default exit code stays 0. `--strict` is for a caller that needs every arm before it starts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # noqa: S404 - fixed argv, no shell
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import antigravity_delegate  # noqa: E402
import dsh_delegate  # noqa: E402
import env_file  # noqa: E402
import kilo_cli_delegate  # noqa: E402
import kilo_server_manager  # noqa: E402
import opencode_delegate  # noqa: E402

#: No probe may run longer than this, however the worker misbehaves.
PROBE_TIMEOUT = 10
#: The Kilo health check polls with backoff, so 2 s is already several attempts.
SERVER_PROBE_TIMEOUT = 2
#: The provider `tools/model_api.py` uses for its default model.
MODEL_API_PROVIDER = "deepseek"
#: Column width for the free-text state cell; a long version line must not break the table.
STATE_WIDTH = 40


def _row(worker: str, found: bool, state: str, verdict: str) -> dict[str, object]:
    return {"worker": worker, "found": found, "state": state, "verdict": verdict}


def probe_version(binary: str, env: dict[str, str] | None = None) -> tuple[bool, str]:
    """Run `<binary> --version` and return (ok, one-line state).

    Never raises and never outlives `PROBE_TIMEOUT`: a worker that hangs is exactly the
    failure this tool exists to make visible, so it is reported, not waited on.
    """
    if env is None:
        env = os.environ.copy()
        if not os.access(Path.home(), os.W_OK):
            home = TOOLS_DIR.parent / ".solocode" / "home"
            home.mkdir(parents=True, exist_ok=True)
            env["HOME"] = str(home)
    try:
        proc = subprocess.run(  # noqa: S603,S607 - fixed argv, no shell
            [binary, "--version"],  # noqa: S607 - resolved absolute path
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {PROBE_TIMEOUT}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"error: {type(exc).__name__}"

    text = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    line = text.splitlines()[0].strip() if text else ""
    if proc.returncode != 0:
        return False, f"exit {proc.returncode}" + (f": {line}" if line else "")
    return True, line or "(no version output)"


def check_opencode() -> dict[str, object]:
    """opencode: the binary `opencode_delegate` would spawn."""
    binary = opencode_delegate.find_opencode_binary()
    if not binary:
        return _row("opencode", False, "binary not found", "unusable")
    ok, version = probe_version(binary)
    return _row("opencode", True, version, "ready" if ok else "unusable")


def check_kilo() -> dict[str, object]:
    """kilo: the binary the wrapper resolves, plus the server it talks to.

    The server is reported, never started: a dead server is the wrapper's most common
    failure and `ensure` is the caller's decision, not a side effect of a preflight.
    """
    binary = kilo_cli_delegate.find_kilo_binary()
    if not binary:
        return _row("kilo", False, "binary not found", "unusable")
    ok, version = probe_version(binary)
    server_up = kilo_server_manager.health_check(
        kilo_server_manager.DEFAULT_PORT, timeout=SERVER_PROBE_TIMEOUT
    )
    state = f"{version}; server {'up' if server_up else 'down'}"
    return _row("kilo", True, state, "ready" if ok and server_up else "unusable")


def check_codex() -> dict[str, object]:
    """codex: the `codex` executable on PATH or via common install locations."""
    binary = shutil.which("codex")
    if not binary:
        for cand in (
            TOOLS_DIR.parent / ".solocode" / "npm" / "node_modules" / ".bin" / "codex",
            Path.home() / ".local" / "bin" / "codex",
            Path.home() / ".npm-global" / "bin" / "codex",
            Path("/usr/local/bin/codex"),
        ):
            if cand.is_file() and os.access(cand, os.X_OK):
                binary = str(cand)
                break
    if not binary:
        return _row("codex", False, "not on PATH", "unusable")
    ok, version = probe_version(binary)
    return _row("codex", True, version, "ready" if ok else "unusable")


def check_antigravity() -> dict[str, object]:
    """antigravity: the `agy` binary the delegate resolves, or running IDE orchestrator."""
    binary = antigravity_delegate.find_agy_binary()
    if binary:
        ok, version = probe_version(binary)
        return _row("antigravity", True, version, "ready" if ok else "unusable")
    # Active Antigravity session/IDE environment check
    if (
        os.environ.get("ANTIGRAVITY_SOURCE_METADATA")
        or os.environ.get("ANTIGRAVITY_CSRF_TOKEN")
        or (Path.home() / ".gemini" / "antigravity").is_dir()
    ):
        return _row("antigravity", True, "active IDE orchestrator session", "ready")
    return _row("antigravity", False, "binary not found", "unusable")


def check_model_api() -> dict[str, object]:
    """model_api: checks whether any supported provider has endpoint and key configured.

    Presence only. The key is never printed, returned or logged; a preflight that leaks a
    credential into a terminal or a CI log is worse than no preflight.
    """
    configured_list: list[str] = []
    for prov in ("deepseek", "commandcode", "openrouter", "freemodel"):
        try:
            url, key = env_file.provider_endpoint(prov)
            if url and key:
                configured_list.append(prov)
        except Exception:
            continue
    configured = bool(configured_list)
    state = f"configured ({', '.join(configured_list)})" if configured else "missing"
    return _row("model_api", configured, state, "ready" if configured else "unusable")


def check_dsh() -> dict[str, object]:
    """dsh: the optional checkout, plus the `pnpm` its launch script needs."""
    checkout = dsh_delegate.find_dsh_dir(None)
    pnpm = shutil.which("pnpm")
    if not checkout:
        return _row("dsh", False, "checkout not found (optional)", "unusable")
    if not pnpm:
        return _row("dsh", True, "checkout found; pnpm missing", "unusable")
    return _row("dsh", True, f"checkout found; pnpm {pnpm}", "ready")


def check_wsl_sandbox() -> dict[str, object]:
    """WSL namespace fence: WSL itself, unshare inside it, and the Linux
    opencode binary the fence runs. `--sandbox wsl` cannot work without all
    three, so this row exists to say which one is missing before a delegate
    tries it.
    """
    if not (shutil.which("wsl") or shutil.which("wsl.exe")):
        return _row("wsl-sandbox", False, "WSL not available", "unusable")
    try:
        ready = subprocess.run(  # noqa: S603,S607 - fixed argv, no shell
            ["wsl", "-e", "sh", "-c", "command -v unshare >/dev/null 2>&1"],  # noqa: S607 - wsl resolved on PATH
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return _row("wsl-sandbox", False, "WSL probe failed", "unusable")
    if ready.returncode != 0:
        return _row("wsl-sandbox", True, "WSL up; unshare missing", "unusable")
    home = subprocess.run(  # noqa: S603,S607 - fixed argv, no shell
        ["wsl", "-e", "sh", "-c", 'printf %s "$HOME"'],  # noqa: S607 - wsl resolved on PATH
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT,
    )
    if home.returncode != 0 or not (home.stdout or "").strip():
        return _row("wsl-sandbox", True, "unshare ok; home unreadable", "unusable")
    binary = home.stdout.strip() + "/.local/bin/opencode"
    installed = subprocess.run(  # noqa: S603,S607 - fixed argv, no shell
        ["wsl", "-e", "test", "-x", binary],  # noqa: S607 - wsl resolved on PATH
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT,
    )
    if installed.returncode != 0:
        return _row(
            "wsl-sandbox", True, "unshare ok; opencode not installed", "unusable"
        )
    version = subprocess.run(  # noqa: S603,S607 - fixed argv, no shell
        ["wsl", "-e", binary, "--version"],  # noqa: S607 - wsl resolved on PATH
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT,
    )
    line = (version.stdout or "").strip() or (version.stderr or "").strip()
    line = line.splitlines()[0].strip() if line else "opencode present"
    return _row("wsl-sandbox", True, f"unshare ok; {line}", "ready")


#: Order is the routing table's order, so the output reads like the docs.
WORKERS = (
    check_opencode,
    check_kilo,
    check_codex,
    check_antigravity,
    check_model_api,
    check_dsh,
    check_wsl_sandbox,
)


def collect() -> list[dict[str, object]]:
    return [check() for check in WORKERS]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report which worker CLIs are usable right now"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when any worker is unusable",
    )
    args = parser.parse_args(argv)

    rows = collect()
    unusable = sum(1 for row in rows if row["verdict"] != "ready")
    ready = len(rows) - unusable

    if args.json:
        payload = {"workers": rows, "ready": ready, "unusable": unusable}
        print(json.dumps(payload, indent=1))
    else:
        print(f"{'worker':12} {'found':5}  {'version/state':{STATE_WIDTH}} verdict")
        for row in rows:
            found = "yes" if row["found"] else "no"
            state = str(row["state"])[:STATE_WIDTH]
            print(
                f"{row['worker']:12} {found:5}  {state:{STATE_WIDTH}} {row['verdict']}"
            )
        print(f"\n{ready} ready, {unusable} unusable")

    return 1 if args.strict and unusable else 0


if __name__ == "__main__":
    sys.exit(main())
