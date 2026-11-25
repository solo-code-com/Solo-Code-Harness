#!/usr/bin/env python3
"""
Integration smoke test for the local shared-state database and VS Code MCP configuration.

Engine parity is covered by garden.py and test_generate_harness.py.

Validates:
  - Shared state: .solocode/shared-state.db schema
  - VS Code MCP config: .vscode/mcp.json

Usage:
    python tools/test_integration.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  -- {detail}")


def test_vscode_mcp() -> None:
    print("\n--- VS Code MCP Config ---")
    mcp = ROOT / ".vscode" / "mcp.json"
    check("mcp.json exists", mcp.is_file())
    if not mcp.is_file():
        return
    try:
        data = json.loads(mcp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        check("mcp.json: valid JSON", False, "parse error")
        return
    check("mcp.json: valid JSON", True)
    check("mcp.json: servers key", "servers" in data)


def test_shared_state() -> None:
    print("\n--- Shared State ---")
    db_path = ROOT / ".solocode" / "shared-state.db"
    # DB is local-only (gitignored, created on first engine run). A fresh
    # checkout legitimately has none — only validate integrity when present.
    if not db_path.is_file():
        check("shared-state.db (local-only, skipped when absent)", True)
        return

    from tools.shared_state import SharedState

    with SharedState(db_path) as state:
        errors = state.integrity_check()
        check("  integrity_check passes", errors == [], f"errors: {errors}")

        recent = state.get_recent_sessions(limit=1)
        check("  get_recent_sessions() returns a list", isinstance(recent, list))


# ─── Main ──────────────────────────────────────────────────────────
def main() -> int:
    print("=" * 60)
    print(" Harness Integration Tests")
    print("=" * 60)

    test_shared_state()
    test_vscode_mcp()

    print()
    print("=" * 60)
    print(f"  Results: {PASS} pass, {FAIL} fail")
    print("=" * 60)

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
