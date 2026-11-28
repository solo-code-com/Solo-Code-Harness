#!/usr/bin/env python3
"""Workspace snapshots shared by the delegation wrappers.

One implementation, so Antigravity's write fence and the implementer wrappers'
post-run scope report compare the same files and ignore the same generated noise.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
from pathlib import Path

SNAPSHOT_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".solocode",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".ruff_cache",
        ".pytest_cache",
    }
)
# Pruned at the workspace root only: a nested ``build``/``dist`` directory can
# hold authored files, and skipping it anywhere would hide an out-of-scope write
# from the post-run scope check.
SNAPSHOT_ROOT_ONLY_IGNORED_DIRS = frozenset({"dist", "build"})
SNAPSHOT_IGNORED_DIR_PREFIXES = (".pytest_temp",)


def is_snapshot_ignored(relative: Path) -> bool:
    """Return whether a workspace-relative path is generated noise.

    Matches the path *relative to the snapshot root*: a workspace that itself
    lives under a directory named ``build`` must still be snapshotted.
    """
    parts = relative.parts
    if not parts:
        return False
    if parts[0] in SNAPSHOT_ROOT_ONLY_IGNORED_DIRS:
        return True
    return any(
        part in SNAPSHOT_IGNORED_DIRS or part.startswith(SNAPSHOT_IGNORED_DIR_PREFIXES)
        for part in parts
    )


def is_reparse_point(path: Path) -> bool:
    """True for a symlink or a Windows directory junction.

    Both redirect a path elsewhere, and both must be recorded rather than walked:
    descending into one hashes or copies content from outside the root.
    """
    if path.is_symlink():
        return True
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def read_reparse_target(path: Path) -> str:
    """Return the target string for a reparse point, or <reparse> if unreadable."""
    try:
        return os.readlink(path)
    except OSError:
        return "<reparse>"


def snapshot_workspace(root: Path) -> dict[str, str]:
    """Hash workspace files so post-run scope validation sees untracked changes too."""
    snapshot: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirpath_path = Path(dirpath)
        pruned_dirnames: list[str] = []
        for dirname in dirnames:
            sub_dir = dirpath_path / dirname
            relative = sub_dir.relative_to(root)
            if is_snapshot_ignored(relative):
                continue
            if is_reparse_point(sub_dir):
                snapshot[relative.as_posix()] = f"link:{read_reparse_target(sub_dir)}"
                continue
            pruned_dirnames.append(dirname)
        dirnames[:] = pruned_dirnames

        for filename in filenames:
            file_path = dirpath_path / filename
            relative = file_path.relative_to(root)
            if is_snapshot_ignored(relative):
                continue
            if is_reparse_point(file_path):
                snapshot[relative.as_posix()] = f"link:{read_reparse_target(file_path)}"
                continue
            if not file_path.is_file():
                continue
            try:
                snapshot[relative.as_posix()] = hashlib.sha256(
                    file_path.read_bytes()
                ).hexdigest()
            except OSError:
                continue
    return snapshot


def changed_workspace_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Return created, modified, and deleted paths between two snapshots."""
    return sorted(
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    )


def paths_outside_directory(paths: list[str], allowed_dir: str) -> list[str]:
    """Return changed paths not contained by a relative allowed directory.

    Paths are normalised first: a literal ``allowed/../outside.py`` starts with the
    allowed prefix but points outside it, and a raw string check would pass it.
    The original spelling is returned so the report matches what is on disk.
    """
    if allowed_dir == ".":
        return []
    prefix = allowed_dir.rstrip("/") + "/"
    outside: list[str] = []
    seen: set[str] = set()
    for path in paths:
        normalised = posixpath.normpath(path)
        if normalised == allowed_dir or normalised.startswith(prefix):
            continue
        if path in seen:
            continue
        seen.add(path)
        outside.append(path)
    return outside
