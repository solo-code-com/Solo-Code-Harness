#!/usr/bin/env python3
"""
Claude Engine Generator Tests
=============================
Tests for tools/claude_engine.py — frontmatter parsing, permission -> tools
mapping, and command/skill generation.

Usage:
    python -m pytest tools/test_claude_engine.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_engine import (
    _build_tools_allowlist,
    _parse_kilo_permissions,
    _split_frontmatter,
    generate_agents,
    generate_commands,
    generate_instructions,
    generate_memory,
    generate_skills,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── _split_frontmatter ───────────────────────────────────────────────────


def test_split_frontmatter_valid():
    content = "---\nname: x\ndescription: y\n---\n# Body\ntext"
    result = _split_frontmatter(content)
    assert result is not None
    fm, body = result
    assert "name: x" in fm
    assert body.strip().startswith("# Body")


def test_split_frontmatter_none():
    assert _split_frontmatter("no frontmatter here") is None


# ── _parse_kilo_permissions ──────────────────────────────────────────────


def test_parse_simple_permissions():
    fm = (
        'description: "System architect"\n'
        "mode: subagent\n"
        "permission:\n"
        "  edit: deny\n"
        "  bash: deny\n"
        "  read: allow\n"
        "  grep: allow\n"
        "  codesearch: allow\n"
    )
    perms, desc, mode = _parse_kilo_permissions(fm)
    assert desc == "System architect"
    assert mode == "subagent"
    assert perms["read"] == "allow"
    assert perms["edit"] == "deny"
    assert perms["bash"] == "deny"


def test_parse_nested_permissions_most_permissive():
    """Nested patterns collapse to the most permissive effect."""
    fm = (
        "permission:\n"
        "  read: allow\n"
        "  edit:\n"
        '    "*": deny\n'
        '    "*.md": allow\n'
        "  bash:\n"
        '    "*": deny\n'
        '    "python *": allow\n'
    )
    perms, _desc, _mode = _parse_kilo_permissions(fm)
    # edit has an allow rule (*.md) -> most permissive is allow
    assert perms["edit"] == "allow"
    assert perms["bash"] == "allow"
    assert perms["read"] == "allow"


# ── _build_tools_allowlist ───────────────────────────────────────────────


def test_tools_allowlist_deny_excluded():
    perms = {"read": "allow", "grep": "allow", "edit": "deny", "bash": "deny"}
    tools = _build_tools_allowlist(perms)
    assert tools == ["Read", "Grep"]
    assert "Edit" not in tools
    assert "Bash" not in tools


def test_tools_allowlist_edit_maps_to_edit_and_write():
    perms = {"edit": "allow"}
    tools = _build_tools_allowlist(perms)
    assert "Edit" in tools
    assert "Write" in tools


def test_tools_allowlist_ask_is_included():
    """'ask' effect counts as allowed for tool exposure."""
    perms = {"bash": "ask"}
    tools = _build_tools_allowlist(perms)
    assert tools == ["Bash"]


def test_tools_allowlist_codesearch_folds_to_grep():
    perms = {"codesearch": "allow"}
    tools = _build_tools_allowlist(perms)
    assert tools == ["Grep"]


def test_tools_allowlist_deterministic_order():
    perms = {"bash": "allow", "read": "allow", "task": "allow", "edit": "allow"}
    tools = _build_tools_allowlist(perms)
    # Must follow _TOOL_ORDER: Read, Grep, Glob, Edit, Write, Bash, Task
    assert tools == ["Read", "Edit", "Write", "Bash", "Task"]


# ── memory + manifest parity (feat-008, feat-010) ─────────────────────────


def test_claude_memory_generated():
    """feat-008: .claude/memory/ mirrors .kilo/memory/."""
    root = Path(__file__).resolve().parent.parent
    kilo_mem = root / ".kilo" / "memory"
    claude_mem = root / ".claude" / "memory"
    assert claude_mem.is_dir(), ".claude/memory/ must exist"
    kilo_files = {f.name for f in kilo_mem.glob("*.md")}
    claude_files = {f.name for f in claude_mem.glob("*.md")}
    assert kilo_files == claude_files, f"memory drift: {kilo_files ^ claude_files}"


def test_memory_has_tech_stack_and_gotchas():
    """feat-008 definition of done: Tech Stack + >=3 gotcha entries."""
    root = Path(__file__).resolve().parent.parent
    text = (root / ".kilo" / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "Tech Stack" in text
    assert text.count("[gotcha]") >= 3


def test_garden_manifest_parser_accurate():
    """feat-010: agent.yaml list parser matches reality."""
    import garden

    root = Path(__file__).resolve().parent.parent
    text = (root / "agent.yaml").read_text(encoding="utf-8")
    skills = garden._parse_agent_yaml_list(text, "skills")
    agents = garden._parse_agent_yaml_list(text, "agents")
    actual_skills = {p.name for p in (root / ".kilo" / "skill").iterdir() if p.is_dir()}
    actual_agents = {f.stem for f in (root / ".kilo" / "agents").glob("*.md")}
    assert set(skills) == actual_skills, f"skill drift: {set(skills) ^ actual_skills}"
    assert set(agents) == actual_agents, f"agent drift: {set(agents) ^ actual_agents}"


def test_garden_manifest_check_clean():
    """feat-010: garden.check_manifest reports no drift on current tree."""
    import garden

    root = Path(__file__).resolve().parent.parent
    assert garden.check_manifest(root) == []


# ── generators ───────────────────────────────────────────────────────────


def test_split_frontmatter_requires_a_closing_marker():
    assert _split_frontmatter("---\nname: x") is None


def test_generate_instructions_scopes_rules_and_prunes_stale_copies(tmp_path):
    """A path-scoped source lands in .claude/rules/ with `paths:` frontmatter;
    neither copy of the other form survives the run."""
    kilo = tmp_path / ".kilo"
    claude = tmp_path / ".claude"
    _write(kilo / "instruction" / "rules-typescript.md", "RULE BODY\n")
    _write(kilo / "instruction" / "behavior-rules.md", "PLAIN BODY\n")
    # Artifacts from the previous layout: a plain copy of a now-scoped source
    # and a rule copy of a now-plain one.
    _write(claude / "instruction" / "rules-typescript.md", "STALE\n")
    _write(claude / "rules" / "behavior-rules.md", "STALE\n")

    assert generate_instructions(kilo, claude) == 0

    scoped = (claude / "rules" / "rules-typescript.md").read_text(encoding="utf-8")
    assert scoped.startswith('---\npaths:\n  - "**/*.ts"\n  - "**/*.tsx"\n---\n')
    assert "RULE BODY" in scoped
    assert not (claude / "instruction" / "rules-typescript.md").exists()

    plain = claude / "instruction" / "behavior-rules.md"
    assert plain.read_text(encoding="utf-8") == "PLAIN BODY\n"
    assert not (claude / "rules" / "behavior-rules.md").exists()


@pytest.mark.parametrize(
    "generate",
    [
        generate_agents,
        generate_skills,
        generate_commands,
        generate_instructions,
        generate_memory,
    ],
    ids=["agents", "skills", "commands", "instructions", "memory"],
)
def test_generators_report_a_missing_source_directory(tmp_path, capsys, generate):
    """A missing source fails the generator instead of writing an empty mirror."""
    assert generate(tmp_path / ".kilo", tmp_path / ".claude") == 1

    out = capsys.readouterr().out
    assert "[ERROR]" in out
    assert "not found" in out
