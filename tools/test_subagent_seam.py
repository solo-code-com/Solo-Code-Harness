#!/usr/bin/env python3
"""
Subagent Seam — unit tests
==========================
Unit coverage for tools/subagent_seam.py vocabulary types and the
capability-gating helper. No network or subprocess is needed.

The seam splits worker EVIDENCE (trusted, machine-verifiable) from worker
SUMMARY (untrusted prose). These tests prove the Service Definition types
obey the Consumer contract before any provider is wired in.

Usage:
    python -m pytest tools/test_subagent_seam.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from tools import subagent_seam  # noqa: E402


class _WriteOnlyRuntime:
    """Minimal provider that supports only the 'write' capability."""

    def supported_capabilities(self) -> frozenset[str]:
        return frozenset({"write"})


def test_subagent_request_defaults():
    req = subagent_seam.SubagentRequest(prompt="do X")
    assert req.prompt == "do X"
    assert req.options == {}
    assert req.capabilities == frozenset()


def test_subagent_result_reads_evidence_properties():
    res = subagent_seam.SubagentResult(
        ok=False,
        evidence={"session_id": "ses_1", "error": "timeout after 120s"},
    )
    assert res.session_id == "ses_1"
    assert res.error == "timeout after 120s"


def test_check_capabilities_raises_for_missing_capability():
    runtime = _WriteOnlyRuntime()
    req = subagent_seam.SubagentRequest(prompt="do X", capabilities=frozenset({"bash"}))
    with pytest.raises(ValueError):
        subagent_seam.check_capabilities(req, runtime)


def test_check_capabilities_passes_when_capabilities_supported():
    runtime = _WriteOnlyRuntime()
    subagent_seam.check_capabilities(
        subagent_seam.SubagentRequest(prompt="do X", capabilities=frozenset({"write"})),
        runtime,
    )
    subagent_seam.check_capabilities(
        subagent_seam.SubagentRequest(prompt="do X"), runtime
    )


def test_stop_reason_accepts_closed_union_values():
    assert (
        subagent_seam.SubagentResult(ok=False, stop_reason="scope_exceeded").stop_reason
        == "scope_exceeded"
    )
    assert subagent_seam.SubagentResult(ok=True, stop_reason=None).stop_reason is None
