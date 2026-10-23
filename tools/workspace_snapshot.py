#!/usr/bin/env python3
"""Workspace snapshots shared by the delegation wrappers.

One implementation, so Antigravity's write fence and the implementer wrappers'
post-run scope report compare the same files and ignore the same generated noise.
"""

from __future__ import annotations

import hashlib
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


def snapshot_workspace(root: Path) -> dict[str, str]:
    """Hash workspace files so post-run scope validation sees untracked changes too."""
    snapshot: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if is_snapshot_ignored(relative):
            continue
        try:
            snapshot[relative.as_posix()] = hashlib.sha256(
                path.read_bytes()
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
    """Return changed paths not contained by a relative allowed directory."""
    if allowed_dir == ".":
        return []
    prefix = allowed_dir.rstrip("/") + "/"
    return [
        path for path in paths if path != allowed_dir and not path.startswith(prefix)
    ]
