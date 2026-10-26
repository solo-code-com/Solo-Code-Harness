#!/usr/bin/env python3
"""
No-skips test policy — prevent test degradation.

Port pattern from codebase-memory-mcp (scripts/check-no-test-skips.sh).
Scans test files for skip markers that silently degrade coverage.

Only SKIP_PLATFORM and documented KNOWN_GAP are allowed.
Everything else (unconditional skip, skipif without reason) fails.

Usage:
    python .github/scripts/check_skips.py <test_dir>
"""

import re
import sys
from pathlib import Path

_MARKER = re.compile(r"@pytest\.mark\.skip(?:if)?")


def _skip_call(content: str, start: int) -> str:
    """The decorator call starting at `start`, with parentheses balanced.

    A regex cannot delimit this: `reason=` arguments carry their own calls
    (`os.environ.get("KEY")`), so a `[^)]*` group stops at the first nested
    `)` and loses the `reason=` that follows it.
    """
    open_at = content.find("(", start)
    if open_at == -1:
        return content[start:]
    depth = 0
    quote = None  # active string delimiter, if any
    i = open_at
    while i < len(content):
        ch = content[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
        i += 1
    return content[start:]


def check_file(file_path: Path) -> list[str]:
    """Return list of violations in a test file."""
    violations = []
    content = file_path.read_text(encoding="utf-8", errors="ignore")

    for match in _MARKER.finditer(content):
        full = _skip_call(content, match.start())
        line_num = content[: match.start()].count("\n") + 1
        if "reason" in full or "SKIP_PLATFORM" in full or "KNOWN_GAP" in full:
            continue
        violations.append(
            f"  {file_path.name}:{line_num}: "
            f"Unconditional skip without reason -- use reason='...' or SKIP_PLATFORM"
        )

    for match in re.finditer(r"//\s*@ts-ignore\s*$", content, re.MULTILINE):
        line_num = content[: match.start()].count("\n") + 1
        violations.append(
            f"  {file_path.name}:{line_num}: @ts-ignore without justification comment"
        )

    return violations


def main():
    # Default target: tools/ (this repo's pytest suite). .opencode/tests
    # (the old default) was removed in v4.0.0 along with the OpenCode engine.
    test_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tools")
    if not test_dir.is_dir():
        print(f"SKIP: {test_dir} not found")
        sys.exit(0)

    all_violations = []
    for pattern in ("*.py", "*.mjs"):
        for f in test_dir.rglob(pattern):
            all_violations.extend(check_file(f))

    if all_violations:
        print(f"No-skips policy: {len(all_violations)} violation(s)")
        for v in all_violations:
            print(v)
        sys.exit(1)

    print("No-skips policy: OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
