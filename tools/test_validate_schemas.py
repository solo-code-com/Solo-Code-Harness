"""Tests for tools/validate_schemas.py

.opencode/skills/ is the generated tree an engine actually loads, so the
suite covers it alongside the .kilo/ source.
"""

import tempfile
from pathlib import Path

import pytest

from tools.validate_schemas import (
    parse_frontmatter,
    read_frontmatter_scalar,
    validate_agent,
    validate_skill,
)

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


# ── read_frontmatter_scalar ────────────────────────────────────────────────────


def test_read_frontmatter_scalar_reads_an_inline_value():
    content = "---\nname: my-skill\ndescription: routes Claude work\n---\n"
    assert read_frontmatter_scalar(content, "name") == "my-skill"


def test_read_frontmatter_scalar_unfolds_a_literal_block():
    content = "---\ndescription: |-\n  first line\n  second line\nlicense: MIT\n---\n"
    value = read_frontmatter_scalar(content, "description")
    assert value == "first line\nsecond line"
    assert value != "|-"


def test_read_frontmatter_scalar_unfolds_a_folded_block():
    content = "---\ndescription: >-\n  first line\n  second line\n---\n"
    value = read_frontmatter_scalar(content, "description")
    assert value == "first line second line"


def test_read_frontmatter_scalar_stops_at_the_next_key():
    content = "---\ndescription: |-\n  trigger text\nlicense: MIT\n---\n"
    value = read_frontmatter_scalar(content, "description")
    assert value == "trigger text"
    assert "MIT" not in value


def test_read_frontmatter_scalar_returns_none_for_an_absent_key():
    content = "---\nname: my-skill\n---\n"
    assert read_frontmatter_scalar(content, "description") is None


def test_read_frontmatter_scalar_returns_empty_for_an_empty_block():
    # Present-but-empty is not absent: an empty `description:` is still a skill
    # with no routing text, so the caller has to be able to tell the two apart.
    content = "---\ndescription: |-\nname: my-skill\n---\n"
    assert read_frontmatter_scalar(content, "description") == ""


def test_read_frontmatter_scalar_reads_a_plain_multiline_scalar():
    """YAML plain scalars continue on indented following lines; a reader that
    stopped at the first line measured a 1201-character description as 100."""
    content = "---\nname: probe\ndescription: first\n  second\n  third\n---\n"
    assert read_frontmatter_scalar(content, "description") == "first second third"


def test_read_frontmatter_scalar_keeps_a_dashed_line_inside_a_block_scalar():
    """The closing fence holds only dashes, so an indented `---` is block
    content; truncating there silently shortened every measured description."""
    content = (
        "---\n"
        "description: |-\n"
        "  line one\n"
        "  ---\n"
        "  line two after a dash line\n"
        "license: MIT\n"
        "---\n"
    )
    value = read_frontmatter_scalar(content, "description")
    assert "line two after a dash line" in value


# ── validate_skill against the Agent Skills spec ───────────────────────────────


def _skill_file(frontmatter_body: str) -> Path:
    with tempfile.NamedTemporaryFile(
        suffix=".md", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(f"---\n{frontmatter_body}\n---\n# Skill\n")
        return Path(f.name)


def test_validate_skill_accepts_a_description_at_the_limit():
    path = _skill_file(f"name: my-skill\ndescription: {'x' * 1024}")
    assert validate_skill(path) == []


def test_validate_skill_rejects_a_description_over_the_limit():
    path = _skill_file(f"name: my-skill\ndescription: {'x' * 1025}")
    errors = validate_skill(path)
    assert len(errors) == 1
    assert "1024" in errors[0]


def test_validate_skill_rejects_a_name_over_64_characters():
    path = _skill_file(f"name: {'a' * 65}\ndescription: routes Claude work")
    errors = validate_skill(path)
    assert len(errors) == 1
    assert "64" in errors[0]


def test_validate_skill_rejects_an_uppercase_name():
    path = _skill_file("name: My-Skill\ndescription: routes Claude work")
    errors = validate_skill(path)
    assert len(errors) == 1
    assert "lowercase" in errors[0]


def test_validate_skill_rejects_a_trailing_hyphen_name():
    path = _skill_file("name: my-skill-\ndescription: routes Claude work")
    errors = validate_skill(path)
    assert len(errors) == 1
    assert "lowercase" in errors[0]


def test_validate_skill_rejects_a_missing_description():
    path = _skill_file("name: my-skill")
    errors = validate_skill(path)
    assert len(errors) == 1
    assert "description" in errors[0]


def test_validate_skill_with_a_block_scalar_description_is_measured_as_folded():
    """The `|-` trap: `parse_frontmatter` returns `|-` here, which always passes."""
    path = _skill_file(f"name: my-skill\ndescription: |-\n  {'x' * 1025}")
    errors = validate_skill(path)
    assert len(errors) == 1
    assert "1025" in errors[0]


def test_validate_skill_measures_a_plain_multiline_description():
    """Plain-scalar twin of the `|-` trap above: the 1024 cap must bite
    whatever YAML style the author happened to write the description in."""
    path = _skill_file(
        f"name: my-skill\ndescription: {'a' * 100}\n  {'b' * 600}\n  {'c' * 500}"
    )
    errors = validate_skill(path)
    assert errors, "a 1202-character plain multiline description passed"
    assert any("1024" in e for e in errors)


def test_every_shipped_skill_obeys_the_spec():
    """Shipped-roster guard, mirroring the agent-roster test above.

    Nothing measured a shipped skill's description, so a 1068-character
    description (claude-api) validated green while an engine would drop it.
    """
    skills_dir = Path(__file__).resolve().parent.parent / ".kilo" / "skill"
    failures = {
        f.parent.name: validate_skill(f) for f in sorted(skills_dir.glob("*/SKILL.md"))
    }
    assert failures, "no skill files found -- the path moved?"
    assert {n: e for n, e in failures.items() if e} == {}


def test_every_generated_opencode_skill_obeys_the_spec():
    """Roster guard for the tree an engine actually loads: main() validates
    .opencode/skills/*/SKILL.md, and a green .kilo/ source says nothing about
    what the generator emitted into it."""
    skills_dir = Path(__file__).resolve().parent.parent / ".opencode" / "skills"
    if not skills_dir.is_dir():
        pytest.skip("no generated .opencode/skills tree in this checkout")
    failures = {
        f.parent.name: validate_skill(f) for f in sorted(skills_dir.glob("*/SKILL.md"))
    }
    assert {n: e for n, e in failures.items() if e} == {}
