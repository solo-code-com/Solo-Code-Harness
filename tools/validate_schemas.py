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


def parse_frontmatter(content: str) -> dict | None:
    """Extract YAML frontmatter between --- markers.

    Returns parsed key-value pairs or None if malformed.
    """
    content = content.strip()
    if not content.startswith("---"):
        return None

    end = content.find("\n---", 3)
    if end == -1:
        return None

    raw = content[3:end]
    result = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\w[\w.-]*)\s*:\s*(.*)", line)
        if m:
            key = m.group(1)
            val = m.group(2).strip().strip('"').strip("'")
            result[key] = val
    return result


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
    # silently unreachable. Deriving it per engine (claude_engine's fallback)
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
    """Validate a SKILL.md file's frontmatter."""
    errors: list[str] = []
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    fm = parse_frontmatter(content)
    if fm is None:
        errors.append(f"{file_path.name}: Missing or malformed frontmatter")
    else:
        if "name" not in fm:
            errors.append(f"{file_path.name}: Missing 'name' field in frontmatter")
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
