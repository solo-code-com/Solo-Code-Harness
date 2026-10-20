#!/usr/bin/env python3
"""Enforce SPEC section 4's stdlib-only rule for the harness's Python scripts.

SPEC section 4 requires every Python script under ``tools/`` and
``.github/scripts/`` to be stdlib-only -- zero external dependencies. This
gate enforces that rule: it scans those two trees with ``ast`` (never regex,
so a comment or a string cannot fool it) and reports every import whose
top-level module is not allowed. Nothing enforced the rule before, which is
how ``tools/kilo_server_manager.py`` shipped with ``import requests`` and a
pip-install instruction that only a human noticed.

An import wrapped in ``try/except ImportError`` is still a dependency: the
``except`` branch is a fallback, but the module named in the ``try`` is still
expected to be installed for the primary path. So imports nested inside
try/except (or a function, or an ``if``) are scanned exactly like top-level
ones -- ``ast.walk`` visits every node regardless of depth.

Allowed top-level module names:
  * anything in ``sys.stdlib_module_names``;
  * the local package name ``tools``;
  * any module whose ``.py`` stem lives in a scanned directory, or in the
    harness's own hook directory ``.claude/hooks/`` (so ``import garden``,
    ``import opencode_engine`` and ``import guard`` are all local harness
    modules, not external dependencies);
  * ``pytest``, but only for files whose name starts with ``test_``.

Everything else is a finding, printed as ``path:line: module``, and the exit
code is 1. Files are read as ``utf-8-sig`` so a leading BOM is stripped before
parsing; a file the gate still cannot parse is reported as
``path:line: unparseable (<error>)``, never skipped. A clean tree exits 0. A
missing scanned directory is not an error.

Usage:
    python tools/check_stdlib_only.py [root]   # root defaults to the repo root
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _scanned_dirs(root: Path) -> list[Path]:
    """The two directories whose *.py files are checked for disallowed imports."""
    return [root / "tools", root / ".github" / "scripts"]


def _local_stem_dirs(root: Path) -> list[Path]:
    """Directories whose *.py stems count as local harness modules.

    The harness's Python hooks live under ``.claude/hooks/`` and are
    legitimately imported by ``tools/test_guard.py`` (``import guard``): a
    local harness module, not an external dependency. Including them here
    keeps the gate from reporting a false positive on that import.
    """
    return [*_scanned_dirs(root), root / ".claude" / "hooks"]


def _local_stems(root: Path) -> set[str]:
    """Return the module stems that resolve to a local harness module."""
    stems: set[str] = set()
    for directory in _local_stem_dirs(root):
        if directory.is_dir():
            stems.update(path.stem for path in directory.rglob("*.py"))
    return stems


def _allowed_names(root: Path) -> set[str]:
    """Build the set of allowed top-level module names for this root."""
    allowed = set(sys.stdlib_module_names)
    allowed.add("tools")
    allowed.update(_local_stems(root))
    return allowed


def _is_allowed(top: str, allowed: set[str], is_test: bool) -> bool:
    """Whether a top-level module name is allowed for the file being scanned."""
    if top in allowed:
        return True
    return top == "pytest" and is_test


def _findings_in_file(path: Path, root: Path, allowed: set[str]) -> list[str]:
    """Return one ``relpath:line: module`` string per disallowed import.

    The file is read as ``utf-8-sig`` so a leading BOM is stripped before
    parsing (a UTF-8 BOM would otherwise become U+FEFF and fail the parse).
    A file the gate still cannot parse is reported as
    ``path:line: unparseable (<error>)``, never skipped -- a file the gate
    cannot read must not count as clean.
    """
    rel = path.relative_to(root).as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except SyntaxError as exc:
        return [f"{rel}:{exc.lineno or 1}: unparseable ({exc})"]
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel}:1: unparseable ({exc})"]
    is_test = path.name.startswith("test_")
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if not _is_allowed(top, allowed, is_test):
                    findings.append(f"{rel}:{node.lineno}: {top}")
        elif isinstance(node, ast.ImportFrom) and (
            node.level == 0
            and node.module
            and not _is_allowed((top := node.module.split(".", 1)[0]), allowed, is_test)
        ):
            findings.append(f"{rel}:{node.lineno}: {top}")
    return findings


def find_findings(root: Path) -> list[str]:
    """Scan the root's tools/ and .github/scripts/ trees for disallowed imports.

    Returns one ``path:line: module`` string per finding, with the path made
    relative to ``root``. A missing scanned directory is not an error.
    """
    allowed = _allowed_names(root)
    findings: list[str] = []
    for directory in _scanned_dirs(root):
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            findings.extend(_findings_in_file(path, root, allowed))
    return findings


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: print findings and exit 1 when any are found."""
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]) if args else ROOT
    findings = find_findings(root)
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
