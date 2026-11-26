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
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import garden, generate_harness, opencode_engine  # noqa: E402

MIRRORS = [
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
    src, dst = root / ".kilo", root / ".gemini" / "antigravity"
    _write(dst / "instruction" / "rules-git.md", "DRIFTED\n")

    assert garden.check_instruction_content(src, dst, ".gemini"), (
        "expected garden to report drift before sync"
    )
    generate_harness.sync_instruction_mirrors(src, root)
    assert not garden.check_instruction_content(src, dst, ".gemini"), (
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


def test_memory_md_is_under_the_hard_cap():
    """MEMORY.md is injected every session and blocked by memory_gate at 8k.

    Checked for every engine copy, since the hook reads .claude/memory/ while
    .kilo/ is the source people edit.
    """
    for engine in (".kilo",):
        f = ROOT / engine / "memory" / "MEMORY.md"
        if not f.is_file():
            continue
        n = len(f.read_text(encoding="utf-8"))
        assert n < 8000, f"{engine}/memory/MEMORY.md is {n} chars (hard cap 8000)"


# ─── skill body mirrors ──────────────────────────────────────────────────────

SKILL_MIRRORS = [
    (".gemini/antigravity", Path(".gemini") / "antigravity" / "skills"),
]


def _fake_skill_tree(tmp_path: Path) -> Path:
    _write(
        tmp_path / ".kilo" / "skill" / "plan" / "SKILL.md",
        "---\ndescription: Kilo plan\n---\n# Plan\nCommon instruction body.\n",
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
        root / ".gemini" / "antigravity" / "skills" / "plan" / "SKILL.md",
        "---\ndescription: Gemini plan\n---\n# Plan\nDRIFTED body.\n",
    )

    synced = generate_harness.sync_skill_body_mirrors(root / ".kilo", root)
    assert synced == len(SKILL_MIRRORS)

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
    # the solocode-orchestrator agent orchestrates and can spawn solo-code-engineer via its `task:` block, so
    # the orchestrator is the default and the executor stays reachable.
    assert data["default_agent"] == "solocode-orchestrator"
    # The orchestrator agent runs on the free DeepSeek V4.1 Flash.
    expected_orchestrator_model = "opencode-go/deepseek-v4.1-flash"
    assert (
        data["agents"]["solocode-orchestrator"]["model"] == expected_orchestrator_model
    )
    # Legacy `permission` object (Kilo's bundled OpenCode v1 rejects `permissions`).
    assert "permissions" not in data
    # v2-only `providers` is split out of the root file into .opencode/.
    assert "providers" not in data
    local = json.loads(
        (tmp_path / ".opencode" / "opencode.json").read_text(encoding="utf-8")
    )
    assert "deepseek" in local["providers"]
    assert "freemodel" not in local["providers"]
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

    # The name is the slug, not the heading: the spec rejects a human title here.
    assert 'name: "rules-python"' in doc
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


def test_harness_opencode_run_generates_the_opencode_engine(tmp_path, monkeypatch):
    """`--harness opencode` must reach the OpenCode engine and report its status."""
    calls = []

    class _StubEngine:
        @staticmethod
        def generate_all(kilo_root, opencode_dir, root, skip_names=None):
            calls.append((kilo_root, opencode_dir, root))
            return 3

    monkeypatch.setattr(generate_harness, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(generate_harness, "_load_opencode_engine", lambda: _StubEngine)
    monkeypatch.setattr(
        sys, "argv", ["generate_harness.py", "--harness", "opencode", "--include-all"]
    )

    assert generate_harness.main() == 3
    assert calls == [(tmp_path / ".kilo", tmp_path / ".opencode", tmp_path)]


# ─── engine imports and skip list ────────────────────────────────────────────
#
# generate_harness is also run as `python tools/generate_harness.py`, where only
# tools/ itself is on sys.path. The engine modules are siblings, so the loader
# has to add that directory before importing them.


def test_load_opencode_engine_adds_tools_to_sys_path(monkeypatch):
    monkeypatch.setattr(
        sys,
        "path",
        [p for p in sys.path if p != str(generate_harness.TOOLS_DIR)],
    )

    engine = generate_harness._load_opencode_engine()

    assert hasattr(engine, "generate_all")
    assert str(generate_harness.TOOLS_DIR) in sys.path


def test_load_skip_list_warns_and_copies_everything_without_a_file(tmp_path, capsys):
    assert generate_harness.load_skip_list(tmp_path / "absent.txt") == set()

    out = capsys.readouterr().out
    assert "[WARN] Skip file not found" in out
    assert "All skills will be copied" in out


def test_load_skip_list_names_each_entry(tmp_path, capsys):
    skip_file = tmp_path / "skip.txt"
    skip_file.write_text("alpha\n\n  beta  \n", encoding="utf-8")

    assert generate_harness.load_skip_list(skip_file) == {"alpha", "beta"}

    out = capsys.readouterr().out
    assert "Loaded 2 skip entries" in out
    assert "- alpha" in out
    assert "- beta" in out


def test_split_frontmatter_without_a_block_keeps_the_whole_text():
    assert generate_harness._split_frontmatter("# Title\nbody\n") == (
        "",
        "# Title\nbody\n",
    )


# ─── agent mirrors ───────────────────────────────────────────────────────────

AGENT_MIRRORS = [
    (".gemini/antigravity", Path(".gemini") / "antigravity" / "agents"),
]


def _fake_agent_tree(tmp_path: Path) -> Path:
    _write(tmp_path / ".kilo" / "agents" / "worker.md", "SOURCE\n")
    for _, rel in AGENT_MIRRORS:
        _write(tmp_path / rel / "worker.md", "DRIFTED\n")
    return tmp_path


def test_agent_sync_repairs_drifted_mirrors(tmp_path):
    root = _fake_agent_tree(tmp_path)

    assert generate_harness.sync_agent_mirrors(root / ".kilo", root) == len(
        AGENT_MIRRORS
    )

    for _, rel in AGENT_MIRRORS:
        assert (root / rel / "worker.md").read_text(encoding="utf-8") == "SOURCE\n"


def test_agent_sync_is_idempotent(tmp_path):
    root = _fake_agent_tree(tmp_path)
    generate_harness.sync_agent_mirrors(root / ".kilo", root)

    assert generate_harness.sync_agent_mirrors(root / ".kilo", root) == 0


def test_agent_sync_skips_an_absent_source_dir(tmp_path):
    assert generate_harness.sync_agent_mirrors(tmp_path / ".kilo", tmp_path) == 0


def test_agent_sync_skips_absent_mirror_dirs(tmp_path):
    _write(tmp_path / ".kilo" / "agents" / "worker.md", "SOURCE\n")

    assert generate_harness.sync_agent_mirrors(tmp_path / ".kilo", tmp_path) == 0


def test_module_entry_point_exits_with_the_main_status(monkeypatch, capsys):
    """`python tools/generate_harness.py` must exit through main(), not off the end."""
    monkeypatch.setattr(sys, "argv", ["generate_harness.py", "--help"])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(generate_harness.__file__), run_name="__main__")

    assert excinfo.value.code == 0
    assert "--harness" in capsys.readouterr().out


def test_agents_skills_are_generated_for_any_harness(tmp_path, monkeypatch):
    """The vendor-neutral skills copy must not depend on the Claude engine run."""
    _write(
        tmp_path / ".kilo" / "skill" / "demo" / "SKILL.md",
        "---\nname: demo\n---\nbody\n",
    )
    monkeypatch.setattr(generate_harness, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["generate_harness.py", "--harness", "opencode"])

    # The engine run may exit non-zero on a minimal tree; what matters here is
    # that the vendor-neutral copy is produced regardless of --harness.
    generate_harness.main()
    assert (tmp_path / ".agents" / "skills" / "demo" / "SKILL.md").is_file()
