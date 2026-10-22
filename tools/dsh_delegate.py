#!/usr/bin/env python3
"""Run the DeepSeek Harness (dsh) as an optional worker from an external checkout.

The dsh checkout is NOT shipped with this repository and is not expected to exist
here. This wrapper resolves one from an explicit `--dsh-dir`, then the `DSH_DIR`
environment variable, then three sibling locations
(`../reference/deepseek-harness-master`, `../deepseek-harness-master`,
`../../reference/deepseek-harness-master`). When it finds a checkout, the wrapper
loads `DEEPSEEK_API_KEY` from the project `.env`, runs the task from source with
`pnpm dsh --profile <name> "<task>"`, and returns its output. `--self-test` prints
`dsh checkout: NOT FOUND` and still exits 0, because the checkout is optional. A
real run needs that checkout, `pnpm` on PATH, and one `pnpm build` in it.

This wrapper injects NO guardrail prompt, takes no directory lock, and runs no
post-run scope audit -- unlike `antigravity_delegate.py`. Whatever the task can
write, it writes: containment comes from the dsh profile it launches, not from
this file. Pass a read-only profile if you need read-only.

UNVERIFIED: dsh has not been installed or built on this machine beyond checking
that its prerequisites (Node >= 22.19, pnpm) are satisfied, so this wrapper's
execution path has not been exercised end to end. `--self-test` covers argument
handling and checkout resolution only. Confirm with a real run before relying on
it.

Usage:
    python tools/dsh_delegate.py --self-test
    python tools/dsh_delegate.py "<self-contained task>"
    python tools/dsh_delegate.py "<task>" --profile headless --timeout 600

Headless note (all found on a real run):
  - `pnpm dsh` first runs pnpm's dependency check; without a TTY it aborts
    (ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY). Set `CI=true` to let it pass.
  - deps may be fully installed ("Already up to date") and dsh still fails: its
    entry runs from source (`node --import tsx/esm apps/cli/src/bin.ts`), but the
    workspace packages are consumed from their built output, so several report
    "failed to import" until `pnpm build` has been run once in the checkout.
  - `pnpm build` needs a Git commit hash for browser build metadata; a checkout
    with no `.git` fails `git rev-parse HEAD`, so export
    `DSH_CLIENT_COMMIT_HASH=<7-hex>` for that run.
  - the harness's `.env` `DEEPSEEK_BASE_URL` (an OpenAI-compatible `/v1` root) is
    deliberately NOT forwarded: it does not match dsh's default `messages`
    protocol (root `https://api.deepseek.com/anthropic`) and caused an
    `HTTP_404 ... DeepSeek Messages`. Set `DEEPSEEK_BASE_URL` in the process
    environment only when you really mean to override dsh's endpoint.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess  # noqa: S404 — runs this checkout's own pinned launcher
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_TIMEOUT_SECONDS = 600

# Minimal environment variable names dsh reads for a real run.
API_KEY_ENVS = ("DEEPSEEK_API_KEY",)
# Forwarded only when the operator set them in the PROCESS environment. The .env
# value is the harness's OpenAI-compatible root (https://api.deepseek.com/v1),
# which does NOT match dsh's default `messages` protocol -- its root is
# https://api.deepseek.com/anthropic -- and the mismatch produced an HTTP 404.
# Leaving it unset lets dsh fall back to its own correct default.
PASSTHROUGH_ENVS = ("DEEPSEEK_BASE_URL",)


def _stderr(message: str) -> None:
    print(f"[dsh_delegate] {message}", file=sys.stderr)


def _load_sibling(module_name: str):
    """Import a sibling tool (tools/ may not be on sys.path)."""
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    import importlib

    return importlib.import_module(module_name)


def _load_registry():
    return _load_sibling("session_registry")


def _load_control():
    return _load_sibling("session_control")


def _make_streams_encoding_safe() -> None:
    """Never crash on worker output the console encoding cannot represent.

    dsh prints check marks / box-drawing characters; the Windows console default
    (cp1252) raised UnicodeEncodeError mid-report on a real run. Reconfigure both
    streams to UTF-8 with replacement, exactly as opencode_delegate does.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE .env file; return {} when it is absent or unreadable."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def find_dsh_dir(explicit: str | None = None) -> Path | None:
    """Resolve the dsh checkout from an explicit path, DSH_DIR, or known candidates."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_dir = os.environ.get("DSH_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates += [
        ROOT.parent / "reference" / "deepseek-harness-master",
        ROOT.parent / "deepseek-harness-master",
        ROOT.parent.parent / "reference" / "deepseek-harness-master",
    ]
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if (resolved / "package.json").is_file() and (resolved / "packages").is_dir():
            return resolved
    return None


def dsh_script_declared(dsh_dir: Path) -> bool:
    """True when the checkout's package.json declares a `dsh` script."""
    import json

    try:
        data = json.loads((dsh_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    scripts = data.get("scripts")
    return isinstance(scripts, dict) and "dsh" in scripts


def build_command(pnpm: str, profile: str, task: str) -> list[str]:
    """Build the dsh source-launch command. Task is a single positional arg."""
    return [pnpm, "dsh", "--profile", profile, task]


def run_dsh(
    *, command: list[str], dsh_dir: Path, env: dict[str, str], timeout_s: int
) -> tuple[int, str, str]:
    """Execute dsh, record the session, and return (exit_code, stdout, stderr)."""
    registry = _load_registry()
    try:
        proc = subprocess.Popen(  # noqa: S603 — argv built here, no shell
            command,
            cwd=dsh_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except OSError as exc:  # no pid exists yet, so there is no session to record
        return 1, "", str(exc)

    # The worker is `pnpm dsh ...`, so child_pid is the launcher; kill_tree walks
    # down from it. The record follows Popen because the pid is what makes the run
    # reachable from `session_control`.
    run_id = registry.record_start("dsh", pid=proc.pid, argv=command, log_path=dsh_dir)

    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _load_control().kill_tree(proc.pid)
        stdout, stderr = proc.communicate()

    registry.record_finish(
        run_id, proc.returncode, state="timeout" if timed_out else "exited"
    )
    if timed_out:
        return 124, "", f"Timeout after {timeout_s}s"
    return proc.returncode, stdout or "", stderr or ""


def _self_test() -> int:
    """Exercise argument handling and checkout resolution without running dsh."""
    failures: list[str] = []

    command = build_command("pnpm", "headless", "do a thing")
    if command != ["pnpm", "dsh", "--profile", "headless", "do a thing"]:
        failures.append(f"build_command produced {command!r}")

    if build_command("pnpm", "headless", "a\nb")[-1] != "a\nb":
        failures.append("task with a newline was not kept as one argument")

    env_sample = Path(__file__).parent / "nonexistent.env"
    if load_env_file(env_sample) != {}:
        failures.append("load_env_file should return {} for a missing file")

    resolved = find_dsh_dir(None)
    if resolved is not None and not (resolved / "packages").is_dir():
        failures.append(f"find_dsh_dir returned a non-checkout: {resolved}")

    if failures:
        for item in failures:
            _stderr(f"SELF-TEST FAIL: {item}")
        return 1
    print("self-test OK")
    print(f"dsh checkout: {resolved if resolved else 'NOT FOUND'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _make_streams_encoding_safe()
    parser = argparse.ArgumentParser(description="DeepSeek Harness delegation wrapper")
    parser.add_argument("prompt", nargs="?", help="Self-contained task for dsh")
    parser.add_argument(
        "--profile", default="headless", help="dsh profile (default: headless)"
    )
    parser.add_argument(
        "--dsh-dir", help="Path to the dsh checkout (default: auto-detect)"
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--self-test", action="store_true", help="Validate setup without running dsh"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.prompt:
        parser.error("a task prompt is required unless --self-test is given")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    dsh_dir = find_dsh_dir(args.dsh_dir)
    if dsh_dir is None:
        _stderr(
            "dsh checkout not found. Pass --dsh-dir, set DSH_DIR, or place the "
            "checkout at reference/deepseek-harness-master."
        )
        return 1
    if not dsh_script_declared(dsh_dir):
        _stderr(f"{dsh_dir}/package.json declares no `dsh` script; wrong checkout?")
        return 1

    pnpm = shutil.which("pnpm")
    if not pnpm:
        _stderr("pnpm not found on PATH; dsh runs from source through pnpm.")
        return 1

    env = os.environ.copy()
    file_env = load_env_file(ROOT / ".env")
    for key in API_KEY_ENVS:
        value = os.environ.get(key) or file_env.get(key)
        if value:
            env[key] = value
    # Base URLs come only from the process environment (explicit intent); the
    # .env one is the harness's OpenAI-compatible root, wrong for dsh. See
    # PASSTHROUGH_ENVS.
    for key in PASSTHROUGH_ENVS:
        if key in os.environ:
            env[key] = os.environ[key]
    missing = [key for key in API_KEY_ENVS if not env.get(key)]
    if missing:
        _stderr(f"{', '.join(missing)} not set; dsh needs it to call a model.")
        return 1

    command = build_command(pnpm, args.profile, args.prompt)
    _stderr(f"dsh_dir={dsh_dir} profile={args.profile} timeout={args.timeout}s")
    code, out, err = run_dsh(
        command=command, dsh_dir=dsh_dir, env=env, timeout_s=args.timeout
    )
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        _stderr(err.strip()[:1000])
    if code != 0:
        _stderr(f"dsh exited with code {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
