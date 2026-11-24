#!/usr/bin/env python3
"""
Integration tests for Copilot harness structure + shared state.

.opencode/ engine coverage lives in tools/garden.py's drift checks (see
check_opencode) and tools/test_generate_harness.py. .kilo/ (source of truth)
is covered by tools/validate_schemas.py + tools/garden.py's drift checks
instead. (The OpenCode engine was removed in v4.0.0 and reintroduced in
v4.2.0 as a first-class primary engine -- see .harness.lock.)

Validates:
  - Copilot: agents, skills, instructions, commands, rulebook, prompts, vscode
  - Shared state: .solocode/shared-state.db schema

Usage:
    python tools/test_integration.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COPILOT = ROOT / ".copilot"
KILO = ROOT / ".kilo"
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


def test_copilot_agents() -> None:
    print("\n--- Copilot Agents (parity with .kilo/agents/) ---")
    agents_dir = COPILOT / "agents"
    check("agents/ directory exists", agents_dir.is_dir())
    if not agents_dir.is_dir():
        return

    agents = sorted(agents_dir.glob("*.md"))
    expected = len(list((KILO / "agents").glob("*.md")))
    check(
        f"agent count = {len(agents)} (matches .kilo/)",
        len(agents) == expected,
        f"got {len(agents)}, .kilo/ has {expected}",
    )

    for f in agents:
        content = f.read_text(encoding="utf-8")
        has_fm = content.startswith("---")
        check(f"  {f.name}: YAML frontmatter", has_fm)
        if has_fm:
            end = content.find("\n---", 3)
            check(f"  {f.name}: closing ---", end != -1)
            fm = content[3:end] if end != -1 else ""
            check(
                f"  {f.name}: mode or permission key",
                "mode:" in fm or "permission:" in fm,
                "missing mode/permission",
            )


def test_copilot_skills() -> None:
    print("\n--- Copilot Skills (parity with .kilo/skill/) ---")
    skills_dir = COPILOT / "skill"
    check("skill/ directory exists", skills_dir.is_dir())
    if not skills_dir.is_dir():
        return

    skills = sorted([d for d in skills_dir.iterdir() if d.is_dir()])
    # Compare against .kilo/ (source of truth) rather than a hardcoded
    # number -- the literal 49 silently rotted when the 50th skill landed,
    # failing this suite for a drift that did not exist.
    expected = len([d for d in (KILO / "skill").iterdir() if d.is_dir()])
    check(
        f"skill count = {len(skills)} (matches .kilo/)",
        len(skills) == expected,
        f"got {len(skills)}, .kilo/ has {expected}",
    )

    for d in skills:
        skill_md = d / "SKILL.md"
        has_md = skill_md.is_file()
        check(f"  {d.name}: SKILL.md exists", has_md)
        if has_md:
            content = skill_md.read_text(encoding="utf-8")
            check(f"  {d.name}: has frontmatter", content.startswith("---"))


def test_copilot_instructions() -> None:
    print("\n--- Copilot Instructions (expect 10) ---")
    inst_dir = COPILOT / "instruction"
    check("instruction/ directory exists", inst_dir.is_dir())
    if not inst_dir.is_dir():
        return

    files = sorted(inst_dir.glob("*.md"))
    check(f"instruction count = {len(files)}", len(files) == 10, f"got {len(files)}")

    for f in files:
        size = f.stat().st_size
        check(f"  {f.name}: non-empty", size > 0, "empty file")


def test_copilot_commands() -> None:
    print("\n--- Copilot Commands (parity with .kilo/command/) ---")
    cmd_dir = COPILOT / "command"
    check("command/ directory exists", cmd_dir.is_dir())
    if not cmd_dir.is_dir():
        return

    cmds = sorted(cmd_dir.glob("*.md"))
    expected = len(list((KILO / "command").glob("*.md")))
    check(
        f"command count = {len(cmds)} (matches .kilo/)",
        len(cmds) == expected,
        f"got {len(cmds)}, .kilo/ has {expected}",
    )


def test_copilot_rulebook() -> None:
    print("\n--- Copilot Rulebook ---")
    rulebook = ROOT / ".github" / "copilot-instructions.md"
    check("copilot-instructions.md exists", rulebook.is_file())
    if rulebook.is_file():
        content = rulebook.read_text(encoding="utf-8")
        check("  non-empty", len(content) > 100, "too small to be valid rulebook")
        check(
            "  contains 'Request Classification'", "Request Classification" in content
        )
        check("  contains 'Socratic Gate'", "Socratic Gate" in content)
        check("  contains 'Solo-Code' branding", "Solo-Code" in content)


def test_copilot_prompts() -> None:
    print("\n--- Copilot Prompts (expect 6) ---")
    prompts_dir = ROOT / ".github" / "prompts"
    check("prompts/ directory exists", prompts_dir.is_dir())
    if not prompts_dir.is_dir():
        return

    prompts = sorted(prompts_dir.glob("*.prompt.md"))
    check(f"prompt count = {len(prompts)}", len(prompts) == 6, f"got {len(prompts)}")

    for f in prompts:
        content = f.read_text(encoding="utf-8")
        check(f"  {f.name}: has frontmatter", content.startswith("---"))
        check(f"  {f.name}: non-empty", len(content) > 50, "too small")


def test_copilot_vscode() -> None:
    print("\n--- VS Code Config ---")
    vscode = ROOT / ".vscode"
    check(".vscode/ exists", vscode.is_dir())
    if not vscode.is_dir():
        return

    settings = vscode / "settings.json"
    check("  settings.json exists", settings.is_file())
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            check("  settings.json: valid JSON", True)
            check(
                "  settings.json: copilot instructions",
                "github.copilot.chat.codeGeneration.instructions" in data,
            )
            check(
                "  settings.json: promptFiles",
                "github.copilot.chat.promptFiles" in data,
            )
        except json.JSONDecodeError:
            check("  settings.json: valid JSON", False, "parse error")

    mcp = vscode / "mcp.json"
    check("  mcp.json exists", mcp.is_file())
    if mcp.is_file():
        try:
            data = json.loads(mcp.read_text(encoding="utf-8"))
            check("  mcp.json: valid JSON", True)
            check("  mcp.json: servers key", "servers" in data)
            servers = data.get("servers", {})
            check("  mcp.json: context7 server", "context7" in servers)
        except json.JSONDecodeError:
            check("  mcp.json: valid JSON", False, "parse error")


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

        features = state.get_features()
        # No hardcoded minimum count: shared-state.db is local-only, machine-
        # specific state (gitignored, not committed) -- how many features a
        # given dev machine happens to have registered is not something a
        # portable integration test should assert on. Just validate shape.
        check("  get_features() returns a list", isinstance(features, list))

        for f in features:
            if f["status"] == "in-progress":
                check(f"  {f['id']}: has owner", f["owner"].get("engine") is not None)


# ─── Main ──────────────────────────────────────────────────────────
def main() -> int:
    print("=" * 60)
    print(" Copilot Harness + Shared State — Integration Tests")
    print("=" * 60)

    test_shared_state()

    print("\n  [Copilot Engine]")
    test_copilot_agents()
    test_copilot_skills()
    test_copilot_instructions()
    test_copilot_commands()
    test_copilot_rulebook()
    test_copilot_prompts()
    test_copilot_vscode()

    print()
    print("=" * 60)
    print(f"  Results: {PASS} pass, {FAIL} fail")
    print("=" * 60)

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
