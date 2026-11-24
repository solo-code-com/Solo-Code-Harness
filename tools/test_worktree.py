#!/usr/bin/env python3
"""Tests for tools/worktree.py — git worktree support for delegation."""

from __future__ import annotations

import datetime
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import worktree  # noqa: E402
from worktree import (  # noqa: E402
    Worktree,
    WorktreeError,
    create,
    dirty_paths,
    make_stamp,
    remove,
    slugify,
    summary,
)

git_required = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not available"
)


def _git_cmd(cwd: Path | None, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = ["git"]
    if cwd is not None:
        cmd.extend(["-C", str(cwd)])
    cmd.extend(args)
    return subprocess.run(  # noqa: S603, S607
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def init_repo(path: Path) -> Path:
    """Initialise a temporary git repository with a commit."""
    path.mkdir(parents=True, exist_ok=True)
    _git_cmd(None, "init", str(path))
    _git_cmd(path, "config", "user.email", "test@example.com")
    _git_cmd(path, "config", "user.name", "Test User")
    _git_cmd(path, "config", "commit.gpgsign", "false")
    test_file = path / "README.md"
    test_file.write_text("initial content", encoding="utf-8")
    _git_cmd(path, "add", "README.md")
    _git_cmd(path, "commit", "-m", "initial commit")
    return path


def test_slugify_turns_fix_the_login_bug_into_slug():
    assert slugify("Fix the login bug!") == "fix-the-login-bug"


def test_slugify_returns_task_for_empty_or_punctuation():
    assert slugify("") == "task"
    assert slugify("!@#$%^&*()_+") == "task"
    assert slugify("   ") == "task"


def test_slugify_never_longer_than_limit_for_long_input():
    long_input = " ".join(["word"] * 20)
    result = slugify(long_input, limit=40)
    assert len(result) <= 40
    assert len(slugify(long_input, limit=15)) <= 15


def test_make_stamp_with_fixed_datetime():
    fixed = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
    assert make_stamp(fixed) == "20260102-030405"


@git_required
def test_create_builds_worktree_in_temp_repo(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = create(repo, "my-task")
    assert isinstance(wt, Worktree)
    assert wt.path.exists()
    assert wt.branch.startswith("jev/")
    assert wt.path == (repo / worktree.WORKTREE_ROOT / wt.path.name).resolve()
    proc = _git_cmd(repo, "worktree", "list")
    normalized_output = proc.stdout.replace("\\", "/").lower()
    normalized_path = str(wt.path).replace("\\", "/").lower()
    assert (str(wt.path) in proc.stdout) or (normalized_path in normalized_output)


@git_required
def test_create_twice_same_stamp_raises(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    create(repo, "dup-task", stamp="fixed-stamp")
    with pytest.raises(WorktreeError):
        create(repo, "dup-task", stamp="fixed-stamp")


@git_required
def test_dirty_paths(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    assert dirty_paths(repo) == []
    new_file = repo / "dirty.txt"
    new_file.write_text("dirty content", encoding="utf-8")
    assert "dirty.txt" in dirty_paths(repo)


@git_required
def test_summary_reports_changed_file(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = create(repo, "summary-task")
    change = wt.path / "change.txt"
    change.write_text("some change", encoding="utf-8")
    status = summary(wt.path)
    assert "change.txt" in status


@git_required
def test_remove_deletes_directory(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")
    wt = create(repo, "remove-task")
    assert wt.path.exists()
    remove(repo, wt.path)
    assert not wt.path.exists()
