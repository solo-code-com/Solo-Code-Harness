#!/usr/bin/env python3
"""Unit tests for the workspace snapshot helpers shared by the wrappers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from tools import workspace_snapshot  # noqa: E402


def test_changed_workspace_paths_detects_create_modify_and_delete():
    before = {"a.py": "one", "gone.py": "old"}
    after = {"a.py": "two", "new.py": "new"}

    assert workspace_snapshot.changed_workspace_paths(before, after) == [
        "a.py",
        "gone.py",
        "new.py",
    ]


def test_changed_workspace_paths_is_empty_for_identical_snapshots():
    before = {"a.py": "one"}

    assert workspace_snapshot.changed_workspace_paths(before, dict(before)) == []


def test_paths_outside_directory_uses_path_boundaries():
    paths = ["src/a.py", "src2/b.py", "README.md"]

    assert workspace_snapshot.paths_outside_directory(paths, "src") == [
        "src2/b.py",
        "README.md",
    ]
    assert workspace_snapshot.paths_outside_directory(paths, ".") == []


def test_paths_outside_directory_treats_the_directory_itself_as_inside():
    assert workspace_snapshot.paths_outside_directory(["src"], "src") == []


def test_snapshot_ignores_root_generated_dirs(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / ".venv" / "lib").mkdir(parents=True)
    (ws / ".pytest_temp_x").mkdir()
    (ws / "src" / "a.py").write_text("a", encoding="utf-8")
    (ws / ".venv" / "lib" / "v.py").write_text("v", encoding="utf-8")
    (ws / ".pytest_temp_x" / "t.py").write_text("t", encoding="utf-8")

    assert sorted(workspace_snapshot.snapshot_workspace(ws)) == ["src/a.py"]


def test_snapshot_ignores_root_build_and_dist_but_keeps_nested_ones(tmp_path):
    ws = tmp_path / "ws"
    (ws / "build").mkdir(parents=True)
    (ws / "dist").mkdir()
    (ws / "docs" / "build").mkdir(parents=True)
    (ws / "build" / "b.py").write_text("b", encoding="utf-8")
    (ws / "dist" / "d.js").write_text("d", encoding="utf-8")
    (ws / "docs" / "build" / "n.py").write_text("n", encoding="utf-8")

    assert sorted(workspace_snapshot.snapshot_workspace(ws)) == ["docs/build/n.py"]


def test_snapshot_keeps_workspace_under_ignored_name_in_its_path(tmp_path):
    """A workspace path containing 'build' must not prune every file."""
    ws = tmp_path / "build" / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("a", encoding="utf-8")

    before = workspace_snapshot.snapshot_workspace(ws)
    (ws / "src" / "a.py").write_text("changed", encoding="utf-8")
    after = workspace_snapshot.snapshot_workspace(ws)

    assert workspace_snapshot.changed_workspace_paths(before, after) == ["src/a.py"]


def test_snapshot_detects_change_in_nested_build_dir(tmp_path):
    """Only root-level build/dist are noise; a nested one can hold real content."""
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "docs" / "build").mkdir(parents=True)
    (ws / "docs" / "build" / "x.py").write_text("orig", encoding="utf-8")

    before = workspace_snapshot.snapshot_workspace(ws)
    (ws / "docs" / "build" / "x.py").write_text("out of scope", encoding="utf-8")
    after = workspace_snapshot.snapshot_workspace(ws)

    changed = workspace_snapshot.changed_workspace_paths(before, after)
    assert workspace_snapshot.paths_outside_directory(changed, "src") == [
        "docs/build/x.py"
    ]


def test_snapshot_reports_a_deleted_file(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("a", encoding="utf-8")

    before = workspace_snapshot.snapshot_workspace(ws)
    (ws / "src" / "a.py").unlink()
    after = workspace_snapshot.snapshot_workspace(ws)

    assert workspace_snapshot.changed_workspace_paths(before, after) == ["src/a.py"]
