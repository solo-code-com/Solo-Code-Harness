#!/usr/bin/env python3
"""
Dispatch command - contract tests
=================================
Every command `dispatch.py` prints must be runnable by the wrapper it names. A
flag the wrapper does not define is a command that fails on its first use, and a
plan that reports "enforced" must emit the flag that actually enforces it.

These tests read each wrapper's own --help output, so a new flag or a renamed one
is caught here instead of in a delegation that silently falls back.

Usage:
    python -m pytest tools/test_dispatch_commands.py -v
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import dispatch  # noqa: E402

FLAG_RE = re.compile(r"(?<![\w-])(--?[A-Za-z][\w-]*)")

#: wrapper path -> the flag that makes its write real
WRAPPER_FENCE = {
    "tools/opencode_delegate.py": ["--enforce-scope"],
    "tools/antigravity_delegate.py": ["--auto-approve", "--allow-dir"],
    "tools/free_delegate.py": ["--enforce-scope", "--write"],
}


def _wrapper_of(command: str) -> str:
    for path in WRAPPER_FENCE:
        if path in command:
            return path
    raise AssertionError(f"no known wrapper in command: {command}")


@pytest.fixture(scope="module")
def help_flags() -> dict[str, set[str]]:
    """Every long/short flag each wrapper's own parser accepts."""
    found: dict[str, set[str]] = {}
    for path in WRAPPER_FENCE:
        proc = subprocess.run(
            [sys.executable, str(ROOT / path), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=str(ROOT),
            check=False,
        )
        assert proc.returncode == 0, f"{path} --help failed: {proc.stderr[:300]}"
        found[path] = set(FLAG_RE.findall(proc.stdout))
        assert "--help" in found[path], f"{path} help has no --help flag"
    return found


@pytest.fixture(scope="module")
def fleet() -> dict:
    return dispatch.load_json(dispatch.FLEET)


def _plan(fleet, arm: str, role_task: str, directory: str = "src") -> dict:
    return dispatch.plan(role_task, [arm], fleet, {}, {}, "auto", None, directory)


@pytest.mark.parametrize("arm", ["deepseek", "gemini", "claude", "chatgpt"])
@pytest.mark.parametrize("task", ["implement a retry", "review the auth module"])
def test_every_flag_is_defined_by_its_wrapper(arm, task, fleet, help_flags):
    decision = _plan(fleet, arm, task)
    command = decision["command"]
    wrapper = _wrapper_of(command)
    tokens = shlex.split(command, posix=False)
    used = {tok for tok in tokens if FLAG_RE.fullmatch(tok)}
    unknown = used - help_flags[wrapper]
    assert not unknown, f"{arm} emits {unknown} which {wrapper} does not define"


@pytest.mark.parametrize("arm", ["deepseek", "gemini", "claude", "chatgpt"])
def test_a_write_plan_emits_the_flag_that_enforces_it(arm, fleet):
    """A plan that says "enforced" must carry the flag, not just the word."""
    decision = _plan(fleet, arm, "implement a retry")
    if decision["fence"] != "enforced":
        pytest.skip(f"{arm} has no enforceable write template")
    wrapper = _wrapper_of(decision["command"])
    for flag in WRAPPER_FENCE[wrapper]:
        assert flag in decision["command"], f"{arm} claims enforced without {flag}"


@pytest.mark.parametrize("arm", ["deepseek", "gemini", "claude", "chatgpt"])
def test_a_code_plan_is_not_reported_read_only_silently(arm, fleet):
    """A code task on an arm without a write template must say so."""
    decision = _plan(fleet, arm, "implement a retry")
    has_write = bool(fleet["families"][arm].get("command_write"))
    if not has_write:
        assert decision["fence"].startswith("read-only")


def test_chatgpt_plan_has_no_codex_only_flag(fleet):
    """-c is a codex exec flag; free_delegate takes --effort and maps it."""
    command = _plan(fleet, "chatgpt", "implement a retry")["command"]
    tokens = shlex.split(command, posix=False)
    assert "-c" not in tokens
    assert "--effort" in tokens


def test_repo_root_plan_does_not_claim_a_fence(fleet):
    decision = _plan(fleet, "claude", "implement a retry", directory=".")
    assert decision["fence"] == "repo-root (no enforceable fence)"
    assert "--enforce-scope" not in decision["command"]
