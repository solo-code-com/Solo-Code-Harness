"""Tests for tools/guard_rules.py - the shared destructive-command rules."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_rules


def test_prompt_naming_a_force_push_is_caught():
    assert guard_rules.match_prompt("please run git push --force origin main") == (
        "git push --force"
    )


def test_prompt_is_matched_case_insensitively():
    assert guard_rules.match_prompt("RUN GIT RESET --HARD HEAD~1") == "git reset --hard"


def test_benign_prompt_is_not_caught():
    assert guard_rules.match_prompt("review the parser and run pytest -q") is None


def test_prompt_rules_are_not_command_anchored():
    """A sentence wrapper must not defeat the check; BLOCK_PATTERNS would miss it."""
    prose = "after the merge, do a git clean -fd please"
    assert guard_rules.match_prompt(prose) == "git clean -fd"
    assert guard_rules.match_block_pattern(prose) is None
