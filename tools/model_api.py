#!/usr/bin/env python3
"""Worker over a provider's chat endpoint: one-shot, or with a fenced tool loop.

Two modes, one file:

* **one-shot** (default) - one completion, the answer is text. Fast for a brief that is already
  precise: measured 2.3s for a trivial prompt and 22s for a single-file change, where a Kilo run of
  the same class took 1200-1557s. The worker never writes the repo; the orchestrator applies.
* **tool loop** (`--tools read,command,write`) - the model can read files, run allowlisted
  programs and (opt-in) write inside a fence, until it produces a final answer or the step budget
  runs out. This is what a brief needs when the worker has to run the tests itself.

Fences, because a model with tools is a model with your shell:

* `run_command` takes an argv list, never a shell string; the program must be in
  `--allow-program`, and the joined argv is refused when the harness's own guard flags it as
  destructive - the same judgement the Bash guard applies.
* `write_file` is refused unless `write` is enabled and the resolved path is inside an
  `--allow-dir`.
* `read_file` stays inside the repository and refuses credential-bearing paths.
* Every call is logged, and `--transcript` keeps the full message/tool exchange for audit.

Usage:
    python tools/model_api.py "explain the diff" --include src/app.py
    python tools/model_api.py --prompt-file brief.md --tools read,command --allow-program python
    python tools/model_api.py - --model commandcode/deepseek/deepseek-v4.1-flash-fast < prompt.txt
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))
import env_file  # noqa: E402

ROOT = TOOLS_DIR.parent
#: The vendor-prefixed id: this endpoint rejects the harness's short key form with
#: `unsupported_model` (measured 2026-10-16), so the full upstream id is what goes on the wire.
DEFAULT_MODEL = "commandcode/deepseek/deepseek-v4.1-flash"
USAGE_LOG = ROOT / ".solocode" / "model-api-usage.jsonl"
MAX_TOOL_OUTPUT = 4000

GUARDRAIL = """\
You are a worker invoked by an orchestrator. Return only the artefact it asked for - code,
a unified diff, or the requested answer - with no preamble and no self-assessment. Constraints
you must respect: (1) do not invent APIs or file paths; if the request is underspecified, say
so in one line instead of guessing; (2) if you return code, return it complete enough to apply,
and state the exact file paths you would touch; (3) never assume you can run commands or read
files you were not given, and when you can, use the tools rather than guessing at their output.
"""

TOOL_SCHEMAS: dict[str, dict] = {
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 file from the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "repo-relative path"}
                },
                "required": ["path"],
            },
        },
    },
    "write_file": {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Replace a file's contents inside the allowed directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "run_command": {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run an allowlisted program with an argv list (no shell).",
            "parameters": {
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    }
                },
                "required": ["argv"],
            },
        },
    },
}

REFUSED_READ = (".env", ".solocode/", ".git/", ".pem", ".key", "id_rsa", "credentials")


def log_start(model: str, prompt_len: int, run_id: str) -> None:
    """Append a phase:start row so a lost run is distinguishable from a never-run one."""
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with USAGE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "model": model,
                        "prompt_chars": prompt_len,
                        "phase": "start",
                        "run_id": run_id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass


def log_usage(record: dict) -> None:
    """Append one usage line, so a direct call is accounted for like a CLI run."""
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with USAGE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def call_model(
    model_ref: str,
    messages: list[dict],
    *,
    max_tokens: int,
    temperature: float,
    timeout: int,
    tools: list[dict] | None = None,
) -> tuple[dict | None, dict, str]:
    """POST one completion. Returns (message, usage, error)."""
    provider, _, model_id = model_ref.partition("/")
    if not model_id:
        return None, {}, f"model must be <provider>/<model-id>, got {model_ref!r}"
    try:
        url, key = env_file.provider_endpoint(provider, "chat")
    except KeyError as exc:
        return None, {}, str(exc)
    if not url or not key:
        return None, {}, f"missing base url or {provider} credential in .env"
    body: dict = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
    request = urllib.request.Request(  # noqa: S310 - operator-configured https endpoint
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "opencode/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200].replace("\n", " ")
        return None, {}, f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001 - any transport failure is reported, never raised
        return None, {}, type(exc).__name__
    usage = payload.get("usage") or {}
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None, usage, f"unexpected body: {json.dumps(payload)[:160]}"
    return message, usage, ""


def message_text(message: dict) -> str:
    """The answer text: `content`, falling back to `reasoning_content` (the DeepSeek family)."""
    return str(message.get("content") or message.get("reasoning_content") or "")


def _guard_flags(text: str) -> str | None:
    """The harness guard's verdict on a command, reused instead of a home-made deny list."""
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "guard_for_api", ROOT / ".claude" / "hooks" / "guard.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.find_destructive(text)
    except Exception:  # noqa: BLE001 - never let the lookup break the fence
        return None


def _wsl_path(win_path: Path) -> str:
    """Translate a Windows path to its WSL mount path (F:\\x -> /mnt/f/x)."""
    text = str(win_path).replace("\\", "/")
    if len(text) > 2 and text[1] == ":":
        return f"/mnt/{text[0].lower()}{text[2:]}"
    return text


def run_tool(
    name: str,
    args: dict,
    *,
    allow_dirs: list[Path],
    allow_programs: set[str],
    sandbox: str = "",
) -> str:
    """Execute one tool call inside its fence. Returns the text fed back to the model."""
    if name == "read_file":
        raw = str(args.get("path", ""))
        normalised = raw.replace("\\", "/").casefold()
        if any(marker in normalised for marker in REFUSED_READ):
            return f"refused: {raw} is not readable by a worker"
        path = (ROOT / raw).resolve()
        if ROOT not in path.parents and path != ROOT:
            return f"refused: {raw} is outside the repository"
        # The filesystem may be case-insensitive (NTFS), so a name can only become sensitive
        # after resolution: test the repo-relative form too, casefolded for every platform.
        relative = path.relative_to(ROOT).as_posix().casefold()
        if any(marker in relative for marker in REFUSED_READ):
            return f"refused: {raw} is not readable by a worker"
        if not path.is_file():
            return f"error: {raw} does not exist"
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:MAX_TOOL_OUTPUT] + (
            "\n...[truncated]" if len(text) > MAX_TOOL_OUTPUT else ""
        )

    if name == "write_file":
        raw = str(args.get("path", ""))
        path = (ROOT / raw).resolve()
        if not allow_dirs or not any(
            path == d or d in path.parents for d in allow_dirs
        ):
            return f"refused: {raw} is outside the allowed directories"
        content = str(args.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return f"wrote {len(content)} characters to {raw}"

    if name == "run_command":
        argv = args.get("argv") or []
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(a, str) for a in argv)
        ):
            return "refused: argv must be a non-empty list of strings"
        program = Path(argv[0]).name.lower()
        if program not in allow_programs:
            return f"refused: {program} is not in --allow-program"
        flagged = _guard_flags(" ".join(argv))
        if flagged:
            return f"refused: the guard flags this command ({flagged})"
        if sandbox == "wsl":
            # Inside WSL the interpreter is `python3`, and the fence is what contains the command:
            # namespaces with only the workspace writable and networking untouched (see
            # tools/wsl_sandbox.sh). The guard veto above still applies first.
            if argv[0] == "python":
                argv = ["python3", *argv[1:]]
            argv = [
                "wsl",
                "-e",
                "unshare",
                "--user",
                "--map-root-user",
                "--mount",
                "--pid",
                "--fork",
                "--",
                "sh",
                _wsl_path(ROOT / "tools" / "wsl_sandbox.sh"),
                _wsl_path(ROOT),
                ".",
                "--",
                *argv,
            ]
        try:
            proc = subprocess.run(  # noqa: S603 - pinned argv, no shell, allowlisted program
                argv,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=900 if sandbox == "wsl" else 300,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "error: the command timed out after 300s"
        output = (proc.stdout or "") + (
            ("\n--stderr--\n" + proc.stderr) if proc.stderr else ""
        )
        return f"exit={proc.returncode}\n{output[:MAX_TOOL_OUTPUT]}"

    return f"refused: unknown tool {name}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Worker over a provider's chat API")
    parser.add_argument("prompt", nargs="?", help="the task; '-' reads stdin")
    parser.add_argument("--prompt-file", help="read the task from a file instead")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"<provider>/<model-id> (default {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="FILE",
        help="inline a file's contents (repeatable)",
    )
    parser.add_argument("--out", help="write the final answer here as well as stdout")
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=300, help="per-request timeout")
    parser.add_argument(
        "--no-guardrail", action="store_true", help="omit the worker preamble"
    )
    parser.add_argument(
        "--json", action="store_true", help="print a JSON result object"
    )
    parser.add_argument(
        "--tools", default="", help="comma list of read,write,command (default: none)"
    )
    parser.add_argument(
        "--sandbox",
        choices=("", "wsl"),
        default="",
        help="run run_command inside a namespace fence in WSL (needs WSL; keeps TLS working)",
    )
    parser.add_argument(
        "--allow-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="fence for write_file (repeatable)",
    )
    parser.add_argument(
        "--allow-program",
        action="append",
        default=[],
        metavar="NAME",
        help="program allowlist for run_command (repeatable)",
    )
    parser.add_argument("--max-steps", type=int, default=8, help="tool-loop budget")
    parser.add_argument("--transcript", help="save the full message/tool exchange here")
    args = parser.parse_args(argv)

    # Accept both repeated flags and comma lists: `--allow-program python,git` is the shape a
    # caller reaches for, and silently refusing everything because of the comma is a trap
    # (measured 2026-10-16: the worker reported every interpreter as refused).
    def _split(values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            out.extend(part.strip() for part in value.split(",") if part.strip())
        return out

    args.allow_dir = _split(args.allow_dir)
    args.allow_program = _split(args.allow_program)

    env_file.load_project_env()

    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    elif args.prompt == "-":
        prompt = sys.stdin.read()
    elif args.prompt:
        prompt = args.prompt
    else:
        parser.error("a prompt, --prompt-file or '-' is required")

    for rel in args.include:
        path = Path(rel)
        if not path.is_file():
            print(f"[model_api] include not found: {rel}", file=sys.stderr)
            return 2
        prompt += f"\n\n--- file: {rel} ---\n{path.read_text(encoding='utf-8')}"

    enabled = {t.strip() for t in args.tools.split(",") if t.strip()}
    keyword_for = {"read_file": "read", "write_file": "write", "run_command": "command"}
    tool_schemas = [
        schema for name, schema in TOOL_SCHEMAS.items() if keyword_for[name] in enabled
    ]
    if "write" in enabled and not args.allow_dir:
        parser.error("--tools write requires at least one --allow-dir")
    allow_dirs = [(ROOT / d).resolve() for d in args.allow_dir]
    allow_programs = {p.lower() for p in args.allow_program}

    messages: list[dict] = []
    if not args.no_guardrail:
        messages.append({"role": "system", "content": GUARDRAIL})
    messages.append({"role": "user", "content": prompt})

    run_id = uuid.uuid4().hex
    log_start(args.model, len(prompt), run_id)
    started = time.monotonic()
    transcript: list[dict] = list(messages)
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
    answer, error, steps = "", "", 0

    while steps < max(1, args.max_steps):
        steps += 1
        message, usage, error = call_model(
            args.model,
            messages,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            timeout=args.timeout,
            tools=tool_schemas or None,
        )
        if message is None:
            break
        for key in usage_total:
            usage_total[key] += usage.get(key) or 0
        transcript.append({"role": "assistant", "message": message})
        calls = message.get("tool_calls") or []
        if not calls:
            answer = message_text(message)
            break
        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": calls,
            }
        )
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            try:
                call_args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                call_args = {}
            result = run_tool(
                name,
                call_args,
                allow_dirs=allow_dirs,
                allow_programs=allow_programs,
                sandbox=args.sandbox,
            )
            print(
                f"[model_api] tool {name}({json.dumps(call_args)[:90]}) -> {result.splitlines()[0][:90] if result else ''}",
                file=sys.stderr,
            )
            transcript.append(
                {"role": "tool", "name": name, "args": call_args, "result": result}
            )
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id", ""), "content": result}
            )
    else:
        error = error or f"step budget exhausted after {args.max_steps} steps"

    elapsed = time.monotonic() - started
    ok = bool(answer)
    log_usage(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": args.model,
            "prompt_chars": len(prompt),
            "includes": args.include,
            "tools": sorted(enabled),
            "sandbox": args.sandbox or None,
            "steps": steps,
            "elapsed_s": round(elapsed, 2),
            "prompt_tokens": usage_total["prompt_tokens"],
            "completion_tokens": usage_total["completion_tokens"],
            "ok": ok,
            "error": error or None,
            "phase": "finish",
            "run_id": run_id,
        }
    )
    if args.transcript:
        Path(args.transcript).write_text(
            json.dumps(transcript, indent=1), encoding="utf-8", newline="\n"
        )

    if not ok:
        print(
            f"[model_api] failed after {elapsed:.1f}s ({steps} steps): {error or 'empty response'}",
            file=sys.stderr,
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": error or "empty response",
                        "steps": steps,
                        "seconds": round(elapsed, 2),
                    }
                )
            )
        return 2

    if args.out:
        # Models habitually wrap an artefact in a ```diff fence; strip it before saving so the
        # file is apply-able. The hunk headers may still need a manual fix: measured 2026-10-16,
        # one logically correct patch carried `+N,13` for a 12-line body and a start line ~29 off,
        # which `git apply` rejects even with --recount.
        clean = answer
        stripped = clean.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            clean = "\n".join(lines) + "\n"
        Path(args.out).write_text(clean, encoding="utf-8", newline="\n")
    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "model": args.model,
                    "seconds": round(elapsed, 2),
                    "steps": steps,
                    "prompt_tokens": usage_total["prompt_tokens"],
                    "completion_tokens": usage_total["completion_tokens"],
                    "out": args.out,
                    "content": answer,
                }
            )
        )
    else:
        sys.stdout.write(answer if answer.endswith("\n") else answer + "\n")
    print(
        f"[model_api] {args.model} ok in {elapsed:.1f}s ({steps} step(s), "
        f"{usage_total['completion_tokens']} completion tokens)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
