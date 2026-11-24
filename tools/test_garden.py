#!/usr/bin/env python3
"""
Garden Drift-Detection Tests
============================
Unit tests for tools/garden.py's content-level drift checks. Filename-only
parity (a file exists in both .kilo/ and a mirror engine) previously let
real content drift (e.g. .claude/memory/MEMORY.md 19 lines behind source,
or .copilot/.gemini skill bodies silently missing whole sections) go
undetected for a long time. These tests guard the fix: check_memory(),
check_instruction_content(), and check_skill_content() must diff actual
file content, not just names.

Usage:
    python -m pytest tools/test_garden.py -v
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import garden  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ─── check_memory (content diff) ────────────────────────────────────────────


def test_check_memory_detects_content_drift(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "memory" / "MEMORY.md", "# Memory\n- [decision] A\n")
    _write(dst / "memory" / "MEMORY.md", "# Memory\n- [decision] A\n- [decision] B\n")
    issues = garden.check_memory(src, dst, ".example")
    assert any("Content drift" in i for i in issues)


def test_check_memory_clean_when_identical(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "memory" / "MEMORY.md", "# Memory\n- [decision] A\n")
    _write(dst / "memory" / "MEMORY.md", "# Memory\n- [decision] A\n")
    assert garden.check_memory(src, dst, ".example") == []


# ─── check_instruction_content ──────────────────────────────────────────────


def test_check_instruction_content_detects_drift(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "instruction" / "rules-git.md", "line one\nline two\n")
    _write(dst / "instruction" / "rules-git.md", "line one\nline TWO (different)\n")
    issues = garden.check_instruction_content(src, dst, ".example")
    assert any("rules-git.md" in i and "Content drift" in i for i in issues)


def test_check_instruction_content_clean_when_identical(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "instruction" / "rules-git.md", "same text\n")
    _write(dst / "instruction" / "rules-git.md", "same text\n")
    assert garden.check_instruction_content(src, dst, ".example") == []


def test_check_instruction_content_missing_dirs_is_silent(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    assert garden.check_instruction_content(src, dst, ".example") == []


# ─── check_skill_content (frontmatter-agnostic) ─────────────────────────────


def test_check_skill_content_ignores_frontmatter_differences(tmp_path):
    """Copilot legitimately uses a different frontmatter schema (quoted
    description + license field) -- that alone must NOT be flagged."""
    src, dst = tmp_path / "src", tmp_path / "dst_skill"
    _write(
        src / "skill" / "plan" / "SKILL.md",
        "---\ndescription: Plan mode.\ndisable-model-invocation: true\n---\n"
        "# Plan\nBody text.\n",
    )
    _write(
        dst / "plan" / "SKILL.md",
        '---\ndescription: "Plan mode."\nlicense: MIT\n---\n# Plan\nBody text.\n',
    )
    assert garden.check_skill_content(src, dst, ".copilot") == []


def test_check_skill_content_detects_body_drift(tmp_path):
    """A real body difference (missing section) must be flagged even though
    frontmatter matches exactly."""
    src, dst = tmp_path / "src", tmp_path / "dst_skill"
    _write(
        src / "skill" / "code-review-expert" / "SKILL.md",
        "---\ndescription: x\n---\n# Title\nParagraph one.\n## Extra Section\nDetail.\n",
    )
    _write(
        dst / "code-review-expert" / "SKILL.md",
        "---\ndescription: x\n---\n# Title\nParagraph one.\n",
    )
    issues = garden.check_skill_content(src, dst, ".copilot")
    assert any("code-review-expert" in i and "Content drift" in i for i in issues)


def test_check_skill_content_missing_dirs_is_silent(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst_skill"
    assert garden.check_skill_content(src, dst, ".copilot") == []


def test_check_skill_content_skips_skills_missing_on_either_side(tmp_path):
    """A skill that only exists on one side is skill-parity's job, not
    content drift's -- must not raise or false-flag here."""
    src, dst = tmp_path / "src", tmp_path / "dst_skill"
    _write(src / "skill" / "only-in-src" / "SKILL.md", "---\n---\nbody\n")
    dst.mkdir(parents=True)
    assert garden.check_skill_content(src, dst, ".copilot") == []


def test_check_skill_content_reports_the_real_destination_dirname(tmp_path):
    """The reported path must name the directory actually checked.

    The Gemini engine mirrors skills into `skills/` (plural) while Copilot
    uses `skill/`. The message hardcoded "/skill/", so every Gemini drift
    pointed at a path that does not exist on disk -- following it led
    nowhere. Every other test here passed a dst named `dst_skill`, so none
    of them could catch it.
    """
    src = tmp_path / "src"
    _write(src / "skill" / "wayfinder" / "SKILL.md", "---\n---\nbody A\n")

    plural = tmp_path / "antigravity" / "skills"
    _write(plural / "wayfinder" / "SKILL.md", "---\n---\nbody B\n")
    issue = garden.check_skill_content(src, plural, ".gemini/antigravity")[0]
    assert ".gemini/antigravity/skills/wayfinder/SKILL.md" in issue

    singular = tmp_path / "copilot" / "skill"
    _write(singular / "wayfinder" / "SKILL.md", "---\n---\nbody B\n")
    issue = garden.check_skill_content(src, singular, ".copilot")[0]
    assert ".copilot/skill/wayfinder/SKILL.md" in issue


def test_check_skill_content_detects_subdirectory_file_drift(tmp_path):
    """A stale shared/*.md file (SKILL.md matches, subdirectory doesn't) must
    be flagged -- this is the exact bug that let 70 lines of drift in
    claude-api/shared/anthropic-cli.md go undetected."""
    src, dst = tmp_path / "src", tmp_path / "dst_skill"
    _write(src / "skill" / "claude-api" / "SKILL.md", "---\n---\nbody\n")
    _write(src / "skill" / "claude-api" / "shared" / "models.md", "current content\n")
    _write(dst / "claude-api" / "SKILL.md", "---\n---\nbody\n")
    _write(dst / "claude-api" / "shared" / "models.md", "stale content\n")
    issues = garden.check_skill_content(src, dst, ".copilot")
    assert any("shared/models.md" in i and "Content drift" in i for i in issues)


def test_check_skill_content_detects_missing_subdirectory_file(tmp_path):
    """A file present in .kilo but absent from the mirror must be reported."""
    src, dst = tmp_path / "src", tmp_path / "dst_skill"
    _write(src / "skill" / "claude-api" / "SKILL.md", "---\n---\nbody\n")
    _write(src / "skill" / "claude-api" / "shared" / "new-file.md", "content\n")
    _write(dst / "claude-api" / "SKILL.md", "---\n---\nbody\n")
    (dst / "claude-api" / "shared").mkdir(parents=True, exist_ok=True)
    issues = garden.check_skill_content(src, dst, ".copilot")
    assert any("shared/new-file.md" in i and "is missing" in i for i in issues)


def test_check_skill_content_detects_extra_stale_subdirectory_file(tmp_path):
    """A file present in a mirror but absent from .kilo (leftover after a
    trim) must be reported."""
    src, dst = tmp_path / "src", tmp_path / "dst_skill"
    _write(src / "skill" / "claude-api" / "SKILL.md", "---\n---\nbody\n")
    _write(dst / "claude-api" / "SKILL.md", "---\n---\nbody\n")
    _write(dst / "claude-api" / "shared" / "leftover.md", "stale\n")
    issues = garden.check_skill_content(src, dst, ".copilot")
    assert any("shared/leftover.md" in i and "no counterpart" in i for i in issues)


def test_check_skill_content_ignores_matching_subdirectory_files(tmp_path):
    """Byte-identical subdirectory files must not be flagged."""
    src, dst = tmp_path / "src", tmp_path / "dst_skill"
    _write(src / "skill" / "claude-api" / "SKILL.md", "---\n---\nbody\n")
    _write(src / "skill" / "claude-api" / "shared" / "models.md", "same\n")
    _write(dst / "claude-api" / "SKILL.md", "---\n---\nbody\n")
    _write(dst / "claude-api" / "shared" / "models.md", "same\n")
    assert garden.check_skill_content(src, dst, ".copilot") == []


# ─── _split_frontmatter ──────────────────────────────────────────────────────


def test_split_frontmatter_with_delimiters():
    text = "---\nkey: value\n---\nBody line.\n"
    fm, body = garden._split_frontmatter(text)
    assert fm == "---\nkey: value\n---\n"
    assert body == "Body line.\n"


def test_split_frontmatter_without_delimiters():
    text = "No frontmatter here.\n"
    fm, body = garden._split_frontmatter(text)
    assert fm == ""
    assert body == text


# ─── Live repo regression guard ─────────────────────────────────────────────


def test_live_repo_has_zero_skill_content_drift():
    kilo = ROOT / ".kilo"
    assert (
        garden.check_skill_content(kilo, ROOT / ".copilot" / "skill", ".copilot") == []
    )
    assert (
        garden.check_skill_content(
            kilo, ROOT / ".gemini" / "antigravity" / "skills", ".gemini/antigravity"
        )
        == []
    )


def test_live_repo_has_zero_instruction_content_drift():
    kilo = ROOT / ".kilo"
    assert garden.check_instruction_content(kilo, ROOT / ".copilot", ".copilot") == []
    assert (
        garden.check_instruction_content(
            kilo, ROOT / ".gemini" / "antigravity", ".gemini/antigravity"
        )
        == []
    )


def _normalise_dashes(text: str) -> str:
    """AGENTS.md uses an em dash where the CLAUDE.md template uses two hyphens."""
    return text.replace("\u2014", "--")


def test_check_doc_counts_detects_wrong_count(tmp_path):
    # Set up .kilo/ structure
    kilo = tmp_path / ".kilo"
    (kilo / "skill" / "skill-a").mkdir(parents=True)
    (kilo / "agents").mkdir(parents=True)
    (kilo / "agents" / "agent-a.md").write_text("body", encoding="utf-8")
    (kilo / "command").mkdir(parents=True)
    (kilo / "command" / "command-a.md").write_text("body", encoding="utf-8")
    (kilo / "instruction").mkdir(parents=True)
    (kilo / "instruction" / "instruction-a.md").write_text("body", encoding="utf-8")

    # Write a test file with a wrong count
    (tmp_path / "AGENTS.md").write_text(
        "Solo-Code Harness active: 2 skills, 1 agent.", encoding="utf-8"
    )

    issues = garden.check_doc_counts(root=tmp_path)
    assert len(issues) > 0
    assert any("claimed 2 skills, but ground truth is 1" in i for i in issues)


def test_check_doc_counts_clean_when_correct(tmp_path):
    # Set up .kilo/ structure
    kilo = tmp_path / ".kilo"
    (kilo / "skill" / "skill-a").mkdir(parents=True)
    (kilo / "agents").mkdir(parents=True)
    (kilo / "agents" / "agent-a.md").write_text("body", encoding="utf-8")
    (kilo / "command").mkdir(parents=True)
    (kilo / "command" / "command-a.md").write_text("body", encoding="utf-8")
    (kilo / "instruction").mkdir(parents=True)
    (kilo / "instruction" / "instruction-a.md").write_text("body", encoding="utf-8")

    # Write a test file with correct count
    (tmp_path / "AGENTS.md").write_text(
        "Solo-Code Harness active: 1 skill, 1 agent.", encoding="utf-8"
    )

    issues = garden.check_doc_counts(root=tmp_path)
    assert issues == []


def _make_engines(tmp_path, *, gemini_commands: int = 2):
    """Build a minimal multi-engine tree.

    .kilo/ gets 1 skill + 1 agent + 1 command + 1 instruction;
    .gemini/antigravity/ gets the same but a configurable command count, so a
    genuine per-engine divergence can be exercised.
    """
    kilo = tmp_path / ".kilo"
    (kilo / "skill" / "skill-a").mkdir(parents=True)
    for sub, name in (
        ("agents", "agent-a.md"),
        ("command", "command-a.md"),
        ("instruction", "instruction-a.md"),
    ):
        (kilo / sub).mkdir(parents=True, exist_ok=True)
        (kilo / sub / name).write_text("body", encoding="utf-8")

    gem = tmp_path / ".gemini" / "antigravity"
    (gem / "skills" / "skill-a").mkdir(parents=True)
    (gem / "agents").mkdir(parents=True)
    (gem / "agents" / "agent-a.md").write_text("body", encoding="utf-8")
    (gem / "commands").mkdir(parents=True)
    for i in range(gemini_commands):
        (gem / "commands" / f"cmd-{i}.md").write_text("body", encoding="utf-8")
    return kilo, gem


def test_check_doc_counts_respects_engine_divergence(tmp_path):
    """A line describing `.gemini/` is measured against .gemini/, not .kilo/.

    Engines legitimately differ — .gemini/ ships fewer commands than .kilo/ —
    so comparing every number to the source of truth reports false drift.
    """
    _make_engines(tmp_path, gemini_commands=2)

    (tmp_path / "README.md").write_text(
        "| `.gemini/` | Gemini: skills (1), commands (2) |\n", encoding="utf-8"
    )

    assert garden.check_doc_counts(root=tmp_path) == []


def test_check_doc_counts_flags_wrong_count_for_named_engine(tmp_path):
    """Per-engine resolution must still catch a count that is wrong *for that engine*."""
    _make_engines(tmp_path, gemini_commands=2)

    (tmp_path / "README.md").write_text(
        "| `.gemini/` | Gemini: skills (1), commands (7) |\n", encoding="utf-8"
    )

    issues = garden.check_doc_counts(root=tmp_path)
    assert any(
        "claimed 7 commands" in i and "ground truth is 2" in i and ".gemini/" in i
        for i in issues
    ), issues


def test_check_doc_counts_unnamed_line_falls_back_to_kilo(tmp_path):
    """A line naming no engine is checked against .kilo/, the source of truth."""
    _make_engines(tmp_path, gemini_commands=2)

    (tmp_path / "AGENTS.md").write_text(
        "Harness active: 1 skill, 2 commands.", encoding="utf-8"
    )

    issues = garden.check_doc_counts(root=tmp_path)
    assert any("claimed 2 commands, but ground truth is 1" in i for i in issues), issues


def test_check_doc_counts_reports_an_unreadable_doc_file(tmp_path, monkeypatch):
    """An unreadable doc file is one issue naming it, never a traceback."""
    _make_engines(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_text("Harness active: 1 skill.", encoding="utf-8")
    real_read_text = Path.read_text

    def _deny(self, *args, **kwargs):
        if self == agents:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _deny)

    issues = garden.check_doc_counts(root=tmp_path)

    assert len(issues) == 1, issues
    assert "Could not read file AGENTS.md" in issues[0]


def test_count_pattern_reads_a_thousands_separator():
    r"""`1,000 agents` is one count of 1000, not a claim about zero agents.

    The ungrouped (\d+) capture started at "000", so a doc line stating the
    runtime's own agent cap was reported as claiming 0 agents against 16.
    """
    match = re.search(
        garden._COUNT_PATTERN + r"\s+agents?\b", "capped at 1,000 agents per run"
    )
    assert match is not None
    assert int(match.group(1).replace(",", "")) == 1000


# ── check_doc_paths ──────────────────────────────────────────────────────


def _doc_paths_fixture(tmp_path):
    """Minimal repo: one real tool + a docs file citing paths."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "real.py").write_text("x", encoding="utf-8")
    return tmp_path


def test_check_doc_paths_detects_missing_path(tmp_path):
    root = _doc_paths_fixture(tmp_path)
    (root / "AGENTS.md").write_text(
        "Run `tools/ghost.py` before shipping.", encoding="utf-8"
    )
    issues = garden.check_doc_paths(root=root)
    assert any("tools/ghost.py" in i for i in issues), issues
    assert any("AGENTS.md:1" in i for i in issues), issues


def test_check_doc_paths_clean_when_path_exists(tmp_path):
    root = _doc_paths_fixture(tmp_path)
    (root / "AGENTS.md").write_text("Run `tools/real.py`.", encoding="utf-8")
    assert garden.check_doc_paths(root=root) == []


def test_check_doc_paths_ignores_generic_examples(tmp_path):
    """`src/models/user.py` is illustrative, not a claim about this repo."""
    root = _doc_paths_fixture(tmp_path)
    (root / "AGENTS.md").write_text(
        "For example, edit `src/models/user.py` or `path/to/file.json`.",
        encoding="utf-8",
    )
    assert garden.check_doc_paths(root=root) == []


def test_check_doc_paths_respects_negation_marker(tmp_path):
    """Docs may name a dead path precisely to warn about it."""
    root = _doc_paths_fixture(tmp_path)
    (root / "AGENTS.md").write_text(
        "The rulebook is at ROOT, not `tools/old_location.py`.", encoding="utf-8"
    )
    assert garden.check_doc_paths(root=root) == []


def test_check_doc_paths_skips_runtime_generated(tmp_path):
    root = _doc_paths_fixture(tmp_path)
    (root / ".solocode").mkdir()
    (root / "AGENTS.md").write_text(
        "Checkpoints land in `.solocode/context-checkpoint.json`.", encoding="utf-8"
    )
    assert garden.check_doc_paths(root=root) == []


def test_check_doc_paths_skips_archives_and_plans(tmp_path):
    """History files describe a past layout; their paths may be gone."""
    root = _doc_paths_fixture(tmp_path)
    (root / "decisions-archive.md").write_text(
        "We removed `tools/gone.py`.", encoding="utf-8"
    )
    (root / "plans").mkdir()
    (root / "plans" / "old.md").write_text("Touch `tools/gone.py`.", encoding="utf-8")
    assert garden.check_doc_paths(root=root) == []


# ── check_doc_flags ──────────────────────────────────────────────────────


def _flag_fixture(tmp_path, script_body: str):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "cli.py").write_text(script_body, encoding="utf-8")
    return tmp_path


_ARGPARSE_CLI = (
    "import argparse\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--real')\n"
    "p.parse_args()\n"
)


def test_check_doc_flags_detects_unknown_flag(tmp_path):
    root = _flag_fixture(tmp_path, _ARGPARSE_CLI)
    (root / "AGENTS.md").write_text(
        "Run `python tools/cli.py --bogus` nightly.", encoding="utf-8"
    )
    issues = garden.check_doc_flags(root=root)
    assert any("--bogus" in i for i in issues), issues


def test_check_doc_flags_accepts_real_flag(tmp_path):
    root = _flag_fixture(tmp_path, _ARGPARSE_CLI)
    (root / "AGENTS.md").write_text(
        "Run `python tools/cli.py --real x`.", encoding="utf-8"
    )
    assert garden.check_doc_flags(root=root) == []


def test_check_doc_flags_accepts_subcommand_flag(tmp_path):
    """argparse hides subcommand flags from the top-level --help."""
    root = _flag_fixture(
        tmp_path,
        (
            "import argparse\n"
            "p = argparse.ArgumentParser()\n"
            "sub = p.add_subparsers(dest='cmd')\n"
            "s = sub.add_parser('sessions')\n"
            "s.add_argument('--limit', type=int)\n"
            "p.parse_args()\n"
        ),
    )
    (root / "AGENTS.md").write_text(
        "Run `python tools/cli.py sessions --limit 10`.", encoding="utf-8"
    )
    assert garden.check_doc_flags(root=root) == []


def test_check_doc_flags_accepts_hand_rolled_argv_flag(tmp_path):
    """Scripts parsing sys.argv by hand expose no --help; source is checked."""
    root = _flag_fixture(
        tmp_path,
        ("import sys\nstrict = '--strict' in sys.argv\nprint('strict:', strict)\n"),
    )
    (root / "AGENTS.md").write_text(
        "Run `python tools/cli.py --strict`.", encoding="utf-8"
    )
    assert garden.check_doc_flags(root=root) == []


# --- check_enforcement_claims ------------------------------------------------


def _enforce_fixture(tmp_path, hook_src: str, doc: str):
    root = tmp_path
    (root / ".kilo" / "hooks").mkdir(parents=True)
    (root / ".kilo" / "hooks" / "gate.js").write_text(hook_src, encoding="utf-8")
    (root / "AGENTS.md").write_text(doc, encoding="utf-8")
    return root


_ADVISORY_HOOK = "console.error('warn');\nprocess.exit(0);\n"
_BLOCKING_HOOK = "console.error('nope');\nprocess.exit(2);\n"


def test_check_enforcement_claims_detects_advisory_hook(tmp_path):
    """The real bug: a doc promising a block from a hook that only exits 0."""
    root = _enforce_fixture(
        tmp_path, _ADVISORY_HOOK, "`gate.js` will block the commit."
    )
    issues = garden.check_enforcement_claims(root=root)
    assert any("gate.js" in i for i in issues), issues


def test_check_enforcement_claims_accepts_real_blocker(tmp_path):
    """A hook that can exit non-zero backs its claim — must stay silent."""
    root = _enforce_fixture(
        tmp_path, _BLOCKING_HOOK, "`gate.js` will block the commit."
    )
    assert garden.check_enforcement_claims(root=root) == []


def test_check_enforcement_claims_detects_vietnamese_claim(tmp_path):
    """Skills in this harness are written in Vietnamese as often as English."""
    root = _enforce_fixture(
        tmp_path, _ADVISORY_HOOK, "Hook `gate.js` sẽ chặn commit của bạn."
    )
    issues = garden.check_enforcement_claims(root=root)
    assert any("gate.js" in i for i in issues), issues


def test_check_enforcement_claims_ignores_prose_without_script(tmp_path):
    """No named script means no checkable claim; stay out of general prose."""
    root = _enforce_fixture(
        tmp_path, _ADVISORY_HOOK, "The review process will block bad commits."
    )
    assert garden.check_enforcement_claims(root=root) == []


def test_check_enforcement_claims_respects_negation_marker(tmp_path):
    """Docs correcting the record must not be punished for naming the script."""
    root = _enforce_fixture(
        tmp_path,
        _ADVISORY_HOOK,
        "`gate.js` no longer blocks the commit — it is advisory only.",
    )
    assert garden.check_enforcement_claims(root=root) == []


# --- check_skill_refs --------------------------------------------------------


def _skillref_fixture(tmp_path, body: str):
    root = tmp_path
    for name in ("plan", "testing-patterns"):
        d = root / ".kilo" / "skill" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    (root / ".kilo" / "agents").mkdir(parents=True)
    (root / ".kilo" / "agents" / "tdd-guide.md").write_text("x", encoding="utf-8")
    (root / ".kilo" / "command").mkdir(parents=True)
    (root / ".kilo" / "skill" / "plan" / "SKILL.md").write_text(body, encoding="utf-8")
    return root


def test_check_skill_refs_detects_phantom_in_prose(tmp_path):
    """The real bug: `x` skill where x never existed."""
    root = _skillref_fixture(tmp_path, "See `test-driven-development` skill.\n")
    issues = garden.check_skill_refs(root=root)
    assert any("test-driven-development" in i for i in issues), issues


def test_check_skill_refs_detects_phantom_in_router_arrow(tmp_path):
    root = _skillref_fixture(
        tmp_path, "    - Writing tests? ----> test-driven-development\n"
    )
    issues = garden.check_skill_refs(root=root)
    assert any("test-driven-development" in i for i in issues), issues


def test_check_skill_refs_detects_phantom_in_path_form(tmp_path):
    root = _skillref_fixture(
        tmp_path, "Follow `skills/test-driven-development/SKILL.md`.\n"
    )
    issues = garden.check_skill_refs(root=root)
    assert any("test-driven-development" in i for i in issues), issues


def test_check_skill_refs_accepts_real_skill(tmp_path):
    root = _skillref_fixture(tmp_path, "See `testing-patterns` skill.\n")
    assert garden.check_skill_refs(root=root) == []


def test_check_skill_refs_accepts_agent_name(tmp_path):
    """Skills legitimately reference agents, not just other skills."""
    root = _skillref_fixture(tmp_path, "Delegate to `tdd-guide` skill.\n")
    assert garden.check_skill_refs(root=root) == []


def test_check_skill_refs_ignores_pipeline_diagram(tmp_path):
    """`decompose -> research -> build -> verdict` are stages, not skills.

    Requiring a `?` on the line is what separates a routing decision from a
    flow diagram; without it this produced a false positive on spike/.
    """
    root = _skillref_fixture(
        tmp_path, "```\ndecompose  ----> research  ----> verdict\n```\n"
    )
    assert garden.check_skill_refs(root=root) == []


def test_check_skill_refs_ignores_npm_package(tmp_path):
    """A first draft flagged 190 npm packages and YAML keys as skills."""
    root = _skillref_fixture(
        tmp_path, "Install `express-rate-limit` and set disable-model-invocation.\n"
    )
    assert garden.check_skill_refs(root=root) == []


def test_check_doc_flags_detects_missing_script(tmp_path):
    """A fenced `python tools/x.py --flag` where x.py is in the wrong dir.

    check_doc_paths() only reads backticked paths, so a command inside a
    ```bash fence was invisible to both checks -- gate-check.md ran
    `python tools/eval_harness.py --min-score 60` when the script lives in
    .github/scripts/ and no such flag ever existed.
    """
    root = _flag_fixture(tmp_path, _ARGPARSE_CLI)
    (root / "AGENTS.md").write_text(
        "```bash\npython tools/missing.py --real\n```", encoding="utf-8"
    )
    issues = garden.check_doc_flags(root=root)
    assert any("no such script" in i for i in issues), issues


# --- check_pattern_counts ----------------------------------------------------


_GUARD_SRC = """\
BLOCK_PATTERNS = [
    ("a", 1),
    ("b", 2),
    ("c", 3),
]

SECRET_PATTERNS: list[tuple[str, str]] = [
    ("x", "1"),
    ("y", "2"),
]
"""


def _pattern_fixture(tmp_path, doc: str):
    root = tmp_path
    # The pattern lists moved to tools/guard_rules.py on 2026-11-23; the fixture
    # follows the source of truth, not the old hook path.
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "guard_rules.py").write_text(_GUARD_SRC, encoding="utf-8")
    (root / "README.md").write_text(doc, encoding="utf-8")
    return root


def test_check_pattern_counts_detects_understated_claim(tmp_path):
    """The real bug: commit 340ae20 added 6 secret patterns, README kept 15."""
    root = _pattern_fixture(tmp_path, "3 destructive patterns + 1 secret patterns\n")
    issues = garden.check_pattern_counts(root=root)
    assert any("SECRET_PATTERNS" in i for i in issues), issues


def test_check_pattern_counts_detects_overstated_claim(tmp_path):
    """The dangerous direction: promising more coverage than exists."""
    root = _pattern_fixture(tmp_path, "99 destructive patterns\n")
    issues = garden.check_pattern_counts(root=root)
    assert any("BLOCK_PATTERNS" in i for i in issues), issues


def test_check_pattern_counts_accepts_correct_claim(tmp_path):
    """Accurate prose must stay silent, or the check gets switched off."""
    root = _pattern_fixture(tmp_path, "3 destructive patterns + 2 secret patterns\n")
    assert garden.check_pattern_counts(root=root) == []


def test_check_pattern_counts_ignores_missing_source(tmp_path):
    """No guard.py (e.g. a deployed target) — must not invent drift."""
    root = tmp_path
    (root / "README.md").write_text("33 destructive patterns\n", encoding="utf-8")
    assert garden.check_pattern_counts(root=root) == []


def test_check_pattern_counts_matches_live_repo():
    """Pins the live README against the live guard.py, both directions."""
    assert garden.check_pattern_counts(root=garden.ROOT) == []


# --- check_launcher_defaults -------------------------------------------------


# Verbatim shape of the real bug (claude-env.ps1 before 4bc5bdb): the arg
# loop can clear the flag, but the no-arg path hardcodes it anyway.
_LAUNCHER_UNCONDITIONAL = """\
$useBare = $true
if ($normalizedArgs.Count -gt 0) {
    & claude @normalizedArgs
}
else {
    & claude --bare
}
"""

# The fix: --bare only reaches the process when the user asked for it.
_LAUNCHER_OPTIN = """\
$useBare = $false
foreach ($arg in $claudeArgs) {
    if ($arg -eq "--bare") { $useBare = $true }
}
if ($useBare) {
    Write-Warning "--bare: hooks are DISABLED for this session."
}
& claude @normalizedArgs
"""


def _launcher_fixture(tmp_path, src: str, name: str = "claude-env.ps1"):
    (tmp_path / name).write_text(src, encoding="utf-8")
    return tmp_path


def test_check_launcher_defaults_detects_unconditional_bare(tmp_path):
    """The real bug: `& claude --bare` on the no-arg path, hooks off."""
    root = _launcher_fixture(tmp_path, _LAUNCHER_UNCONDITIONAL)
    issues = garden.check_launcher_defaults(root=root)
    assert any("--bare" in i for i in issues), issues


def test_check_launcher_defaults_accepts_optin_bare(tmp_path):
    """Supporting --bare as an opt-in is legitimate — must stay silent."""
    root = _launcher_fixture(tmp_path, _LAUNCHER_OPTIN)
    assert garden.check_launcher_defaults(root=root) == []


def test_check_launcher_defaults_detects_safe_mode(tmp_path):
    """--safe-mode disables hooks, skills, agents, commands and MCP."""
    root = _launcher_fixture(tmp_path, "& claude --safe-mode\n")
    issues = garden.check_launcher_defaults(root=root)
    assert any("--safe-mode" in i for i in issues), issues


def test_check_launcher_defaults_ignores_comments(tmp_path):
    """A comment explaining --bare is documentation, not an invocation."""
    root = _launcher_fixture(
        tmp_path, "# Default to --bare unless overridden.\n& claude\n"
    )
    assert garden.check_launcher_defaults(root=root) == []


def test_check_launcher_defaults_ignores_unrelated_script(tmp_path):
    """A script that never launches an engine is out of scope."""
    root = _launcher_fixture(tmp_path, "Write-Host '--bare'\n", name="notes.ps1")
    assert garden.check_launcher_defaults(root=root) == []


def test_check_launcher_defaults_matches_live_repo():
    """Pins the live launchers: neither may disable the harness by default."""
    assert garden.check_launcher_defaults(root=garden.ROOT) == []


# --- check_api_key_approval --------------------------------------------------


def _write_key_config(tmp_path, approved, rejected):
    """Point HOME at a temp dir holding a synthetic ~/.claude.json."""
    cfg = tmp_path / ".claude.json"
    cfg.write_text(
        json.dumps(
            {"customApiKeyResponses": {"approved": approved, "rejected": rejected}}
        ),
        encoding="utf-8",
    )
    return cfg


# ─── _doc_scan_skip_dirs (reference-repo exclusion) ─────────────────────────
#
# Reference repos (unpacked upstream projects) used to be excluded by a
# hardcoded name list. That list named 17 repos -- all since deleted -- while
# the one repo actually on disk was absent from it, so the doc-scanning checks
# walked it and reported 169 drift issues that were not the harness's own.
# These tests pin the replacement signal: zero tracked files == reference repo.


def _git(tmp_path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True, check=True)


def _init_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")


def test_doc_scan_skips_untracked_reference_repo(tmp_path):
    _init_repo(tmp_path)
    _write(tmp_path / "tools" / "real.py", "x = 1\n")
    _git(tmp_path, "add", "tools/real.py")
    _git(tmp_path, "commit", "-qm", "init")
    _write(tmp_path / "vendored-repo" / "SKILL.md", "# unpacked upstream\n")

    skip = garden._doc_scan_skip_dirs(tmp_path)

    assert "vendored-repo" in skip


def test_doc_scan_keeps_tracked_dir_holding_untracked_files(tmp_path):
    """`git ls-files --others --directory` collapses a fully-untracked *nested*
    directory into a `tools/newsub/` entry; taking its first path segment
    promotes that to `tools` and excludes the entire harness directory.
    Adding one new unstaged subdirectory under tools/ or .claude/ -- routine
    during development -- would have silenced the gate over that whole tree.
    Only directories with zero tracked files anywhere beneath them may be
    skipped."""
    _init_repo(tmp_path)
    _write(tmp_path / "tools" / "real.py", "x = 1\n")
    _git(tmp_path, "add", "tools/real.py")
    _git(tmp_path, "commit", "-qm", "init")
    # A brand-new, entirely unstaged subdirectory -- git collapses this to
    # `tools/newsub/`, whose first segment is the tracked dir `tools`.
    _write(tmp_path / "tools" / "newsub" / "a.py", "x = 1\n")

    skip = garden._doc_scan_skip_dirs(tmp_path)

    assert "tools" not in skip


def test_doc_scan_falls_back_to_static_set_outside_git(tmp_path):
    """Outside a git repo the scan must check everything rather than silently
    skipping real drift."""
    _write(tmp_path / "somedir" / "a.md", "# x\n")

    skip = garden._doc_scan_skip_dirs(tmp_path)

    assert skip == garden._SCAN_SKIP_DIRS
    assert "somedir" not in skip


# ─── check_agent_permissions / check_claude_agent_tools ─────────────────────

_AGENT_ALLOW = """---
name: reviewer
permission:
  edit: allow
  read: allow
---

Body.
"""

_AGENT_DENY = """---
name: reviewer
permission:
  edit: deny
  read: allow
---

Body.
"""


def test_permission_block_keeps_scalar_and_nested_rules(tmp_path):
    agent = tmp_path / "a.md"
    _write(
        agent,
        "---\nname: a\npermission:\n  edit: allow\n  bash:\n"
        '    "*": deny\n    "git diff*": allow\n---\nbody\n',
    )

    assert garden._permission_block(agent) == (
        'permission:\n  edit: allow\n  bash:\n    "*": deny\n    "git diff*": allow'
    )


def test_permission_block_absent_is_empty(tmp_path):
    agent = tmp_path / "a.md"
    _write(agent, "---\nname: a\n---\nbody\n")

    assert garden._permission_block(agent) == ""


def test_permission_block_stops_at_next_top_level_key(tmp_path):
    agent = tmp_path / "a.md"
    _write(agent, '---\npermission:\n  edit: allow\ncolor: "#fff"\n---\nbody\n')

    assert garden._permission_block(agent) == "permission:\n  edit: allow"


def test_check_agent_permissions_detects_an_edit_that_reached_one_engine(tmp_path):
    """The regression this check exists for: `edit: deny` -> `edit: allow` landed
    in .kilo/ and .opencode/ while .copilot/ kept the old value, and the older
    filename-only parity check reported the engine clean."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "agents" / "reviewer.md", _AGENT_ALLOW)
    _write(dst / "agents" / "reviewer.md", _AGENT_DENY)

    issues = garden.check_agent_permissions(src, dst, ".copilot")

    assert len(issues) == 1
    assert "Permission drift" in issues[0]
    assert "reviewer.md" in issues[0]


def test_check_agent_permissions_clean_when_identical(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "agents" / "reviewer.md", _AGENT_ALLOW)
    _write(dst / "agents" / "reviewer.md", _AGENT_ALLOW)

    assert garden.check_agent_permissions(src, dst, ".copilot") == []


def test_check_agent_permissions_defers_missing_copy_to_check_agents(tmp_path):
    """A missing mirror file is check_agents()' finding; this check must not
    duplicate it, or every new agent would report twice."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "agents" / "reviewer.md", _AGENT_ALLOW)
    (dst / "agents").mkdir(parents=True)

    assert garden.check_agent_permissions(src, dst, ".copilot") == []


def test_check_agent_permissions_silent_when_dirs_absent(tmp_path):
    assert (
        garden.check_agent_permissions(tmp_path / "src", tmp_path / "dst", ".x") == []
    )


def test_live_repo_has_zero_agent_permission_drift():
    kilo = ROOT / ".kilo"
    for engine_dir, label in (
        (ROOT / ".copilot", ".copilot"),
        (ROOT / ".gemini" / "antigravity", ".gemini/antigravity"),
    ):
        issues = garden.check_agent_permissions(kilo, engine_dir, label)
        assert issues == [], f"{label} agent permission drift: {issues}"


# --- check_no_symlinks -------------------------------------------------------
#
# Every other check here compares file content, and content is what the OS
# returns after following the path -- so a symlink inside a mirror makes a
# parity check pass while pointing outside the repo. These tests pin the
# structural check that reads the path itself instead of the bytes.


def _symlink_fixture(tmp_path):
    """Minimal repo: one scanned harness dir plus tools/, each with one file."""
    root = tmp_path / "repo"
    _write(root / ".kilo" / "agents" / "a.md", "body\n")
    _write(root / "tools" / "real.py", "x = 1\n")
    return root


def test_check_no_symlinks_clean_tree_reports_nothing(tmp_path):
    assert garden.check_no_symlinks(_symlink_fixture(tmp_path)) == []


def test_check_no_symlinks_detects_symlink(tmp_path, monkeypatch):
    """A symlink is reported with its path and what it points at.

    Windows refuses symlink creation without developer mode (OSError). Rather
    than skip -- the no-skips gate rejects that -- the same entry is made to
    report itself as a symlink, so the issue path is exercised everywhere.
    """
    root = _symlink_fixture(tmp_path)
    target = root / ".kilo" / "agents" / "a.md"
    link = root / ".kilo" / "agents" / "linked.md"
    try:
        link.symlink_to(target)
    except OSError:
        _write(link, target.read_text(encoding="utf-8"))
        real_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path, "is_symlink", lambda self: self == link or real_is_symlink(self)
        )

    issues = garden.check_no_symlinks(root)

    assert any(
        i.startswith("Symlink in harness tree: .kilo/agents/linked.md ->")
        for i in issues
    ), issues


def test_check_no_symlinks_detects_path_resolving_outside_root(tmp_path, monkeypatch):
    """A Windows junction redirects without being a symlink, so resolution is
    checked for every entry, not only for entries is_symlink() flags. That is
    reproduced here by resolving one real file to a real path outside the
    root, which stays deterministic on every platform."""
    root = _symlink_fixture(tmp_path)
    escapee = root / "tools" / "escapee.py"
    _write(escapee, "x = 1\n")
    outside = tmp_path / "outside" / "escapee.py"
    _write(outside, "x = 1\n")

    real_resolve = Path.resolve

    def _fake_resolve(self, *args, **kwargs):
        return outside if self == escapee else real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _fake_resolve)

    issues = garden.check_no_symlinks(root)

    assert any(
        "Path resolves outside the repo: tools/escapee.py ->" in i for i in issues
    ), issues


# ─── check_harness_fingerprint (.harness.lock content digest) ───────────────


def _fingerprint_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _write(repo / "tools" / "a.py", "a = 1\n")
    _write(repo / "tools" / "b.py", "b = 2\n")
    return repo


def _write_fingerprint_lock(
    repo: Path, paths: list[str], *, mode: str | None = "root"
) -> None:
    from tools import deploy

    digest = deploy.harness_content_fingerprint(paths, repo)
    listed = "".join(f'    "{path}",\n' for path in paths)
    mode_line = f'mode = "{mode}"\n' if mode is not None else ""
    _write(
        repo / ".harness.lock",
        "[harness]\n"
        "version = 4.3.1\n"
        f"{mode_line}"
        f"content_fingerprint = {digest}\n"
        "\n"
        "[shared_files]\n"
        "paths = [\n"
        f"{listed}"
        "]\n",
    )


def test_check_harness_fingerprint_clean_tree_is_silent(tmp_path):
    repo = _fingerprint_repo(tmp_path)
    _write_fingerprint_lock(repo, ["tools/a.py", "tools/b.py"])

    assert garden.check_harness_fingerprint(repo) == []


def test_check_harness_fingerprint_detects_an_edited_file(tmp_path):
    repo = _fingerprint_repo(tmp_path)
    _write_fingerprint_lock(repo, ["tools/a.py", "tools/b.py"])

    _write(repo / "tools" / "b.py", "b = 3\n")

    issues = garden.check_harness_fingerprint(repo)
    assert len(issues) == 1, issues
    assert "run python tools/deploy.py --root-lock" in issues[0]


def test_check_harness_fingerprint_reports_a_missing_field(tmp_path):
    repo = _fingerprint_repo(tmp_path)
    _write(repo / ".harness.lock", '[harness]\nversion = "4.3.1"\n')

    issues = garden.check_harness_fingerprint(repo)

    assert any("content_fingerprint" in i for i in issues), issues


def test_check_harness_fingerprint_reports_an_ambiguous_lock(tmp_path):
    """Two [shared_files] sections must be reported, never silently resolved."""
    repo = _fingerprint_repo(tmp_path)
    _write(
        repo / ".harness.lock",
        "[harness]\n"
        'content_fingerprint = "sha256:deadbeef"\n'
        "\n"
        "[shared_files]\n"
        'paths = ["tools/a.py"]\n'
        "\n"
        "[shared_files]\n"
        'paths = ["tools/b.py"]\n',
    )

    issues = garden.check_harness_fingerprint(repo)

    assert any("shared_files" in issue for issue in issues), issues


def test_check_harness_fingerprint_mode_decides_whether_drift_fails(tmp_path, capsys):
    """Target mode warns, root mode fails, and no mode fails closed."""
    repo = _fingerprint_repo(tmp_path)
    paths = ["tools/a.py", "tools/b.py"]
    _write_fingerprint_lock(repo, paths, mode="target")
    _write(repo / "tools" / "b.py", "b = 3\n")

    assert garden.check_harness_fingerprint(repo) == []
    out = capsys.readouterr().out
    assert "WARN" in out
    assert "advisory" in out

    lock = repo / ".harness.lock"
    _write(
        lock,
        lock.read_text(encoding="utf-8").replace('mode = "target"', 'mode = "root"'),
    )
    root_issues = garden.check_harness_fingerprint(repo)
    assert len(root_issues) == 1, root_issues
    assert "content changed" in root_issues[0]

    _write(lock, lock.read_text(encoding="utf-8").replace('mode = "root"\n', ""))
    no_mode_issues = garden.check_harness_fingerprint(repo)
    assert any("mode" in issue for issue in no_mode_issues), no_mode_issues
    assert any("content changed" in issue for issue in no_mode_issues), no_mode_issues


def test_check_harness_fingerprint_reports_only_the_missing_path(tmp_path):
    """One missing declared file is one issue, not a bogus digest mismatch too."""
    repo = _fingerprint_repo(tmp_path)
    _write_fingerprint_lock(repo, ["tools/a.py", "tools/b.py"], mode="root")
    (repo / "tools" / "b.py").unlink()

    issues = garden.check_harness_fingerprint(repo)

    assert len(issues) == 1, issues
    assert "tools/b.py" in issues[0]
    assert "content changed" not in issues[0]


# ─── Gemini command parity (documented exceptions) ──────────────────────────


def _gemini_commands_fixture(tmp_path, src_commands, dst_commands):
    """Fake .kilo/command + .gemini/antigravity/commands trees for parity tests."""
    src, dst = tmp_path / ".kilo", tmp_path / ".gemini" / "antigravity"
    for name in src_commands:
        _write(src / "command" / f"{name}.md", "body\n")
    for name in dst_commands:
        _write(dst / "commands" / f"{name}.md", "body\n")
    return src, dst


def test_check_gemini_commands_ignore_documented_exceptions(tmp_path, monkeypatch):
    """The two engine-specific commands and Antigravity's own code-review are
    declared exceptions, so they must not be reported as command drift."""
    monkeypatch.setattr(garden, "ROOT", tmp_path)
    src, dst = _gemini_commands_fixture(
        tmp_path,
        {"commit", "commit-push-pr", "decide", "review", "ship"},
        {"code-review", "commit", "commit-push-pr", "decide"},
    )

    assert garden.check_gemini(src, dst) == []


def test_check_gemini_commands_flag_undeclared_divergence(tmp_path, monkeypatch):
    """A divergence outside the declared exceptions is real drift, named by file."""
    monkeypatch.setattr(garden, "ROOT", tmp_path)
    src, dst = _gemini_commands_fixture(
        tmp_path,
        {"commit", "commit-push-pr", "decide", "enhance", "review", "ship"},
        {"code-review", "commit", "ghost"},
    )

    issues = garden.check_gemini(src, dst)

    assert any(
        "Missing command: .gemini/antigravity/commands/enhance.md" in i for i in issues
    ), issues
    assert any(
        "Stale command (no source): .gemini/antigravity/commands/ghost.md" in i
        for i in issues
    ), issues


# ─── check_commands (body comparison) ───────────────────────────────────────


def test_command_body_drift_is_detected(tmp_path):
    """Names alone stayed green while a mirror carried a stale body."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "command" / "one.md", "---\ntask: x\n---\nline one\n")
    _write(dst / "command" / "one.md", "---\ntask: x\n---\nline one\nline two\n")

    issues = garden.check_commands(src, dst, ".copilot", compare_body=True)

    assert issues
    assert any("Content drift" in i for i in issues)


def test_command_frontmatter_difference_is_not_drift(tmp_path):
    """Frontmatter legitimately differs per engine; identical bodies are clean."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "command" / "one.md", "---\ntask: x\n---\nline one\n")
    _write(dst / "command" / "one.md", "---\nmode: write\n---\nline one\n")

    assert garden.check_commands(src, dst, ".copilot", compare_body=True) == []


def test_command_body_keeps_text_after_a_fence_lookalike_line(tmp_path):
    """A line starting with "---" is body text, not a closing fence."""
    src = tmp_path / "src"
    _write(src / "command" / "one.md", "---\ntask: x\n---extra\nbody\n")

    assert (
        garden._command_body(src / "command" / "one.md")
        == "---\ntask: x\n---extra\nbody\n"
    )


# --- Instruction size caps -------------------------------------------------


def test_check_instruction_caps_fails_an_oversized_agents_md(tmp_path):
    _write(tmp_path / "AGENTS.md", "x" * (32_768 + 1))

    issues = garden.check_instruction_caps(tmp_path)

    assert len(issues) == 1, issues
    assert "bytes" in issues[0] and "Codex" in issues[0]
    assert "[WARN]" not in issues[0]


def test_check_instruction_caps_warns_above_90_percent_without_failing(
    tmp_path, capsys
):
    _write(tmp_path / "AGENTS.md", "x" * int(32_768 * 0.95))

    issues = garden.check_instruction_caps(tmp_path)

    assert issues == []
    assert "WARN" in capsys.readouterr().out


def test_check_instruction_caps_passes_with_a_warning_at_exactly_the_cap(
    tmp_path, capsys
):
    _write(tmp_path / "AGENTS.md", "x" * 32_768)

    issues = garden.check_instruction_caps(tmp_path)

    assert issues == []
    assert "WARN" in capsys.readouterr().out


def test_check_instruction_caps_is_silent_at_half_the_limit(tmp_path):
    _write(tmp_path / "AGENTS.md", "x" * (32_768 // 2))

    assert garden.check_instruction_caps(tmp_path) == []


def test_check_instruction_caps_missing_root_reports_nothing(tmp_path):
    assert garden.check_instruction_caps(tmp_path / "absent") == []


def test_check_instruction_caps_directory_named_agents_md_reports_nothing(tmp_path):
    (tmp_path / "AGENTS.md").mkdir()

    assert garden.check_instruction_caps(tmp_path) == []


def test_check_instruction_caps_unreadable_agents_md_is_one_issue(
    tmp_path, monkeypatch
):
    """A PermissionError must fail closed as one issue, never abort garden.

    chmod cannot deny reads on Windows, so the read itself is made to raise;
    that exercises the same OSError path a real unreadable file takes.
    """
    agents_md = tmp_path / "AGENTS.md"
    _write(agents_md, "x")
    real_read_bytes = Path.read_bytes

    def _deny_agents_md(self):
        if self == agents_md:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _deny_agents_md)

    issues = garden.check_instruction_caps(tmp_path)

    assert len(issues) == 1, issues
    assert "AGENTS.md" in issues[0]


def test_check_instruction_caps_counts_memory_characters(tmp_path):
    """ASCII cannot tell characters from bytes, so use a 2-byte character.

    4,001 "é" is 8,002 bytes but only 4,001 characters -- a byte-counting gate
    would wrongly fail it; 8,001 characters is what the cap actually rejects.
    """
    memory = tmp_path / ".kilo" / "memory" / "MEMORY.md"
    _write(memory, "é" * 4_001)

    assert len(memory.read_bytes()) == 8_002
    assert garden.check_instruction_caps(tmp_path) == []

    _write(memory, "é" * 8_001)

    issues = garden.check_instruction_caps(tmp_path)

    assert len(issues) == 1, issues
    assert "8,001" in issues[0]
    assert "characters" in issues[0]


# ─── check_script_syntax ────────────────────────────────────────────────────


def test_check_script_syntax_detects_a_broken_script(tmp_path):
    """A file the parser rejects is one issue naming that file."""
    _write(tmp_path / "tools" / "broken.py", "def (:\n")

    issues = garden.check_script_syntax(tmp_path)

    assert len(issues) == 1, issues
    assert "tools/broken.py" in issues[0]


def test_check_script_syntax_clean_tree_is_silent(tmp_path):
    """Every scanned directory is covered; valid scripts produce no issue."""
    _write(tmp_path / "tools" / "fine.py", "x = 1\n")
    _write(tmp_path / ".github" / "scripts" / "fine.py", "y = 2\n")
    _write(tmp_path / ".claude" / "hooks" / "fine.py", "z = 3\n")
    _write(tmp_path / ".kilo" / "hooks" / "fine.py", "w = 4\n")

    assert garden.check_script_syntax(tmp_path) == []


def test_check_script_syntax_tolerates_absent_directories(tmp_path):
    """A deployed target without every engine carries no script to parse."""
    assert garden.check_script_syntax(tmp_path) == []


def test_check_script_syntax_reports_an_unreadable_script(tmp_path, monkeypatch):
    """A file that exists but cannot be read is drift, never a silent skip."""
    script = tmp_path / "tools" / "locked.py"
    _write(script, "x = 1\n")
    real_read_text = Path.read_text

    def _deny(self, *args, **kwargs):
        if self == script:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _deny)

    issues = garden.check_script_syntax(tmp_path)

    assert len(issues) == 1, issues
    assert "tools/locked.py" in issues[0]


def test_live_repo_scripts_all_parse():
    assert garden.check_script_syntax(root=garden.ROOT) == []


# ─── check_hook_references ──────────────────────────────────────────────────


def _hooks_fixture(tmp_path, payload, scripts=()):
    """Write .kilo/hooks/hooks.json plus the script files it may point at."""
    for name in scripts:
        _write(tmp_path / name, "// hook\n")
    _write(
        tmp_path / ".kilo" / "hooks" / "hooks.json",
        json.dumps(payload, indent=2),
    )
    return tmp_path


def test_check_hook_references_detects_a_missing_script(tmp_path):
    root = _hooks_fixture(
        tmp_path,
        {
            "hooks": {
                "PreToolUse": [
                    {"id": "pre:gate", "hook": "node .kilo/hooks/pre-tool-use/gate.js"}
                ]
            }
        },
    )

    issues = garden.check_hook_references(root)

    assert len(issues) == 1, issues
    assert ".kilo/hooks/pre-tool-use/gate.js" in issues[0]


def test_check_hook_references_clean_when_scripts_exist(tmp_path):
    root = _hooks_fixture(
        tmp_path,
        {
            "hooks": {
                "PreToolUse": [
                    {"id": "pre:gate", "hook": "node .kilo/hooks/pre-tool-use/gate.js"}
                ]
            }
        },
        scripts=(".kilo/hooks/pre-tool-use/gate.js",),
    )

    assert garden.check_hook_references(root) == []


def test_check_hook_references_detects_a_duplicate_id(tmp_path):
    root = _hooks_fixture(
        tmp_path,
        {
            "hooks": {
                "PreToolUse": [{"id": "pre:gate", "hook": "node .kilo/hooks/a.js"}],
                "PostToolUse": [{"id": "pre:gate", "hook": "node .kilo/hooks/a.js"}],
            }
        },
        scripts=(".kilo/hooks/a.js",),
    )

    issues = garden.check_hook_references(root)

    assert len(issues) == 1, issues
    assert "Duplicate hook id" in issues[0]


def test_check_hook_references_malformed_json_is_one_issue(tmp_path):
    """A malformed file is one reported issue, never an exception."""
    _write(tmp_path / ".kilo" / "hooks" / "hooks.json", "{not json")

    issues = garden.check_hook_references(tmp_path)

    assert len(issues) == 1, issues
    assert "hooks.json" in issues[0]


def test_check_hook_references_absent_file_is_not_an_issue(tmp_path):
    """An absent hooks.json is optional, not drift: removing the Kilo hook
    layer must not fail the gate."""
    assert garden.check_hook_references(tmp_path) == []


def test_check_hook_references_rejects_a_payload_without_a_hooks_object(tmp_path):
    """A present but wrongly shaped file is one issue, never a traceback."""
    root = _hooks_fixture(tmp_path, {"profiles": {}})

    issues = garden.check_hook_references(root)

    assert len(issues) == 1, issues
    assert "no `hooks` object" in issues[0]


def test_check_hook_references_rejects_a_non_list_event(tmp_path):
    root = _hooks_fixture(tmp_path, {"hooks": {"PreToolUse": "not a list"}})

    issues = garden.check_hook_references(root)

    assert len(issues) == 1, issues
    assert "hooks.PreToolUse is not a list" in issues[0]


def test_check_hook_references_rejects_a_non_object_entry(tmp_path):
    root = _hooks_fixture(tmp_path, {"hooks": {"PreToolUse": ["not an object"]}})

    issues = garden.check_hook_references(root)

    assert len(issues) == 1, issues
    assert "has a non-object entry" in issues[0]


def test_check_hook_references_rejects_an_entry_without_a_command(tmp_path):
    root = _hooks_fixture(tmp_path, {"hooks": {"PreToolUse": [{"id": "pre:gate"}]}})

    issues = garden.check_hook_references(root)

    assert len(issues) == 1, issues
    assert "entry pre:gate has no `hook` command string" in issues[0]


def test_live_repo_hook_references_resolve():
    assert garden.check_hook_references(root=garden.ROOT) == []


def test_historical_docs_entries_all_exist():
    """An exemption for a file that is gone would be an invisible hole."""
    listed = garden.historical_docs(garden.ROOT)
    assert listed, "the list must not be empty while entries are expected"
    missing = sorted(entry for entry in listed if not (garden.ROOT / entry).is_file())
    assert missing == [], missing


def test_historical_doc_is_skipped_by_the_citation_check(tmp_path):
    """A listed record may cite a removed path; an unlisted one may not."""
    record = tmp_path / "docs" / "record.md"
    record.parent.mkdir(parents=True)
    record.write_text("The guard lived at `tools/codex_guard.py`.\n", encoding="utf-8")
    (tmp_path / "tools" / "config").mkdir(parents=True)
    (tmp_path / "tools" / "config" / "historical-docs.txt").write_text(
        "docs/record.md   # historical\n", encoding="utf-8"
    )
    assert garden.check_doc_paths(root=tmp_path) == []

    fresh = tmp_path / "docs" / "fresh.md"
    fresh.write_text("Run `tools/codex_guard.py` now.\n", encoding="utf-8")
    issues = garden.check_doc_paths(root=tmp_path)
    assert any("fresh.md" in issue for issue in issues), issues
