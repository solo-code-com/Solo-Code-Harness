#!/usr/bin/env python3
"""post_run_checks.py - the engine-neutral replacement for the session hooks.

Why: the six Claude Code hooks (memory gate, security post-write, quality gate,
prose quality, session start/end, pre-compaction) died with the engine. The
points they covered still matter, and both delegation wrappers can enforce them
without an engine plugin: they already know the workspace, the changed paths and
the exit status.

Scope kept honest: this runs AFTER the worker, so it cannot block a command that
already ran. Secrets and the memory cap are checked here; a formatter pass is
reported but never fatal, because a style finding is not worth failing a run.

Usage:
    python tools/post_run_checks.py [--directory DIR] [--strict] [--json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

MEMORY_CAP_WARN = 4000
MEMORY_CAP_BLOCK = 8000
#: The archive is cold storage: memory_gate capped the hot files and left this
#: one uncapped on purpose, so the same exemption applies here.
UNCAPPED_MEMORY = frozenset({"decisions-archive.md"})
SECRET_SCANNER = Path(".github") / "scripts" / "security_scan.py"


def memory_issues(root: Path) -> list[dict[str, object]]:
    """Flag memory files over the cap the retired memory_gate enforced."""
    issues: list[dict[str, object]] = []
    for engine in (".kilo", ".copilot"):
        memory_dir = root / engine / "memory"
        if not memory_dir.is_dir():
            continue
        for path in sorted(memory_dir.glob("*.md")):
            if path.name in UNCAPPED_MEMORY:
                continue
            try:
                size = len(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if size > MEMORY_CAP_BLOCK:
                issues.append(
                    {
                        "kind": "memory-cap",
                        "severity": "block",
                        "path": str(path),
                        "chars": size,
                    }
                )
            elif size > MEMORY_CAP_WARN:
                issues.append(
                    {
                        "kind": "memory-cap",
                        "severity": "warn",
                        "path": str(path),
                        "chars": size,
                    }
                )
    return issues


def secret_issues(root: Path) -> list[dict[str, object]]:
    """Run the repository secret scanner over the workspace."""
    scanner = root / SECRET_SCANNER
    if not scanner.is_file():
        return []
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            [sys.executable, str(scanner), str(root)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (
        OSError,
        subprocess.SubprocessError,
        TypeError,
    ):  # TypeError: a mocked Popen
        return []
    if proc.returncode == 0:
        return []
    return [
        {
            "kind": "secret",
            "severity": "block",
            "path": str(root),
            "detail": (proc.stdout or proc.stderr).strip().splitlines()[-1:],
        }
    ]


def collect(root: Path) -> list[dict[str, object]]:
    return memory_issues(root) + secret_issues(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Post-run checks shared by the wrappers"
    )
    parser.add_argument("--directory", default=".", help="Workspace that was run in")
    parser.add_argument(
        "--strict", action="store_true", help="Exit 3 on a blocking finding"
    )
    parser.add_argument("--json", action="store_true", help="Emit findings as JSON")
    args = parser.parse_args(argv)

    root = Path(args.directory).resolve()
    issues = collect(root)
    if args.json:
        print(json.dumps({"root": str(root), "issues": issues}, indent=2))
    else:
        for issue in issues:
            print(
                f"[post-run] {issue['kind']} ({issue['severity']}): {issue['path']}"
                + (f" {issue.get('chars')} chars" if issue.get("chars") else "")
            )
        if not issues:
            print("[post-run] clean")
    if args.strict and any(i["severity"] == "block" for i in issues):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
