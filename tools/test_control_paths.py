#!/usr/bin/env python3
"""Unit tests for the control-path check shared by the delegation wrappers."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from tools import control_paths  # noqa: E402


def test_changed_paths_parses_modified_untracked_and_renamed_entries():
    porcelain = " M a.py\n?? b.md\nR  old.py -> new.py\n"

    assert control_paths.changed_paths(porcelain) == [
        "a.py",
        "b.md",
        "old.py",
        "new.py",
    ]


def test_violations_reports_control_paths_only():
    porcelain = " M tools/x.py\n?? src/app.py\n M AGENTS.md\n"

    assert control_paths.violations(porcelain) == ["AGENTS.md", "tools/x.py"]


def test_violations_de_duplicates_and_sorts():
    porcelain = "?? tools/z.py\n M tools/a.py\n?? tools/z.py\n M tools/a.py\n"

    assert control_paths.violations(porcelain) == ["tools/a.py", "tools/z.py"]


def test_is_harness_repo_needs_all_markers(tmp_path):
    for marker in control_paths.HARNESS_REPO_MARKERS:
        path = tmp_path / marker
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    assert control_paths.is_harness_repo(tmp_path) is True

    (tmp_path / control_paths.HARNESS_REPO_MARKERS[0]).unlink()

    assert control_paths.is_harness_repo(tmp_path) is False


def test_control_paths_are_repo_relative():
    for entry in control_paths.CONTROL_PATHS:
        assert not entry.startswith(("/", "\\")), entry
        assert not re.match(r"^[A-Za-z]:", entry), entry


def test_new_violations_ignores_pre_existing_dirt():
    """A dirty tree the operator already had is not the worker's fault."""
    before = " M tools/old_edit.py\n"
    after = before + " M tools/new_edit.py\n"
    assert control_paths.new_violations(before, after) == ["tools/new_edit.py"]
