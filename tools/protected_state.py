#!/usr/bin/env python3
"""The workspace snapshot ignores .solocode as generated noise, and git ignores it
too, so every control-path check that relies on git status is blind there. That
would not matter if .solocode held only logs, but it also holds the review
receipts that gate a commit, the task receipts, the executor-mode toggle and the
circuit-breaker state. A delegated worker that writes those can forge the gate
that judges it. This module fingerprints exactly those entries, and nothing else,
so a run that touches them is reported instead of trusted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            if target.is_dir():
                files = sorted(p for p in target.rglob("*") if p.is_file())
                if not files:
                    state[relative] = "<empty>"
                for path in files:
                    key = path.relative_to(root).as_posix()
                    state[key] = _sha256(path)
            elif target.is_file():
                state[relative] = _sha256(target)
            else:
                state[relative] = "<absent>"
        except OSError:
            state[relative] = "<unreadable>"
    return state


def new_violations(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Paths whose hash differs between two fingerprints, sorted."""
    keys = set(before) | set(after)
    return sorted(key for key in keys if before.get(key) != after.get(key))


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
