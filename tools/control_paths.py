#!/usr/bin/env python3
"""
control_paths.py — detect a worker that changed the harness fencing it.

A delegation wrapper drives a worker that the harness's own gates never see. Left
alone, "fix the failing test" can become "relax the guard, then fix the test": the
worker edits the very files that constrain it and reports success. This module
names those files so a wrapper can report the run instead of trusting it.

Pure text on purpose. The caller owns `git status --porcelain` and the filesystem;
here only the classification lives, so every rule below is testable without a
repository.
"""

from __future__ import annotations

from pathlib import Path

#: Repo-relative paths a worker has no business writing. A directory entry ends
#: with "/" and matches as a prefix; every other entry matches exactly, so a
#: stale `.harness.lock.bak` is not read as the lock file itself.
CONTROL_PATHS: tuple[str, ...] = (
    "tools/",
    ".github/scripts/",
    ".kilo/hooks/",
    ".claude/hooks/",
    ".opencode/",
    "AGENTS.md",
    "CLAUDE.md",
    ".mcp.json",
    ".harness.lock",
    ".harness-tier",
)

#: Present together, these mean the root IS this harness repo -- where editing a
#: control path is maintenance, not tampering.
HARNESS_REPO_MARKERS: tuple[str, ...] = (
    ".harness.lock",
    ".kilo/hooks/hooks.json",
    "tools/opencode_delegate.py",
)

#: Porcelain spells a rename as `<old> -> <new>` in the path column.
_RENAME_SEPARATOR = " -> "


def is_harness_repo(root: Path) -> bool:
    """True when `root` holds every harness marker, False if any is missing."""
    return all((root / marker).exists() for marker in HARNESS_REPO_MARKERS)


def changed_paths(porcelain: str) -> list[str]:
    """Paths named by `git status --porcelain` output, in the order git printed them.

    A rename contributes BOTH names: moving a file out of a control path changes
    that path as surely as writing into it. Porcelain quotes a path holding a
    special character, and the quotes come off so a prefix test still sees
    `tools/` in front of it.
    """
    paths: list[str] = []
    for line in porcelain.splitlines():
        # Two status columns, one space, then the path.
        if len(line) < 4:
            continue
        entry = line[3:]
        if _RENAME_SEPARATOR in entry:
            old, new = entry.split(_RENAME_SEPARATOR, 1)
            paths.extend((_unquote(old), _unquote(new)))
        else:
            paths.append(_unquote(entry))
    return paths


def violations_in(paths: list[str]) -> list[str]:
    """Sorted, de-duplicated control paths among already-parsed paths.

    agy reports a changed-path list rather than porcelain text, so the rule
    lives here once instead of being re-implemented per wrapper.
    """
    return sorted({path for path in paths if _is_control_path(path)})


def violations(porcelain: str) -> list[str]:
    """Sorted, de-duplicated control paths the porcelain output touches."""
    return violations_in(changed_paths(porcelain))


def _unquote(path: str) -> str:
    """Drop the quotes porcelain wraps around a path with special characters."""
    if len(path) > 1 and path[0] == '"' and path[-1] == '"':
        return path[1:-1]
    return path


def _is_control_path(path: str) -> bool:
    for entry in CONTROL_PATHS:
        if entry.endswith("/"):
            if path.startswith(entry):
                return True
        elif path == entry:
            return True
    return False


def new_violations(before: str, after: str) -> list[str]:
    """Control paths this run dirtied: after minus before.

    Pre-existing dirt belongs to the operator, not the worker, so a wrapper that
    passed over a dirty tree must not fail the run it just observed.
    """
    return sorted(set(violations(after)) - set(violations(before)))
