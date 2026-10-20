#!/usr/bin/env python3
"""Text Hygiene Gate - a source file a commit hook would rewrite is churn.

A commit hook such as end-of-file-fixer, trailing-whitespace, or a CRLF
normalizer rewrites a source file to its canonical bytes at commit time. A
file that disagrees with that canonical form is churn: every commit rewrites
it, the working tree stays permanently dirty, and the noise hides real diffs.
This gate catches the three ways a tracked text file can disagree with the
hooks before the commit does:

  no-final-newline - the file does not end with a newline
  trailing-space   - a line ends with a space or tab
  crlf             - the file contains CRLF (\\r\\n) line endings

A file ending with two or more newlines is not checked, because it has never
caused churn.

Scope is git-tracked files only, under <root>/.kilo/ (excluding .kilo/state,
which is runtime state written by hooks, not source) and <root>/docs/, plus
root-level .md files and Makefile. ``git ls-files`` is the source of truth so
untracked runtime state never counts; a root where git is unavailable is
reported as an error and exits 2, because this gate cannot verify what it
cannot enumerate. Binary files (a NUL byte in the first 1024 bytes) are
skipped.

Usage:
    python tools/check_text_hygiene.py [root]   # root defaults to the repo root
"""

from __future__ import annotations

import subprocess  # noqa: S404 - runs git ls-files only, fixed argv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _in_scope(rel: Path) -> bool:
    """Whether a git-tracked relative path belongs to the checked scope."""
    parts = rel.parts
    if not parts:
        return False
    if parts[0] == ".kilo":
        return not (len(parts) > 1 and parts[1] == "state")
    if parts[0] == "docs":
        return True
    if len(parts) == 1:
        return rel.suffix == ".md" or rel.name == "Makefile"
    return False


def _tracked_files(root: Path) -> list[Path] | None:
    """Return the in-scope git-tracked files, or None when git is unavailable."""
    proc = subprocess.run(
        ["git", "ls-files"],  # noqa: S603,S607 - fixed argv, no shell
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        return None
    return [Path(line) for line in proc.stdout.splitlines() if _in_scope(Path(line))]


def _is_binary(data: bytes) -> bool:
    """A file whose first 1024 bytes contain a NUL byte is treated as binary."""
    return b"\x00" in data[:1024]


def _findings_in_file(path: Path, root: Path) -> list[str]:
    """Return one ``path:line: rule`` string per text-hygiene finding."""
    rel = path.relative_to(root).as_posix()
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"{rel}:1: unreadable ({exc})"]
    if _is_binary(data):
        return []
    findings: list[str] = []
    if not data.endswith(b"\n"):
        findings.append(f"{rel}:1: no-final-newline")
    if b"\r\n" in data:
        findings.append(f"{rel}:1: crlf")
    for lineno, line in enumerate(data.split(b"\n"), 1):
        if line.endswith(b" ") or line.endswith(b"\t"):
            findings.append(f"{rel}:{lineno}: trailing-space")
            break
    return findings


def find_findings(root: Path) -> list[str] | None:
    """Scan the in-scope tracked files and return every text-hygiene finding.

    Returns None when git is unavailable, so the caller can tell "cannot run"
    apart from "ran clean".
    """
    tracked = _tracked_files(root)
    if tracked is None:
        return None
    findings: list[str] = []
    for rel in sorted(tracked):
        findings.extend(_findings_in_file(root / rel, root))
    return findings


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: print findings and exit 1 when any are found."""
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]) if args else ROOT
    findings = find_findings(root)
    if findings is None:
        print(
            "error: git ls-files unavailable; cannot enumerate tracked files",
            file=sys.stderr,
        )
        return 2
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
