#!/usr/bin/env python3
"""Fingerprints harness state across three classes: protected state in .solocode,
the gate files that enforce the fence itself, and writes into generated trees
the workspace snapshot skips.

Watching these locations prevents a delegated worker from forging receipts,
moving fence rules, or hiding changes in ignored build directories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

PROTECTED_PATHS = (
    ".solocode/reviews",
    ".solocode/task-receipts",
    ".solocode/executor-mode",
    ".solocode/executor-config.json",
    ".solocode/circuit-breaker-state.json",
)

# The session registry, the usage logs, the worktrees and the context checkpoint
# are NOT in this list: the wrappers and the OpenCode plugin write them during
# a normal run, so watching them would report every delegation as tampering.

GATE_PATHS = (
    ".kilo/hooks/pre-tool-use/gate-guard.js",
    ".kilo/hooks/pre-tool-use/executor-mode.js",
    "tools/control_paths.py",
    "tools/guard_hook.py",
    "tools/guard_rules.py",
    "tools/protected_state.py",
)

IGNORED_TREES = ("node_modules", "build", "dist", ".venv", "venv", "target")

#: Credential locations in the operator home directory. Fingerprinted because
#: the repository snapshot cannot see them, and a worker that edits one is doing
#: something no delegation should do without a person saying so.
HOME_PROTECTED_PATHS = (
    ".ssh",
    ".aws",
    ".netrc",
    ".gitconfig",
    ".config/gh",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(path: Path) -> str:
    """A typed fingerprint: a symlink is never read through.

    Hashing the content behind a symlink would report the target file as this
    path, so a worker could point a receipt at any file it liked and the
    fingerprint would look unchanged.
    """
    try:
        if path.is_symlink():
            return "link:" + os.readlink(path)
        if path.is_file():
            return f"file:{_sha256(path)}:{stat.S_IMODE(path.stat().st_mode):o}"
        return "other"
    except OSError:
        return "unreadable"


def fingerprint(root: Path) -> dict[str, str]:
    """Content hashes for the protected entries, with a marker for each absent one.

    A declared path that does not exist still gets an entry: creating it later
    must register as a change, which is how a fresh executor-mode file or a new
    receipt is caught.
    """
    state: dict[str, str] = {}
    for relative in PROTECTED_PATHS:
        target = root / relative
        try:
            if target.is_symlink():
                state[relative] = _entry(target)
            elif target.is_dir():
                files = sorted(
                    p for p in target.rglob("*") if p.is_file() or p.is_symlink()
                )
                if not files:
                    state[relative] = "<empty>"
                for path in files:
                    key = path.relative_to(root).as_posix()
                    state[key] = _entry(path)
            elif target.is_file():
                state[relative] = _entry(target)
            else:
                state[relative] = "<absent>"
        except OSError:
            state[relative] = "<unreadable>"
    return state


def new_violations(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Paths whose hash differs between two fingerprints, sorted."""
    keys = set(before) | set(after)
    return sorted(key for key in keys if before.get(key) != after.get(key))


def gate_fingerprint(root: Path) -> dict[str, str]:
    """Fingerprints for the files that enforce the fence itself."""
    state: dict[str, str] = {}
    for relative in GATE_PATHS:
        path = root / relative
        state[relative] = (
            _entry(path) if path.exists() or path.is_symlink() else "<absent>"
        )
    return state


def gate_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Gate files that differ between two fingerprints."""
    return new_violations(before, after)


def home_fingerprint(home: Path) -> dict[str, str]:
    """Typed fingerprint of the home credential paths.

    The hashes are computed in memory and never written anywhere; only the list
    of changed paths leaves this process. A missing path still gets an entry, so
    creating one registers as a change.
    """
    state: dict[str, str] = {}
    for relative in HOME_PROTECTED_PATHS:
        target = home / relative
        try:
            if target.is_symlink():
                state[relative] = "link:" + os.readlink(target)
            elif target.is_dir():
                files = sorted(
                    p for p in target.rglob("*") if p.is_file() and not p.is_symlink()
                )
                if not files:
                    state[relative] = "<empty>"
                for path in files:
                    state[f"{relative}/{path.relative_to(target).as_posix()}"] = (
                        _sha256(path)
                    )
            elif target.is_file():
                state[relative] = _sha256(target)
            else:
                state[relative] = "<absent>"
        except OSError:
            state[relative] = "<unreadable>"
    return state


def home_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Credential paths that differ between two home fingerprints."""
    return new_violations(before, after)


def recent_ignored_writes(root: Path, since: float, limit: int = 200_000) -> list[str]:
    """Files under a generated tree modified at or after `since` (epoch seconds).

    The snapshot skips these directories because they churn, which also makes
    them a place to hide a write. This walks them once, after the run, and names
    what changed. The walk is bounded: it stops after `limit` entries or 20 hits,
    and says so, so a large node_modules cannot turn a check into a hang.
    """
    hits: list[str] = []
    seen = 0
    for tree in IGNORED_TREES:
        base = root / tree
        if base.is_symlink() or not base.is_dir():
            continue
        for path in base.rglob("*"):
            seen += 1
            if seen > limit:
                hits.append(f"<walk truncated after {limit} entries>")
                return hits
            if path.is_symlink() or not path.is_file():
                continue
            if any(part in ("__pycache__", ".pytest_cache") for part in path.parts):
                continue
            try:
                if path.stat().st_mtime >= since:
                    hits.append(path.relative_to(root).as_posix())
            except OSError:
                continue
            if len(hits) >= 20:
                hits.append("<more>")
                return hits
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fingerprint the protected harness state"
    )
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    print(json.dumps(fingerprint(Path(args.root).resolve()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
