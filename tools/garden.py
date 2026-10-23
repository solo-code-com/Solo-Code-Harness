#!/usr/bin/env python3
"""
Garden — Drift Detection
=========================
Checks for stale artifacts, missing generated files, and dead links
between .kilo/ (source of truth) ↔ .claude/ (generated) and
.kilo/ ↔ .copilot/ (synced by tools/generate_harness.py) directories.

.opencode/ was removed in v4.0.0, then reintroduced in v4.2.0 as a
first-class primary engine — see .harness.lock and .kilo/memory/MEMORY.md
"Decisions" section.

Usage:
    python tools/garden.py
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess  # noqa: S404 — runs `--help` on this repo's own scripts only
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.harness_fingerprint import (  # noqa: E402
    content_fingerprint,
    lock_shared_paths,
    lock_value,
)

SKIP_FILE = ROOT / "tools" / "opencode-skip-skills.txt"

_SCAN_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    ".pytest_cache",
    ".pytest_temp",
    ".ruff_cache",
    ".solocode",
    "__pycache__",
}


def _doc_scan_skip_dirs(root: Path) -> set[str]:
    """Directories the doc-scanning checks must not walk into.

    Beyond the usual build/cache noise, this excludes reference repos --
    unpacked upstream projects kept alongside the harness for study. They are
    detected as top-level directories with zero git-tracked files (harness
    directories all contain tracked files), so a newly unpacked repo is
    excluded the moment it lands rather than when someone remembers to add it
    to a list. Without this, one unpacked repo reported 169 drift issues, none
    of which were the harness's own.

    Degrades to the static set when git is unavailable, so a non-repo checkout
    still scans everything rather than silently skipping real drift.
    """
    skip = set(_SCAN_SKIP_DIRS)
    try:
        proc = subprocess.run(  # noqa: S603,S607 — fixed argv, no shell
            ["git", "-C", str(root), "ls-files"],  # noqa: S607 -- partial path, fixed argv
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return skip
    if proc.returncode != 0:
        return skip

    # If git returned success but zero output, we're in a gitignored subdir
    # (e.g., .pytest_temp/ inside the main repo). Treat as non-git: don't
    # skip directories based on tracking status, use static set only.
    if not proc.stdout.strip():
        return skip

    tracked_top_level = {
        line.split("/")[0] for line in proc.stdout.splitlines() if "/" in line
    }
    skip.update(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and entry.name not in tracked_top_level
    )
    return skip


def _load_skip_skills() -> set[str]:
    """Read opencode-skip-skills.txt — skills intentionally excluded."""
    if not SKIP_FILE.is_file():
        return set()
    return {
        line.strip()
        for line in SKIP_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _check_parity_dir(src: Path, dst: Path, dst_label: str, subdir: str) -> list[str]:
    """Generic parity check: all src/subdir files exist in dst/subdir with same name."""
    issues: list[str] = []
    src_dir = src / subdir
    dst_dir = dst / subdir
    if not src_dir.is_dir():
        return issues
    src_files = {f.name for f in src_dir.iterdir() if f.is_file() and f.suffix == ".md"}
    dst_files = {f.name for f in dst_dir.iterdir()} if dst_dir.is_dir() else set()
    missing = src_files - dst_files
    for name in sorted(missing):
        issues.append(f"Missing {subdir}: {dst_label}/{subdir}/{name}")
    stale = dst_files - src_files
    for name in sorted(stale):
        issues.append(f"Stale {subdir} (no source): {dst_label}/{subdir}/{name}")
    return issues


def _check_parity_dir_glob(
    src: Path, dst: Path, dst_label: str, subdir: str
) -> list[str]:
    """Parity check for directories (skills): subdirs must exist in both."""
    issues: list[str] = []
    src_dir = src / subdir
    dst_dir = dst / subdir
    if not src_dir.is_dir():
        return issues
    src_dirs = {d.name for d in src_dir.iterdir() if d.is_dir()}
    dst_dirs = {d.name for d in dst_dir.iterdir()} if dst_dir.is_dir() else set()
    missing = src_dirs - dst_dirs
    for name in sorted(missing):
        issues.append(f"Missing {subdir}: {dst_label}/{subdir}/{name}/")
    stale = dst_dirs - src_dirs
    for name in sorted(stale):
        issues.append(f"Stale {subdir} (no source): {dst_label}/{subdir}/{name}/")
    return issues


def check_agents(src: Path, dst: Path, dst_label: str) -> list[str]:
    """Check that all src/agents/ files have a dst/ copy."""
    return _check_parity_dir(src, dst, dst_label, "agents")


def _permission_block(path: Path) -> str:
    """Return an agent file's `permission:` block as normalized text ("" if none).

    Agents are the one artifact whose frontmatter is semantically transformed
    per engine, so the manual-parity engines (.copilot, .gemini) must carry the
    source block verbatim. Comparing this text is what catches a hand-edit that
    reached only some engines -- `check_agents` compares filenames only.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    kept: list[str] = []
    inside = False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not inside:
            if line.strip() == "permission:":
                inside = True
                kept.append("permission:")
            continue
        if line.strip() and not line[0].isspace():
            break  # dedent to the next top-level key ends the block
        if line.strip():
            kept.append(line.rstrip())
    return "\n".join(kept)


def check_agent_permissions(src: Path, dst: Path, dst_label: str) -> list[str]:
    """Check the `permission:` block matches between .kilo/agents/ and a mirror.

    A `bash: deny` -> `bash: ask` change once reached .kilo/ and .opencode/ while
    .copilot/ and .gemini/ kept the old value, and garden still reported 0 drift.
    """
    issues: list[str] = []
    src_dir = src / "agents"
    dst_dir = dst / "agents"
    if not src_dir.is_dir() or not dst_dir.is_dir():
        return issues
    for agent_file in sorted(src_dir.glob("*.md")):
        dst_file = dst_dir / agent_file.name
        if not dst_file.is_file():
            continue  # absent copy is already reported by check_agents
        want = _permission_block(agent_file)
        if want != _permission_block(dst_file):
            issues.append(
                f"Permission drift: {dst_label}/agents/{agent_file.name} block differs "
                f"from .kilo/agents/{agent_file.name} (resync it verbatim)"
            )
    return issues


def check_claude_agent_tools(src: Path, dst: Path) -> list[str]:
    """Check .claude/agents/*.md `tools:` still equals what the source derives.

    Claude Code expresses permissions as a bare tool-name allowlist, so the
    mapping is lossy: `bash: ask` and `bash: allow` both become `Bash`. That
    lossiness is by design (see claude_engine._build_tools_allowlist), so the
    only assertable property is that the generated allowlist matches the current
    source block -- which catches a hand-edited .claude/ file, not a widening.
    """
    try:
        from tools.claude_engine import (  # noqa: PLC0415 -- cycle-free at call time
            _build_tools_allowlist,
            _parse_kilo_permissions,
        )
        from tools.claude_engine import (
            _split_frontmatter as _ce_split_frontmatter,
        )
    except ImportError as exc:
        return [
            f"Cannot check .claude agent tools (claude_engine import failed): {exc}"
        ]

    issues: list[str] = []
    src_dir = src / "agents"
    dst_dir = dst / "agents"
    if not src_dir.is_dir() or not dst_dir.is_dir():
        return issues
    for agent_file in sorted(src_dir.glob("*.md")):
        dst_file = dst_dir / agent_file.name
        if not dst_file.is_file():
            continue
        src_split = _ce_split_frontmatter(agent_file.read_text(encoding="utf-8"))
        dst_split = _ce_split_frontmatter(dst_file.read_text(encoding="utf-8"))
        if src_split is None or dst_split is None:
            continue
        perms, _description, _mode = _parse_kilo_permissions(src_split[0])
        expected = ", ".join(_build_tools_allowlist(perms))
        actual = ""
        for line in dst_split[0].splitlines():
            if line.startswith("tools:"):
                actual = line.split(":", 1)[1].strip()
                break
        if actual != expected:
            issues.append(
                f"Agent tools drift: .claude/agents/{agent_file.name} has "
                f"tools=[{actual or 'inherit'}] but its source permission block "
                f"derives tools=[{expected or 'inherit'}] "
                "(run 'python tools/generate_harness.py --harness claude')"
            )
    return issues


def check_instructions(src: Path, dst: Path, dst_label: str) -> list[str]:
    """Check that all src/instruction/ files have a direct dst/ copy (same filename)."""
    issues: list[str] = []
    src_dir = src / "instruction"
    dst_dir = dst / "instruction"
    if not src_dir.is_dir():
        return issues
    src_names = {f.name for f in src_dir.iterdir() if f.is_file()}
    dst_names = {f.name for f in dst_dir.iterdir()} if dst_dir.is_dir() else set()
    missing = src_names - dst_names
    for name in sorted(missing):
        issues.append(f"Missing instruction: {dst_label}/instruction/{name}")
    return issues


def check_instruction_content(src: Path, dst: Path, dst_label: str) -> list[str]:
    """Check src/instruction/*.md content matches dst/instruction/*.md exactly.

    Instructions have no per-engine frontmatter transform (unlike skills, whose
    SKILL.md frontmatter legitimately differs per engine schema), so a byte-
    for-byte diff is the correct check here -- any mismatch is real drift.
    """
    issues: list[str] = []
    src_dir = src / "instruction"
    dst_dir = dst / "instruction"
    if not src_dir.is_dir() or not dst_dir.is_dir():
        return issues
    src_names = {f.name for f in src_dir.iterdir() if f.is_file()}
    dst_names = {f.name for f in dst_dir.iterdir() if f.is_file()}
    for name in sorted(src_names & dst_names):
        if (src_dir / name).read_text(encoding="utf-8") != (dst_dir / name).read_text(
            encoding="utf-8"
        ):
            issues.append(
                f"Content drift: {dst_label}/instruction/{name} differs from "
                f".kilo/instruction/{name} (out of sync — resync from source of truth)"
            )
    return issues


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split a markdown file into (frontmatter incl. delimiters, body). Returns
    ("", text) if there's no `---`-delimited frontmatter block."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[: end + 5], text[end + 5 :]
    return "", text


def check_skill_content(src: Path, dst_skill_dir: Path, dst_label: str) -> list[str]:
    """Check skill directory content matches between .kilo/skill/ and a
    manually-mirrored engine (dst_skill_dir).

    SKILL.md is compared body-only (frontmatter stripped): frontmatter
    legitimately differs per engine (e.g. Copilot requires quoted
    `description` strings + a `license` field, Kilo doesn't) -- diffing it
    would be a constant false positive. Every other file in the skill's
    directory tree (shared/, scripts/, per-language dirs, etc.) is compared
    byte-for-byte, and missing/extra files between the two sides are
    reported -- non-SKILL.md files have no frontmatter exemption and any
    subdirectory drift is real content loss, not an intentional platform
    adaptation.
    """
    issues: list[str] = []
    src_dir = src / "skill"
    if not src_dir.is_dir() or not dst_skill_dir.is_dir():
        return issues
    for skill_dir in sorted(p for p in src_dir.iterdir() if p.is_dir()):
        dst_dir = dst_skill_dir / skill_dir.name
        if not dst_dir.is_dir():
            continue
        src_skill_md = skill_dir / "SKILL.md"
        dst_skill_md = dst_dir / "SKILL.md"
        if src_skill_md.is_file() and dst_skill_md.is_file():
            _, src_body = _split_frontmatter(src_skill_md.read_text(encoding="utf-8"))
            _, dst_body = _split_frontmatter(dst_skill_md.read_text(encoding="utf-8"))
            if src_body != dst_body:
                issues.append(
                    f"Content drift: {dst_label}/{dst_skill_dir.name}/{skill_dir.name}/SKILL.md body "
                    f"differs from .kilo/skill/{skill_dir.name}/SKILL.md "
                    "(out of sync — resync body from source of truth, keep dst frontmatter)"
                )
        elif src_skill_md.is_file() or dst_skill_md.is_file():
            continue

        src_rel = {
            p.relative_to(skill_dir).as_posix()
            for p in skill_dir.rglob("*")
            if p.is_file() and p.name != "SKILL.md"
        }
        dst_rel = {
            p.relative_to(dst_dir).as_posix()
            for p in dst_dir.rglob("*")
            if p.is_file() and p.name != "SKILL.md"
        }
        for rel in sorted(src_rel - dst_rel):
            issues.append(
                f"Content drift: {dst_label}/{dst_skill_dir.name}/{skill_dir.name}/{rel} "
                f"is missing (present in .kilo/skill/{skill_dir.name}/{rel})"
            )
        for rel in sorted(dst_rel - src_rel):
            issues.append(
                f"Content drift: {dst_label}/{dst_skill_dir.name}/{skill_dir.name}/{rel} "
                f"has no counterpart in .kilo/skill/{skill_dir.name}/ (extra/stale file)"
            )
        for rel in sorted(src_rel & dst_rel):
            src_file = skill_dir / rel
            dst_file = dst_dir / rel
            try:
                src_bytes = src_file.read_bytes()
                dst_bytes = dst_file.read_bytes()
            except OSError:
                continue
            if src_bytes != dst_bytes:
                issues.append(
                    f"Content drift: {dst_label}/{dst_skill_dir.name}/{skill_dir.name}/{rel} "
                    f"differs from .kilo/skill/{skill_dir.name}/{rel} "
                    "(out of sync — resync from source of truth)"
                )
    return issues


def check_instructions_opencode(src: Path, dst: Path, dst_label: str) -> list[str]:
    """Check that all src/instruction/ files have dst/ copy with .instructions.md suffix."""
    issues: list[str] = []
    src_dir = src / "instruction"
    dst_dir = dst / "instruction"
    if not src_dir.is_dir():
        return issues
    src_names = {f.stem for f in src_dir.iterdir() if f.is_file()}
    dst_names = (
        {f.stem.replace(".instructions", "") for f in dst_dir.iterdir()}
        if dst_dir.is_dir()
        else set()
    )
    missing = src_names - dst_names
    for name in sorted(missing):
        issues.append(
            f"Missing instruction: {dst_label}/instruction/{name}.instructions.md"
        )
    return issues


def check_memory(src: Path, dst: Path, dst_label: str) -> list[str]:
    """Check that all src/memory/ files have a dst/ copy AND identical content.

    .kilo/memory/ is the source of truth for the project's own accumulated
    memory (MEMORY.md, project-conventions.md, harness-design-intent.md).
    .claude/memory/ and .copilot/memory/ are generated mirrors -- claude_engine
    for the former, generate_harness.sync_memory_mirrors() for the latter.
    File-existence parity alone previously let content silently drift out of
    sync (one engine's writer updates its own copy but forgets the others)
    without garden.py ever catching it. Both mirrors were hand-maintained
    until 2026-08-03, which meant this check reported drift that the fix
    command it advertises could not actually repair.
    """
    issues = _check_parity_dir(src, dst, dst_label, "memory")
    src_dir = src / "memory"
    dst_dir = dst / "memory"
    if not src_dir.is_dir() or not dst_dir.is_dir():
        return issues
    src_files = {f.name for f in src_dir.iterdir() if f.is_file() and f.suffix == ".md"}
    dst_files = {f.name for f in dst_dir.iterdir() if f.is_file()}
    for name in sorted(src_files & dst_files):
        src_text = (src_dir / name).read_text(encoding="utf-8")
        dst_text = (dst_dir / name).read_text(encoding="utf-8")
        if src_text != dst_text:
            issues.append(
                f"Content drift: {dst_label}/memory/{name} differs from "
                f".kilo/memory/{name} (out of sync — resync from source of truth)"
            )
    return issues


def check_commands(src: Path, dst: Path, dst_label: str) -> list[str]:
    """Check that every .kilo/command/*.md has a counterpart in dst/command/.

    `run_engine_checks` covered agents, skills, instructions and memory but
    never commands, so .copilot/ silently lost `ship.md` with no drift ever
    reported -- the hardcoded "expect 13" in test_integration.py masked it
    from that side too. .claude/ has its own command check in check_claude()
    (it uses the plural `commands/` directory name).
    """
    issues: list[str] = []
    src_cmd = src / "command"
    dst_cmd = dst / "command"
    if not src_cmd.is_dir():
        return issues
    src_names = {f.name for f in src_cmd.iterdir() if f.suffix == ".md"}
    dst_names = (
        {f.name for f in dst_cmd.iterdir() if f.suffix == ".md"}
        if dst_cmd.is_dir()
        else set()
    )
    for name in sorted(src_names - dst_names):
        issues.append(f"Missing command: {dst_label}/command/{name}")
    for name in sorted(dst_names - src_names):
        issues.append(f"Stale command (no source): {dst_label}/command/{name}")
    return issues


def check_skills(dst: Path, dst_label: str) -> list[str]:
    """Check each skill directory has a valid SKILL.md."""
    issues: list[str] = []
    skill_dir = dst / "skill"
    if not skill_dir.is_dir():
        return issues
    for entry in sorted(skill_dir.iterdir()):
        if entry.is_dir():
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                issues.append(f"Skill missing SKILL.md: {dst_label}/skill/{entry.name}")
    return issues


def check_skill_parity(
    src: Path, dst: Path, dst_label: str, *, skip_set: set[str] | None = None
) -> list[str]:
    """Check that all src/skill/ dirs exist in dst/skill/, respecting skip list."""
    issues = _check_parity_dir_glob(src, dst, dst_label, "skill")
    if skip_set:
        issues = [i for i in issues if not _issue_matches_skip(i, skip_set)]
    return issues


def _issue_matches_skip(issue: str, skip_set: set[str]) -> bool:
    """Check if a skill parity issue is for an intentionally skipped skill."""
    # "Missing skill: .opencode/skill/block-no-verify/"
    # "Stale skill (no source): .opencode/skill/block-no-verify/"
    for name in skip_set:
        if f"/skill/{name}/" in issue or f"/skill/{name}" in issue.rstrip("/"):
            return True
    return False


def check_claude(
    src: Path, dst: Path, *, skip_set: set[str] | None = None
) -> list[str]:
    """Parity checks for the Claude engine (.claude/).

    Claude uses different directory names than the other engines:
      .kilo/agents      -> .claude/agents
      .kilo/skill       -> .claude/skills   (plural)
      .kilo/command     -> .claude/commands (plural)
      .kilo/instruction -> .claude/instruction
    Plus static infra: CLAUDE.md, settings.json, hooks/guard.py.
    """
    issues: list[str] = []
    skip_set = skip_set or set()

    # Agents (same subdir name)
    issues.extend(_check_parity_dir(src, dst, ".claude", "agents"))
    issues.extend(check_claude_agent_tools(src, dst))

    # Skills: .kilo/skill/* dirs must exist in .claude/skills/*
    src_skills = src / "skill"
    dst_skills = dst / "skills"
    if src_skills.is_dir():
        src_names = {p.name for p in src_skills.iterdir() if p.is_dir()} - skip_set
        dst_names = (
            {p.name for p in dst_skills.iterdir() if p.is_dir()}
            if dst_skills.is_dir()
            else set()
        )
        for name in sorted(src_names - dst_names):
            issues.append(f"Missing skill: .claude/skills/{name}/")
        for name in sorted(dst_names - src_names):
            issues.append(f"Stale skill (no source): .claude/skills/{name}/")
        # SKILL.md health
        if dst_skills.is_dir():
            for entry in sorted(dst_skills.iterdir()):
                if entry.is_dir() and not (entry / "SKILL.md").exists():
                    issues.append(
                        f"Skill missing SKILL.md: .claude/skills/{entry.name}"
                    )

    # Commands: .kilo/command/*.md must exist in .claude/commands/*.md
    src_cmd = src / "command"
    dst_cmd = dst / "commands"
    if src_cmd.is_dir():
        src_names = {f.name for f in src_cmd.iterdir() if f.suffix == ".md"}
        dst_names = {f.name for f in dst_cmd.iterdir()} if dst_cmd.is_dir() else set()
        for name in sorted(src_names - dst_names):
            issues.append(f"Missing command: .claude/commands/{name}")
        for name in sorted(dst_names - src_names):
            issues.append(f"Stale command (no source): .claude/commands/{name}")

    # Instructions (direct copy, same filename)
    issues.extend(check_instructions(src, dst, ".claude"))

    # Memory (direct copy, same filename) — feat-008 parity
    issues.extend(check_memory(src, dst, ".claude"))

    # Static harness infra required for the Claude engine
    for rel, desc in (
        ("hooks/guard.py", "guard hook (PreToolUse)"),
        ("hooks/quality_gate.py", "quality-gate hook (PostToolUse)"),
        ("hooks/security_post.py", "security-post hook (PostToolUse)"),
        ("hooks/pre_compact.py", "pre-compact hook (PreCompact)"),
        ("hooks/session_start.py", "session-start hook (SessionStart)"),
        ("hooks/session_end.py", "session-end hook (SessionEnd)"),
        ("hooks/memory_gate.py", "memory-gate hook (PostToolUse size cap)"),
        ("settings.json", "settings (hook registration)"),
    ):
        if not (dst / rel).exists():
            issues.append(f"Missing {desc}: .claude/{rel}")
    if not (ROOT / "CLAUDE.md").exists():
        issues.append(
            "Missing rulebook: CLAUDE.md (run 'python tools/generate_harness.py --harness claude')"
        )
    else:
        issues.extend(check_claude_md_regenerable())

    return issues


def check_claude_md_regenerable() -> list[str]:
    """Check the committed CLAUDE.md matches what its generator produces.

    CLAUDE.md is generated from `claude_engine.py`'s template, but nothing
    used to verify that. The two silently diverged: the live file was
    hand-edited and the template was left behind, so regenerating would
    have SILENTLY DELETED real content -- the exact failure mode the
    "GENERATED ... do not edit by hand" banner is supposed to prevent.
    Comparing here makes that divergence a loud drift error instead.

    Compared on normalized line endings: the point is content parity, and
    git checks out CRLF on Windows for a LF-committed file, which would
    otherwise make this fail for everyone on Windows.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "claude_engine", ROOT / "tools" / "claude_engine.py"
    )
    claude_engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(claude_engine)

    live = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    expected = claude_engine._CLAUDE_MD_TEMPLATE.format(
        **claude_engine._claude_md_counts(ROOT)
    )
    if live.replace("\r\n", "\n") != expected.replace("\r\n", "\n"):
        return [
            "Content drift: CLAUDE.md differs from claude_engine.py's template "
            "(regenerating would overwrite hand edits -- port them into the "
            "template, then run 'python tools/generate_harness.py --harness claude')"
        ]
    return []


def check_shared_state() -> list[str]:
    """Check .solocode/shared-state.db integrity — IF it exists.

    The DB is local-only by design: gitignored + deploy-excluded, created on
    first engine run. A fresh checkout (CI, clone, deploy) legitimately has no
    DB, so its absence is NOT drift — treating it as such breaks every clean
    environment. We only validate integrity/staleness when the DB is present.
    """
    issues: list[str] = []
    db_path = ROOT / ".solocode" / "shared-state.db"

    if not db_path.exists():
        return issues

    from tools.shared_state import SharedState

    with SharedState(db_path) as state:
        errors = state.integrity_check()
        if errors:
            issues.append(f"Corrupt DB: .solocode/shared-state.db — {errors}")
            return issues

        now = datetime.now(timezone.utc)
        for feat in state.get_features():
            if feat["status"] == "in-progress" and feat["last_updated"]:
                try:
                    dt = datetime.fromisoformat(feat["last_updated"])
                    if now - dt > timedelta(days=7):
                        issues.append(
                            f"Stale feature: {feat['id']} in-progress since {feat['last_updated'][:10]}"
                        )
                except ValueError:
                    pass

    return issues


# Two commands stay engine-specific by design. `ship` is the only command
# carrying Kilo's `task:` frontmatter, a key no Gemini command uses. Gemini's
# `code-review` is not a replacement for `review`: review records a receipt
# over the git index, and code-review has no such step.
GEMINI_COMMAND_EXCEPTIONS = {
    "missing_in_gemini": {"review", "ship"},
    "extra_in_gemini": {"code-review"},
}


def check_gemini(
    src: Path, dst: Path, *, skip_set: set[str] | None = None
) -> list[str]:
    """Parity checks for the Gemini/Antigravity engine (.gemini/antigravity/).

    Gemini uses different directory names/paths than the other engines:
      .kilo/agents      -> .gemini/antigravity/agents
      .kilo/skill       -> .gemini/antigravity/skills   (plural, nested under antigravity/)
      .kilo/instruction -> .gemini/antigravity/instruction
    No memory/ parity check: Gemini stores project knowledge under
    knowledge/ (artifacts + metadata.json), not a MEMORY.md/project-
    conventions.md mirror like the other engines -- not a comparable shape.
    Commands are checked against .kilo/command with documented exceptions.
    """
    issues: list[str] = []
    skip_set = skip_set or set()

    # Agents (same subdir name)
    issues.extend(_check_parity_dir(src, dst, ".gemini/antigravity", "agents"))
    issues.extend(check_agent_permissions(src, dst, ".gemini/antigravity"))

    # Skills: .kilo/skill/* dirs must exist in .gemini/antigravity/skills/*
    src_skills = src / "skill"
    dst_skills = dst / "skills"
    if src_skills.is_dir():
        src_names = {p.name for p in src_skills.iterdir() if p.is_dir()} - skip_set
        dst_names = (
            {p.name for p in dst_skills.iterdir() if p.is_dir()}
            if dst_skills.is_dir()
            else set()
        )
        for name in sorted(src_names - dst_names):
            issues.append(f"Missing skill: .gemini/antigravity/skills/{name}/")
        for name in sorted(dst_names - src_names):
            issues.append(
                f"Stale skill (no source): .gemini/antigravity/skills/{name}/"
            )
        if dst_skills.is_dir():
            for entry in sorted(dst_skills.iterdir()):
                if entry.is_dir() and not (entry / "SKILL.md").exists():
                    issues.append(
                        f"Skill missing SKILL.md: .gemini/antigravity/skills/{entry.name}"
                    )

    # Commands: .kilo/command/*.md must exist in .gemini/antigravity/commands/*.md,
    # minus the documented exceptions (GEMINI_COMMAND_EXCEPTIONS). A missing
    # directory on either side is reported rather than skipped -- with no mirror
    # there is nothing to compare, so staying silent would hide the whole gap.
    src_cmd = src / "command"
    dst_cmd = dst / "commands"
    if not src_cmd.is_dir():
        issues.append(
            "Missing command source: .kilo/command (command parity uncheckable)"
        )
    elif not dst_cmd.is_dir():
        issues.append(
            "Missing command mirror: .gemini/antigravity/commands "
            "(command parity uncheckable)"
        )
    else:
        src_stems = {f.stem for f in src_cmd.iterdir() if f.suffix == ".md"}
        dst_stems = {f.stem for f in dst_cmd.iterdir() if f.suffix == ".md"}
        expected = src_stems - GEMINI_COMMAND_EXCEPTIONS["missing_in_gemini"]
        for name in sorted(expected - dst_stems):
            issues.append(f"Missing command: .gemini/antigravity/commands/{name}.md")
        for name in sorted(
            dst_stems - src_stems - GEMINI_COMMAND_EXCEPTIONS["extra_in_gemini"]
        ):
            issues.append(
                f"Stale command (no source): .gemini/antigravity/commands/{name}.md"
            )

    # Instructions (direct copy, same filename + identical content)
    issues.extend(check_instructions(src, dst, ".gemini/antigravity"))
    issues.extend(check_instruction_content(src, dst, ".gemini/antigravity"))

    # Skill body content (frontmatter-agnostic — see check_skill_content docstring)
    issues.extend(check_skill_content(src, dst_skills, ".gemini/antigravity"))

    # Antigravity native discovery: .agents/skills (if present, must resolve to skills)
    agents_skills = ROOT / ".agents" / "skills"
    if agents_skills.exists():
        if not agents_skills.is_dir():
            issues.append(".agents/skills exists but is not a directory or junction")
        else:
            try:
                count = sum(
                    1
                    for p in agents_skills.iterdir()
                    if p.is_dir() and (p / "SKILL.md").exists()
                )
                if count == 0:
                    issues.append(".agents/skills contains no valid skills")
            except OSError as err:
                issues.append(f".agents/skills is broken: {err}")

    return issues


def check_opencode(src: Path, dst: Path) -> list[str]:
    """Parity checks for the OpenCode engine (.opencode/), OpenCode v2.

    OpenCode mirrors .kilo/ with transforms (see tools/opencode_engine.py):
      .kilo/agents  -> .opencode/agents  (Kilo `permission:` object rewritten
                     to v2 ordered `permissions:` arrays)
      .kilo/command -> .opencode/commands (plural, copied verbatim)
      .kilo/instruction -> .opencode/skills/<id>/SKILL.md (on-demand skills;
                     v2 loads only AGENTS.md for always-on rules)
    The .kilo/skill/* library is NOT mirrored: v2 loads .opencode/skills/,
    .claude/skills/ and .agents/skills/, so a mirror of the same ids would
    register every skill twice; those load from .claude/skills/ alone (this
    check flags mirror ids under .opencode/skills/).
    Plus root opencode.json (v1-safe: model default + legacy `permission`) and
    .opencode/opencode.json (v2-only `providers`).
    """
    issues: list[str] = []

    # Agents (same subdir name; frontmatter drops unknown keys, so name parity only)
    issues.extend(_check_parity_dir(src, dst, ".opencode", "agents"))

    # Skills: `.opencode/skills/` holds instruction-derived on-demand skills and
    # must NOT mirror .kilo/skill/* -- those already load from .claude/skills/.
    dst_skills = dst / "skills"
    if not (ROOT / ".claude" / "skills").is_dir():
        issues.append(
            "Missing skills source: .claude/skills (OpenCode-compatible location)"
        )
    src_skill = src / "skill"
    if dst_skills.is_dir() and src_skill.is_dir():
        kilo_ids = {p.name for p in src_skill.iterdir() if p.is_dir()}
        for d in sorted(p for p in dst_skills.iterdir() if p.is_dir()):
            if d.name in kilo_ids:
                issues.append(
                    f"Duplicate skill: .opencode/skills/{d.name} mirrors .kilo/skill/{d.name} "
                    "(already loaded from .claude/skills — remove the mirror)"
                )

    # Commands: .kilo/command/*.md must exist in .opencode/commands/*.md
    src_cmd = src / "command"
    dst_cmd = dst / "commands"
    if src_cmd.is_dir():
        src_names = {f.name for f in src_cmd.iterdir() if f.suffix == ".md"}
        dst_names = {f.name for f in dst_cmd.iterdir()} if dst_cmd.is_dir() else set()
        for name in sorted(src_names - dst_names):
            issues.append(f"Missing command: .opencode/commands/{name}")
        for name in sorted(dst_names - src_names):
            issues.append(f"Stale command (no source): .opencode/commands/{name}")

    # Instructions: v2 resolves only AGENTS.md, so the v1 .opencode/instruction/
    # mirror must be gone and each .kilo/instruction/*.md must appear as an
    # on-demand skill (see opencode_engine.generate_instruction_skills).
    if (dst / "instruction").exists():
        issues.append(
            "Stale mirror: .opencode/instruction exists — instructions are "
            "emitted as skills (run "
            "'python tools/generate_harness.py --harness opencode')"
        )
    src_instr = src / "instruction"
    if src_instr.is_dir():
        dst_ids = (
            {p.name for p in dst_skills.iterdir() if p.is_dir()}
            if dst_skills.is_dir()
            else set()
        )
        for f in sorted(src_instr.glob("*.md")):
            if f.stem not in dst_ids:
                issues.append(
                    f"Missing instruction skill: .opencode/skills/{f.stem}/SKILL.md"
                )
    agents_md = ROOT / "AGENTS.md"
    try:
        agents_text = agents_md.read_text(encoding="utf-8")
    except OSError:
        agents_text = ""
    if "opencode-v2-inline-instructions" in agents_text:
        issues.append(
            "Legacy embedded instructions: AGENTS.md still carries the inline "
            "instructions block (instructions are now on-demand skills)"
        )

    # Static engine config
    if not (ROOT / "opencode.json").exists():
        issues.append(
            "Missing config: opencode.json (run 'python tools/generate_harness.py --harness opencode')"
        )

    return issues


def run_engine_checks(
    src: Path,
    dst: Path,
    dst_label: str,
    *,
    instruction_suffix: bool = False,
    skip_set: set[str] | None = None,
) -> list[str]:
    """Run all parity checks for one engine."""
    issues: list[str] = []
    checks = [
        (f"Agent drift ({dst_label})", lambda: check_agents(src, dst, dst_label)),
        (
            f"Permission drift ({dst_label})",
            lambda: check_agent_permissions(src, dst, dst_label),
        ),
        (
            f"Instruction drift ({dst_label})",
            lambda: (
                check_instructions_opencode(src, dst, dst_label)
                if instruction_suffix
                else check_instructions(src, dst, dst_label)
            ),
        ),
        (
            f"Instruction content drift ({dst_label})",
            lambda: []
            if instruction_suffix
            else check_instruction_content(src, dst, dst_label),
        ),
        (f"Memory drift ({dst_label})", lambda: check_memory(src, dst, dst_label)),
        (f"Command parity ({dst_label})", lambda: check_commands(src, dst, dst_label)),
        (
            f"Skill parity ({dst_label})",
            lambda: check_skill_parity(src, dst, dst_label, skip_set=skip_set),
        ),
        (
            f"Skill content drift ({dst_label})",
            lambda: check_skill_content(src, dst / "skill", dst_label),
        ),
        (f"Skill health ({dst_label})", lambda: check_skills(dst, dst_label)),
    ]
    for name, check_fn in checks:
        result = check_fn()
        if result:
            print(f"[DRIFT] {name}:")
            for i in result:
                print(f"  {i}")
            issues.extend(result)
        else:
            print(f"[OK] {name}")
    return issues


def check_harness_fingerprint(root: Path) -> list[str]:
    """Compare .harness.lock's content_fingerprint with the declared files.

    The lock names which files are harness; this proves they are the bytes the
    recorded version shipped. The lock's ``[harness] mode`` decides what a
    mismatch means: a deployed target customises harness files legitimately, so
    there it is a WARN and returns [] (garden stays green); on the source side
    it fails the gate, because the root lock is regenerated on every commit. A
    lock with no ``mode`` is treated as root and fails closed, with an issue
    telling the operator to regenerate it.
    """
    lock_path = root / ".harness.lock"
    try:
        text = lock_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [
            "lock_stale: .harness.lock is missing - "
            "run python tools/deploy.py --root-lock"
        ]

    recorded = lock_value(text, "harness", "content_fingerprint")
    if not recorded:
        return [
            "lock_stale: .harness.lock has no content_fingerprint - "
            "run python tools/deploy.py --root-lock"
        ]

    paths = lock_shared_paths(text)
    if paths is None:
        return [
            "lock_stale: .harness.lock has no readable [shared_files] paths - "
            "run python tools/deploy.py --root-lock"
        ]

    mode = lock_value(text, "harness", "mode")
    target_mode = mode == "target"
    issues: list[str] = []
    if mode is None:
        issues.append(
            "lock_stale: .harness.lock has no [harness] mode (treated as root, "
            "fail closed) - run python tools/deploy.py --root-lock"
        )

    missing = {p for p in paths if not (root / p).is_file()}
    if missing:
        for path in sorted(missing):
            issues.append(
                f"lock_stale: .harness.lock declares {path} but it does not exist; "
                "run python tools/deploy.py --root-lock"
            )
        # Comparing the digest over the remaining subset would report a bogus
        # "content changed" on top of every missing path.
        if target_mode:
            print(
                "WARN: harness content differs from the recorded fingerprint "
                "(customised target); advisory only"
            )
            return []
        return issues

    try:
        current = content_fingerprint(paths, root)
    except OSError as exc:
        issues.append(
            f"lock_stale: cannot read a declared harness file ({exc}); "
            "run python tools/deploy.py --root-lock"
        )
        return issues

    if current != recorded:
        if target_mode:
            print(
                "WARN: harness content differs from the recorded fingerprint "
                "(customised target); advisory only"
            )
            return []
        issues.append(
            f"lock_stale: harness content changed since .harness.lock was "
            f"generated (recorded {recorded} != current {current}) for "
            f"{len(paths)} declared file(s); "
            "run python tools/deploy.py --root-lock"
        )
    return issues


def _parse_agent_yaml_list(text: str, key: str) -> list[str]:
    """Extract a top-level YAML list (e.g. `skills:` / `agents:`) without PyYAML.

    Only handles the simple `key:` followed by `  - item` block style used in
    agent.yaml. Stops at the next top-level key (a line starting in column 0
    that is not a list item). Stdlib-only to keep the harness zero-dependency.
    """
    items: list[str] = []
    in_block = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not in_block:
            # Enter the block only on the bare `key:` header (no inline value).
            if stripped == f"{key}:":
                in_block = True
            continue
        # Inside the block.
        if raw.startswith(("  - ", "- ")) or stripped.startswith("- "):
            items.append(stripped[2:].strip())
        elif stripped == "":
            continue
        elif not raw.startswith(" "):
            # New top-level key — block ended.
            break
    return items


def check_manifest(root: Path) -> list[str]:
    """feat-010: validate agent.yaml skills/agents match real .kilo/ file counts.

    Prevents drift between the published manifest (agent.yaml) and reality:
      - skills listed must match .kilo/skill/*/ directory names exactly
      - agents listed must match .kilo/agents/*.md file stems exactly
      - version must match the harness version in .harness.lock
    """
    issues: list[str] = []
    manifest = root / "agent.yaml"
    if not manifest.is_file():
        return issues
    text = manifest.read_text(encoding="utf-8")

    # Skills parity
    skill_dir = root / ".kilo" / "skill"
    if skill_dir.is_dir():
        actual = {p.name for p in skill_dir.iterdir() if p.is_dir()}
        listed = set(_parse_agent_yaml_list(text, "skills"))
        for name in sorted(actual - listed):
            issues.append(f"agent.yaml missing skill: {name} (exists in .kilo/skill/)")
        for name in sorted(listed - actual):
            issues.append(f"agent.yaml stale skill: {name} (no .kilo/skill/{name}/)")

    # Agents parity
    agent_dir = root / ".kilo" / "agents"
    if agent_dir.is_dir():
        actual_a = {f.stem for f in agent_dir.glob("*.md")}
        listed_a = set(_parse_agent_yaml_list(text, "agents"))
        for name in sorted(actual_a - listed_a):
            issues.append(f"agent.yaml missing agent: {name} (exists in .kilo/agents/)")
        for name in sorted(listed_a - actual_a):
            issues.append(f"agent.yaml stale agent: {name} (no .kilo/agents/{name}.md)")

    # Version parity with .harness.lock
    lock = root / ".harness.lock"
    if lock.is_file():
        lock_version = ""
        for line in lock.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("version"):
                lock_version = s.split("=", 1)[1].strip().strip('"') if "=" in s else ""
                break
        manifest_version = ""
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("version:"):
                manifest_version = s.split(":", 1)[1].strip().strip('"')
                break
        if lock_version and manifest_version and lock_version != manifest_version:
            issues.append(
                f"agent.yaml version {manifest_version} != .harness.lock version {lock_version}"
            )

    return issues


# Where each engine keeps its artifacts. Engines genuinely diverge —
# .gemini/ ships 14 commands, not .kilo/'s 15 — so a doc line describing
# .gemini/ must be measured against .gemini/, never against the source of
# truth. Comparing everything to .kilo/ produces false drift reports.
_ENGINE_LAYOUT = {
    ".kilo": {
        "skill": "skill",
        "agent": "agents",
        "command": "command",
        "instruction": "instruction",
    },
    ".claude": {
        "skill": "skills",
        "agent": "agents",
        "command": "commands",
        "instruction": "instruction",
    },
    ".copilot": {
        "skill": "skill",
        "agent": "agents",
        "command": "command",
        "instruction": "instruction",
    },
    ".gemini": {
        "skill": "skills",
        "agent": "agents",
        "command": "commands",
        "instruction": "instruction",
    },
    ".opencode": {
        "skill": "skills",
        "agent": "agents",
        "command": "commands",
        "instruction": "instruction",
    },
}

# .gemini/ nests its harness one level deeper.
_ENGINE_ROOT = {".gemini": Path(".gemini") / "antigravity"}

# Matches an engine mention anywhere on the line, e.g. "`.gemini/` commands (14)".
_ENGINE_MENTION = re.compile(r"`?(\.(?:kilo|claude|copilot|gemini|opencode))/")


def _measure_engine(root: Path, engine: str) -> dict[str, int]:
    """Count skills/agents/commands/instructions actually present in an engine."""
    base = root / _ENGINE_ROOT.get(engine, Path(engine))
    layout = _ENGINE_LAYOUT[engine]
    counts: dict[str, int] = {}

    skills = base / layout["skill"]
    counts["skill"] = (
        len([d for d in skills.iterdir() if d.is_dir()]) if skills.is_dir() else 0
    )

    for kind in ("agent", "command", "instruction"):
        d = base / layout[kind]
        counts[kind] = len(list(d.glob("*.md"))) if d.is_dir() else 0

    return counts


def check_doc_counts(root: Path = ROOT) -> list[str]:
    """Verify that hardcoded counts in documentation match reality.

    Counts are resolved per-engine: a line that names `.gemini/` is checked
    against `.gemini/antigravity/`, a line that names nothing falls back to
    `.kilo/` (the source of truth).
    """
    issues: list[str] = []

    engines = {name: _measure_engine(root, name) for name in _ENGINE_LAYOUT}
    truth = engines[".kilo"]

    files_to_scan = [
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        ".github/copilot-instructions.md",
        ".gemini/antigravity/AGENTS.md",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
    ]

    # (kind, label, patterns) — label is what appears in the drift message.
    scanners = [
        (
            "skill",
            "skills",
            [
                re.compile(
                    r"\b(\d+)\s+(?:specialized\s+|domain\s+)?skills?\b", re.IGNORECASE
                ),
                re.compile(r"\bskills?\s*\(\s*(\d+)\b", re.IGNORECASE),
            ],
        ),
        (
            "agent",
            "agents",
            [
                re.compile(
                    r"\b(\d+)\s+(?:specialized\s+)?(?:sub)?agents?\b", re.IGNORECASE
                ),
                re.compile(r"\b(?:sub)?agents?\s*\(\s*(\d+)\b", re.IGNORECASE),
            ],
        ),
        (
            "command",
            "commands",
            [
                re.compile(r"\b(\d+)\s+(?:slash\s+)?commands?\b", re.IGNORECASE),
                re.compile(r"\b(?:slash\s+)?commands?\s*\(\s*(\d+)\b", re.IGNORECASE),
            ],
        ),
        (
            "instruction",
            "instructions",
            [
                re.compile(r"\b(\d+)\s+instructions?\b", re.IGNORECASE),
                re.compile(r"\binstructions?\s*\(\s*(\d+)\b", re.IGNORECASE),
            ],
        ),
    ]

    for rel_path in files_to_scan:
        p = root / rel_path
        if not p.is_file():
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except OSError as e:
            issues.append(f"Could not read file {rel_path}: {e}")
            continue

        for line_no, line in enumerate(content.splitlines(), 1):
            mention = _ENGINE_MENTION.search(line)
            expected = engines.get(mention.group(1), truth) if mention else truth
            scope = f" (for {mention.group(1)}/)" if mention else ""

            for kind, label, patterns in scanners:
                want = expected[kind]
                for pat in patterns:
                    for m in pat.finditer(line):
                        val = int(m.group(1))
                        if val != want:
                            issues.append(
                                f"{rel_path}:{line_no}: claimed {val} {label}, "
                                f"but ground truth is {want}{scope}"
                            )

    return issues


_PATH_IN_BACKTICKS = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\."
    r"(?:py|md|sh|ps1|js|mjs|cjs|json|jsonc|toml|yaml|yml|txt|sql|lock))`"
)

# A line carrying one of these is making a point ABOUT a missing/renamed path
# (e.g. SPEC.md: "at ROOT, not `.claude/CLAUDE.md`"). Flagging those would
# punish the docs for being precise, so they opt out explicitly.
_PATH_NEGATION_MARKERS = (
    "not exist",
    "doesn't exist",
    "does not exist",
    "never existed",
    "no longer",
    "removed",
    "deleted",
    "outdated",
    "stale",
    "deprecated",
    "instead of",
    "not `",
    "khong ton tai",
    "không tồn tại",
    "đã lỗi thời",
    "da loi thoi",
    "thay vì",
    "thay vi",
    "không phải",
    "khong phai",
)

# Generated at runtime, so absence is normal rather than drift.
_RUNTIME_PATH_PREFIXES = (".solocode/", ".kilo/logs/", ".claude/logs/")


def check_doc_paths(root: Path = ROOT) -> list[str]:
    """Verify that repo paths cited in docs actually resolve on disk.

    Motivated by three real cases that survived every gate for months:
    `.opencode/tests/test-guard.mjs` in the PR checklist, `.claude/state/`
    called "the existing convention" in handoff/SKILL.md, and a
    `tools/eval.py --check-triggers` loop in writing-great-skills -- none
    of which existed. Counts were already checked; paths were not.

    Only *anchored* paths are checked: the first segment must match a real
    top-level entry (so `src/models/user.py` in a generic example is
    ignored). Lines that explicitly say a path is missing/renamed opt out
    via _PATH_NEGATION_MARKERS, and runtime-generated paths are skipped.
    """
    issues: list[str] = []
    skip_dirs = _doc_scan_skip_dirs(root)
    top_level = {p.name for p in root.iterdir() if p.name not in skip_dirs}

    for md in sorted(root.rglob("*.md")):
        # Check relative path parts from root, not absolute path
        try:
            rel_parts = md.relative_to(root).parts
        except ValueError:
            continue
        if any(part in skip_dirs for part in rel_parts):
            continue
        # Archives and plan snapshots describe history; paths may be gone by design.
        posix = md.as_posix()
        if (
            "decisions-archive" in md.name
            or "/plans/" in posix
            or ("/docs/" in posix and ("review" in md.name or "report" in md.name))
        ):
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for lineno, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if any(marker in low for marker in _PATH_NEGATION_MARKERS):
                continue
            for cited in _PATH_IN_BACKTICKS.findall(line):
                if "/" not in cited or cited.split("/", 1)[0] not in top_level:
                    continue
                if cited.startswith(_RUNTIME_PATH_PREFIXES):
                    continue
                if not (root / cited).exists():
                    issues.append(
                        f"{md.relative_to(root).as_posix()}:{lineno} cites "
                        f"`{cited}` — no such path"
                    )
    return issues


def _script_flag_surface(root: Path, script: str) -> str:
    """Every string in which a valid flag for `script` could appear.

    Combines top-level --help, each subcommand's --help, and the source
    text itself. Union rather than any single source: argparse omits
    subcommand flags from the top-level help, and hand-rolled sys.argv
    parsers produce no help at all.
    """
    parts: list[str] = []
    try:
        parts.append((root / script).read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return ""

    def _help(*args: str) -> str:
        try:
            proc = subprocess.run(  # noqa: S603 — fixed argv, repo-local script
                [sys.executable, script, *args],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=root,
            )
            return proc.stdout + proc.stderr
        except (OSError, subprocess.SubprocessError):
            return ""

    top = _help("--help")
    parts.append(top)
    # `{show,features,sessions}` — argparse's subcommand listing
    for match in re.finditer(r"\{([a-z0-9_,-]+)\}", top):
        for sub in match.group(1).split(","):
            parts.append(_help(sub, "--help"))
    return "\n".join(parts)


_DOC_CMD = re.compile(
    r"python3?\s+((?:tools|\.github/scripts)/[A-Za-z0-9_./-]+\.py)([^\n`]*)"
)
_LONG_FLAG = re.compile(r"(--[a-z][a-z0-9-]*)")


def check_doc_flags(root: Path = ROOT) -> list[str]:
    """Verify CLI flags shown in docs actually exist in the script's --help.

    check_doc_paths() proves a cited file exists; it cannot tell whether the
    *invocation* is real. Three `.gemini/` commands told agents to run
    `python tools/garden.py --strict` -- garden.py never reads sys.argv at
    all, so the flag was silently ignored and the docs implied a strict mode
    that does not exist.

    Only long flags on scripts inside tools/ and .github/scripts/ are
    checked. Verification is deliberately generous, because a false
    positive here would push someone to delete a working flag:
      - subcommand flags are covered by also reading `<script> <sub>
        --help` (argparse hides them from the top-level help);
      - the flag is accepted if it appears in the script source at all,
        which covers tools that parse `sys.argv` by hand and therefore
        have no argparse help (e.g. security_scan.py's `--strict`).
    """
    issues: list[str] = []
    skip_dirs = _doc_scan_skip_dirs(root)
    help_cache: dict[str, str] = {}

    for md in sorted(root.rglob("*.md")):
        # Check relative path parts from root, not absolute path
        try:
            rel_parts = md.relative_to(root).parts
        except ValueError:
            continue
        if any(part in skip_dirs for part in rel_parts):
            continue
        posix = md.as_posix()
        if "decisions-archive" in md.name or "/plans/" in posix:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for lineno, line in enumerate(text.splitlines(), 1):
            if any(m in line.lower() for m in _PATH_NEGATION_MARKERS):
                continue
            for script, rest in _DOC_CMD.findall(line):
                flags = _LONG_FLAG.findall(rest)
                if not flags:
                    continue
                if not (root / script).exists():
                    # Missing script in a fenced command block. check_doc_paths()
                    # only reads backticked paths, so `python tools/eval_harness.py
                    # --min-score 60` inside a ```bash fence was invisible to both
                    # checks -- wrong directory AND an invented flag.
                    issues.append(
                        f"{md.relative_to(root).as_posix()}:{lineno} runs "
                        f"`python {script}` — no such script"
                    )
                    continue
                if script not in help_cache:
                    help_cache[script] = _script_flag_surface(root, script)
                help_text = help_cache[script]
                if not help_text:
                    continue
                for flag in flags:
                    if flag not in help_text:
                        issues.append(
                            f"{md.relative_to(root).as_posix()}:{lineno} documents "
                            f"`python {script} {flag}` — no such flag in --help"
                        )
    return issues


# A doc line claiming some *named script* will stop you. Vietnamese and
# English, because the skills are written in both.
_ENFORCE_VERB = re.compile(
    r"(will block|blocks the|blocks your|prevents you|rejects|refuses|"
    r"sẽ chặn|chặn commit|chặn bạn|từ chối|ném ra lỗi|báo lỗi và chặn)",
    re.IGNORECASE,
)
_SCRIPT_IN_BACKTICKS = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|js|mjs|cjs|sh))`")

# What a script must contain to be *capable* of blocking. A hook that only
# ever exits 0 is advisory no matter how the prose describes it.
_CAN_BLOCK = re.compile(
    r"sys\.exit\([1-9]|process\.exit\([1-9]|exit\([1-9]|"
    r"return 2\b|\"deny\"|'deny'|permissionDecision",
)


def check_enforcement_claims(root: Path = ROOT) -> list[str]:
    """Verify that a script described as blocking can actually block.

    check_doc_paths/check_doc_flags prove a citation *resolves*; neither
    reads what the script does. algorithmic-discipline/SKILL.md told four
    engines that `quality-gate.js` "sẽ tự động ném ra lỗi và chặn commit"
    for a missing ALGO-CHECK tag. That file never mentions ALGO-CHECK, and
    all three of its exits are `process.exit(0)` -- its own header even
    says "1 = WARNING (non-blocking)". The prose invented an enforcement
    layer, which is worse than no rule: it invites relying on it.

    Deliberately narrow. Only fires when a line pairs a blocking verb with
    a backticked script path, so it cannot judge prose in general -- but
    that pairing is exactly the claim a reader will act on.
    """
    issues: list[str] = []
    skip_dirs = _doc_scan_skip_dirs(root)
    verdict: dict[str, bool] = {}

    for md in sorted(root.rglob("*.md")):
        # Check relative path parts from root, not absolute path
        try:
            rel_parts = md.relative_to(root).parts
        except ValueError:
            continue
        if any(part in skip_dirs for part in rel_parts):
            continue
        posix = md.as_posix()
        if "decisions-archive" in md.name or "/plans/" in posix:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for lineno, line in enumerate(text.splitlines(), 1):
            if not _ENFORCE_VERB.search(line):
                continue
            if any(m in line.lower() for m in _PATH_NEGATION_MARKERS):
                continue
            for script in _SCRIPT_IN_BACKTICKS.findall(line):
                if script not in verdict:
                    hits = []
                    for p in root.rglob(script.split("/")[-1]):
                        if not p.is_file():
                            continue
                        try:
                            rel_parts = p.relative_to(root).parts
                        except ValueError:
                            continue
                        if any(d in rel_parts for d in skip_dirs):
                            continue
                        if p.as_posix().endswith(script):
                            hits.append(p)
                    verdict[script] = bool(hits) and any(
                        _CAN_BLOCK.search(
                            p.read_text(encoding="utf-8", errors="ignore")
                        )
                        for p in hits
                    )
                    if not hits:  # missing file is check_doc_paths' job
                        verdict[script] = True
                if not verdict[script]:
                    issues.append(
                        f"{md.relative_to(root).as_posix()}:{lineno} says "
                        f"`{script}` blocks/rejects, but it only ever exits 0"
                    )
    return issues


# A skill reference is only checkable when the doc marks it AS a skill.
# Bare kebab-case is not a signal: a first draft of this check produced 190
# hits, nearly all npm packages (`express-rate-limit`, `chrome-devtools-mcp`),
# YAML keys (`disable-model-invocation`), branch names and hyphenated prose.
# These three forms carry an explicit marker, so they cannot be confused.
_SKILL_REF_FORMS = (
    # skills/<name>/SKILL.md — an actual path claim
    re.compile(r"skills?/([a-z][a-z0-9-]{4,})/SKILL\.md"),
    # router arrow: "├── Reviewing code? ──→ code-review-expert". The `?` is
    # required -- it marks a routing *decision*. Without it the pattern also
    # matched pipeline diagrams ("decompose → research → build → verdict"),
    # whose nodes are stages, not skills.
    re.compile(r"\?[^\n]*?(?:→|-->)\s*`?([a-z][a-z0-9-]{4,})`?\s*$"),
    # the word "skill" adjacent: "See `x` skill", "skill `x`"
    re.compile(r"`([a-z][a-z0-9-]{4,})`\s+skill\b"),
    re.compile(r"\bskill\s+`([a-z][a-z0-9-]{4,})`"),
)


def _count_pattern_list(py_file: Path, var_name: str) -> int | None:
    """Return the literal length of a module-level list `var_name`, or None.

    Parsed via ast rather than regex: these lists contain regexes full of
    brackets and quotes, so a textual count is unreliable.
    """
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
        if getattr(target, "id", None) != var_name:
            continue
        value = node.value
        if isinstance(value, ast.List):
            return len(value.elts)
        # e.g. frozenset({...}) / set([...])
        if isinstance(value, ast.Call) and value.args:
            arg = value.args[0]
            if isinstance(arg, ast.List | ast.Set):
                return len(arg.elts)
    return None


# Prose files that make countable claims about the harness. Shared by
# check_pattern_counts(); mirrors the list in check_doc_counts().
_DOC_FILES_TO_SCAN: list[str] = [
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    ".github/copilot-instructions.md",
    ".gemini/antigravity/AGENTS.md",
    ".kilo/instruction/harness-checklist.md",
    ".claude/instruction/harness-checklist.md",
    ".copilot/instruction/harness-checklist.md",
]


# (doc phrase regex, source file, variable) — the number in the doc must
# equal the measured length of that list.
_PATTERN_COUNT_CLAIMS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"(\d+)\s+destructive\s+patterns?", re.IGNORECASE),
        ".claude/hooks/guard.py",
        "BLOCK_PATTERNS",
    ),
    (
        re.compile(r"(\d+)\s+secret\s+patterns?", re.IGNORECASE),
        ".claude/hooks/guard.py",
        "SECRET_PATTERNS",
    ),
]


def check_pattern_counts(root: Path = ROOT) -> list[str]:
    """Verify documented guard-pattern counts match the real pattern lists.

    README.md advertised "33 destructive patterns + 15 secret patterns"
    while guard.py actually held 21 secret patterns: commit 340ae20 added
    six prefixed-token formats (sk-ant-, sk-proj-, npm_, glpat-, dop_v1_,
    Bearer) and never touched the prose. The number understated the
    harness, but the direction is not the point -- nothing was checking it,
    so it could drift either way, and an overstated security claim is the
    dangerous version of this bug.

    check_doc_counts() could not catch it: that scanner only knows
    skills/agents/commands/instructions, all counted from directory
    listings. These counts come from list literals inside a Python module,
    so they need ast, not a glob.
    """
    issues: list[str] = []
    measured: dict[tuple[str, str], int | None] = {}

    for _, rel_src, var in _PATTERN_COUNT_CLAIMS:
        key = (rel_src, var)
        if key not in measured:
            measured[key] = _count_pattern_list(root / rel_src, var)

    for rel_path in _DOC_FILES_TO_SCAN:
        doc = root / rel_path
        if not doc.is_file():
            continue
        try:
            content = doc.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for lineno, line in enumerate(content.splitlines(), 1):
            for pattern, rel_src, var in _PATTERN_COUNT_CLAIMS:
                want = measured[(rel_src, var)]
                if want is None:
                    continue
                for match in pattern.finditer(line):
                    claimed = int(match.group(1))
                    if claimed != want:
                        issues.append(
                            f"{rel_path}:{lineno}: claims {claimed} for "
                            f"{var}, but {rel_src} defines {want}"
                        )
    return issues


# Always-on instruction files have hard size caps, and both were invisible:
# Codex silently truncates an always-on AGENTS.md at 32,768 bytes, and
# MEMORY.md documents an 8,000-character gate on its own line 4.
AGENTS_MD_BYTE_LIMIT = 32_768
AGENTS_MD_WARN_RATIO = 0.9
MEMORY_MD_CHAR_LIMIT = 8_000


def check_instruction_caps(root: Path = ROOT) -> list[str]:
    """Flag always-on instruction files that are past their size caps.

    The 90%..cap band is a printed warning, not an issue: garden stays green
    while the file is still under the cap. An unreadable file fails closed with
    one issue, so a size check can never abort the later garden checks with a
    traceback.
    """
    issues: list[str] = []

    agents_md = root / "AGENTS.md"
    if agents_md.is_file():
        try:
            size = len(agents_md.read_bytes())
        except OSError as exc:
            issues.append(f"Cannot read AGENTS.md to check its size cap: {exc}")
            return issues
        if size > AGENTS_MD_BYTE_LIMIT:
            issues.append(
                f"AGENTS.md is {size:,} bytes, over the "
                f"{AGENTS_MD_BYTE_LIMIT:,}-byte cap at which Codex silently "
                "truncates it, so the tail is lost without a warning"
            )
        elif size > AGENTS_MD_BYTE_LIMIT * AGENTS_MD_WARN_RATIO:
            print(
                f"WARN: AGENTS.md is {size:,} bytes, above 90% of the "
                f"{AGENTS_MD_BYTE_LIMIT:,}-byte cap at which Codex truncates "
                "it silently"
            )

    memory_md = root / ".kilo" / "memory" / "MEMORY.md"
    if memory_md.is_file():
        try:
            count = len(memory_md.read_text(encoding="utf-8"))
        except OSError as exc:
            issues.append(
                f"Cannot read .kilo/memory/MEMORY.md to check its character cap: {exc}"
            )
            return issues
        if count > MEMORY_MD_CHAR_LIMIT:
            issues.append(
                f".kilo/memory/MEMORY.md is {count:,} characters "
                "(len(text), not bytes), over the "
                f"{MEMORY_MD_CHAR_LIMIT:,}-character gate its own line 4 "
                "documents"
            )
    return issues


# Launcher scripts that start an engine, and the flags that switch that
# engine into a reduced mode where the harness stops being enforced.
# `claude --bare` skips hooks AND CLAUDE.md auto-discovery; `--safe-mode`
# disables hooks, skills, agents, commands and MCP servers wholesale.
_HARNESS_DISABLING_FLAGS = ("--bare", "--safe-mode", "--dangerously-skip-permissions")

# A launcher may still *support* a reduced mode -- it must not *default* to
# one. These mark a line as a guarded/one-off use rather than the default
# path: an opt-in branch, a warning about the mode, or prose about it.
_LAUNCHER_OPTIN_MARKERS = (
    "-eq",
    "-contains",
    "-match",
    "if ",
    "elseif",
    "write-warning",
    "write-error",
    "write-host",
    "#",
    "notcontains",
)


def check_launcher_defaults(root: Path = ROOT) -> list[str]:
    """Fail if a launcher passes a harness-disabling flag unconditionally.

    The gap this closes: claude-env.ps1 injected `--bare` on every launch,
    including its no-arg path (`& claude --bare`). Per `claude --help`,
    bare mode skips hooks and CLAUDE.md auto-discovery -- so the documented
    way to start this harness ran with guard.py, memory_gate, quality_gate,
    security_post and both session hooks inert, while README.md and
    CLAUDE.md advertised them as active.

    Every existing gate looked straight past it. check_enforcement_claims()
    verifies a hook *can* exit non-zero and that settings.json wires it up;
    it has no notion of the process being started in a mode where hooks are
    never invoked at all. A harness that exists but is not in force is the
    failure mode this repo is supposed to prevent.

    Narrow on purpose: only flags a flag literal that is both un-negated and
    outside any conditional/warning context, i.e. the unconditional default
    path. Opt-in support for these modes is legitimate and stays silent.
    """
    issues: list[str] = []

    for script in sorted(root.glob("*.ps1")) + sorted(root.glob("*.sh")):
        try:
            text = script.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            lowered = line.lower()
            # Only care about lines that actually invoke the engine.
            if not re.search(r"(?:^|[&|;\s])claude\b", lowered):
                continue
            if any(m in lowered for m in _LAUNCHER_OPTIN_MARKERS):
                continue
            for flag in _HARNESS_DISABLING_FLAGS:
                if flag in lowered:
                    issues.append(
                        f"{script.relative_to(root).as_posix()}:{lineno} "
                        f"launches with `{flag}` unconditionally — that "
                        f"disables the harness (hooks/CLAUDE.md) by default"
                    )
    return issues


def _key_from_env_file(env_file: Path) -> str:
    """Read ANTHROPIC_API_KEY out of a `.env` file, or "" if absent."""
    try:
        text = env_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == "ANTHROPIC_API_KEY":
            return value.strip().strip('"').strip("'")
    return ""


def check_api_key_approval(root: Path = ROOT) -> list[str]:
    """Warn if the active API key is stored as *rejected* in ~/.claude.json.

    Interactive Claude Code asks "use this custom API key?" and records the
    answer under `customApiKeyResponses`. A stored "no" makes every
    interactive session fail with "Not logged in - Please run /login".

    The reason this needs a gate: `-p` (print) mode and `--bare` never read
    that list, so every headless probe authenticates fine while the only
    path a human uses is dead. That asymmetry already cost one bad fix --
    a launcher change shipped with five green gates and still broke startup,
    because no gate could see interactive-only state.

    Environment-dependent, so it never fails the run: it reports a warning
    and returns [] to keep CI (where no key or config exists) clean.

    Falls back to `.env` when the shell has no key, because the launcher is
    what loads `.env` -- running the gate from a plain shell in a deployed
    project would otherwise see nothing and report all-clear on a repo whose
    interactive sessions are dead.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    source = "shell environment"
    if not key:
        key = _key_from_env_file(root / ".env")
        source = ".env"
    config = Path(os.path.expanduser("~/.claude.json"))
    if not key or len(key) < 20 or not config.exists():
        return []

    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []

    responses = data.get("customApiKeyResponses", {})
    if not isinstance(responses, dict):
        return []

    # Claude Code fingerprints a custom key by its last 20 characters.
    fingerprint = key[-20:]
    if fingerprint in responses.get("rejected", []):
        print(
            f"[WARN] The ANTHROPIC_API_KEY from {source} is marked *rejected* "
            "in ~/.claude.json.\n"
            "  Interactive sessions will fail with 'Not logged in' "
            "(-p and --bare modes are unaffected, so this hides well).\n"
            "  Fix: python tools/approve_api_key.py --apply"
        )
    return []


def check_skill_refs(root: Path = ROOT) -> list[str]:
    """Verify skill names cited as skills resolve to a real skill.

    The router (`using-agent-skills`) sent readers to five skills that have
    never existed -- `test-driven-development`, `code-review-and-quality`,
    `code-simplification`, `git-workflow-and-versioning` and
    `api-and-interface-design` -- 104 references across four engines. Each
    had a real counterpart under a different name (`testing-patterns`,
    `code-review-expert`, `simplify-code`, `git-workflow-master`,
    `api-patterns`), so the harness looked complete while its own index
    pointed at nothing.

    Invisible to check_doc_paths(): the names appear as bare words, not
    paths, and every one reads like a plausible skill. Agents and commands
    count as valid targets, since skills legitimately reference both.

    Known limit: only the marked forms in _SKILL_REF_FORMS are checked, so
    a bare mention in a heading ("### With test-driven-development") still
    slips through. Widening it costs more in false positives than the
    remaining coverage is worth.
    """
    issues: list[str] = []
    real = {p.parent.name for p in (root / ".kilo" / "skill").glob("*/SKILL.md")}
    real |= {p.stem for p in (root / ".kilo" / "agents").glob("*.md")}
    real |= {p.stem for p in (root / ".kilo" / "command").glob("*.md")}
    if not real:  # not a harness layout; nothing to assert
        return issues

    skill_dirs = [
        ".kilo/skill",
        ".claude/skills",
        ".copilot/skill",
        ".gemini/antigravity/skills",
    ]
    for d in skill_dirs:
        for md in sorted((root / d).glob("*/SKILL.md")):
            try:
                text = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            own = md.parent.name
            for lineno, line in enumerate(text.splitlines(), 1):
                if any(m in line.lower() for m in _PATH_NEGATION_MARKERS):
                    continue
                for pattern in _SKILL_REF_FORMS:
                    for name in pattern.findall(line):
                        if name in real or name == own:
                            continue
                        issues.append(
                            f"{md.relative_to(root).as_posix()}:{lineno} routes "
                            f"to `{name}` — no such skill, agent or command"
                        )
    return issues


# Harness directories whose contents the parity checks compare. A symlink
# anywhere inside one of them makes every content comparison read through it
# to the target -- so the link itself has to be checked, not only the bytes
# that come back.
_SYMLINK_SCAN_DIRS: tuple[str, ...] = (
    ".kilo",
    ".claude",
    ".copilot",
    ".gemini",
    ".opencode",
    ".agents",
    ".contracts",
    ".github",
    ".vscode",
    "tools",
)

# Build/cache directories no check in this file reads, so a link inside one
# of them cannot make a mirror comparison pass.
_SYMLINK_PRUNE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
}


def check_no_symlinks(root: Path = ROOT) -> list[str]:
    """Report symlinks -- and entries resolving outside the repo -- in harness dirs.

    Every other check in this file compares *content*, and content is what
    the OS returns after following the path. A symlink placed inside .kilo/
    or one of its mirrors therefore makes those comparisons pass by
    construction, even when the mirror points outside the repository: the
    parity check reads the same bytes on both sides because it is reading
    the same file. Only the link itself can reveal that, so this check walks
    the harness directories and reports two things -- an entry that IS a
    symlink (with its target), and an entry whose resolution lands outside
    `root`. The second covers Windows junctions, which redirect exactly like
    a symlink but report is_symlink() as False.

    Symlinked directories are reported and not descended into: their
    contents belong to the target, which may not be in this repo at all.
    """
    issues: list[str] = []
    resolved_root = root.resolve()

    def _report(entry: Path) -> bool:
        """Record one entry's issues; False means it leads outside the repo."""
        rel = entry.relative_to(root).as_posix()
        if entry.is_symlink():
            try:
                target = str(entry.readlink())
            except OSError:
                target = "unreadable target"
            issues.append(f"Symlink in harness tree: {rel} -> {target}")
        resolved = entry.resolve()
        if not resolved.is_relative_to(resolved_root):
            issues.append(
                f"Path resolves outside the repo: {rel} -> {resolved.as_posix()}"
            )
            return False
        return True

    for name in _SYMLINK_SCAN_DIRS:
        top = root / name
        # is_symlink() first: exists()/is_dir() are False for a broken link,
        # and a broken link is exactly what this check must still report.
        if top.is_symlink():
            _report(top)
            continue
        if not top.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = sorted(d for d in dirnames if d not in _SYMLINK_PRUNE_DIRS)
            current = Path(dirpath)
            # A directory that is a link, or that redirects outside, is
            # reported and then pruned: its contents belong to the target.
            inside: list[str] = []
            for child in dirnames:
                entry = current / child
                if _report(entry) and not entry.is_symlink():
                    inside.append(child)
            dirnames[:] = inside
            for child in sorted(filenames):
                _report(current / child)
    return issues


def main() -> int:
    kilo = ROOT / ".kilo"

    all_issues: list[str] = []
    skip_skills = _load_skip_skills()

    # Symlinks in the harness dirs -- every check below reads through them, so
    # a link can make a mirror compare equal while pointing outside the repo.
    print("--- Harness Tree ---")
    symlink_issues = check_no_symlinks(ROOT)
    if symlink_issues:
        print("[DRIFT] Symlinks in harness directories:")
        for i in symlink_issues:
            print(f"  {i}")
        all_issues.extend(symlink_issues)
    else:
        print("[OK] No symlinks in harness directories")

    # .copilot/ engine — direct copy, no suffix
    print("--- .copilot/ ---")
    all_issues.extend(
        run_engine_checks(kilo, ROOT / ".copilot", ".copilot", instruction_suffix=False)
    )

    # .claude/ engine — different subdir names (skills/commands plural) + static infra
    print("\n--- .claude/ ---")
    claude_issues = check_claude(kilo, ROOT / ".claude", skip_set=skip_skills)
    if claude_issues:
        print("[DRIFT] Claude engine (.claude):")
        for i in claude_issues:
            print(f"  {i}")
        all_issues.extend(claude_issues)
    else:
        print("[OK] Claude engine (.claude)")

    # .gemini/antigravity/ engine — nested path + skills/ plural, no memory check
    print("\n--- .gemini/ ---")
    gemini_issues = check_gemini(
        kilo, ROOT / ".gemini" / "antigravity", skip_set=skip_skills
    )
    if gemini_issues:
        print("[DRIFT] Gemini engine (.gemini):")
        for i in gemini_issues:
            print(f"  {i}")
        all_issues.extend(gemini_issues)
    else:
        print("[OK] Gemini engine (.gemini)")

    # .opencode/ engine — primary agent engine (agents/commands/skills plural)
    print("\n--- .opencode/ ---")
    opencode_issues = check_opencode(kilo, ROOT / ".opencode")
    if opencode_issues:
        print("[DRIFT] OpenCode engine (.opencode):")
        for i in opencode_issues:
            print(f"  {i}")
        all_issues.extend(opencode_issues)
    else:
        print("[OK] OpenCode engine (.opencode)")

    # Shared state health (không thuộc riêng engine nào)
    print("\n--- Shared State ---")
    shared_issues = check_shared_state()
    if shared_issues:
        print("[DRIFT] Shared state:")
        for i in shared_issues:
            print(f"  {i}")
        all_issues.extend(shared_issues)
    else:
        print("[OK] Shared state (local DB present or absent — both valid)")

    # Manifest sync (agent.yaml vs reality) — feat-010
    print("\n--- Manifest (agent.yaml) ---")
    manifest_issues = check_manifest(ROOT)
    if manifest_issues:
        print("[DRIFT] Manifest sync:")
        for i in manifest_issues:
            print(f"  {i}")
        all_issues.extend(manifest_issues)
    else:
        print("[OK] Manifest sync (agent.yaml)")

    # Harness content fingerprint (lock vs the bytes it declares)
    print("\n--- Harness Lock ---")
    fingerprint_issues = check_harness_fingerprint(ROOT)
    if fingerprint_issues:
        print("[DRIFT] Harness content fingerprint:")
        for i in fingerprint_issues:
            print(f"  {i}")
        all_issues.extend(fingerprint_issues)
    else:
        print("[OK] Harness content fingerprint")

    # Document counts checks
    print("\n--- Document Counts ---")
    doc_issues = check_doc_counts(ROOT)
    if doc_issues:
        print("[DRIFT] Stale counts in documentation:")
        for i in doc_issues:
            print(f"  {i}")
        all_issues.extend(doc_issues)
    else:
        print("[OK] Document counts")

    # Document paths — do cited repo paths actually resolve?
    print("\n--- Document Paths ---")
    path_issues = check_doc_paths(ROOT)
    if path_issues:
        print("[DRIFT] Docs cite paths that do not exist:")
        for i in path_issues:
            print(f"  {i}")
        all_issues.extend(path_issues)
    else:
        print("[OK] Document paths")

    # Document flags — do documented CLI invocations actually accept them?
    print("\n--- Document Flags ---")
    flag_issues = check_doc_flags(ROOT)
    if flag_issues:
        print("[DRIFT] Docs document flags that do not exist:")
        for i in flag_issues:
            print(f"  {i}")
        all_issues.extend(flag_issues)
    else:
        print("[OK] Document flags")

    # Instruction caps - are the always-on instruction files near their caps?
    print("\n--- Instruction Caps ---")
    cap_issues = check_instruction_caps(ROOT)
    if cap_issues:
        print("[DRIFT] Instruction files at or over their size caps:")
        for i in cap_issues:
            print(f"  {i}")
        all_issues.extend(cap_issues)
    else:
        print("[OK] Instruction caps")

    # Enforcement claims — can a script called "blocking" actually block?
    print("\n--- Enforcement Claims ---")
    enforce_issues = check_enforcement_claims(ROOT)
    if enforce_issues:
        print("[DRIFT] Docs claim enforcement that the script cannot perform:")
        for i in enforce_issues:
            print(f"  {i}")
        all_issues.extend(enforce_issues)
    else:
        print("[OK] Enforcement claims")

    # Pattern counts — do documented guard-pattern totals match the code?
    print("\n--- Pattern Counts ---")
    pattern_issues = check_pattern_counts(ROOT)
    if pattern_issues:
        print("[DRIFT] Docs claim guard-pattern counts that the code contradicts:")
        for i in pattern_issues:
            print(f"  {i}")
        all_issues.extend(pattern_issues)
    else:
        print("[OK] Pattern counts")

    # Launcher defaults — does a launcher start the engine with the harness off?
    print("\n--- Launcher Defaults ---")
    launcher_issues = check_launcher_defaults(ROOT)
    if launcher_issues:
        print("[DRIFT] Launcher disables the harness by default:")
        for i in launcher_issues:
            print(f"  {i}")
        all_issues.extend(launcher_issues)
    else:
        print("[OK] Launcher defaults")

    # API key approval — can an interactive session actually authenticate?
    print("\n--- API Key Approval ---")
    approval_issues = check_api_key_approval(ROOT)
    all_issues.extend(approval_issues)
    print("[OK] API key approval (or not applicable)")

    # Skill references — does the router point at skills that exist?
    print("\n--- Skill References ---")
    ref_issues = check_skill_refs(ROOT)
    if ref_issues:
        print("[DRIFT] Skill docs reference skills that do not exist:")
        for i in ref_issues:
            print(f"  {i}")
        all_issues.extend(ref_issues)
    else:
        print("[OK] Skill references")

    print(f"\nTotal drift issues: {len(all_issues)}")
    if all_issues:
        print("Run 'python tools/generate_harness.py --harness all' to fix drift.")
        return 1
    print("Garden is clean — no drift detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
