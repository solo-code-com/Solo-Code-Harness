#!/usr/bin/env python3
"""FreeModel arm wrapper: drive Claude Code or Codex CLI under the harness contract.

Both CLIs are run headless with a self-contained brief inside a working directory,
then checked for writes outside it - the same contract the DeepSeek and Gemini
wrappers hold. Reads FREEMODEL_API from the nearest .env (walking up), so each
project uses its own key and no machine-wide setup is needed.

  python tools/free_delegate.py "<brief>" --platform claude  --model claude-opus-5-5 --effort high
  python tools/free_delegate.py "<brief>" --platform chatgpt --model gpt-6.2-sol --directory src --enforce-scope
  python tools/free_delegate.py "<brief>" --platform claude --write --restore-on-failure

Behaviour:
  * claude  -> `claude -p` (headless). --bare is deliberately NOT added: the env key
    authenticates a headless run, and --bare would skip the user's settings.
  * chatgpt -> `codex exec -C <dir> -a never -s workspace-write` when writing, and
    `-s read-only` otherwise. `-a never` is what stops the headless approval
    rejection that killed earlier arm runs.
  * The zero-width characters Claude emits are stripped before the result prints.
  * A provider overload (429/502/503/504/timeout) is retried with backoff, then the
    next id in --fallback is tried, so one dead model does not fail the run.
  * A writing run snapshots the directory first (restore.py) and prints the id;
    --restore-on-failure puts every file under that directory back byte for byte.
    An out-of-fence write is outside the captured tree by definition, so it is
    reported, never silently called restored.
  * The post-run scope check reports changed paths outside --directory, or exits 4
    with --enforce-scope. It fails closed when a snapshot is unavailable.

Exit codes: 0 ok, 2 usage/environment, 3 provider failure, 4 scope violation.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
LOG = ROOT / ".solocode" / "free_arm" / "runs.jsonl"
PROVIDER_EXIT_CODE = 3
SCOPE_EXIT_CODE = 4
OVERLOAD_HINTS = (
    "overloaded",
    "overload",
    "rate limit",
    "429",
    "502",
    "503",
    "504",
    "524",
    "timed out",
    "timeout",
    "reconnecting",
)


def strip_zw(text: str) -> str:
    """Drop the zero-width watermark Claude appends, so code stays copy-pasteable."""
    return (text or "").translate(ZW)


#: Windows npm installs put a .cmd shim on PATH. subprocess cannot start one
#: without a shell, and its %* expansion truncates a multi-line brief at the
#: first newline. Both CLIs ship a real entry point next to the shim, so resolve
#: that instead: claude has bin/claude.exe, codex has bin/codex.js for node.
_SHIM_PACKAGES = {
    "claude": ("@anthropic-ai", "claude-code", ("bin/claude.exe", "bin/claude")),
    "codex": ("@openai", "codex", ("bin/codex.js",)),
}


def resolve_binary(platform: str, explicit: str | None = None) -> list[str] | None:
    """Return the argv prefix that starts the CLI, bypassing a Windows .cmd shim."""
    name = "codex" if platform == "chatgpt" else "claude"
    if explicit:
        return [str(Path(explicit))]
    found = shutil.which(name)
    if not found:
        return None
    shim = Path(found)
    if shim.suffix.lower() not in (".cmd", ".bat", ".ps1"):
        return [str(shim)]
    scope, package, entries = _SHIM_PACKAGES[name]
    base = shim.parent / "node_modules" / scope / package
    for entry in entries:
        candidate = base / entry
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() == ".js":
            node = shutil.which("node") or str(shim.parent / "node.exe")
            return [node, str(candidate)]
        return [str(candidate)]
    return [str(shim)]


def _stderr(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def find_env_file(start: Path) -> Path | None:
    """Nearest .env walking up from `start`, so a project ships its own key."""
    directory = start.resolve()
    while True:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return env
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        env[key.strip()] = value.strip().strip("\"'")
    return env


def classify_failure(text: str) -> str:
    """Name a provider failure: overloaded, denied, or error."""
    low = (text or "").lower()
    if "user not found" in low or "unauthorized" in low or "invalid api key" in low:
        return "denied"
    if any(hint in low for hint in OVERLOAD_HINTS):
        return "overloaded"
    return "error"


def build_claude_argv(
    prefix: list[str], prompt: str, model: str, effort: str | None, write: bool
) -> list[str]:
    """Headless Claude Code invocation. No --bare: the env key already authenticates."""
    argv = [*prefix, "-p", prompt, "--output-format", "json", "--model", model]
    if effort:
        argv += ["--effort", effort]
    if write:
        argv += ["--permission-mode", "acceptEdits"]
    return argv


def build_codex_argv(
    prefix: list[str],
    prompt: str,
    model: str,
    effort: str | None,
    directory: Path,
    write: bool,
    last_message: Path | None = None,
) -> list[str]:
    """Headless Codex invocation.

    `approval_policy="never"` is the config key that removes the headless approval
    deadlock; `codex exec` itself has no -a flag. `-o` writes only the final
    message, so the answer needs no log filtering.
    """
    argv = [
        *prefix,
        "exec",
        "--skip-git-repo-check",
        "-C",
        str(directory),
        "-m",
        model,
        "-c",
        'approval_policy="never"',
        "-s",
        "workspace-write" if write else "read-only",
    ]
    if effort:
        argv += ["-c", f'model_reasoning_effort="{effort}"']
    if last_message is not None:
        argv += ["-o", str(last_message)]
    argv.append(prompt)
    return argv


def parse_claude_output(raw: str) -> str:
    """`--output-format json` puts the answer under result; fall back to the last JSON line."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return strip_zw(str(parsed.get("result") or ""))
    except ValueError:
        pass
    for line in reversed(raw.splitlines()):
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict) and parsed.get("result"):
            return strip_zw(str(parsed["result"]))
    return strip_zw(raw)


CODEX_NOISE = ("tokens used", "Reconnecting...", "codex_", "OpenAI Codex v")


def parse_codex_output(raw: str) -> str:
    """Keep the answer, drop the CLI's own log lines and the token count.

    The count arrives on its own line after "tokens used", so the marker list
    alone leaves a bare ``11,652`` behind.
    """
    lines = []
    for line in raw.splitlines():
        if any(marker in line for marker in CODEX_NOISE):
            continue
        if line.strip().replace(",", "").isdigit():
            continue
        lines.append(line)
    return strip_zw("\n".join(lines)).strip()


def _load_sibling(module_name: str):
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    return __import__(module_name)


def snapshot_root():
    """Hash the whole repository, or None when it cannot be read.

    The snapshot covers ROOT rather than --directory on purpose: a fence that
    only watches the allowed directory can never see the write it must catch.
    """
    try:
        if not ROOT.is_dir():
            return None
        return _load_sibling("workspace_snapshot").snapshot_workspace(ROOT)
    except OSError:
        return None


def scope_violations(before, after, allowed: Path) -> list[str] | None:
    """Changed repo paths outside `allowed`, or None when a snapshot is missing."""
    if before is None or after is None:
        return None
    module = _load_sibling("workspace_snapshot")
    changed = module.changed_workspace_paths(before, after)
    try:
        prefix = allowed.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        prefix = ""  # the target is outside the repo, so nothing changed is inside
    return module.paths_outside_directory(changed, prefix)


LOCK_EXIT_CODE = 5


def _lock_key(directory: Path) -> str:
    """The directory as the lock table keys it: repo-relative, "." at the root."""
    try:
        return directory.relative_to(ROOT).as_posix() or "."
    except ValueError:
        return str(directory)


def acquire_lock(
    directory: Path, platform: str, model: str, session_id: str = ""
) -> bool:
    """Take the shared-state directory lock so two writers cannot share a tree.

    A missing module means this checkout has no lock service, and the run may
    proceed. A module that exists but fails is different: the database may be
    corrupt or contended, so the run is refused rather than allowed to write
    unserialised. Without a session_id, two runs of the SAME arm match each other
    as the same holder and the second silently overwrites the first lock.
    """
    try:
        module = _load_sibling("shared_state")
    except ImportError:
        return True
    try:
        with module.SharedState() as state:
            return bool(
                state.acquire_directory_lock(
                    _lock_key(directory),
                    engine=f"free-{platform}",
                    model=model,
                    session_id=session_id,
                    reason="FreeModel headless write delegation",
                )
            )
    except Exception as exc:  # noqa: BLE001 - a broken lock service must deny
        _stderr(
            "free_delegate: lock service failed "
            f"({type(exc).__name__}); refusing to write without a lock"
        )
        return False


def release_lock(directory: Path, platform: str, session_id: str = "") -> None:
    """Release the directory lock; a failure here must never mask the run result."""
    try:
        module = _load_sibling("shared_state")
        with module.SharedState() as state:
            state.release_directory_lock(
                _lock_key(directory), engine=f"free-{platform}", session_id=session_id
            )
    except Exception as exc:  # noqa: BLE001 - must never mask the run result
        # A stuck lock is worth reporting; raising here would replace the worker's
        # exit code with a lock error, which is the worse failure.
        _stderr(
            f"free_delegate: could not release the directory lock ({type(exc).__name__})"
        )


def capture(directory: Path) -> str | None:
    """Copy the directory into the restore store so --restore-on-failure can undo."""
    try:
        module = _load_sibling("restore")
        snap = module.capture(ROOT, directory)
        return getattr(snap, "run_id", None)
    except Exception:  # noqa: BLE001 - a missing snapshot must not block the run
        return None


def restore(directory: Path, run_id: str) -> bool:
    try:
        module = _load_sibling("restore")
        snap = module.load(ROOT, run_id)
        module.apply(ROOT, snap)
        return True
    except Exception:  # noqa: BLE001
        return False


def run_argv(
    argv: list[str], directory: Path, timeout: int, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv built above, no shell
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(directory),
            env=env,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def model_chain(primary: str, fallback: str | None) -> list[str]:
    chain = [primary]
    for item in (fallback or "").split(","):
        item = item.strip()
        if item and item not in chain:
            chain.append(item)
    return chain


def log_run(entry: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def delegate(args: argparse.Namespace) -> int:
    start = Path.cwd()
    env_file = find_env_file(start)
    if env_file is None:
        _stderr("free_delegate: no .env found in this directory or above")
        return 2
    env = read_env(env_file)
    key = env.get("FREEMODEL_API", "").strip()
    if not key:
        _stderr(f"free_delegate: FREEMODEL_API missing in {env_file}")
        return 2

    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        _stderr(f"free_delegate: --directory {args.directory} is not a directory")
        return 2

    prompt = args.prompt
    if args.prompt_file:
        try:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        except OSError as exc:
            _stderr(f"free_delegate: cannot read --prompt-file: {exc}")
            return 2
    if not prompt.strip():
        _stderr("free_delegate: empty prompt")
        return 2

    child_env = dict(os.environ)
    anthropic = env.get("FREEMODE_BASE_URL_Anthropic", "https://cc.freemodel.dev")
    openai = env.get("FREEMODE_BASE_URL_OPENAI", "https://api.freemodel.dev")
    child_env["FREEMODEL_API"] = key
    child_env["ANTHROPIC_API_KEY"] = key
    child_env["ANTHROPIC_BASE_URL"] = anthropic
    child_env["FREEMODE_BASE_URL_OPENAI"] = openai
    child_env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"

    prefix = resolve_binary(args.platform, args.binary)
    if prefix is None:
        _stderr(f"free_delegate: {args.platform} CLI not found on PATH")
        return 2

    if args.enforce_scope:
        try:
            relative_fence = directory.relative_to(ROOT)
        except ValueError:
            relative_fence = None
        if relative_fence is not None and _load_sibling(
            "workspace_snapshot"
        ).is_snapshot_ignored(relative_fence):
            _stderr(
                f"free_delegate: --directory {args.directory} is inside a snapshot-ignored "
                "path, so the scope check would see nothing. Use a fence outside "
                ".solocode/dist/build, or drop --enforce-scope."
            )
            return 2

    write = bool(args.write)
    if args.platform == "claude" and write and directory != ROOT:
        # Claude Code resolves relative paths from the git repository root and
        # ignores the process cwd (verified 2026-11-28: cwd=repo/sub wrote to
        # repo/). The fence is therefore stated repo-relative, in the brief.
        try:
            fence = directory.relative_to(ROOT).as_posix()
        except ValueError:
            fence = str(directory)
        prompt = (
            f"Scope: change files only under {fence}/ . Every path you write is relative "
            f"to the repository root, which is this run's working directory. Do not "
            f"create or edit anything outside {fence}/.\n\n{prompt}"
        )
    before = snapshot_root() if write or args.enforce_scope else None
    if args.enforce_scope and before is None:
        _stderr(
            "scope: snapshot unavailable before the run, so --enforce-scope cannot "
            f"verify anything; refusing to start (exit {SCOPE_EXIT_CODE})"
        )
        return SCOPE_EXIT_CODE
    run_id = capture(directory) if (write and args.restore_on_failure) else None
    if run_id:
        _stderr(
            f"free_delegate: snapshot {run_id} (restore with tools/restore.py apply {run_id} --yes)"
        )

    # A writing run holds the shared-state directory lock until it finishes, so two
    # arms cannot edit one tree at once. The lock is released in the finally.
    # The token is the lock's session_id: without it, two runs of the same arm match
    # each other as the same holder and the second overwrites the first lock.
    run_token = f"free-{args.platform}-{os.getpid()}-{os.urandom(3).hex()}"
    locked = False
    if write:
        if not acquire_lock(directory, args.platform, args.model, run_token):
            _stderr(
                f"free_delegate: {args.directory} is locked by another writer "
                f"(exit {LOCK_EXIT_CODE}); run again when it is free"
            )
            return LOCK_EXIT_CODE
        locked = True
    try:
        attempts: list[dict] = []
        text = ""
        rc = PROVIDER_EXIT_CODE
        started = time.time()
        for model in model_chain(args.model, args.fallback):
            for attempt in range(1, args.retries + 2):
                last_msg = directory / ".solocode-free-last-message.txt"
                if args.platform == "claude":
                    argv = build_claude_argv(prefix, prompt, model, args.effort, write)
                else:
                    with contextlib.suppress(OSError):
                        last_msg.unlink()
                    argv = build_codex_argv(
                        prefix, prompt, model, args.effort, directory, write, last_msg
                    )
                run_cwd = ROOT if args.platform == "claude" else directory
                code, out, err = run_argv(argv, run_cwd, args.timeout, child_env)
                if args.platform == "claude":
                    parsed = parse_claude_output(out)
                else:
                    parsed = ""
                    with contextlib.suppress(OSError):
                        parsed = strip_zw(last_msg.read_text(encoding="utf-8")).strip()
                    if not parsed:
                        parsed = parse_codex_output(out)
                    with contextlib.suppress(OSError):
                        last_msg.unlink()
                ok = code == 0 and bool(parsed.strip())
                attempts.append(
                    {
                        "model": model,
                        "attempt": attempt,
                        "exit": code,
                        "chars": len(parsed),
                        "ok": ok,
                    }
                )
                if ok:
                    text, rc = parsed, 0
                    break
                failure = classify_failure(out + "\n" + err)
                _stderr(
                    f"free_delegate: {model} attempt {attempt} failed ({failure}, exit {code})"
                )
                if failure in ("denied", "error") and attempt >= 2:
                    break
                time.sleep(min(2.0 * attempt, 6.0))
            if rc == 0:
                break

        after = snapshot_root() if (write or args.enforce_scope) else None
        outside = scope_violations(before, after, directory)
        scope_note = ""
        if args.enforce_scope and outside is None:
            scope_note = "snapshot unavailable (fails closed)"
        elif outside:
            scope_note = (
                f"{len(outside)} path(s) changed outside {args.directory}: "
                + ", ".join(outside[:10])
            )

        scope_failed = args.enforce_scope and outside is None
        scope_violated = bool(outside) and args.enforce_scope
        final_rc = SCOPE_EXIT_CODE if (scope_failed or scope_violated) else rc
        elapsed = round(time.time() - started, 1)
        log_run(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "run_id": run_token,
                "arm": args.platform,
                "platform": args.platform,
                "model": args.model,
                "effort": args.effort,
                "directory": str(directory),
                "write": write,
                "exit": final_rc,
                "chars": len(text),
                "prompt_chars": len(prompt),
                "elapsed_s": elapsed,
                "error": "" if final_rc == 0 else scope_note or f"exit {final_rc}",
                "scope": scope_note,
                "attempts": attempts,
            }
        )

        if scope_failed:
            _stderr(f"scope: {scope_note}")
            return SCOPE_EXIT_CODE
        if scope_violated:
            _stderr(f"scope: {scope_note}")
            if run_id and args.restore_on_failure:
                # restore.capture covers --directory only, so an out-of-fence write is
                # by definition outside what it can put back. Say so instead of
                # printing a bare "restored" that does not cover the violation.
                _stderr(
                    f"restored inside {args.directory}: {restore(directory, run_id)}; "
                    "the out-of-fence paths above are NOT covered - revert them by hand "
                    f"({', '.join(outside[:5])})"
                )
            return SCOPE_EXIT_CODE
        if outside:
            _stderr(f"scope: {scope_note}")
        if rc == 0 and write and before is not None and after is not None:
            changed = _load_sibling("workspace_snapshot").changed_workspace_paths(
                before, after
            )
            if not changed:
                _stderr(
                    "free_delegate: warning - the run reported success but changed no file "
                    f"under {args.directory}"
                )
        if rc != 0:
            if run_id and args.restore_on_failure:
                _stderr(f"restored: {restore(directory, run_id)}")
            _stderr("free_delegate: provider failed on every model in the chain")
            return rc

        if args.json:
            print(
                json.dumps(
                    {
                        "text": text,
                        "model": args.model,
                        "platform": args.platform,
                        "scope": scope_note,
                    }
                )
            )
        else:
            print(text)
        return 0
    finally:
        if locked:
            release_lock(directory, args.platform, run_token)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FreeModel arm wrapper (Claude Code / Codex CLI)"
    )
    parser.add_argument(
        "prompt", nargs="?", default="", help="Self-contained brief for the worker"
    )
    parser.add_argument("--prompt-file", help="Read the brief from a file instead")
    parser.add_argument("--platform", choices=["claude", "chatgpt"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--fallback", help="Comma list of model ids to try when the primary fails"
    )
    parser.add_argument(
        "--effort",
        help="Reasoning/effort level (claude --effort, codex model_reasoning_effort)",
    )
    parser.add_argument(
        "--directory",
        default=".",
        help="Working root; the write fence and scope check use it",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Allow the worker to edit files in --directory",
    )
    parser.add_argument(
        "--enforce-scope",
        action="store_true",
        help="Exit 4 on a write outside --directory",
    )
    parser.add_argument(
        "--restore-on-failure", action="store_true", help="Undo the run on failure"
    )
    parser.add_argument(
        "--timeout", type=int, default=900, help="Per-attempt timeout in seconds"
    )
    parser.add_argument(
        "--retries", type=int, default=1, help="Extra attempts per model on overload"
    )
    parser.add_argument("--binary", help="Explicit path to the claude/codex executable")
    parser.add_argument(
        "--json", action="store_true", help="Print a JSON envelope instead of raw text"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    return delegate(args)


if __name__ == "__main__":
    sys.exit(main())
