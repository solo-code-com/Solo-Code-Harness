#!/usr/bin/env python3
"""Audit provider configuration for retired gateways and unsafe Codex settings.

The harness runs through several config surfaces (`.env`, `opencode.json`, a Claude settings
file, a Codex config). A stale surface can keep pointing at a gateway the harness retired, or
turn off the Codex sandbox, and nothing fails until an arm behaves oddly. This turns that
discovery into one read-only command:

    python tools/provider_audit.py                # default locations + current directory
    python tools/provider_audit.py PATH [PATH...]  # one file or one directory
    python tools/provider_audit.py --json          # machine-readable
    python tools/provider_audit.py --quiet         # exit code only

Default targets are `~/.claude/settings.json`, `~/.codex/config.toml` and
`~/.config/opencode/opencode.jsonc`; the current directory is always scanned with it. A
directory scan only looks at the names in `TARGET_FILES`, stops two levels below the given
directory and never follows a directory symlink, so it stays bounded.

Read-only by construction: it never writes, never loads a credential and never prints one. A
line that holds a secret is reported by name only -- the matched hostname, or the variable
name -- because an audit that echoes `.env` into a terminal or CI log has itself leaked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

#: Gateways the harness retired. A config that still points at one is misconfigured.
BANNED_HOSTS = frozenset(
    {
        "api.freemodel.dev",
        "cc.freemodel.dev",
        "api-cc.freemodel.dev",
        "cc-t2.freemodel.dev",
        "work.freemodel.dev",
        "api-t2-sg.freemodel.dev",
    }
)

#: Credentials for those gateways: any name here is a leftover provider reference.
BANNED_ENV_VARS = frozenset({"FREEMODEL_API_KEY"})

#: The only names a directory scan inspects, relative to the directory being scanned.
TARGET_FILES = (
    ".env",
    ".env.template",
    ".opencode/opencode.json",
    "opencode.json",
    ".claude/settings.json",
    "agent.yaml",
)

#: The given directory is level 0, so its grandchildren are the last directories walked.
MAX_DEPTH = 2
#: Fan-out cap: a scan of a huge tree must still finish, and six names per directory is cheap.
MAX_DIRECTORIES = 250

#: Longest first: `cc.freemodel.dev` is a substring of `api-cc.freemodel.dev`, and the
#: specific name is the useful one to report.
_BANNED_HOSTS_BY_LENGTH = tuple(sorted(BANNED_HOSTS, key=len, reverse=True))

_ENV_VAR_RULE = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in sorted(BANNED_ENV_VARS)) + r")\b"
)

#: kind, pattern, detail. The detail is a setting, never a credential.
_CODEX_RULES = (
    (
        "codex-danger-full-access",
        re.compile(r"""sandbox_mode\s*=\s*["']?danger-full-access"""),
        "sandbox_mode=danger-full-access",
    ),
    (
        "codex-approval-never",
        re.compile(r"""approval_policy\s*=\s*["']?never"""),
        "approval_policy=never",
    ),
)


def _default_files() -> list[Path]:
    home = Path.home()
    return [
        home / ".claude" / "settings.json",
        home / ".codex" / "config.toml",
        home / ".config" / "opencode" / "opencode.jsonc",
    ]


def _targets_in(directory: Path) -> list[Path]:
    """Every known config name within `MAX_DEPTH` levels of `directory`."""
    found: list[Path] = []
    queue: list[tuple[Path, int]] = [(directory, 0)]
    visited = 0
    while queue and visited < MAX_DIRECTORIES:
        current, depth = queue.pop(0)
        visited += 1
        for name in TARGET_FILES:
            candidate = current / name
            # A broken symlink or a file the system refuses to stat (WinError
            # 1920 inside .venv) must not abort the whole audit.
            try:
                if candidate.is_file():
                    found.append(candidate)
            except OSError:
                continue
        if depth >= MAX_DEPTH:
            continue
        try:
            with os.scandir(current) as entries:
                children = sorted(
                    Path(entry.path)
                    for entry in entries
                    if entry.is_dir(follow_symlinks=False)
                )
        except OSError:
            continue
        queue.extend((child, depth + 1) for child in children)
    return found


def targets(paths: list[str]) -> list[Path]:
    """The files to audit: the defaults plus the current directory, or the given paths."""
    if not paths:
        return [*_default_files(), *_targets_in(Path.cwd())]
    found: list[Path] = []
    for raw in paths:
        root = Path(raw).expanduser()
        found.extend(_targets_in(root) if root.is_dir() else [root])
    return found


def scan_line(line: str) -> list[tuple[str, str]]:
    """(kind, detail) pairs for one line.

    The line itself never leaves this function: only a hostname, a variable name or a
    setting reaches the caller, so a value that happens to sit on the same line stays put.
    """
    hits: list[tuple[str, str]] = []
    for host in _BANNED_HOSTS_BY_LENGTH:
        if host in line:
            hits.append(("banned-host", host))
            break
    variable = _ENV_VAR_RULE.search(line)
    if variable:
        hits.append(("banned-env-var", variable.group(1)))
    for kind, pattern, detail in _CODEX_RULES:
        if pattern.search(line):
            hits.append((kind, detail))
    return hits


def audit_file(path: Path) -> list[dict[str, object]] | None:
    """Violations in one file, or None when it cannot be read."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    violations: list[dict[str, object]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        # A comment is documentation, not wiring: a template that names a banned
        # host in order to say "do not use this" must not fail the gate. The
        # removal markers this harness writes are comments for the same reason.
        if line.lstrip().startswith("#"):
            continue
        for kind, detail in scan_line(line):
            violations.append(
                {
                    "path": str(path),
                    "line": number,
                    "kind": kind,
                    "detail": detail,
                }
            )
    return violations


def collect(paths: list[str]) -> tuple[int, list[dict[str, object]]]:
    """(files read, violations) for the resolved targets, each file counted once."""
    checked = 0
    violations: list[dict[str, object]] = []
    seen: set[str] = set()
    for path in targets(paths):
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        found = audit_file(path)
        if found is None:
            continue
        checked += 1
        violations.extend(found)
    return checked, violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report banned provider hosts and unsafe Codex settings"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="file or directory to audit (default: known locations + current directory)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing; the exit code carries the result (ignored with --json)",
    )
    args = parser.parse_args(argv)

    missing = [raw for raw in args.paths if not Path(raw).expanduser().exists()]
    if missing:
        print(f"error: no such path: {', '.join(missing)}", file=sys.stderr)
        return 2

    checked, violations = collect(args.paths)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not violations,
                    "checked": checked,
                    "violations": violations,
                },
                indent=1,
            )
        )
    elif not args.quiet:
        for violation in violations:
            print(
                f"[VIOLATION] {violation['path']}:{violation['line']} "
                f"{violation['kind']} {violation['detail']}"
            )
        print(f"checked {checked} target(s), {len(violations)} violation(s)")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
