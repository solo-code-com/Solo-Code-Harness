#!/usr/bin/env python3
"""Bypass-claim gate - a documented escape-hatch variable must be read somewhere.

A doc line can advertise an escape hatch that does not exist. Twice this repo
did exactly that: ``gate-guard.js`` printed ``GATE_GUARD_BYPASS=1`` and
``config-protection.js`` printed ``CONFIG_PROTECT_BYPASS=1`` while no code read
either variable, and the hooks README documented ``ECC_GOVERNANCE_CAPTURE``
while the code read ``SOLOCODE_GOVERNANCE``. Nothing stopped the next one, so
this gate closes it: any SCREAMING_SNAKE name ending in ``_BYPASS``,
``_OVERRIDE``, ``_SKIP``, ``_DISABLE`` or ``_IGNORE`` that a tracked markdown
file presents as an escape hatch -- inside backticks or immediately before an
equals sign -- must be read by some tracked code file, or it is a finding.

A "read" is a real access in code, not a mention in prose: ``process.env.NAME``
in JavaScript, ``os.environ["NAME"]`` / ``os.environ.get("NAME")`` /
``os.getenv("NAME")`` in Python, ``$env:NAME`` in PowerShell, or ``$NAME`` /
``${NAME}`` in a shell script. A candidate line that carries a removal or
negation marker is correcting the record, not advertising a hatch, so it is
skipped. The markers are the spec rule 4 list -- ``removed``, ``deleted``,
``no longer``, ``xoa``, ``da xoa``, ``khong con`` -- plus the negation phrases
``not yet`` / ``chua duoc`` / ``chưa được`` for the "seam chua duoc enforce"
shape, so a line describing the defect never fires as if it made the claim.

Scope is git-tracked files only: candidates are collected from tracked markdown
files and reads are looked for in tracked code files, so untracked scratch never
counts. A root where git cannot enumerate is an error (exit 2), never a silent
clean, because this gate cannot verify what it cannot enumerate.

Usage:
    python tools/check_bypass_claims.py [root]   # root defaults to the repo root
"""

from __future__ import annotations

import re
import subprocess  # noqa: S404 - runs git ls-files only, fixed argv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_BYPASS_TAIL = r"(?:BYPASS|OVERRIDE|SKIP|DISABLE|IGNORE)"
# A SCREAMING_SNAKE name ending in one of the bypass tails.
_NAME = rf"[A-Z][A-Z0-9_]*_{_BYPASS_TAIL}"
_TOKEN_IN_BACKTICKS = re.compile(rf"`({_NAME})`")
_TOKEN_BEFORE_EQUALS = re.compile(rf"({_NAME})=")

_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_CODE_SUFFIXES = {
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".py",
    ".pyw",
    ".ps1",
    ".psm1",
    ".sh",
    ".bash",
    ".zsh",
    ".ksh",
    ".fish",
}
_SHELL_SUFFIXES = {".sh", ".bash", ".zsh", ".ksh", ".fish"}
_SHELL_NAMES = {"makefile", "gnumakefile"}

# A candidate line carrying one of these is correcting the record, not
# advertising a hatch. The first six are the spec rule 4 removal markers
# (English and diacritic-less Vietnamese); the last three are the negation
# phrases for the "not yet enforced" shape.
_NEGATION_MARKERS = (
    "removed",
    "deleted",
    "no longer",
    "xoa",
    "da xoa",
    "khong con",
    "not yet",
    "chua duoc",
    "chưa được",
)

_QUOTE = "['\"]"  # a single or double quote, for the read-position regexes


def _is_markdown(rel: str) -> bool:
    """Whether a tracked relative path is a markdown file (a candidate source)."""
    return Path(rel).suffix.lower() in _MARKDOWN_SUFFIXES


def _is_code_file(rel: str) -> bool:
    """Whether a tracked relative path can contain a read (a code/script file)."""
    path = Path(rel)
    return path.suffix.lower() in _CODE_SUFFIXES or path.name.lower() in _SHELL_NAMES


def _is_shell_file(rel: str) -> bool:
    """Whether a tracked path is a shell script (``$NAME`` reads only count there)."""
    path = Path(rel)
    return path.suffix.lower() in _SHELL_SUFFIXES or path.name.lower() in _SHELL_NAMES


def _tracked(root: Path) -> list[str] | None:
    """git-tracked paths relative to root, or None when git cannot enumerate."""
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
    return proc.stdout.splitlines()


def _negated(line: str) -> bool:
    """Whether a line carries a removal/negation marker and must be skipped."""
    low = line.lower()
    return any(marker in low for marker in _NEGATION_MARKERS)


def _candidates_in_file(path: Path, root: Path) -> list[tuple[str, int, str]]:
    """(relpath, lineno, name) for every bypass token documented in one file."""
    rel = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    found: list[tuple[str, int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _negated(line):
            continue
        for match in _TOKEN_IN_BACKTICKS.finditer(line):
            found.append((rel, lineno, match.group(1)))
        for match in _TOKEN_BEFORE_EQUALS.finditer(line):
            found.append((rel, lineno, match.group(1)))
    return found


def _read_regexes(name: str) -> list[re.Pattern[str]]:
    """Regexes for the JavaScript/Python/PowerShell read positions of ``name``."""
    return [
        re.compile(rf"process\.env\.{name}\b"),
        re.compile(rf"process\.env\[{_QUOTE}{name}{_QUOTE}\]"),
        re.compile(rf"os\.environ\[{_QUOTE}{name}{_QUOTE}\]"),
        re.compile(rf"os\.environ\.get\(\s*{_QUOTE}{name}{_QUOTE}"),
        re.compile(rf"os\.getenv\(\s*{_QUOTE}{name}{_QUOTE}"),
        re.compile(rf"\$env:{name}\b"),
    ]


def _shell_read_regexes(name: str) -> list[re.Pattern[str]]:
    """Regexes for the shell ``$NAME`` / ``${NAME}`` read positions."""
    return [
        re.compile(rf"\$\{{{name}\}}"),
        re.compile(rf"\${name}\b"),
    ]


def _is_read(name: str, code_files: list[Path], root: Path) -> bool:
    """Whether any tracked code file reads ``name``."""
    patterns = _read_regexes(name)
    shell_patterns = _shell_read_regexes(name)
    for rel in code_files:
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(pattern.search(text) for pattern in patterns):
            return True
        if _is_shell_file(rel) and any(
            pattern.search(text) for pattern in shell_patterns
        ):
            return True
    return False


def find_findings(root: Path) -> list[str] | None:
    """Scan tracked markdown for unread bypass tokens; None when git is down."""
    tracked = _tracked(root)
    if tracked is None:
        return None
    markdown = [root / rel for rel in tracked if _is_markdown(rel)]
    code = [root / rel for rel in tracked if _is_code_file(rel)]
    occurrences: list[tuple[str, int, str]] = []
    for path in sorted(markdown):
        occurrences.extend(_candidates_in_file(path, root))
    read_cache: dict[str, bool] = {}
    findings: list[str] = []
    for rel, lineno, name in sorted(set(occurrences)):
        if name not in read_cache:
            read_cache[name] = _is_read(name, code, root)
        if not read_cache[name]:
            findings.append(
                f"{rel}:{lineno}: {name} is documented as an escape hatch "
                "but nothing reads it."
            )
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
