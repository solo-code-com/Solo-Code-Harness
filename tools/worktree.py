#!/usr/bin/env python3
"""Git worktree support for the delegation wrappers.

Why: two writing workers in one checkout see each other half-finished edits, and
both wrappers snapshot the whole tree, so each run reports the other as a scope
violation. A worktree per worker gives each run its own HEAD and its own index,
and the orchestrator merges the branch afterwards.

Worktrees live under .solocode/worktrees/ so the workspace snapshot ignores them:
a concurrent worker cannot look like this run violation.
"""

from __future__ import annotations

import argparse
import contextlib  # noqa: F401
import datetime
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

WORKTREE_ROOT = Path(".solocode") / "worktrees"


class WorktreeError(RuntimeError):
    """A worktree could not be created or removed."""


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    base: str


def slugify(text: str, limit: int = 40) -> str:
    """A branch-safe slug for a task, at most roughly `limit` characters."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    words = re.split(r"[^a-z0-9]+", ascii_text)
    slug = "-".join(word for word in words if word)
    if len(slug) > limit:
        slug = "-".join(word for word in slug.split("-"))
        while len(slug) > limit and "-" in slug:
            slug = slug.rsplit("-", 1)[0]
        if len(slug) > limit:
            slug = slug[:limit].strip("-")
    return slug or "task"


def make_stamp(now: datetime.datetime | None = None) -> str:
    """UTC timestamp for a worktree name, second precision."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    return moment.strftime("%Y%m%d-%H%M%S")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def dirty_paths(root: Path) -> list[str]:
    """Paths with uncommitted changes, so a caller can decide whether to proceed."""
    proc = _git(root, "status", "--porcelain")
    if proc.returncode != 0:
        return []
    return [line[3:].strip() for line in proc.stdout.splitlines() if line.strip()]


def create(
    root: Path, slug: str, *, base: str = "HEAD", stamp: str | None = None
) -> Worktree:
    """Add a worktree at .solocode/worktrees/<slug>-<stamp> on a new branch."""
    stamp = stamp or make_stamp()
    path = (root / WORKTREE_ROOT / f"{slug}-{stamp}").resolve()
    branch = f"jev/{slug}-{stamp}"
    if path.exists():
        raise WorktreeError(f"worktree already exists: {path}")
    proc = _git(root, "worktree", "add", "-b", branch, str(path), base)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise WorktreeError(f"git worktree add failed: {detail}")
    return Worktree(path=path, branch=branch, base=base)


def summary(path: Path) -> str:
    """The porcelain status inside a worktree, for the orchestrator to read."""
    proc = _git(path, "status", "--porcelain")
    return proc.stdout.strip() if proc.returncode == 0 else ""


def remove(root: Path, path: Path, *, force: bool = False) -> None:
    """Remove a worktree. Never called automatically: deleting is the caller call."""
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    proc = _git(root, *args, str(path))
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise WorktreeError(f"git worktree remove failed: {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage delegation worktrees")
    sub = parser.add_subparsers(dest="command", required=True)

    create_parser = sub.add_parser("create", help="Add a worktree for a task")
    create_parser.add_argument("slug", help="Short task name")
    create_parser.add_argument("--base", default="HEAD")
    create_parser.add_argument("--root", default=".")

    list_parser = sub.add_parser("list", help="List worktrees")
    list_parser.add_argument("--root", default=".")

    remove_parser = sub.add_parser("remove", help="Remove a worktree")
    remove_parser.add_argument("path")
    remove_parser.add_argument("--root", default=".")
    remove_parser.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "create":
        try:
            worktree = create(root, slugify(args.slug), base=args.base)
        except WorktreeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"path={worktree.path}")
        print(f"branch={worktree.branch}")
        return 0

    if args.command == "list":
        proc = _git(root, "worktree", "list")
        if proc.returncode != 0:
            print((proc.stderr or proc.stdout).strip(), file=sys.stderr)
            return 1
        print(proc.stdout.rstrip())
        return 0

    try:
        remove(root, Path(args.path), force=args.force)
    except WorktreeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
