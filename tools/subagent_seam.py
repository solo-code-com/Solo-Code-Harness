#!/usr/bin/env python3
"""
subagent_seam.py — Service Definition for the subagent capability.

Pattern ported from DeepSeek Harness packages/subagent/ (capability seam:
Service Definition / Service Provider / Consumer). This module owns only the
Service Definition role — the vocabulary types and the SubagentRuntime
Protocol. No provider is imported here, matching the seam rule that
Consumer and Provider import only the Definition.

The central contract, taken from dsh's verified lessons:

    Worker EVIDENCE is trustworthy; worker SELF-ASSESSMENT is not.

Every controlled delegation in this harness produced at least one error
invisible in the worker's own summary. So `SubagentResult` splits the two:

    - `evidence` — machine-verifiable facts: session id, commands run,
      file paths written, exit codes, raw output.
    - `summary` — the worker's prose self-report, treated as untrusted.

An orchestrator must always re-run the evidence before trusting a result.

This module is the interface only. The current single provider is
`tools/opencode_delegate.py` (CLI subprocess); it is NOT refactored to
implement this Protocol yet — that happens only when a second provider
exists (see docs/dsh-port-plan.md, A2).

Usage:
    python tools/subagent_seam.py --self-test
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ── Vocabulary types ─────────────────────────────────────────────────────────


@dataclass
class SubagentRequest:
    """A self-contained task for a subagent.

    The prompt must inline every piece of context the worker needs: workers
    are context-blind and cannot see this session's history.
    """

    prompt: str
    model: str | None = None
    cwd: str | None = None
    timeout_s: int = 120
    # Extra keyword args passed through to the provider (e.g. --free, --no-guardrail).
    options: dict[str, Any] = field(default_factory=dict)
    # Capabilities this task needs from the provider (e.g. {"write", "bash"}).
    capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass
class SubagentResult:
    """Outcome of one subagent run, split into evidence vs summary.

    Attributes:
        ok: Whether the run completed without a transport/tooling error.
            Note: `ok` describes the run, not the quality of the answer.
        evidence: Machine-verifiable facts. Providers must fill only fields
            they actually observed; leave others None.
        summary: The worker's own prose. Untrusted by contract.
        raw_events: The parsed event stream, when the provider emits one.
    """

    ok: bool
    summary: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    raw_events: list[Any] = field(default_factory=list)
    # Closed union of why the run stopped: 'completed' | 'scope_exceeded' |
    # 'timeout' | 'error' | None.
    stop_reason: str | None = None

    @property
    def session_id(self) -> str | None:
        """Session id recorded in evidence, if the provider observed one."""
        return self.evidence.get("session_id")

    @property
    def error(self) -> str | None:
        """Transport/tooling error, if the run failed."""
        return self.evidence.get("error")


@runtime_checkable
class SubagentRuntime(Protocol):
    """The seam a provider implements.

    A Provider supplies this protocol and registers under a runtime name; a
    Consumer calls ``run(request)`` without importing the provider. This is
    the exact zero-coupling contract from dsh: `agent-loop` never imports
    `shell`; here the orchestrator never imports a specific executor.

    Capability contract: before ``run``, a Consumer MUST verify that
    ``request.capabilities`` is a subset of ``supported_capabilities()``.
    If it is not, the Consumer MUST fail loud (raise, or stop and report)
    rather than run the task anyway and silently drop the missing
    capability.
    """

    def supported_capabilities(self) -> frozenset[str]:
        """Return the set of capabilities this provider can satisfy."""
        ...

    def run(self, request: SubagentRequest) -> SubagentResult:
        """Execute one request and return a result.

        Must separate observed evidence from self-assessment in the returned
        ``SubagentResult``.
        """
        ...


# ── Self-test ────────────────────────────────────────────────────────────────


class _FakeRuntime:
    """A fake provider used only to prove the Protocol is not over-specified."""

    def supported_capabilities(self) -> frozenset[str]:
        return frozenset({"write"})

    def run(self, request: SubagentRequest) -> SubagentResult:
        return SubagentResult(
            ok=True,
            summary="looks good",
            evidence={
                "session_id": "fake-session",
                "files_written": ["a.txt"],
                "exit_code": 0,
            },
        )


def check_capabilities(request: SubagentRequest, runtime: SubagentRuntime) -> None:
    """Fail loud when a request needs a capability the runtime does not have.

    Consumers call this before ``runtime.run(request)``. Running a request
    whose required capabilities are not a subset of what the provider
    supports must not silently drop the missing capability — it must raise.
    """
    missing = request.capabilities - runtime.supported_capabilities()
    if missing:
        raise ValueError(f"runtime lacks required capabilities: {sorted(missing)}")


def _self_test() -> None:
    print("Running subagent_seam self-test...")

    # Request carries context inline; options default to empty.
    req = SubagentRequest(prompt="do X", model="provider/model", timeout_s=30)
    assert req.prompt == "do X"
    assert req.options == {}

    # Evidence is separated from summary.
    res = _FakeRuntime().run(req)
    assert res.ok is True
    assert res.summary == "looks good"
    assert res.session_id == "fake-session"
    assert res.error is None

    # ok=True + empty evidence is a real failure mode: evidence is the only
    # trusted channel, so a provider that reports success without evidence
    # must be caught by the consumer, not silently accepted.
    empty = SubagentResult(ok=True, summary="all good")
    assert empty.ok is True
    assert empty.evidence == {}
    assert empty.session_id is None

    # A failed run surfaces its error through evidence, not summary prose.
    failed = SubagentResult(ok=False, evidence={"error": "timeout after 120s"})
    assert failed.error == "timeout after 120s"

    # Protocol is runtime-checkable: the fake satisfies it.
    assert isinstance(_FakeRuntime(), SubagentRuntime)

    # Capability gating must fail loud for a missing capability...
    try:
        check_capabilities(
            SubagentRequest(prompt="do X", capabilities=frozenset({"bash"})),
            _FakeRuntime(),
        )
    except ValueError:
        pass  # expected
    else:
        raise AssertionError(
            "check_capabilities did not raise for unsupported capability"
        )

    # ...and pass when capabilities are empty or fully supported.
    check_capabilities(SubagentRequest(prompt="do X"), _FakeRuntime())
    check_capabilities(
        SubagentRequest(prompt="do X", capabilities=frozenset({"write"})),
        _FakeRuntime(),
    )

    # stop_reason round-trips its closed-union value.
    exceeded = SubagentResult(ok=False, stop_reason="scope_exceeded")
    assert exceeded.stop_reason == "scope_exceeded"

    print("All tests passed.")


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Subagent capability seam (Service Definition only)"
    )
    parser.add_argument("--self-test", action="store_true", help="Run self-test")
    args = parser.parse_args()

    if args.self_test:
        _self_test()
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
