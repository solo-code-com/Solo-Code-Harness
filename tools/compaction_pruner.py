#!/usr/bin/env python3
"""
compaction_pruner.py — Prune oversized tool results to a byte budget.

Consumer role, ported from DeepSeek Harness packages/compaction/
(compaction-tool-result-pruner). A standalone tool, not a hook: it is
engine-agnostic and runs on demand. The remaining hook layer is
`.kilo/hooks/post-tool-use/`, and the pruner is not wired there today.

The pruner applies a byte budget to a piece of text. When the text exceeds
the budget, it keeps a head and a tail with a truncation marker in between,
so the end of a large result (where terminal output usually matters) is
preserved rather than silently cut.

Invariants enforced and self-tested:
    - Empty input is returned unchanged (no marker injected).
    - At-or-under budget is returned unchanged (byte-exact boundary).
    - Over-budget output never exceeds the byte budget.
    - Multibyte text is measured in UTF-8 bytes, not characters.

Usage:
    python tools/compaction_pruner.py --self-test
    python tools/compaction_pruner.py --text "..." --bytes 100
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from compaction import Budget
except ImportError:
    # Allow running from project root (python tools/compaction_pruner.py)
    # or via pytest, matching tools/session_analytics.py.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from compaction import Budget

MARKER = "\n… [truncated] …\n"


def prune(text: str, byte_limit: int) -> str:
    """Return text, pruned to fit byte_limit UTF-8 bytes if it exceeds.

    Empty and at-or-under-budget input pass through unchanged. Over-budget
    input keeps a head and tail split around a marker, so the final bytes
    survive. The marker's own bytes are counted inside the budget.
    """
    if byte_limit <= 0 or text == "":
        return text
    if len(text.encode("utf-8")) <= byte_limit:
        return text

    marker_bytes = len(MARKER.encode("utf-8"))
    # Budget must be large enough to hold the marker plus one byte each side;
    # otherwise fall back to a hard head cut that still respects the budget.
    if byte_limit <= marker_bytes + 2:
        return _cut_to_fit(text, byte_limit)

    head_bytes = (byte_limit - marker_bytes) // 2
    tail_bytes = byte_limit - marker_bytes - head_bytes

    head = _cut_to_fit(text, head_bytes)
    tail = _cut_tail_to_fit(text, tail_bytes)
    return head + MARKER + tail


def _cut_to_fit(text: str, byte_limit: int) -> str:
    """Return the longest prefix of text within byte_limit bytes."""
    if byte_limit <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_limit:
        return text
    # Decode may fail mid-codepoint; walk down until it decodes cleanly.
    cut = encoded[:byte_limit]
    while cut:
        try:
            return cut.decode("utf-8")
        except UnicodeDecodeError:
            cut = cut[:-1]
    return ""


def _cut_tail_to_fit(text: str, byte_limit: int) -> str:
    """Return the longest suffix of text within byte_limit bytes."""
    if byte_limit <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_limit:
        return text
    cut = encoded[-byte_limit:]
    while cut:
        try:
            return cut.decode("utf-8")
        except UnicodeDecodeError:
            cut = cut[1:]
    return ""


def _self_test() -> None:
    print("Running compaction_pruner self-test...")

    # Empty input passes through unchanged.
    assert prune("", 100) == ""

    # At-or-under budget passes through unchanged (byte-exact boundary).
    assert prune("abc", 3) == "abc"
    assert prune("abc", 4) == "abc"

    # Over-budget output never exceeds the byte budget.
    long_text = "x" * 1000
    pruned = prune(long_text, 100)
    assert len(pruned.encode("utf-8")) <= 100
    assert MARKER.strip() in pruned

    # Tail is preserved: the final characters survive the cut.
    assert pruned.endswith("x")

    # Multibyte is measured in UTF-8 bytes, not characters.
    # "éééé" is 4 chars / 8 bytes. budget 6 bytes must prune it, and the
    # result must never split a multibyte codepoint into invalid UTF-8.
    mb = "éééé"
    mb_pruned = prune(mb, 6)
    assert len(mb_pruned.encode("utf-8")) <= 6
    mb_pruned.encode(
        "utf-8"
    )  # raises UnicodeEncodeError only if already valid; just decodes
    mb_pruned.encode("utf-8").decode("utf-8")  # proves valid UTF-8

    # Tiny budget falls back to a hard cut that still respects the budget.
    tiny = prune("abcdef", 3)
    assert len(tiny.encode("utf-8")) <= 3

    # Budget is idempotent: pruning an already-pruned result is stable.
    once = prune(long_text, 100)
    twice = prune(once, 100)
    assert once == twice

    print("All tests passed.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prune oversized tool results to a byte budget"
    )
    parser.add_argument("--self-test", action="store_true", help="Run self-test")
    parser.add_argument("--text", help="Text to prune (stdin if omitted)")
    parser.add_argument(
        "--bytes", type=int, default=4000, help="Byte budget (default 4000)"
    )
    args = parser.parse_args()

    if args.self_test:
        _self_test()
        return 0

    text = args.text
    if text is None:
        text = sys.stdin.read()

    budget = Budget(byte_limit=args.bytes)
    exceeds, reason = budget.exceeds(text)
    if not exceeds:
        sys.stdout.write(text)
        return 0

    pruned = prune(text, args.bytes)
    sys.stderr.write(f"[compaction_pruner] {reason}\n")
    sys.stdout.write(pruned)
    return 0


if __name__ == "__main__":
    sys.exit(main())
