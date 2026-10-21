#!/usr/bin/env python3
"""
kilo_cli_delegate.py — CLI-based delegation wrapper for Kilo workers.

ORCHESTRATION MODEL: Claude Code / Kilo Code is ALWAYS the orchestrator.
This script uses `kilo run --attach` to send a single stateless prompt to a
running Kilo server and parses the JSON events stream. The orchestrator must
read and verify every result.

CLI APPROACH (Kilo run --attach):
  kilo run "prompt" --attach http://server --format json --auto --model provider/model

IMPROVEMENTS OVER kilo_delegate.py (subprocess-based, no structured events):
  1. Stable API — JSON events stream with typed schemas vs parsing stdout
  2. Structured JSON — every event has type, timestamp, sessionID, structured data
  3. Observable state — events include step_start, text, tool_use, step_finish
  4. Session tracking — sessionID in every event for debugging
  5. Proper errors — JSON error events with type/message/ref vs exit-code guessing

IMPROVEMENTS OVER kilo_delegate.py (HTTP API):
  6. Works immediately — no debugging HTTP payload structure
  7. Proven stable — kilo run --attach is the official CLI interface
  8. Same benefits — both use same underlying Kilo server

Usage:
    python tools/kilo_cli_delegate.py "<self-contained prompt>"
    python tools/kilo_cli_delegate.py "<prompt>" --model commandcode/deepseek/deepseek-v4-pro
    python tools/kilo_cli_delegate.py "<prompt>" --no-guardrail

Requires Kilo server running: kilo serve --port 14096
Or will auto-find Kilo binary and use --attach to default server.
"""

import argparse
import contextlib
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_SERVER = "http://127.0.0.1:14096"
DEFAULT_MODEL = "commandcode/deepseek/deepseek-v4.1-flash"

#: Prompts at or below this length go on the command line; longer ones are written to a brief
#: file and referenced by a short pointer (see `run_kilo_cli`). The bound comes from the
#: measurements: 1650 characters inline was fine, 3642 hung or killed the CLI.
INLINE_PROMPT_LIMIT = 2000

USAGE_LOG = Path(".solocode/kilo-usage.jsonl")
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
    """Print to stderr with [kilo_cli_delegate] prefix."""
    print(f"[kilo_cli_delegate] {msg}", file=sys.stderr)


def _load_sibling(module_name: str):
    """Import a sibling tool (tools/ may not be on sys.path)."""
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    import importlib

    return importlib.import_module(module_name)


def _load_project_env() -> None:
    """Export the project `.env` (with the launcher's aliases) before spawning the CLI.

    The delegate spawns Kilo directly, so the launcher never runs and anything it would
    derive from `.env` -- `FREEMODEL_API_KEY` from `OPENAI_API_KEY`, for one -- is missing.
    """
    _load_sibling("env_file").load_project_env()


def _model_override(role: str) -> str | None:
    """The `.env` override for this role's default model, if the operator set one."""
    return _load_sibling("env_file").model_override(role)


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


def _server_healthy(url: str) -> bool:
    """True when the Kilo server answers its `/doc` endpoint.

    A dead server is the most common failure of this wrapper (measured
    2026-10-16: 325 of 327 logged calls over 14 days), and the CLI reports it
    only as an opaque nonzero exit. Probing first turns that into a named
    precondition. Never raises: an unreachable server is just an unhealthy one.
    """
    probe_url = f"{url.rstrip('/')}/doc"
    try:
        with urllib.request.urlopen(probe_url, timeout=2) as response:  # noqa: S310 - http only
            return response.status < 400
    except urllib.error.HTTPError:
        # HTTPError subclasses URLError: the server answered, with an error.
        return False
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _server_port(server: str) -> int | None:
    """The port a --server URL names, or None when it names none."""
    try:
        return urllib.parse.urlparse(server).port
    except ValueError:
        return None


def _make_streams_encoding_safe() -> None:
    """Never crash on worker output the console encoding cannot represent.

    A worker can return non-Latin characters (arrows, em-dashes, box drawing);
    printing those to a cp1252 console on Windows raises UnicodeEncodeError and
    would discard the whole result. Keep the console's encoding, only relax errors.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


# A Windows npm install puts `kilo.cmd` on PATH. Running it via subprocess
# expands arguments through batch `%*`, which destroys a multi-line prompt (the
# guardrail reaches the model truncated or empty). Prefer a real executable.
_BATCH_SHIM_SUFFIXES = (".cmd", ".bat")


def _is_batch_shim(path: str, *, platform: str | None = None) -> bool:
    """True if `path` is a Windows `.cmd`/`.bat` shim."""
    platform = platform or sys.platform
    return platform == "win32" and Path(path).suffix.lower() in _BATCH_SHIM_SUFFIXES


def _real_kilo_candidates() -> list[Path]:
    """Known real (non-shim) kilo executables, most-preferred first."""
    candidates: list[Path] = []
    antigravity_base = Path.home() / ".antigravity-ide" / "extensions"
    if antigravity_base.exists():
        candidates.extend(
            sorted(
                antigravity_base.glob("kilocode.kilo-code-*/bin/kilo.exe"), reverse=True
            )
        )
    candidates.append(Path.home() / ".local" / "share" / "kilo" / "bin" / "kilo")
    return candidates


def _wrapped_executable(shim: str) -> str | None:
    """Return the real executable an npm `.cmd`/`.bat` shim wraps, if found."""
    prefix = Path(shim).parent
    for pattern in (
        "node_modules/*/bin/kilo.exe",  # npm: node_modules/<pkg>/bin/kilo.exe
        "node_modules/*/*/bin/kilo.exe",  # npm scoped: node_modules/@scope/<pkg>/...
        "node_modules/*/bin/kilo",
        "node_modules/*/*/bin/kilo",
    ):
        for candidate in sorted(prefix.glob(pattern)):
            if candidate.is_file():
                return str(candidate)
    return None


def find_kilo_binary(*, platform: str | None = None) -> str | None:
    """Find Kilo CLI binary, preferring a real executable over a batch shim.

    PATH is checked first, but a Windows npm shim (`kilo.cmd`) is not returned
    as-is: its batch `%*` expansion destroys multi-line arguments. When the PATH
    hit is a shim, prefer a known real executable, then the exe the shim wraps,
    and only fall back to the shim itself as a last resort.
    """
    platform = platform or sys.platform

    kilo_path = shutil.which("kilo")
    if kilo_path and not _is_batch_shim(kilo_path, platform=platform):
        return kilo_path

    for candidate in _real_kilo_candidates():
        if candidate.is_file():
            return str(candidate)

    if kilo_path:
        wrapped = _wrapped_executable(kilo_path)
        if wrapped:
            return wrapped
        return kilo_path

    return None


def parse_json_events(output: str) -> dict[str, Any]:
    """Parse JSON events stream from kilo run --format json output.

    Returns dict with:
        - text: concatenated text from all text events
        - events: list of all parsed events
        - session_id: session ID from first event
        - tokens: token usage from step_finish event
        - cost: cost from step_finish event
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
                result["error"] = event.get("part", {}).get("message", "Unknown error")

        except json.JSONDecodeError:
            # Skip non-JSON lines (banner, warnings)
            continue

    return result


def run_kilo_cli(
    prompt: str,
    model: str,
    server_url: str,
    directory: str,
    kilo_binary: str,
    timeout_s: int = 120,
) -> dict[str, Any]:
    """Run kilo CLI with --attach to server, return parsed JSON events."""
    # A brief-sized prompt is unreliable on the command line. Measured 2026-10-16: a ~3.6 KB
    # task either blew a 300-second budget or killed the CLI outright, while the identical
    # text reached the model when it was handed over as a file -- the fix codex_delegate
    # already uses. Short prompts stay inline: delivered as file *content*, a one-line
    # instruction reads as untrusted input and a model may rightly refuse it (observed: a
    # marker echo was declined as a prompt-injection probe).
    if len(prompt) <= INLINE_PROMPT_LIMIT:
        pointer = prompt
    else:
        brief_dir = (
            TOOLS_DIR.parent / ".solocode" / "kilo-briefs" / uuid.uuid4().hex[:12]
        )
        brief_dir.mkdir(parents=True, exist_ok=True)
        brief_file = brief_dir / "brief.md"
        brief_file.write_text(prompt, encoding="utf-8")
        pointer = (
            f"Read the file {brief_file} and carry out the task it contains exactly. "
            "It is your complete brief."
        )
    cmd = [
        kilo_binary,
        "run",
        pointer,
        "--model",
        model,
        "--attach",
        server_url,
        "--format",
        "json",
        "--auto",  # auto-approve non-destructive permissions
        "--dir",
        directory,
    ]

    _stderr(f"Running: {' '.join(cmd[:3])} ... --model {model} --attach {server_url}")

    registry = _load_sibling("session_registry")
    try:
        proc = subprocess.Popen(  # noqa: S603 — fixed argv; the prompt is data
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:  # no pid exists yet, so there is no session to record
        _stderr(f"kilo CLI failed: {exc}")
        return _error_result(str(exc))

    # The pid is what makes this run reachable from `session_control`, so the
    # record is written only after Popen succeeded.
    run_id = registry.record_start("kilo", model=model, pid=proc.pid, argv=cmd)

    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        # Kill the whole tree: the CLI spawns node and helpers of its own, and a
        # bare kill() would leave them running past the timeout this enforces.
        _load_sibling("session_control").kill_tree(proc.pid)
        stdout, stderr = proc.communicate()
    except Exception as exc:
        registry.record_finish(run_id, None, state="failed")
        _stderr(f"kilo CLI failed: {exc}")
        return _error_result(str(exc))

    exit_code = proc.returncode
    # record_finish settles only a `running` record, so an operator's interrupt
    # keeps that verdict instead of being overwritten by `exited`.
    registry.record_finish(
        run_id, exit_code, state="timeout" if timed_out else "exited"
    )

    if timed_out:
        _stderr(f"kilo CLI timed out after {timeout_s}s")
        return _error_result(f"Timeout after {timeout_s}s")

    if exit_code != 0 and not stdout.strip():
        # No JSON output: the CLI failed before it could emit anything.
        _stderr(f"kilo CLI failed with exit code {exit_code}")
        if stderr:
            _stderr(f"stderr: {stderr[:500]}")
        return _error_result(f"kilo CLI exit code {exit_code}")

    # Parse JSON events from stdout
    return parse_json_events(stdout)


def log_start(model: str, prompt_len: int, run_id: str) -> None:
    """Append a phase:start row so a lost run is distinguishable from a never-run one.

    The start row pairs with the finish row log_usage writes: they share run_id, so a
    wrapper that dies mid-run leaves a start without a finish, which the ledger reads
    as a lost run rather than silence.
    """
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "prompt_chars": prompt_len,
            "elapsed_s": 0,
            "phase": "start",
            "run_id": run_id,
        }
        with USAGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # usage logging must never break a run


def log_usage(
    model: str,
    prompt_len: int,
    elapsed: float,
    result: dict[str, Any],
    run_id: str,
) -> None:
    """Log delegation call to .solocode/kilo-usage.jsonl for auditing."""
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "prompt_chars": prompt_len,
            "elapsed_s": round(elapsed, 2),
            "session_id": result.get("session_id"),
            "usage": result.get("tokens"),
            "cost": result.get("cost"),
            "error": result.get("error"),
            "phase": "finish",
            "run_id": run_id,
        }
        with USAGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # usage logging must never break a run


# ── Main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    _make_streams_encoding_safe()
    parser = argparse.ArgumentParser(
        description="Kilo CLI delegation wrapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Every call is logged to " + str(USAGE_LOG),
    )
    parser.add_argument(
        "prompt",
        help="Self-contained task prompt (inline all needed context)",
    )
    parser.add_argument(
        "--server",
        default=DEFAULT_SERVER,
        help=f"Kilo server URL for --attach (default: {DEFAULT_SERVER})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model in provider/model format (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--directory",
        default=".",
        help="Working directory for kilo CLI (default: current dir)",
    )
    parser.add_argument(
        "--no-guardrail",
        action="store_true",
        help="Skip prepending the strict guardrail preamble",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Disable JSON format request (use default text)",
    )
    parser.add_argument(
        "--kilo-bin",
        help="Path to kilo binary (auto-detected if not provided)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--ensure-server",
        action="store_true",
        help="Start the Kilo server first (kilo_server_manager.py ensure) if it is down",
    )

    args = parser.parse_args(argv)

    # ── Server precondition ──
    # Checked before anything is spawned: a dead server is cheaper to report than
    # to discover as `exit code 1` after the CLI has already been started.
    if not _server_healthy(args.server):
        if args.ensure_server:
            _stderr("Kilo server is down - starting it")
            ensure_cmd = [
                sys.executable,
                str(TOOLS_DIR / "kilo_server_manager.py"),
                "ensure",
            ]
            # The manager defaults to the port the default server names, so only a
            # custom --server needs it: otherwise the manager starts 14096 and the
            # probe below still fails with exit code 7.
            port = _server_port(args.server)
            if port is not None and port != _server_port(DEFAULT_SERVER):
                ensure_cmd += ["--port", str(port)]
            subprocess.run(ensure_cmd, check=False)  # noqa: S603 - fixed argv, no shell
        if not _server_healthy(args.server):
            _stderr(
                f"Kilo server is not running at {args.server} - "
                "run: python tools/kilo_server_manager.py ensure"
            )
            return 7

    # A `.env` override wins over the built-in default (see tools/env_file.py): a provider
    # outage becomes one line in `.env` instead of a code edit.
    _load_project_env()
    args.model = args.model or _model_override("kilo") or DEFAULT_MODEL

    # ── Find Kilo binary ──
    kilo_binary = args.kilo_bin or find_kilo_binary()
    if not kilo_binary:
        _stderr("Kilo binary not found. Install Kilo CLI or specify --kilo-bin")
        return 1

    _stderr(f"Using Kilo binary: {kilo_binary}")

    # ── Prepend guardrail unless disabled ──
    prompt = args.prompt
    if not args.no_guardrail:
        prompt = GUARDRAIL + prompt

    _stderr(f"server={args.server}  model={args.model}")

    # ── Run kilo CLI → parse events → log ──
    run_id = uuid.uuid4().hex
    log_start(args.model, len(prompt), run_id)
    start = time.monotonic()
    result = run_kilo_cli(
        prompt=prompt,
        model=args.model,
        server_url=args.server,
        directory=args.directory,
        kilo_binary=kilo_binary,
        timeout_s=args.timeout,
    )
    elapsed = time.monotonic() - start

    log_usage(args.model, len(prompt), elapsed, result, run_id)

    # ── Output ──
    if result.get("error"):
        _stderr(f"Error: {result['error']}")
        return 2

    # Print the concatenated text response
    print(result["text"])

    _stderr(f"session={result.get('session_id')}  elapsed={elapsed:.1f}s")
    if result.get("tokens"):
        tokens = result["tokens"]
        _stderr(
            f"tokens: in={tokens.get('input')} out={tokens.get('output')} total={tokens.get('total')}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
