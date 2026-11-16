#!/usr/bin/env python3
"""Tests for this repository's own .gitignore rules covering .solocode/.

Unlike tools/test_text_hygiene.py, which builds throwaway repositories, these
tests read the real checkout: the rules they guard are this repository's own
.gitignore, so a tmp_path fixture would measure nothing. ``git ls-files`` and
``git check-ignore`` run with ``cwd=ROOT``.

Usage:
    python -m pytest tools/test_repo_ignore_rules.py -q
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed or not on PATH"
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git in this repository and return the process, exit code included."""
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


@requires_git
def test_executor_config_template_is_tracked() -> None:
    result = _git("ls-files", "--error-unmatch", ".solocode/executor-config.json")
    assert result.returncode == 0, (
        "deploy.py copies .solocode/executor-config.json into every target, so a "
        f"clone must carry it: {result.stderr.strip()}"
    )


@requires_git
def test_executor_mode_is_ignored() -> None:
    ignored = _git("check-ignore", "-q", ".solocode/executor-mode")
    assert ignored.returncode == 0, (
        ".solocode/executor-mode must stay ignored: the local copy is 'off' and "
        "deploy.py copies it into every target"
    )
    tracked = _git("ls-files", "--error-unmatch", ".solocode/executor-mode")
    assert tracked.returncode != 0, (
        ".solocode/executor-mode is tracked; a clone and every deployment would "
        "start with the write gate already disabled"
    )


@requires_git
def test_runtime_state_is_ignored() -> None:
    result = _git("check-ignore", "-q", ".solocode/sessions/example.json")
    assert result.returncode == 0, (
        ".solocode/sessions/ holds personal runtime state and must never be committed"
    )


def test_no_directory_exclusion_shadows_the_solocode_negations() -> None:
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    shadowing = [
        line.strip()
        for line in lines
        if line.strip()
        and not line.strip().startswith("#")
        and line.strip().rstrip("/") == ".solocode"
    ]
    assert shadowing == [], (
        "a bare directory exclusion makes git skip .solocode/ entirely, which "
        "silently re-kills every negation below it; shadowing pattern(s) found: "
        f"{shadowing!r}"
    )
