#!/usr/bin/env python3
"""
Harness Generator Tests
=======================
Guards for tools/generate_harness.py's instruction mirroring.

Background: .copilot/instruction/ and .gemini/antigravity/instruction/ were
"manually kept in parity with .kilo/ and verified by tools/garden.py". Garden
detected content drift there and told the user to run

    python tools/generate_harness.py --harness all

...which only regenerated .claude/ and therefore fixed nothing. The advertised
remedy was a no-op and the real fix was an undocumented hand copy.

Usage:
    python -m pytest tools/test_generate_harness.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import garden, generate_harness, opencode_engine  # noqa: E402

MIRRORS = [
    (".copilot", Path(".copilot") / "instruction"),
    (".gemini/antigravity", Path(".gemini") / "antigravity" / "instruction"),
]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fake_tree(tmp_path: Path) -> Path:
    _write(tmp_path / ".kilo" / "instruction" / "rules-git.md", "SOURCE\n")
    for _, rel in MIRRORS:
        _write(tmp_path / rel / "rules-git.md", "SOURCE\n")
    return tmp_path


def test_sync_repairs_drifted_mirror(tmp_path):
    root = _fake_tree(tmp_path)
    for _, rel in MIRRORS:
        _write(root / rel / "rules-git.md", "DRIFTED\n")

    synced = generate_harness.sync_instruction_mirrors(root / ".kilo", root)

    assert synced == len(MIRRORS)
    for _, rel in MIRRORS:
        assert (root / rel / "rules-git.md").read_text(encoding="utf-8") == "SOURCE\n"


def test_sync_is_idempotent(tmp_path):
    root = _fake_tree(tmp_path)
    assert generate_harness.sync_instruction_mirrors(root / ".kilo", root) == 0


def test_sync_does_not_invent_files(tmp_path):
    """Engines carry different instruction subsets — don't add new ones.

    garden.check_instructions() only diffs names present in both trees, so
    creating files here would manufacture parity nobody asked for.
    """
    root = _fake_tree(tmp_path)
    _write(root / ".kilo" / "instruction" / "kilo-only.md", "X\n")

    generate_harness.sync_instruction_mirrors(root / ".kilo", root)

    for _, rel in MIRRORS:
        assert not (root / rel / "kilo-only.md").exists(), "sync invented a file"


def test_sync_skips_absent_mirror_dirs(tmp_path):
    _write(tmp_path / ".kilo" / "instruction" / "a.md", "A\n")
    assert generate_harness.sync_instruction_mirrors(tmp_path / ".kilo", tmp_path) == 0


def test_garden_remediation_command_actually_fixes_drift(tmp_path):
    """The command garden prints must resolve the drift garden reports.

    This is the real regression: garden's advice was a no-op for mirror
    instruction drift. Asserted against garden's own checker so the two
    cannot diverge again.
    """
    root = _fake_tree(tmp_path)
    src, dst = root / ".kilo", root / ".copilot"
    _write(dst / "instruction" / "rules-git.md", "DRIFTED\n")

    assert garden.check_instruction_content(src, dst, ".copilot"), (
        "expected garden to report drift before sync"
    )
    generate_harness.sync_instruction_mirrors(src, root)
    assert not garden.check_instruction_content(src, dst, ".copilot"), (
        "garden still reports drift after running its own remediation"
    )


def test_real_repo_mirrors_are_in_sync():
    """The checked-in mirrors must already match .kilo/."""
    for label, rel in MIRRORS:
        issues = garden.check_instruction_content(
            ROOT / ".kilo", ROOT / rel.parent, label
        )
        assert not issues, f"{label} instruction drift in repo: {issues}"


# ─── memory mirrors ─────────────────────────────────────────────────────────
#
# Same gap as the instruction mirrors, found while pruning MEMORY.md: garden's
# check_memory() docstring said .copilot/memory/ is a "manually-kept mirror
# (no auto-generator regenerates them)", so garden reported memory drift and
# its own remediation command could not clear it.

MEMORY_MIRROR = Path(".copilot") / "memory"


def _fake_memory_tree(tmp_path: Path) -> Path:
    _write(tmp_path / ".kilo" / "memory" / "MEMORY.md", "SOURCE\n")
    _write(tmp_path / MEMORY_MIRROR / "MEMORY.md", "SOURCE\n")
    return tmp_path


def test_memory_sync_repairs_drift(tmp_path):
    root = _fake_memory_tree(tmp_path)
    _write(root / MEMORY_MIRROR / "MEMORY.md", "DRIFTED\n")

    assert generate_harness.sync_memory_mirrors(root / ".kilo", root) == 1
    assert (root / MEMORY_MIRROR / "MEMORY.md").read_text(
        encoding="utf-8"
    ) == "SOURCE\n"


def test_memory_sync_is_idempotent(tmp_path):
    root = _fake_memory_tree(tmp_path)
    assert generate_harness.sync_memory_mirrors(root / ".kilo", root) == 0


def test_memory_sync_does_not_invent_files(tmp_path):
    root = _fake_memory_tree(tmp_path)
    _write(root / ".kilo" / "memory" / "kilo-only.md", "X\n")
    generate_harness.sync_memory_mirrors(root / ".kilo", root)
    assert not (root / MEMORY_MIRROR / "kilo-only.md").exists()


def test_garden_memory_remediation_actually_fixes_drift(tmp_path):
    """garden's advertised fix command must clear the memory drift it reports."""
    root = _fake_memory_tree(tmp_path)
    src, dst = root / ".kilo", root / ".copilot"
    _write(dst / "memory" / "MEMORY.md", "DRIFTED\n")

    assert garden.check_memory(src, dst, ".copilot"), "expected drift before sync"
    generate_harness.sync_memory_mirrors(src, root)
    assert not garden.check_memory(src, dst, ".copilot"), (
        "garden still reports memory drift after running its own remediation"
    )


def test_real_repo_memory_mirrors_are_in_sync():
    issues = garden.check_memory(ROOT / ".kilo", ROOT / ".copilot", ".copilot")
    assert not issues, f".copilot memory drift in repo: {issues}"


def test_memory_md_is_under_the_hard_cap():
    """MEMORY.md is injected every session and blocked by memory_gate at 8k.

    Checked for every engine copy, since the hook reads .claude/memory/ while
    .kilo/ is the source people edit.
    """
    for engine in (".kilo", ".claude", ".copilot"):
        f = ROOT / engine / "memory" / "MEMORY.md"
        if not f.is_file():
            continue
        n = len(f.read_text(encoding="utf-8"))
        assert n < 8000, f"{engine}/memory/MEMORY.md is {n} chars (hard cap 8000)"


# ─── skill body mirrors ──────────────────────────────────────────────────────

SKILL_MIRRORS = [
    (".copilot", Path(".copilot") / "skill"),
    (".gemini/antigravity", Path(".gemini") / "antigravity" / "skills"),
]


def _fake_skill_tree(tmp_path: Path) -> Path:
    _write(
        tmp_path / ".kilo" / "skill" / "plan" / "SKILL.md",
        "---\ndescription: Kilo plan\n---\n# Plan\nCommon instruction body.\n",
    )
    _write(
        tmp_path / ".copilot" / "skill" / "plan" / "SKILL.md",
        '---\ndescription: "Copilot plan"\nlicense: MIT\n---\n# Plan\nCommon instruction body.\n',
    )
    _write(
        tmp_path / ".gemini" / "antigravity" / "skills" / "plan" / "SKILL.md",
        "---\ndescription: Gemini plan\n---\n# Plan\nCommon instruction body.\n",
    )
    return tmp_path


def test_skill_body_sync_repairs_drift(tmp_path):
    root = _fake_skill_tree(tmp_path)
    # Introduce drift in body of mirrors
    _write(
        root / ".copilot" / "skill" / "plan" / "SKILL.md",
        '---\ndescription: "Copilot plan"\nlicense: MIT\n---\n# Plan\nDRIFTED body.\n',
    )
    _write(
        root / ".gemini" / "antigravity" / "skills" / "plan" / "SKILL.md",
        "---\ndescription: Gemini plan\n---\n# Plan\nDRIFTED body.\n",
    )

    synced = generate_harness.sync_skill_body_mirrors(root / ".kilo", root)
    assert synced == len(SKILL_MIRRORS)

    copilot_text = (root / ".copilot" / "skill" / "plan" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "license: MIT" in copilot_text
    assert "Common instruction body." in copilot_text
    assert "DRIFTED" not in copilot_text

    gemini_text = (
        root / ".gemini" / "antigravity" / "skills" / "plan" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "Gemini plan" in gemini_text
    assert "Common instruction body." in gemini_text
    assert "DRIFTED" not in gemini_text

    for label, rel in SKILL_MIRRORS:
        assert not garden.check_skill_content(root / ".kilo", root / rel, label)


def test_skill_body_sync_is_idempotent(tmp_path):
    root = _fake_skill_tree(tmp_path)
    assert generate_harness.sync_skill_body_mirrors(root / ".kilo", root) == 0


def test_skill_body_sync_does_not_invent_files(tmp_path):
    root = _fake_skill_tree(tmp_path)
    _write(root / ".kilo" / "skill" / "kilo-only" / "SKILL.md", "---\n---\nBody\n")
    generate_harness.sync_skill_body_mirrors(root / ".kilo", root)
    for _, rel in SKILL_MIRRORS:
        assert not (root / rel / "kilo-only").exists()


def test_skill_body_sync_skips_absent_mirror_dirs(tmp_path):
    _write(tmp_path / ".kilo" / "skill" / "a" / "SKILL.md", "---\n---\nBody\n")
    assert generate_harness.sync_skill_body_mirrors(tmp_path / ".kilo", tmp_path) == 0


def test_real_repo_skill_bodies_are_in_sync():
    for label, rel in SKILL_MIRRORS:
        issues = garden.check_skill_content(ROOT / ".kilo", ROOT / rel, label)
        assert not issues, f"{label} skill body drift in repo: {issues}"


# ─── opencode engine: disabled-skill permission mapping ──────────────────────
#
# OpenCode v2 ignores Claude's `disable-model-invocation` field, so a skill
# meant to be user-invoked only would otherwise become silently model-invocable.
# The generator maps that flag to an ordered `permissions` entry with action
# `skill` and effect `ask`.

_DISABLED_SKILL_MD = (
    "---\nname: guard\ndescription: x\ndisable-model-invocation: true\n---\nbody\n"
)


def test_collect_disabled_skills_reads_flag(tmp_path):
    _write(tmp_path / ".kilo" / "skill" / "guard" / "SKILL.md", _DISABLED_SKILL_MD)
    _write(
        tmp_path / ".kilo" / "skill" / "open" / "SKILL.md",
        "---\nname: open\ndescription: y\n---\nbody\n",
    )
    assert opencode_engine.collect_disabled_skills(tmp_path / ".kilo") == {
        "guard": "ask"
    }


def test_collect_disabled_skills_ignores_false_and_absent(tmp_path):
    _write(
        tmp_path / ".kilo" / "skill" / "a" / "SKILL.md",
        "---\ndescription: x\ndisable-model-invocation: false\n---\nbody\n",
    )
    assert opencode_engine.collect_disabled_skills(tmp_path / ".kilo") == {}


def test_permission_block_comment_is_not_a_rule():
    """A hash comment inside the permission block must not become a permission."""
    frontmatter = (
        "mode: primary\n"
        "permission:\n"
        "  read: allow\n"
        "  # orchestrates only: enforced, not advisory\n"
        "  edit: deny\n"
    )

    entries = opencode_engine._parse_permission_block(frontmatter)[1]

    assert [key for key, _ in entries] == ["read", "edit"]
    assert ("edit", [("*", "deny")]) in entries


def test_generated_opencode_json_gates_disabled_skills(tmp_path):
    kilo = tmp_path / ".kilo"
    _write(kilo / "skill" / "guard" / "SKILL.md", _DISABLED_SKILL_MD)
    disabled = opencode_engine.collect_disabled_skills(kilo)

    opencode_engine.generate_opencode_json(tmp_path / ".opencode", tmp_path, disabled)

    data = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    # `jev` orchestrates and can spawn solo-code-engineer via its `task:` block, so
    # the orchestrator is the default and the executor stays reachable.
    assert data["default_agent"] == "jev"
    # The jev agent runs on the model named Jev (TypeSafe's router, on OpenRouter)
    # when the catalog declares it; otherwise the harness's default executor.
    expected_jev_model = (
        "openrouter/jev-router"
        if "jev-router" in opencode_engine._OPENROUTER_MODELS
        else "commandcode/deepseek-v4.1-flash"
    )
    assert data["agents"]["jev"]["model"] == expected_jev_model
    # Legacy `permission` object (Kilo's bundled OpenCode v1 rejects `permissions`).
    assert "permissions" not in data
    # v2-only `providers` is split out of the root file into .opencode/.
    assert "providers" not in data
    local = json.loads(
        (tmp_path / ".opencode" / "opencode.json").read_text(encoding="utf-8")
    )
    assert "commandcode" in local["providers"]
    assert "freemodel" in local["providers"]
    freemodel = local["providers"]["freemodel"]
    assert freemodel["models"]["gpt-6-sol"]["modelID"] == "gpt-6-sol"
    assert "gpt-6.1-sol" in freemodel["models"]
    assert "gpt-6-luna" not in freemodel["models"]
    # baseURL must carry /v1 (the launcher strips it from the env var), and auth
    # must use a dedicated var rather than OPENAI_API_KEY.
    assert freemodel["settings"]["baseURL"] == "${OPENAI_BASE_URL}/v1"
    assert freemodel["env"] == ["FREEMODEL_API_KEY"]
    skill_rules = data["permission"]["skill"]
    assert list(skill_rules)[0] == "*"
    assert skill_rules["*"] == "allow"
    assert skill_rules["guard"] == "ask"


def test_generated_opencode_json_omits_skill_block_without_flags(tmp_path):
    opencode_engine.generate_opencode_json(tmp_path / ".opencode", tmp_path, {})
    data = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    assert "skill" not in data["permission"]
    assert "providers" not in data


# ─── opencode engine: instruction -> on-demand skill mapping ─────────────────


def test_generate_instruction_skills_emits_frontmatter(tmp_path):
    kilo = tmp_path / ".kilo"
    _write(
        kilo / "instruction" / "rules-python.md",
        "# Python Rules\n\n> Auto-loaded when editing .py files.\n\nBody line.\n",
    )
    assert (
        opencode_engine.generate_instruction_skills(kilo, tmp_path / ".opencode") == 0
    )
    doc = (tmp_path / ".opencode" / "skills" / "rules-python" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert doc.startswith("---\n")
    assert 'name: "Python Rules"' in doc
    assert 'description: "Auto-loaded when editing .py files."' in doc
    assert "Body line." in doc


def test_generate_instruction_skills_prunes_removed_source(tmp_path):
    kilo = tmp_path / ".kilo"
    _write(kilo / "instruction" / "a.md", "# A\n\n> d\n\nbody\n")
    assert (
        opencode_engine.generate_instruction_skills(kilo, tmp_path / ".opencode") == 0
    )
    assert (tmp_path / ".opencode" / "skills" / "a" / "SKILL.md").exists()
    (kilo / "instruction" / "a.md").unlink()
    assert (
        opencode_engine.generate_instruction_skills(kilo, tmp_path / ".opencode") == 0
    )
    assert not (tmp_path / ".opencode" / "skills" / "a").exists()
