#!/usr/bin/env python3
"""
Schema Validation
==================
Validates frontmatter YAML in agent files and SKILL.md frontmatter.

Usage:
    python tools/validate_schemas.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Frontmatter keys each file type may use (reference list, not an allowlist gate)
AGENT_KEYS = {
    "model",
    "variant",
    "mode",
    "color",
    "steps",
    "hidden",
    "disabled",
    "permissions",
    "description",
    "system",
}
SKILL_KEYS = {"name", "description", "slash"}

# SPEC.md §7.1: every .kilo/agents/*.md must declare these.
AGENT_REQUIRED_KEYS = {"description", "mode"}
# SPEC.md §7.1 accepts either spelling for the permission block.
AGENT_PERMISSION_KEYS = {"permission", "permissions"}
AGENT_MODES = {"primary", "subagent", "all"}

# Agent Skills spec: engines warn about a skill that breaks either cap and drop
# it from the roster, so shipping one green would be a silent regression.
SKILL_NAME_MAX = 64
SKILL_DESCRIPTION_MAX = 1024
# Agent Skills spec: lowercase alphanumerics joined by single hyphens.
SKILL_NAME_PATTERN = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def _frontmatter_body(content: str) -> str | None:
    """Return the text between the fences, or None when the block is absent.

    The closing fence is a line that holds only dashes. A plain `find("\\n---")`
    ended the block at a body line such as `----- two`, which truncated every
    value measured from it.
    """
    content = content.strip()
    if not content.startswith("---"):
        return None
    lines = content.splitlines()
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            return "\n".join(lines[1:index])
    return None


def parse_frontmatter(content: str) -> dict | None:
    """Extract YAML frontmatter between --- markers.

    Returns parsed key-value pairs or None if malformed.
    """
    body = _frontmatter_body(content)
    if body is None:
        return None

    result = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\w[\w.-]*)\s*:\s*(.*)", line)
        if m:
            key = m.group(1)
            val = m.group(2).strip().strip('"').strip("'")
            result[key] = val
    return result


def read_frontmatter_scalar(content: str, key: str) -> str | None:
    """Read one top-level scalar, unfolding a YAML block scalar.

    `parse_frontmatter` is a line regex, so it returns the block indicator
    (`|-`) instead of the folded text. A 1024-character limit measured
    against that value would pass every skill, which is why this reader
    exists. Returns None when the key is absent.
    """
    frontmatter = _frontmatter_body(content)
    if frontmatter is None:
        return None

    lines = frontmatter.splitlines()
    pattern = re.compile(rf"^{re.escape(key)}\s*:\s*(.*)$")
    for index, line in enumerate(lines):
        m = pattern.match(line)
        if m is None:
            continue
        value = m.group(1).strip()
        if not value.startswith(("|", ">")):
            # Indented lines continue a plain scalar in YAML, so stopping at the
            # first line measured a 1201-character description as 100 and passed
            # a cap check that should have failed.
            parts = [value]
            for following in lines[index + 1 :]:
                if following.strip() and not following.startswith((" ", "\t")):
                    break
                parts.append(following.strip() or "\n")
            value = " ".join(parts).strip()
            # Quoted scalars keep their quotes in the raw line; a single
            # matching pair is syntax, not content.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value

        body = []
        for following in lines[index + 1 :]:
            # A line that is neither blank nor indented is the next key, so the
            # block ends there -- swallowing it would fold unrelated text in.
            if following.strip() and not following.startswith((" ", "\t")):
                break
            body.append(following)
        while body and not body[-1].strip():
            body.pop()
        if not body:
            return ""

        indent = min(
            len(line) - len(line.lstrip(" \t")) for line in body if line.strip()
        )
        dedented = [line[indent:] if line.strip() else "" for line in body]
        return ("\n" if value.startswith("|") else " ").join(dedented).strip()
    return None


def validate_agent(file_path: Path) -> list[str]:
    """Validate an agent markdown file's frontmatter (.kilo/agents/ native format).

    Note: this used to check for "deprecated V1 keys" -- that check validated
    the OUTPUT of the Kilo->OpenCode permission migration (V1 `permission:` ->
    V2 `permissions:` array). OpenCode was removed in v4.0.0, so .kilo/agents/
    (the source) legitimately uses its own native V1 `permission:` format --
    that is correct here, not an error.
    """
    errors: list[str] = []
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    fm = parse_frontmatter(content)
    if fm is None:
        errors.append(f"{file_path.name}: Missing or malformed frontmatter")
        return errors

    # SPEC.md §7.1 -- the contract is description + mode + at least one
    # permission key. `description` is not cosmetic: it is the trigger text an
    # engine matches to model-invoke the agent, so an agent without one is
    # silently unreachable. Deriving it per engine (the retired Claude engine's fallback)
    # hid that for months, which is why this is now enforced on the source.
    missing = sorted(AGENT_REQUIRED_KEYS - fm.keys())
    if missing:
        errors.append(
            f"{file_path.name}: Missing required key(s): {', '.join(missing)}"
        )

    if not (AGENT_PERMISSION_KEYS & fm.keys()):
        errors.append(
            f"{file_path.name}: Missing permission block "
            f"(expected 'permission:' or 'permissions:')"
        )

    mode = fm.get("mode")
    if mode is not None and mode not in AGENT_MODES:
        errors.append(
            f"{file_path.name}: Unknown mode '{mode}' "
            f"(expected one of: {', '.join(sorted(AGENT_MODES))})"
        )

    return errors


def validate_skill(file_path: Path) -> list[str]:
    """Validate a SKILL.md file's frontmatter against the Agent Skills spec.

    `description` is not documentation: it is the routing trigger text an engine
    matches to decide whether to load the skill, so a skill without one is
    unreachable rather than merely undescribed.
    """
    errors: list[str] = []
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    fm = parse_frontmatter(content)
    if fm is None:
        errors.append(f"{file_path.name}: Missing or malformed frontmatter")
        return errors

    name = fm.get("name", "").strip()
    if not name:
        errors.append(f"{file_path.name}: Missing 'name' field in frontmatter")
    else:
        if len(name) > SKILL_NAME_MAX:
            errors.append(
                f"{file_path.name}: name is {len(name)} characters (limit 64)"
            )
        if not SKILL_NAME_PATTERN.fullmatch(name):
            errors.append(
                f"{file_path.name}: name '{name}' is not lowercase letters, "
                f"digits and single hyphens"
            )

    description = read_frontmatter_scalar(content, "description")
    if not description:
        errors.append(f"{file_path.name}: Missing 'description' field in frontmatter")
    elif len(description) > SKILL_DESCRIPTION_MAX:
        errors.append(
            f"{file_path.name}: description is {len(description)} characters "
            f"(limit 1024)"
        )
    return errors


def main() -> int:
    root = ROOT
    # .kilo/ is the source of truth (.opencode/ was removed in v4.0.0 -- see
    # .harness.lock). Validate the source directly.
    kilo = root / ".kilo"
    errors: list[str] = []
    checked = 0

    # Validate agents
    agents_dir = kilo / "agents"
    if agents_dir.is_dir():
        for f in sorted(agents_dir.glob("*.md")):
            checked += 1
            errors.extend(validate_agent(f))

    # Validate skills
    skills_dir = kilo / "skill"
    if skills_dir.is_dir():
        for skill_dir in sorted(skills_dir.iterdir()):
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    checked += 1
                    errors.extend(validate_skill(skill_md))

    # The generated OpenCode skill tree is validated too: it is the copy an engine
    # actually loads, and skipping it left all 10 generated skills unchecked.
    opencode_skills = root / ".opencode" / "skills"
    if opencode_skills.is_dir():
        for skill_dir in sorted(opencode_skills.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_dir.is_dir() and skill_md.exists():
                checked += 1
                errors.extend(validate_skill(skill_md))

    print(f"Files checked: {checked}")
    print(f"Errors: {len(errors)}")
    for err in errors:
        print(f"  [FAIL] {err}")

    if errors:
        print("\nValidation FAILED — fix errors above.")
        return 1
    print("\nAll schemas valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
