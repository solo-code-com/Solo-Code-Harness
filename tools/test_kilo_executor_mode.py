#!/usr/bin/env python3
"""
Kilo Executor-Mode Hook Tests
=============================
Runs .kilo/hooks/pre-tool-use/executor-mode.js as a subprocess with JSON
payloads on stdin and asserts the block/allow decision (exit 2 = block,
0 = allow). This is the Kilo-engine parity build of the Claude executor-mode
gate in .claude/hooks/guard.py -- see tools/test_claude_guard.py for the
Claude-side equivalent of every case below.

Unlike guard.py (which reads CLAUDE_PROJECT_DIR), the Kilo hook resolves its
project root via process.cwd() -- that is how the other .kilo hooks resolve
paths (session-start.js, session-end.js, governance-capture.js all use
process.cwd()). So these tests set `cwd` on the subprocess instead of an
env var.

Usage:
    python -m pytest tools/test_kilo_executor_mode.py -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".kilo" / "hooks" / "pre-tool-use" / "executor-mode.js"


def _exec_root(tmp_path: Path, state: str | None) -> Path:
    """Build a project root with executor-mode set to `state` (None = absent)."""
    (tmp_path / ".solocode").mkdir(parents=True, exist_ok=True)
    if state is not None:
        (tmp_path / ".solocode" / "executor-mode").write_text(state, encoding="utf-8")
    return tmp_path


def _run_hook(payload: dict, *, cwd: Path) -> int:
    proc = subprocess.run(  # noqa: S603,S607 — fixed argv, node on PATH, no shell
        ["node", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
    return proc.returncode


def _run_hook_full(payload: dict, *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603,S607 — fixed argv, node on PATH, no shell
        ["node", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


# ── Executor mode (orchestrator must delegate writes) ────────────────────
#
# Default-ON by design: an absent state file means the gate is active, so a
# fresh clone (or a deleted toggle) fails closed rather than open.


def test_executor_mode_defaults_on_when_state_file_absent(tmp_path):
    """No toggle file must mean ENABLED -- fail closed, not open."""
    root = _exec_root(tmp_path, None)
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/app.py", "new_string": "x"},
    }
    assert _run_hook(payload, cwd=root) == 2


@pytest.mark.parametrize(
    "state",
    ["off", "OFF", " off \n", "0", "disabled", "false", "no", "off  # re-enable later"],
)
def test_executor_mode_off_values_allow_writes(tmp_path, state):
    root = _exec_root(tmp_path, state)
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/app.py", "new_string": "x"},
    }
    assert _run_hook(payload, cwd=root) == 0


@pytest.mark.parametrize("state", ["on", "", "true", "1", "yes", "garbage"])
def test_executor_mode_non_off_values_block_writes(tmp_path, state):
    """Anything that is not an explicit off-value keeps the gate closed."""
    root = _exec_root(tmp_path, state)
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/app.py", "new_string": "x"},
    }
    assert _run_hook(payload, cwd=root) == 2


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_executor_mode_blocks_all_write_tools(tmp_path, tool):
    root = _exec_root(tmp_path, "on")
    payload = {
        "tool_name": tool,
        "tool_input": {"file_path": "src/app.py", "content": "x"},
    }
    assert _run_hook(payload, cwd=root) == 2


def test_executor_mode_does_not_gate_bash(tmp_path):
    """Scope is Edit/Write/MultiEdit only (level (a)). Bash stays open on
    purpose: it is how the orchestrator runs the verification gates it still
    owns."""
    root = _exec_root(tmp_path, "on")
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "python -m pytest tools/ -q"},
    }
    assert _run_hook(payload, cwd=root) == 0


def test_executor_mode_does_not_gate_reads(tmp_path):
    root = _exec_root(tmp_path, "on")
    payload = {"tool_name": "Read", "tool_input": {"file_path": "src/app.py"}}
    assert _run_hook(payload, cwd=root) == 0


@pytest.mark.parametrize(
    "rel",
    [
        ".gemini/antigravity/handoff/inbox/my-plan.md",
        ".solocode/executor-mode",
    ],
)
def test_executor_mode_exempts_delegation_plumbing(tmp_path, rel):
    """Writing the handoff brief IS the delegation; the toggle must stay
    writable or the mode could not be turned off from inside a session."""
    root = _exec_root(tmp_path, "on")
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": rel, "content": "plan"},
    }
    assert _run_hook(payload, cwd=root) == 0


def test_executor_mode_exemption_matches_absolute_paths(tmp_path):
    """Some clients pass absolute paths; the exemption must survive that."""
    root = _exec_root(tmp_path, "on")
    target = root / ".gemini" / "antigravity" / "handoff" / "inbox" / "p.md"
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "plan"},
    }
    assert _run_hook(payload, cwd=root) == 0


def test_executor_mode_exemption_is_not_a_substring_hole(tmp_path):
    """`inbox/` is exempt; a sibling path that merely *contains* the prefix
    elsewhere must not inherit the exemption."""
    root = _exec_root(tmp_path, "on")
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "src/.solocode/executor-mode", "content": "off"},
    }
    assert _run_hook(payload, cwd=root) == 2


def test_executor_mode_denial_names_the_delegation_command(tmp_path):
    """The block is only useful if it tells the operator what to do instead."""
    root = _exec_root(tmp_path, "on")
    proc = _run_hook_full(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/app.py", "new_string": "x"},
        },
        cwd=root,
    )
    assert proc.returncode == 2
    assert "kilo_cli_delegate.py" in proc.stderr
    assert ".solocode/executor-mode" in proc.stderr


def test_malformed_json_allowed(tmp_path):
    root = _exec_root(tmp_path, "on")
    proc = subprocess.run(  # noqa: S603,S607 — fixed argv, node on PATH, no shell
        ["node", str(HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    assert proc.returncode == 0


def test_stdin_is_echoed_back(tmp_path):
    """Kilo hooks must pass the original payload through on stdout so a
    later hook in the chain still receives it."""
    root = _exec_root(tmp_path, "off")
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/app.py", "new_string": "x"},
    }
    proc = _run_hook_full(payload, cwd=root)
    assert json.loads(proc.stdout) == payload
