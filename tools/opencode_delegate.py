#!/usr/bin/env python3
"""
opencode_delegate.py — CLI-based delegation wrapper for OpenCode workers.

ORCHESTRATION MODEL: Claude Code is ALWAYS the orchestrator.
This script uses `opencode run` to send a single stateless prompt to OpenCode
and parses the JSON events stream. The orchestrator must read and verify every result.

CLI APPROACH (OpenCode v2 `run`):
  opencode run "prompt" --format json --auto --standalone --model provider/model

OPENCODE V2 NOTES (verified 2026-09-27, @opencode/cli 2.0.18):
  - `run` keeps the v1 flags used here: --format json, --auto, -m/--model.
  - v2 `--format json` emits `step_start` and `text` events only; there is no
    `step_finish` event, so token and cost fields log as null. The parser keeps
    step_finish handling for forward compatibility with a build that restores it.
  - --standalone runs a private server, so delegation never touches the user's
    shared background OpenCode server or its sessions.
  - The provider is self-contained in .opencode/opencode.json (v2 `providers`),
    replacing the retired v1 plugin `commandcode-go-opencode-provider`; the root
    opencode.json stays v1-safe for the host IDE's bundled OpenCode v1.

Usage:
    python tools/opencode_delegate.py "<self-contained prompt>"
    python tools/opencode_delegate.py "<prompt>" --model commandcode/deepseek-v4-pro
    python tools/opencode_delegate.py "<prompt>" --model opencode/deepseek-v4-flash-free
    python tools/opencode_delegate.py "<prompt>" --no-guardrail

Requires OpenCode CLI installed: https://opencode.ai/install
Auth: opencode providers login <url>
"""

import argparse
import contextlib
import importlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_MODEL = "commandcode/deepseek-v4.1-flash"
# The orchestrator runs WITHOUT --auto: that flag auto-approves past its
# `edit: deny`, so the deny only holds once the flag is gone (measured 2026-10-16:
# a write landed with the jev agent selected and --auto in play).
ORCHESTRATOR_AGENT = "jev"
# OpenCode v2 dropped `opencode/deepseek-v4-flash-free`; the free tier now
# exposes models such as opencode/mimo-v2.6-flash-free (verified 2026-09-27).
FREE_MODEL = "opencode/mimo-v2.6-flash-free"

USAGE_LOG = Path(".solocode/opencode-usage.jsonl")
USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)

GUARDRAIL = """\
STRICT OPERATING CONSTRAINTS (must follow, no exceptions):
1. Modify ONLY the files explicitly named in the task below. Do not touch
   any other file, and do not refactor nearby code that wasn't asked for.
2. Do NOT add new dependencies, new files, or new abstractions unless the
   task explicitly asks for them.
3. Match the existing code style/conventions of the surrounding file
   exactly (naming, formatting, error handling patterns).
4. If the task is ambiguous or underspecified, STOP and report back what
   is missing instead of guessing or inventing scope.
5. Never run destructive commands (git push, --force, rm -rf, DB
   migrations) under any circumstance.
6. End your response with a one-line self-check: "Scope check: touched
   only <file list>; no dependencies added" (or state exactly what
   deviated and why).

Your permission scope was frozen when this task was created and cannot be expanded. If you hit a boundary beyond that scope, do not retry or work around it — stop and report exactly what is out of scope so the orchestrator can decide.

"""

# ── Helper Functions ─────────────────────────────────────────────────────────


def _stderr(msg: str) -> None:
    """Print to stderr with [opencode_delegate] prefix."""
    print(f"[opencode_delegate] {msg}", file=sys.stderr)


def _load_sibling(module_name: str):
    """Import a sibling tool (tools/ may not be on sys.path)."""
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    return importlib.import_module(module_name)


def _load_project_env() -> None:
    """Export the project `.env` (with the launcher's aliases) before spawning the CLI.

    The delegate spawns OpenCode directly, so the launcher never runs and anything it would
    derive from `.env` -- `FREEMODEL_API_KEY` from `OPENAI_API_KEY`, for one -- is missing.
    """
    _load_sibling("env_file").load_project_env()


def _model_override(role: str) -> str | None:
    """The `.env` override for this role's default model, if the operator set one."""
    return _load_sibling("env_file").model_override(role)


def resolve_model(*, free: bool, model: str | None, agent: str | None) -> str | None:
    """The `--model` value to hand the CLI, or None to let OpenCode choose.

    OpenCode v2.0.32 ignores an agent's own `model` when `run --agent` gets no `--model`
    (measured 2026-10-20: `--agent jev` ran commandcode/deepseek-v4-pro while `debug agents`
    resolved openrouter/jev-router), so the orchestrator's model is passed explicitly. Other
    agents keep None: their callers already name a model, and an agent with no model of its
    own should still fall back to its own config rather than to the project default.
    """
    if free:
        return FREE_MODEL
    if model:
        return model
    if agent == ORCHESTRATOR_AGENT:
        # One source of truth: model-roles.json, through the engine's own resolver, which
        # already carries the operator's fallback to the default executor.
        engine = _load_sibling("opencode_engine")
        return engine._build_agent_models()[ORCHESTRATOR_AGENT]["model"]
    if agent:
        return None
    return _model_override("opencode") or DEFAULT_MODEL


def _error_result(error: str) -> dict[str, Any]:
    """The shaped empty result every failure path in this module returns."""
    return {
        "text": "",
        "events": [],
        "session_id": None,
        "tokens": None,
        "cost": None,
        "error": error,
    }


def _make_streams_encoding_safe() -> None:
    """Never crash on worker output the console encoding cannot represent."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


# npm/bun installs put a `.cmd`/`.bat` shim on PATH. Its `%*` expansion mangles
# multi-line arguments (the guardrail prompt reaches the model truncated to its
# first line) and can drop trailing flags like `--format json`. The shim wraps a
# real executable; prefer that.
# OpenCode v2 ships only as the npm package `@opencode/cli` (bins opencode,
# opencode2). The retired v1 package `opencode-ai` is deliberately not listed:
# resolving it would run the old CLI against a v2 config.
_SHIM_WRAPPED_RELATIVE_PATHS = (
    Path("node_modules") / "@opencode" / "cli" / "bin" / "opencode.exe",
    Path("node_modules") / "@opencode" / "cli" / "bin" / "opencode",
)


def _prefer_real_executable(binary: str, *, platform: str | None = None) -> str:
    """Return the executable a Windows `.cmd`/`.bat` shim wraps, if it exists.

    On Windows `shutil.which("opencode")` usually returns an npm shim
    (`opencode.cmd`). Running that via subprocess expands arguments through the
    batch `%*`, which truncates multi-line arguments at the first newline and can
    drop trailing flags. Pointing at the wrapped executable avoids the shim.
    Non-Windows and non-shim paths are returned unchanged.
    """
    platform = platform or sys.platform
    if platform != "win32":
        return binary
    shim = Path(binary)
    if shim.suffix.lower() not in (".cmd", ".bat"):
        return binary
    for rel in _SHIM_WRAPPED_RELATIVE_PATHS:
        candidate = shim.parent / rel
        if candidate.is_file():
            return str(candidate)
    return binary


def find_opencode_binary() -> str | None:
    """Find OpenCode CLI binary. Check PATH first, then common install locations."""
    # Check PATH (bypass a Windows npm shim if it wraps a real executable).
    opencode_path = shutil.which("opencode")
    if opencode_path:
        return _prefer_real_executable(opencode_path)

    # Fall back to the @opencode/cli binary next to the node executable, which
    # covers nvm-windows / nvm global installs whose shim is not on PATH. The
    # ~/.opencode/bin native binary is intentionally NOT used: a v1
    # self-updating install can sit there (it did on this machine as v1.18.31
    # until 2026-09-28) and would run the old CLI against a v2 config.
    node_path = shutil.which("node")
    if node_path:
        cli_dir = Path(node_path).parent / "node_modules" / "@opencode" / "cli" / "bin"
        for name in ("opencode.exe", "opencode"):
            candidate = cli_dir / name
            if candidate.is_file():
                return str(candidate)

    return None


def parse_json_events(output: str) -> dict[str, Any]:
    """Parse JSON events stream from opencode run --format json output.

    Returns dict with:
        - text: concatenated text from all text events
        - events: list of all parsed events
        - session_id: session ID from first event
        - tokens: token usage from a step_finish event, when present (v2
          `--format json` emitting only step_start/text leaves this None)
        - cost: cost from a step_finish event, when present
        - error: error message if any error event found
    """
    result: dict[str, Any] = {
        "text": "",
        "events": [],
        "session_id": None,
        "tokens": None,
        "cost": None,
        "error": None,
    }

    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            result["events"].append(event)

            # Extract session ID from first event
            if result["session_id"] is None and "sessionID" in event:
                result["session_id"] = event["sessionID"]

            # Concatenate text from text events
            if event.get("type") == "text":
                part = event.get("part", {})
                text = part.get("text", "")
                result["text"] += text

            # Extract tokens/cost from step_finish
            if event.get("type") == "step_finish":
                part = event.get("part", {})
                result["tokens"] = part.get("tokens")
                result["cost"] = part.get("cost")

            # Capture errors
            if event.get("type") == "error":
                error_data = event.get("error", {})
                result["error"] = error_data.get("data", {}).get(
                    "message", "Unknown error"
                )

        except json.JSONDecodeError:
            # Skip non-JSON lines (banner, warnings)
            continue

    return result


def run_opencode_cli(
    prompt: str,
    model: str,
    directory: str,
    opencode_binary: str,
    timeout_s: int = 900,
    *,
    agent: str | None = None,
    session: str | None = None,
) -> dict[str, Any]:
    """Run opencode CLI, return parsed JSON events."""
    cmd = [
        opencode_binary,
        "run",
        prompt,
        "--format",
        "json",
        "--standalone",  # private server — do not touch the shared user server
    ]
    if agent != ORCHESTRATOR_AGENT:
        # Workers need unattended writes; the orchestrator must not have them.
        cmd.append("--auto")
    if model:  # the orchestrator's model is passed explicitly; v2.0.32 ignores an agent's own model
        cmd += ["--model", model]
    if agent:  # e.g. the `jev` orchestrator agent
        cmd += ["--agent", agent]
    if session:  # continue (or create) a session, for a conversation
        cmd += ["--session", session]

    # Built from what the argv actually got: the orchestrator runs without --auto.
    running = "Running: opencode run ..."
    if agent:
        running += f" --agent {agent}"
    running += f" --model {model}" if model else " --model (agent default)"
    running += " --format json"
    if agent != ORCHESTRATOR_AGENT:
        running += " --auto"
    _stderr(running)

    registry = _load_sibling("session_registry")
    try:
        proc = subprocess.Popen(  # noqa: S603 — fixed argv; the prompt is data
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=directory,
        )
    except Exception as exc:  # no pid exists yet, so there is no session to record
        _stderr(f"opencode CLI failed: {exc}")
        return _error_result(str(exc))

    # The pid is what makes the session interruptible later, which is why the
    # record is written only after Popen succeeded and carries proc.pid.
    run_id = registry.record_start(
        "opencode",
        model=model or (f"agent:{agent}" if agent else None),
        pid=proc.pid,
        argv=cmd,
    )

    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        # Kill the worker's whole tree, not just the direct child: a node worker
        # keeps children of its own, and a bare kill() would leave them running
        # past the timeout this call claims to enforce.
        _load_sibling("session_control").kill_tree(proc.pid)
        stdout, stderr = proc.communicate()
    except Exception as exc:
        registry.record_finish(run_id, None, state="failed")
        _stderr(f"opencode CLI failed: {exc}")
        return _error_result(str(exc))

    exit_code = proc.returncode
    # record_finish refuses to overwrite a state the controller already set, so a
    # session the operator interrupted keeps that verdict instead of `exited`.
    registry.record_finish(
        run_id, exit_code, state="timeout" if timed_out else "exited"
    )

    if timed_out:
        _stderr(f"opencode CLI timed out after {timeout_s}s")
        return _error_result(f"Timeout after {timeout_s}s")

    if exit_code != 0 and not stdout.strip():
        # No JSON output: the CLI failed before it could emit anything.
        _stderr(f"opencode CLI failed with exit code {exit_code}")
        if stderr:
            _stderr(f"stderr: {stderr[:500]}")
        return _error_result(f"opencode CLI exit code {exit_code}")

    # Parse JSON events from stdout
    return parse_json_events(stdout)


def log_usage(
    model: str,
    prompt_len: int,
    elapsed: float,
    result: dict[str, Any],
) -> None:
    """Log delegation call to .solocode/opencode-usage.jsonl for auditing."""
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "prompt_chars": prompt_len,
        "elapsed_s": round(elapsed, 2),
        "session_id": result.get("session_id"),
        "usage": result.get("tokens"),
        "cost": result.get("cost"),
        "error": result.get("error"),
    }
    with USAGE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


# ── Main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    _make_streams_encoding_safe()
    parser = argparse.ArgumentParser(
        description="OpenCode CLI delegation wrapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Every call is logged to " + str(USAGE_LOG),
    )
    parser.add_argument(
        "prompt",
        help="Self-contained task prompt (inline all needed context)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"Model in provider/model format (default: {DEFAULT_MODEL}; the jev agent's "
        "own model is passed explicitly, other agents resolve it from their config)",
    )
    parser.add_argument(
        "--directory",
        default=".",
        help="Working directory for opencode CLI (default: current dir)",
    )
    parser.add_argument(
        "--no-guardrail",
        action="store_true",
        help="Skip prepending the strict guardrail preamble",
    )
    parser.add_argument(
        "--opencode-bin",
        help="Path to opencode binary (auto-detected if not provided)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--free",
        action="store_true",
        help=f"Use free model ({FREE_MODEL})",
    )
    parser.add_argument(
        "--agent", default=None, help="OpenCode agent to run (e.g. 'jev')"
    )
    parser.add_argument(
        "--session", default=None, help="Session id to continue or create"
    )

    args = parser.parse_args(argv)

    # ── Find OpenCode binary ──
    opencode_binary = args.opencode_bin or find_opencode_binary()
    if not opencode_binary:
        _stderr("OpenCode binary not found. Install from https://opencode.ai/install")
        return 1

    _stderr(f"Using OpenCode binary: {opencode_binary}")

    # ── Resolve the model ──
    # An explicit --model wins; the orchestrator's model is passed explicitly because
    # v2.0.32 ignores an agent's own model on `run --agent` (measured 2026-10-20); any
    # other agent keeps None so its own configuration decides. Otherwise the harness
    # default applies -- and a `.env` override wins over that default, so a provider
    # outage is one line in `.env`, not a code edit.
    _load_project_env()
    model = resolve_model(free=args.free, model=args.model, agent=args.agent)

    # ── Prepend guardrail unless disabled ──
    prompt = args.prompt
    if not args.no_guardrail:
        prompt = GUARDRAIL + prompt

    _stderr(f"model={model}")

    # ── Run opencode CLI → parse events → log ──
    start = time.monotonic()
    result = run_opencode_cli(
        prompt=prompt,
        model=model,
        directory=args.directory,
        opencode_binary=opencode_binary,
        timeout_s=args.timeout,
        agent=args.agent,
        session=args.session,
    )
    elapsed = time.monotonic() - start

    log_usage(model or f"agent:{args.agent}", len(prompt), elapsed, result)

    # ── Output ──
    if result.get("error"):
        _stderr(f"Error: {result['error']}")
        return 2

    # Print the concatenated text response (encoding handled in main()).
    print(result["text"])

    _stderr(f"session={result.get('session_id')}  elapsed={elapsed:.1f}s")
    if result.get("tokens"):
        tokens = result["tokens"]
        _stderr(
            f"tokens: in={tokens.get('input')} out={tokens.get('output')} "
            f"reasoning={tokens.get('reasoning')} total={tokens.get('total')} "
            f"cache_read={tokens.get('cache', {}).get('read')} "
            f"cache_write={tokens.get('cache', {}).get('write')}"
        )
    if result.get("cost") is not None:
        _stderr(f"cost: ${result['cost']:.6f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
