#!/usr/bin/env python3
"""subagent_cli.py — CLI Provider for the subagent seam.

`tools/subagent_seam.py` owns the Service *Definition* (the vocabulary and the
`SubagentRuntime` Protocol) and imports no provider. This module is a Provider:
it implements that Protocol on top of the harness's CLI delegate wrappers
(`tools/*_delegate.py`), and a small dispatcher picks the cheapest capable one.

Seam rules honoured here:

  - this Provider imports ONLY the Definition (never another Provider);
  - `supported_capabilities()` is advertised honestly — every CLI worker can
    `read` and run `bash`, and **none advertises `write`**: writing is opt-in
    through `request.options` (`allow_write` / `allow_dir`), so a consumer that
    *needs* to write must say so rather than get it silently;
  - the returned `SubagentResult` separates `evidence` (argv, exit code, the
    error string) from `summary` (the worker's prose, untrusted).

Usage:
    python tools/subagent_cli.py --list
    python tools/subagent_cli.py --self-test
"""

from __future__ import annotations

import argparse
import json
import subprocess  # noqa: S404 — fixed argv; the task is data, not a command
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.subagent_seam import (  # noqa: E402 — path setup must precede the import
    SubagentRequest,
    SubagentResult,
    SubagentRuntime,
    check_capabilities,
)

Runner = Callable[[list[str], int], "tuple[int, str, str]"]
_READ_BASH = frozenset({"read", "bash"})


@dataclass(frozen=True)
class Worker:
    """One CLI worker: its wrapper, its dispatch rank (cheaper first), caps."""

    id: str
    wrapper: str
    rank: int
    capabilities: frozenset[str] = _READ_BASH

    @property
    def ready(self) -> bool:
        return (ROOT / self.wrapper).is_file()


WORKERS: tuple[Worker, ...] = (
    Worker("opencode", "tools/opencode_delegate.py", 0),
    Worker("kilo", "tools/kilo_cli_delegate.py", 1),
    Worker("dsh", "tools/dsh_delegate.py", 2),
    Worker("gemini", "tools/antigravity_delegate.py", 4),
)

ROUTER_WORKERS = {
    "cli:opencode": "opencode",
    "cli:kilo": "kilo",
    "cli:antigravity": "gemini",
}


def provider_id_for_router_worker(worker_id: str) -> str | None:
    """Translate a router worker id to a CLI provider, if it has one."""
    if worker_id == "none" or worker_id.startswith(("subagent:", "in_process:")):
        return None
    return ROUTER_WORKERS[worker_id]


def build_argv(worker: str, request: SubagentRequest) -> list[str]:
    """Map a request to the delegate wrapper argv. Pure — tested directly."""
    py = sys.executable
    opts = request.options
    if worker == "gemini":
        argv = [py, "tools/antigravity_delegate.py", request.prompt]
        if request.model:
            argv += ["--model", request.model]
        if opts.get("allow_tools"):
            argv.append("--allow-tools")
        return argv
    if worker == "opencode":
        argv = [py, "tools/opencode_delegate.py", request.prompt]
        if request.model:
            argv += ["--model", request.model]
        if opts.get("free"):
            argv.append("--free")
        return argv
    if worker == "kilo":
        argv = [py, "tools/kilo_cli_delegate.py", request.prompt]
        if request.model:
            argv += ["--model", request.model]
        return argv
    if worker == "dsh":
        return [py, "tools/dsh_delegate.py", request.prompt]

    raise ValueError(f"unknown worker: {worker!r}")


def _default_runner(argv: list[str], timeout_s: int) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv; argv[0] is an absolute python
            argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(str(exc)) from exc
    return proc.returncode, proc.stdout, proc.stderr


class CliProvider:
    """A `SubagentRuntime` that shells out to one worker's delegate wrapper."""

    def __init__(self, worker: Worker, runner: Runner | None = None) -> None:
        self._worker = worker
        self._runner = runner or _default_runner

    @property
    def id(self) -> str:
        return self._worker.id

    @property
    def rank(self) -> int:
        return self._worker.rank

    def supported_capabilities(self) -> frozenset[str]:
        return self._worker.capabilities

    def run(self, request: SubagentRequest) -> SubagentResult:
        check_capabilities(request, self)
        argv = build_argv(self._worker.id, request)
        try:
            code, out, err = self._runner(argv, request.timeout_s)
        except TimeoutError:
            return SubagentResult(
                ok=False,
                evidence={"argv": argv, "error": f"timeout after {request.timeout_s}s"},
                stop_reason="timeout",
            )
        ok = code == 0
        evidence: dict[str, object] = {"argv": argv, "exit_code": code}
        if not ok:
            # Both streams can carry the fragment a wrapper captured; a timeout
            # arrives on stdout (antigravity_delegate.py, codex_delegate.py).
            parts = [part.strip() for part in (err, out) if part.strip()]
            evidence["error"] = "\n".join(parts)[:2000] or f"exit {code}"
        return SubagentResult(
            ok=ok,
            summary=out,
            evidence=evidence,
            stop_reason="completed" if ok else "error",
        )


def select_provider(
    request: SubagentRequest, registry: tuple[CliProvider, ...] | None = None
) -> CliProvider:
    """Pick a provider: explicit `options['worker']`, else the router, else cheapest."""
    providers = (
        registry if registry is not None else tuple(CliProvider(w) for w in WORKERS)
    )
    by_id = {p.id: p for p in providers}
    explicit = request.options.get("worker")
    if explicit:
        if explicit not in by_id:
            raise ValueError(f"unknown worker: {explicit!r}")
        return by_id[explicit]
    # The router knows the task's shape; the registry only knows argv. Ask it, but
    # never depend on the answer: a routing failure must not break dispatch.
    try:
        from tools import router

        routed_id = provider_id_for_router_worker(
            router.route(request.prompt)["worker"]
        )
    except Exception:  # noqa: BLE001 - any failure means "no opinion", including an unknown id
        routed_id = None
    routed = by_id.get(routed_id) if routed_id else None
    if routed is not None and request.capabilities <= routed.supported_capabilities():
        return routed
    capable = [
        p for p in providers if request.capabilities <= p.supported_capabilities()
    ]
    if not capable:
        raise ValueError(f"no provider supports {sorted(request.capabilities)}")
    return min(capable, key=lambda p: p.rank)


def _self_test() -> None:
    print("Running subagent_cli self-test...")
    # Every worker is a valid runtime and advertises read+bash only.
    for worker in WORKERS:
        provider = CliProvider(worker)
        assert isinstance(provider, SubagentRuntime)
        assert provider.supported_capabilities() == _READ_BASH

    # write is NOT advertised: a consumer that needs it must fail loud.
    provider = CliProvider(WORKERS[0])
    try:
        check_capabilities(
            SubagentRequest(prompt="x", capabilities=frozenset({"write"})), provider
        )
    except ValueError:
        pass
    else:
        raise AssertionError("write must not be silently supported")

    # argv mapping

    assert (
        build_argv(
            "gemini",
            SubagentRequest(prompt="p", model="m", options={"allow_tools": True}),
        )[-1]
        == "--allow-tools"
    )

    # cheapest-capable selection + explicit override
    assert select_provider(SubagentRequest(prompt="p")).id == "opencode"

    # run() splits evidence from summary
    fake = lambda _argv, _t: (0, "prose", "")  # noqa: E731 — tiny test double
    result = CliProvider(WORKERS[0], runner=fake).run(SubagentRequest(prompt="p"))
    assert result.ok and result.summary == "prose" and result.evidence["exit_code"] == 0

    print("All tests passed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Subagent CLI provider (Service Provider)"
    )
    parser.add_argument("prompt", nargs="?", help="self-contained task brief")
    parser.add_argument("--worker", help="force a worker (else cheapest capable)")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--list", action="store_true", help="list workers and exit")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        _self_test()
        return 0
    if args.list or not args.prompt:
        for worker in sorted(WORKERS, key=lambda w: w.rank):
            print(
                f"{worker.id:<9} rank={worker.rank} ready={worker.ready}  {worker.wrapper}"
            )
        return 0 if args.list else 1

    request = SubagentRequest(
        prompt=args.prompt,
        model=args.model,
        timeout_s=args.timeout,
        options={"worker": args.worker} if args.worker else {},
    )
    provider = select_provider(request)
    result = provider.run(request)
    if args.json:
        print(
            json.dumps(
                {
                    "worker": provider.id,
                    "ok": result.ok,
                    "stop_reason": result.stop_reason,
                    "summary": result.summary,
                    "evidence": result.evidence,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(result.summary or f"(no output; stop_reason={result.stop_reason})")
        print(
            f"--- worker={provider.id} ok={result.ok} stop={result.stop_reason} ---",
            file=sys.stderr,
        )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
