#!/usr/bin/env python3
"""
Memory-manager hook - threshold tests
=====================================
The hook enforces the memory cap described in SPEC 8: WARN at 4,000 characters,
BLOCK at 8,000. It had no test, and its block exit disagreed with the documented
one, so nothing caught a drift between the two.

decisions-archive.md is deliberately uncapped: it is cold storage, not loaded
into a session, so it must never trigger the gate.

Usage:
    python -m pytest tools/test_memory_manager_hook.py -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".kilo" / "hooks" / "post-tool-use" / "memory-manager.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _memory(tmp_path: Path, name: str, chars: int) -> None:
    d = tmp_path / ".kilo" / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("x" * chars, encoding="utf-8")


def _run(tmp_path: Path, tool: str = "Edit") -> subprocess.CompletedProcess:
    payload = {"tool_name": tool, "tool_input": {"file_path": ".kilo/memory/MEMORY.md"}}
    return subprocess.run(
        ["node", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(tmp_path),
        timeout=60,
    )


def test_warn_threshold_is_advisory(tmp_path):
    _memory(tmp_path, "MEMORY.md", 5000)
    result = _run(tmp_path)
    assert result.returncode == 0
    assert "Warning threshold" in result.stderr


def test_hard_threshold_blocks(tmp_path):
    """Exit 2 is the block signal every other blocking hook uses."""
    _memory(tmp_path, "MEMORY.md", 9000)
    result = _run(tmp_path)
    assert result.returncode == 2
    assert "BLOCKED" in result.stderr


def test_archive_file_is_never_capped(tmp_path):
    _memory(tmp_path, "decisions-archive.md", 20000)
    result = _run(tmp_path)
    assert result.returncode == 0
    assert "BLOCKED" not in result.stderr


def test_a_non_write_tool_is_ignored(tmp_path):
    _memory(tmp_path, "MEMORY.md", 9000)
    result = _run(tmp_path, tool="Bash")
    assert result.returncode == 0
