#!/usr/bin/env python3
"""Unit tests for the workspace snapshot helpers shared by the wrappers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

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


def _make_junction(link: Path, target: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows directory junctions require win32")
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"mklink /J failed: {proc.stderr or proc.stdout}")


def test_snapshot_reports_a_deleted_file(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("a", encoding="utf-8")

    before = workspace_snapshot.snapshot_workspace(ws)
    (ws / "src" / "a.py").unlink()
    after = workspace_snapshot.snapshot_workspace(ws)

    assert workspace_snapshot.changed_workspace_paths(before, after) == ["src/a.py"]


def test_snapshot_records_junction_as_link_marker_without_descending(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "inside.txt").write_text("inside", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret", encoding="utf-8")

    junc = ws / "ext"
    _make_junction(junc, outside)

    snap = workspace_snapshot.snapshot_workspace(ws)
    assert "inside.txt" in snap
    assert "ext" in snap
    assert snap["ext"].startswith("link:")
    assert not any(key.startswith("ext/") for key in snap)


def test_is_snapshot_ignored_rejects_an_empty_relative_path():
    """A path with no parts is the root itself, which is never generated noise."""
    assert workspace_snapshot.is_snapshot_ignored(Path()) is False


def test_is_reparse_point_detects_a_symlink(tmp_path, monkeypatch):
    entry = tmp_path / "entry.txt"
    entry.write_text("x", encoding="utf-8")

    monkeypatch.setattr(Path, "is_symlink", lambda self: self == entry)

    assert workspace_snapshot.is_reparse_point(entry) is True


def test_read_reparse_target_falls_back_when_unreadable(tmp_path, monkeypatch):
    entry = tmp_path / "entry.txt"
    entry.write_text("x", encoding="utf-8")

    def _raise_oserror(*args, **kwargs):
        raise OSError("unreadable link")

    monkeypatch.setattr(workspace_snapshot.os, "readlink", _raise_oserror)

    assert workspace_snapshot.read_reparse_target(entry) == "<reparse>"


def test_snapshot_skips_an_ignored_file(tmp_path):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "sub" / "node_modules").write_text("not a dir", encoding="utf-8")
    (ws / "sub" / "a.txt").write_text("a", encoding="utf-8")

    snap = workspace_snapshot.snapshot_workspace(ws)

    assert not any("node_modules" in key for key in snap)
    assert "sub/a.txt" in snap


def test_snapshot_marks_a_file_reparse_point(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "sub" / "link.txt").write_text("x", encoding="utf-8")

    real_is_reparse_point = workspace_snapshot.is_reparse_point

    def _is_reparse_point(path):
        if path.name == "link.txt":
            return True
        return real_is_reparse_point(path)

    monkeypatch.setattr(workspace_snapshot, "is_reparse_point", _is_reparse_point)

    snap = workspace_snapshot.snapshot_workspace(ws)

    assert snap["sub/link.txt"].startswith("link:")


def test_snapshot_skips_a_file_that_cannot_be_read(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "sub" / "a.txt").write_text("a", encoding="utf-8")
    (ws / "sub" / "b.txt").write_text("b", encoding="utf-8")

    real_read_bytes = Path.read_bytes

    def _read_bytes(self):
        if self.name == "a.txt":
            raise OSError("unreadable")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _read_bytes)

    snap = workspace_snapshot.snapshot_workspace(ws)

    assert "sub/a.txt" not in snap
    assert "sub/b.txt" in snap


def test_paths_outside_directory_normalises_dotdot():
    """`allowed/../outside.py` starts with the prefix but is outside the fence."""
    paths = ["allowed/../../outside.py", "allowed/ok.py"]
    assert workspace_snapshot.paths_outside_directory(paths, "allowed") == [
        "allowed/../../outside.py"
    ]


def test_paths_outside_directory_accepts_nested_paths():
    assert (
        workspace_snapshot.paths_outside_directory(["allowed/sub/a.py"], "allowed")
        == []
    )


def test_paths_outside_directory_normalised_inside_is_not_outside():
    """A path that normalises back inside the fence is inside it."""
    assert (
        workspace_snapshot.paths_outside_directory(["allowed/sub/../ok.py"], "allowed")
        == []
    )


def test_paths_outside_directory_returns_each_path_once():
    """A duplicate report must not repeat the same path."""
    paths = ["outside.py", "outside.py"]
    assert workspace_snapshot.paths_outside_directory(paths, "allowed") == [
        "outside.py"
    ]


def test_paths_outside_directory_keeps_the_directory_itself_inside():
    assert workspace_snapshot.paths_outside_directory(["allowed"], "allowed") == []
