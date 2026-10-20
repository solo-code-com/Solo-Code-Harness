"""Tests for tools/validate_schemas.py"""

import tempfile
from pathlib import Path

from tools.validate_schemas import parse_frontmatter, validate_agent, validate_skill

# ── parse_frontmatter ──────────────────────────────────────────────────────────


def test_parse_valid_frontmatter():
    content = "---\nname: test\ndescription: a test\n---\n# Body"
    result = parse_frontmatter(content)
    assert result == {"name": "test", "description": "a test"}


def test_parse_frontmatter_strips_quotes():
    content = '---\nname: "quoted"\n---\n'
    result = parse_frontmatter(content)
    assert result["name"] == "quoted"


def test_parse_frontmatter_strips_single_quotes():
    content = "---\nname: 'single'\n---\n"
    result = parse_frontmatter(content)
    assert result["name"] == "single"


def test_parse_frontmatter_no_start_marker():
    result = parse_frontmatter("name: test\n")
    assert result is None


def test_parse_frontmatter_no_end_marker():
    result = parse_frontmatter("---\nname: test\n")
    assert result is None


def test_parse_frontmatter_empty_body():
    content = "---\n---\n"
    result = parse_frontmatter(content)
    assert result == {}


def test_parse_frontmatter_skips_comments():
    content = "---\n# comment\nname: test\n---\n"
    result = parse_frontmatter(content)
    assert result == {"name": "test"}


def test_parse_frontmatter_skips_blank_lines():
    content = "---\n\nname: test\n\n---\n"
    result = parse_frontmatter(content)
    assert result == {"name": "test"}


# ── validate_agent ─────────────────────────────────────────────────────────────


def _agent_file(frontmatter: str) -> Path:
    with tempfile.NamedTemporaryFile(
        suffix=".md", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(f"---\n{frontmatter}\n---\n# Agent\n")
        return Path(f.name)


def test_validate_agent_valid():
    path = _agent_file(
        'description: "Tests things"\nmode: subagent\npermission:\n  edit: deny'
    )
    assert validate_agent(path) == []


def test_validate_agent_accepts_permissions_plural():
    path = _agent_file(
        'description: "Tests things"\nmode: primary\npermissions:\n  - edit'
    )
    assert validate_agent(path) == []


def test_validate_agent_missing_frontmatter():
    with tempfile.NamedTemporaryFile(
        suffix=".md", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write("# No frontmatter here\n")
        path = Path(f.name)
    errors = validate_agent(path)
    assert len(errors) == 1
    assert "malformed" in errors[0].lower() or "missing" in errors[0].lower()


def test_validate_agent_empty_frontmatter_is_invalid():
    path = _agent_file("")
    errors = validate_agent(path)
    assert any("description" in e for e in errors)
    assert any("mode" in e for e in errors)
    assert any("permission" in e for e in errors)


def test_validate_agent_missing_description_flagged():
    # A missing description makes the agent unreachable for model-invocation.
    path = _agent_file("mode: subagent\npermission:\n  edit: deny")
    errors = validate_agent(path)
    assert len(errors) == 1
    assert "description" in errors[0]


def test_validate_agent_missing_mode_flagged():
    path = _agent_file('description: "Tests things"\npermission:\n  edit: deny')
    errors = validate_agent(path)
    assert len(errors) == 1
    assert "mode" in errors[0]


def test_validate_agent_missing_permission_block_flagged():
    path = _agent_file('description: "Tests things"\nmode: subagent')
    errors = validate_agent(path)
    assert len(errors) == 1
    assert "permission" in errors[0]


def test_validate_agent_rejects_unknown_mode():
    path = _agent_file(
        'description: "Tests things"\nmode: orchestrator\npermission:\n  edit: deny'
    )
    errors = validate_agent(path)
    assert len(errors) == 1
    assert "orchestrator" in errors[0]


def test_every_kilo_agent_declares_the_required_keys():
    """SPEC.md §7.1 regression guard for the shipped roster.

    Three agents (solo-code-engineer, test-engineer, web-performance-auditor)
    shipped without a `description` and nothing caught it -- the generated
    .claude/ copies got a derived one, so the gap was invisible in every mirror.
    """
    agents_dir = Path(__file__).resolve().parent.parent / ".kilo" / "agents"
    failures = {f.name: validate_agent(f) for f in sorted(agents_dir.glob("*.md"))}
    assert failures, "no agent files found -- the path moved?"
    assert {n: e for n, e in failures.items() if e} == {}


def test_validate_agent_rejects_an_empty_mode_value(tmp_path) -> None:
    """`mode:` with no value is not a mode; the guard is `is not None`, not falsy."""
    path = _agent_file('description: "Tests things"\nmode:\npermission:\n  edit: deny')
    errors = validate_agent(path)
    assert len(errors) == 1
    assert "Unknown mode ''" in errors[0]


# ── validate_skill ─────────────────────────────────────────────────────────────


def test_validate_skill_valid():
    with tempfile.NamedTemporaryFile(
        suffix=".md", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write("---\nname: my-skill\ndescription: does stuff\nslash: /my-skill\n---\n")
        path = Path(f.name)
    errors = validate_skill(path)
    assert errors == []


def test_validate_skill_missing_name():
    with tempfile.NamedTemporaryFile(
        suffix=".md", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write("---\ndescription: no name\n---\n")
        path = Path(f.name)
    errors = validate_skill(path)
    assert any("name" in e for e in errors)


def test_validate_skill_missing_frontmatter():
    with tempfile.NamedTemporaryFile(
        suffix=".md", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write("# just body\n")
        path = Path(f.name)
    errors = validate_skill(path)
    assert len(errors) == 1
