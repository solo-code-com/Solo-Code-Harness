#!/usr/bin/env python3
"""
compaction.py — Token/byte budget policy for context compaction.

Service Definition role, ported from DeepSeek Harness packages/compaction/
(compaction-basic budget policy, without the LLM summarizer — that part
requires calling a model in-process, out of scope for this seam).

The policy owns ONE decision: does a piece of content exceed its budget?
It is a pure function of the content and a threshold, so it is testable
with empty input, boundary values, and multibyte text without any model.

Budget units are intentionally explicit:
    - char_budget: raw character count (len of the string)
    - byte_budget: UTF-8 byte length (differs from char count for multibyte)

This mirrors dsh's rule "apply bounds to the complete result": the limit
must include wrappers and metadata, not just the payload.

Usage:
    python tools/compaction.py --self-test
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    """Byte and character ceilings. None means unbounded on that axis."""

    char_limit: int | None = None
    byte_limit: int | None = None

    def exceeds(self, text: str) -> tuple[bool, str]:
        """Return (exceeds, reason) for the first violated limit.

        Empty text never exceeds. When both limits are None the budget is
        unbounded and nothing exceeds.
        """
        if self.byte_limit is not None:
            byte_len = len(text.encode("utf-8"))
            if byte_len > self.byte_limit:
                return True, f"byte budget {byte_len}/{self.byte_limit}"
        if self.char_limit is not None:
            char_len = len(text)
            if char_len > self.char_limit:
                return True, f"char budget {char_len}/{self.char_limit}"
        return False, ""


def _self_test() -> None:
    print("Running compaction self-test...")

    # Unbounded budget accepts anything, including empty.
    assert Budget().exceeds("") == (False, "")
    assert Budget().exceeds("hello") == (False, "")

    # Empty text never exceeds a bounded budget.
    assert Budget(char_limit=0).exceeds("") == (False, "")
    assert Budget(byte_limit=0).exceeds("") == (False, "")

    # Character limit is off-by-one correct: at limit passes, limit+1 fails.
    assert Budget(char_limit=5).exceeds("hello") == (False, "")
    ok_5, reason_5 = Budget(char_limit=5).exceeds("hello!")
    assert ok_5 is True and "char budget" in reason_5

    # Byte limit counts UTF-8 bytes, not characters.
    # "é" is 1 char but 2 UTF-8 bytes.
    assert Budget(byte_limit=2).exceeds("é") == (False, "")
    ok_b, reason_b = Budget(byte_limit=2).exceeds("é!")  # 3 bytes
    assert ok_b is True and "byte budget" in reason_b

    # Multibyte: 1 extra byte over the limit is caught (boundary, not just gross overflow).
    assert Budget(byte_limit=3).exceeds("€") == (False, "")  # € = 3 bytes
    assert Budget(byte_limit=3).exceeds("€a")[0] is True  # 4 bytes

    print("All tests passed.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Context compaction budget policy (Service Definition)"
    )
    parser.add_argument("--self-test", action="store_true", help="Run self-test")
    args = parser.parse_args()

    if args.self_test:
        _self_test()
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
