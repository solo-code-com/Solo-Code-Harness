#!/usr/bin/env python3
"""
deploy.py — Deploy or scaffold Solo-Code Harness for new/existing projects.

Two modes:
  scaffold   Create a NEW project with full harness (git init, copy files, validate)
  deploy     Copy harness into an EXISTING project directory

Usage:
    # Scaffold a new project (auto mode — target does not exist)
    python tools/deploy.py scaffold /path/to/new-project
    python tools/deploy.py scaffold /path/to/new-project --engine all
    python tools/deploy.py scaffold /path/to/new-project --name my-project

    # Deploy to an existing project
    python tools/deploy.py deploy /path/to/existing-project
    python tools/deploy.py deploy /path/to/existing-project --engine kilo
    python tools/deploy.py deploy /path/to/existing-project --dry-run

    # Auto-detect: scaffold if target missing, deploy if exists
    python tools/deploy.py /path/to/target

    # Interactive mode (no args)
    python tools/deploy.py
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════
# Deployment manifest — what to copy where
# ═══════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parent.parent

# `python tools/deploy.py` puts tools/ on sys.path, not the repo root, so
# add the root the same way the other tool scripts do before a sibling import.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.harness_fingerprint import content_fingerprint  # noqa: E402

# Public name kept: callers and tests already use this one.
harness_content_fingerprint = content_fingerprint

# Files to copy (relative to ROOT)
# NOTE (v3.7.0 deploy-optimization): a *deployed target project* only needs
# enough to RUN the AI-CLI harness (agent/skill/command/hook definitions +
# config). It does NOT need Solo-Code-CLI's own dev docs (CONTRIBUTING,
# CODE_OF_CONDUCT, SECURITY, SPEC — these describe how to contribute to /
# rebuild THIS repo, not how to use the harness in a target project).
# OpenCode is a first-class engine (reintroduced v4.2.0): opencode.json is in
# ROOT_FILES and .opencode/ is in DIRS_ALL / DIRS_OPENCODE.
ROOT_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "kilo.jsonc",
    "opencode.json",
    ".mcp.json",
    ".gitleaks.toml",
    ".ruff.toml",
    ".pre-commit-config.yaml",
    "eslint.config.js",
    "pyproject.toml",
    "Makefile",
    "claude-env.ps1",
    "codex-env.ps1",
    "opencode-env.ps1",
    "init.sh",
    ".env.template",
    "verify.sh",
    "agent.yaml",
    "extensions_config.json",
    ".gitignore",
]

# `.harness.lock` is NOT in ROOT_FILES — it is generated fresh
# for each scaffold/deploy with its own timestamp.

# Dev-only, Solo-Code-CLI-repo-specific meta files — NEVER deployed to a
# target project (they document/govern THIS repo's own contribution process
# and internal spec, not the target project).
DEV_ONLY_ROOT_FILES = {
    "SPEC.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "LICENSE",  # Solo-Code-CLI's own license, not the target project's
    "opencode.ps1",  # deprecated launcher, see .harness.lock
}

# Root files the harness deployed in EARLIER versions and no longer ships.
# These are the only names eligible for stale-cleanup at the target root
# beyond the current ROOT_FILES, because a previous deploy is what put them
# there. Append here whenever a file is dropped from ROOT_FILES.
RETIRED_ROOT_FILES = {
    "opencode.ps1",  # removed v4.0.0 (launcher superseded by opencode.json)
    "jcode.ps1",  # removed v4.1.0 (jcode engine retired)
}

# The complete set of root-level filenames the harness is permitted to
# delete from a target project. Anything outside this set belongs to the
# project and must never be touched by cleanup.
HARNESS_OWNED_ROOT_FILES = set(ROOT_FILES) | RETIRED_ROOT_FILES | {".harness.lock"}

# Root files a project also legitimately owns. These are never replaced wholesale - only topped up
# with the lines the harness needs (see _merge_list_file). `.gitignore` is the well-defined case
# because it is a line set; pyproject.toml, .ruff.toml, eslint.config.js, .pre-commit-config.yaml
# and Makefile are also project-plausible but merging structured config is not, so they stay
# replaced for now and are listed in the archive as the remaining gap.
MERGE_ROOT_FILES = {".gitignore"}

# Root files a project very likely owns and customises, and which are structured formats - TOML,
# YAML, JS, Make - so the line-set merge used for .gitignore is not available. They are written only
# when the target has none; an existing file is kept and reported. A measured upgrade replaced them
# and silently removed a project's pytest testpaths, one of its ruff exclusions and a pre-commit
# exclude, which is why this set exists.
PROJECT_OWNED_ROOT_FILES = {
    "pyproject.toml",
    ".ruff.toml",
    "eslint.config.js",
    ".pre-commit-config.yaml",
    "Makefile",
}

# Directories that belong to the harness ENTIRELY. Anything inside them that
# the harness no longer ships is by definition a leftover from an older
# deploy, so full stale-cleanup is safe here.
EXCLUSIVE_HARNESS_DIRS = {
    ".codex",
    ".kilo",
    ".copilot",
    ".gemini",
    ".claude",
    ".claude-plugin",
    ".opencode",
    ".contracts",
}

# Directories the harness SHARES with the target project. A project keeps its
# own CI workflows, CODEOWNERS, dependabot config, editor settings and dev
# scripts here. We may only remove files the harness itself deployed, never
# "everything not in the current manifest".
SHARED_DIRS = {".github", ".vscode", "tools"}

# Harness directories that exist in THIS repo but are deliberately NOT
# deployed, because the machine creates them locally rather than the harness
# shipping them.
#
# `.agents/skills` is a junction the Antigravity IDE creates on the developer's
# machine (see garden.py's check for it). It is harness-controlled, so the root
# .harness.lock must keep declaring it — but deploy never writes it, so it must
# stay out of EXCLUSIVE_HARNESS_DIRS. That set means "safe to wipe anything not
# in the current manifest", and handing a locally-created junction to full
# stale-cleanup would delete it at a target.
#
# The root lock is a SUPERSET of what deploy ships (same rule as [shared_files]:
# this repo IS the harness, a target receives a subset). A test asserts
# root_lock_dirs == EXCLUSIVE_HARNESS_DIRS | LOCAL_ONLY_HARNESS_DIRS, so a new
# engine dir cannot be silently omitted when the lock is regenerated.
LOCAL_ONLY_HARNESS_DIRS = {".agents"}

# Files the harness shipped into SHARED_DIRS in earlier versions and no
# longer ships. These are the only shared-dir paths cleanup may delete
# beyond the current manifest. Append here whenever such a file is dropped.
RETIRED_SHARED_FILES = {
    "tools/migrate_to_shared_state.py",  # removed with shared-state migration
    "tools/test_harness.py",  # split into tools/test_*.py
}

# Documents the v4.1.0 deploy shipped into `docs/` and the current one no longer ships. Cleanup must
# never delete from `docs/` (a target's docs are project-owned), so these names only feed the
# upgrade note. Matched by exact name, not by content: only 13 of the 27 carry a Solo-Code marker.
# Five of the originals are absent on purpose - README.md, evaluation-report.md, capability-seams.md,
# code-review-checklist.md and defensive-patterns.md are names a project plausibly owns, and a note
# that calls a project's own file harness debris is the exact false claim this list must avoid.
LEGACY_HARNESS_DOCS = {
    "antigravity-telemetry-hook.md",
    "audit-2026-08-15.md",
    "audit-2026-09-04.md",
    "audit-2026-09-11.md",
    "codex-antigravity-cross-review-report.md",
    "deepseek-cross-review-antigravity-round2.md",
    "deepseek-cross-review-antigravity.md",
    "deepseek-harness-upgrade-acceptance.md",
    "deepseek-harness-upgrade-final-review.md",
    "deepseek-harness-upgrade-review-followup.md",
    "deepseek-harness-upgrade-review-plan.md",
    "deepseek-harness-upgrade-review-response-round2.md",
    "deepseek-harness-upgrade-review-response.md",
    "dsh-port-map.md",
    "dsh-port-plan.md",
    "executor-benchmark-2026-09-04.md",
    "handoff-2026-09-11.md",
    "lifecycle-matrix-verification.md",
    "review-open-code-review-2026-09-16.md",
    "upgrade-summary.md",
    "work-report-2026-09-16.md",
    "work-report-2026-09-27.md",
}


def _legacy_harness_leftovers(target: Path) -> list[str]:
    """Root documents an earlier harness deployed that this one no longer owns.

    Detection is by content for SPEC.md (the marker the harness writes on its first line) and by
    exact name for docs/. Never by bare existence: a target's own SPEC.md is a project file, and
    sharing the name must not turn it into an upgrade claim.
    """
    leftovers: list[str] = []
    spec = target / "SPEC.md"
    if spec.is_file():
        try:
            head = spec.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            head = ""
        if "Solo-Code-Harness" in head:
            leftovers.append(
                "SPEC.md (the earlier harness's specification, dev-only since v4.3.0)"
            )
    docs_dir = target / "docs"
    if docs_dir.is_dir():
        names = sorted(
            p.name for p in docs_dir.glob("*.md") if p.name in LEGACY_HARNESS_DOCS
        )
        if names:
            shown = ", ".join(f"docs/{name}" for name in names[:3])
            more = f" and {len(names) - 3} more" if len(names) > 3 else ""
            leftovers.append(f"{shown}{more}")
    return leftovers


# Directories to copy (relative to ROOT)
DIRS_ALL = [
    ".codex",
    ".kilo",
    ".copilot",
    ".gemini",
    ".claude",
    ".claude-plugin",
    ".opencode",
    ".github",
    ".vscode",
    ".contracts",
    "tools",
]

DIRS_KILO = [
    ".kilo",
    ".github",
    ".contracts",
    "tools",
]

DIRS_COPILOT = [
    ".copilot",
    ".github",
    ".contracts",
    ".vscode",
    "tools",
]

DIRS_CLAUDE = [
    ".claude",
    ".claude-plugin",
    ".github",
    ".contracts",
    "tools",
]

DIRS_OPENCODE = [
    ".opencode",
    # `.opencode/skills/` (instruction-derived, on-demand skills) ships inside
    # `.opencode`. The .kilo/skill library is not mirrored there — OpenCode
    # loads it from the Claude-compatible `.claude/skills/`, so ship that
    # location too.
    ".claude/skills",
    ".github",
    ".contracts",
    "tools",
]

DIRS_CODEX = [
    ".codex",
    ".claude/hooks",
    ".github",
    ".contracts",
    "tools",
]

# Patterns to exclude from copy
EXCLUDE_DIRS = {
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".solocode",
    "cocoindex-code-main",
    "docs",
    # Runtime state directories — never belong in a deploy
    "sessions",  # .kilo/state/sessions/
    "logs",  # .kilo/logs/
    # Solo-Code-CLI's own CI/release pipeline — target project designs its own
    "workflows",  # .github/workflows/ci.yml, release.yml
}
EXCLUDE_FILES = {
    ".env",
    "usage.log",
    "usage.jsonl",
    ".DS_Store",
    "Thumbs.db",
    "settings.local.json",  # .claude/ personal overrides — never deploy
    # Runtime state files — never belong in a deploy
    "token-log.jsonl",  # .kilo/state/token-log.jsonl
    "tool-count.json",  # .kilo/state/tool-count.json
    "edited-files.json",  # .kilo/state/edited-files.json
    # Solo-Code-CLI dev tooling under tools/ — builds/tests/deploys THIS repo,
    # not needed to just RUN the harness in a target project.
    "deploy.py",
    "garden.py",
    "generate_harness.py",
    "claude_engine.py",
    "opencode_engine.py",
    "validate_schemas.py",
    "migrate_to_shared_state.py",
    "harness_config.py",
    # NOTE: individual tools/test_*.py files are NOT listed here — they are
    # excluded by rule in should_copy(). Listing them by hand meant every new
    # test file silently leaked into deployed projects until someone
    # remembered to add it (test_deploy.py and test_garden.py both did).
    # Solo-Code-CLI's OWN accumulated memory — describes THIS repo's design/
    # conventions, not the target project's. Deploy writes fresh blank
    # templates instead (see _write_blank_memory_templates()).
    "MEMORY.md",
    "project-conventions.md",
    "harness-design-intent.md",
    "decisions-archive.md",
    # Shared skill skip-list (consumed by both claude_engine and
    # opencode_engine generation via generate_harness.py) -- dev-only.
    "opencode-skip-skills.txt",
}

# Default README template for scaffolded projects
README_TEMPLATE = """# {project_name}

> Scaffolded from [Solo-Code-CLI](https://github.com/Solo-Code-CLI) — AI agent harness with Kilo Code + OpenCode + Claude Code + Copilot + Gemini engines. This is a RUNTIME-ONLY copy: Solo-Code-CLI's own dev tooling, tests, and internal docs were intentionally excluded (see "Harness Boundaries" below).

{description}

## Quick Start

```bash
# Install dependencies (if any)
pip install -e .          # Python project
# or
npm install               # Node project

# Run security scan
python .github/scripts/security_scan.py .

# Full verification
python .github/scripts/checklist.py .

# Boundary audit (harness dirs stayed clean of project files)
python .github/scripts/boundary_audit.py .
```

## Verification Gates

| Gate | Command |
|------|---------|
| Security | `python .github/scripts/security_scan.py .` |
| Lint | `ruff check .` |
| Boundary | `python .github/scripts/boundary_audit.py .` |
| Full | `python .github/scripts/checklist.py .` |

## Engines

- **[Kilo Code](https://kilo.ai)** — source-of-truth: agents, skills, commands, hooks, memory, orchestrators
- **[OpenCode](https://opencode.ai)** — primary agent engine: agents, skills, commands, `opencode.json`
- **[Claude Code](https://claude.com/claude-code)** — orchestrator: subagents, skills, commands, hooks, `CLAUDE.md`
- **[Copilot](https://github.com/features/copilot)** — agents, skills, commands, prompts
- **[Gemini](https://gemini.google.com)** — additional AI assistant config

## Important: Harness Boundaries

This project was scaffolded with **Solo-Code Harness** — an AI agent discipline layer. Many files and directories in this project are harness infrastructure, NOT project source code. **Do not confuse them.**

| Contains | Purpose |
|----------|---------|
| `.kilo/`, `.copilot/`, `.gemini/`, `.claude/`, `.claude-plugin/`, `.contracts/` | Wholly harness — AI agent rules, skills, hooks. No project code lives here. |
| `AGENTS.md`, `CLAUDE.md`, `kilo.jsonc`, `.harness.lock` | Agent behavior configuration |

**`.github/`, `.vscode/` and `tools/` are SHARED.** The harness puts files
there, but they are also where your project keeps its own CI workflows,
CODEOWNERS, dependabot config, editor settings and dev scripts. Do **not**
treat these directories as harness-owned wholesale — only the exact paths
listed under `[shared_files]` in `.harness.lock` are harness. Everything
else in them is your project's code.

> **Read `.harness.lock`** for the authoritative boundary list — it names harness files inside shared directories explicitly. Never modify harness files to fix a project bug. Never modify project files to fix a harness issue.

## Project Memory

`.kilo/memory/`, `.claude/memory/`, `.copilot/memory/` start **blank** (project-specific templates only) — this project's own conventions/decisions belong here, not Solo-Code-CLI's. Use `/remember` (Claude/Kilo) to append as you go.

## License

MIT
"""

# Engine facts, not project facts: a target's lock states them too, so they live
# outside the template body and are interpolated into both modes.
HARNESS_ENGINE_STATUS = """\
# --- ENGINE STATUS (v{version}) ---
# .opencode/  -> first-class primary agent engine, now targeting OpenCode v2
#                (@opencode/cli + @opencode/plugin; the v1 line opencode-ai /
#                @opencode-ai/plugin is retired). tools/opencode_engine.py
#                regenerates agents, commands, on-demand skills under
#                `.opencode/skills/` for `.kilo/instruction/*.md`, and a config
#                split across root `opencode.json` (v1-safe: model + legacy
#                `permission`) and `.opencode/opencode.json` (v2-only
#                `providers`) so the host IDE's bundled OpenCode v1 stays clean.
# .copilot/   -> NOT auto-generated. Manually kept in parity with .kilo/
#                (source of truth); verified by tools/garden.py drift checks.
# codex       -> direct CLI integration via codex-env.ps1 and tools/codex_*.py.
#                Codex has no project lifecycle hooks, so lifecycle and gates
#                run through the launcher/explicit verification entrypoints.
# jcode       -> RETIRED in v4.1.0. Launcher jcode.ps1 removed; no dedicated
#                harness dir. Older deploys are cleaned via the
#                RETIRED_ROOT_FILES entry in tools/deploy.py.
"""

# How [shared_files] is populated. The two modes differ on purpose: a target
# receives a subset, this repo IS the harness and declares everything it tracks.
ROOT_SHARED_NOTE = """\
# Đường dẫn chính xác của file harness nằm trong shared_dirs. Repo này CHÍNH LÀ
# harness, nên mọi file được git theo dõi trong ba thư mục trên đều là harness.
# Một bản deploy sang dự án đích chỉ khai báo tập con thực sự được ship.
#
# GIỮ BẰNG TAY — KHÔNG regenerate bằng đường deploy: `_shared_harness_paths()`
# chỉ ship những gì một dự án đích nhận được, nên nó BỎ MẤT các entry mà lock gốc
# cố ý giữ (tools/deploy.py, garden.py, test_e2e.py, các test suite,
# .github/workflows/*). Regenerate lock gốc bằng `--root-lock`, không bằng deploy.
"""
TARGET_SHARED_NOTE = """\
# Đường dẫn chính xác của file harness nằm trong shared_dirs. Danh sách này
# được sinh ra từ nội dung deploy thực tế, không phải viết tay.
"""

HARNESS_LOCK_TEMPLATE = """# Solo-Code Harness Manifest
# ===========================
# File này đánh dấu ranh giới giữa harness infrastructure và code dự án.
# AI Model: đọc file này trước khi phân tích codebase.
#
# MỌI FILE/THƯ MỤC trong [boundaries] dirs là HARNESS — KHÔNG phải code dự án.
# Không sửa, không phân tích như logic nghiệp vụ.
# .github/, .vscode/, tools/ là thư mục DÙNG CHUNG — chỉ những đường dẫn trong
# [shared_files] mới là harness; file khác ở đó là CODE DỰ ÁN.

[harness]
version = "{version}"
source = "solo-code-io/solo-code-cli"
generated_at = "{timestamp}"
mode = "{mode}"
content_fingerprint = "{content_fingerprint}"
# sha256 over the sorted (path, content) pairs of [shared_files]; BOM stripped, CRLF->LF.

{engine_status}
[boundaries]
# Thư mục harness — toàn bộ nội dung là harness, KHÔNG chứa code dự án
dirs = [
    ".codex",
    ".kilo",
    ".copilot",
    ".gemini",
{local_only_dirs}    ".claude",
    ".claude-plugin",
    ".opencode",
    ".contracts",
]

# Thư mục DÙNG CHUNG — harness đặt file vào đây, nhưng dự án CŨNG sở hữu file
# riêng ở đây (CI workflow, CODEOWNERS, script dev, cấu hình editor...).
#
# AI Model: KHÔNG được coi cả thư mục này là harness. Chỉ những đường dẫn
# liệt kê trong [shared_files] mới là harness; MỌI file khác trong các thư mục
# này là CODE DỰ ÁN — đọc, phân tích và sửa như bình thường.
shared_dirs = [
    ".github",
    ".vscode",
    "tools",
]

[shared_files]
{shared_note}paths = [
{shared_files}
]

# File gốc harness — KHÔNG phải code dự án
files = [
    "AGENTS.md",
    "CLAUDE.md",
    "agent.yaml",
    "kilo.jsonc",
    "opencode.json",
    ".mcp.json",
    ".ruff.toml",
    ".gitleaks.toml",
    "eslint.config.js",
    "Makefile",
    "pyproject.toml",
    "claude-env.ps1",
    "codex-env.ps1",
    "opencode-env.ps1",
    "init.sh",
    ".env.template",
    "verify.sh",
    "extensions_config.json",
    ".gitignore",
    ".pre-commit-config.yaml",
]

# File đánh dấu dành riêng cho harness
markers = [
    ".harness.lock",
    ".solocode",
{bridge_marker}]
"""


def _transform_agent_frontmatter(source_text: str, filename: str) -> str:
    """Transform OpenCode/Kilo agent YAML frontmatter to Copilot .agent.md format.

    OpenCode format uses: mode, color, permission (allow/deny per tool)
    Copilot format uses: description, user-invocable, tools (list of allowed tools)
    """
    body_parts = source_text.split("---", 2)
    if len(body_parts) < 3:
        # No valid YAML frontmatter — return as-is with minimal header
        return f'---\ndescription: "Agent: {filename}"\n---\n{source_text.strip()}'

    raw_yaml = body_parts[1]
    body_md = body_parts[2]

    # Parse key fields from OpenCode/Kilo frontmatter
    description = ""
    mode = "subagent"
    permissions: dict[str, str] = {}
    nested_perms: dict[str, dict[str, str]] = {}
    task_perms: dict[str, str] = {}

    for line in raw_yaml.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith("description:"):
            description = stripped.split(":", 1)[1].strip().strip('"')
        elif stripped.startswith("mode:"):
            mode = stripped.split(":", 1)[1].strip()

    # If no description, derive one from filename
    if not description:
        description = " ".join(
            w.capitalize() if i == 0 else w
            for i, w in enumerate(filename.replace("-", " ").split())
        )

    # Parse permission block (OpenCode/Kilo nested structure)
    lines = raw_yaml.strip().splitlines()
    in_permission = False
    current_tool = ""
    in_task = False

    for _i, line in enumerate(lines):
        stripped = line.strip()
        # Detect top-level keys to track scope
        if stripped.startswith("permission:"):
            in_permission = True
            in_task = False
            continue
        if not in_permission:
            continue
        # Detect when we leave the permission block (new top-level key at column 0)
        if not stripped.startswith(" ") and ":" in stripped:
            key = stripped.split(":", 1)[0].strip()
            if (
                key
                not in (
                    "edit",
                    "bash",
                    "read",
                    "grep",
                    "codesearch",
                    "glob",
                    "task",
                    "*",
                )
                and not stripped.startswith('"')
                and not stripped.startswith("'")
            ):
                in_permission = False
                continue
        # Handle task sub-block
        if stripped.startswith("task:"):
            in_task = True
            current_tool = ""
            continue
        if in_task:
            if (
                ":" in stripped
                and not stripped.startswith('"')
                and not stripped.startswith("'")
            ):
                parts = stripped.split(":", 1)
                task_key = parts[0].strip()
                task_val = parts[1].strip() if len(parts) > 1 else ""
                if task_val:
                    task_perms[task_key] = task_val
                continue
            elif ":" in stripped:
                parts = stripped.split(":", 1)
                task_val = parts[1].strip() if len(parts) > 1 else ""
                if task_val:
                    task_perms[parts[0].strip().strip('"')] = task_val
                continue
            else:
                in_task = False
                continue
        # Handle tool: value (simple case, e.g., "  read: allow")
        if ":" in stripped:
            parts = stripped.split(":", 1)
            key = parts[0].strip()
            val = parts[1].strip() if len(parts) > 1 else ""
            if key in ("edit", "read", "grep", "bash", "glob", "codesearch"):
                if val and val in ("allow", "ask"):
                    nested_perms.setdefault(key, {})["*"] = val
                    permissions[key] = val
                elif not val:
                    current_tool = key
                continue
            # Nested rule under a tool (e.g., '  "*": ask')
            if current_tool:
                cleaned_key = key.strip('"').strip("'")
                if val:
                    nested_perms.setdefault(current_tool, {})[cleaned_key] = val
                continue

    # Map OpenCode permissions to Copilot tool aliases
    tool_map = {
        "edit": "Edit",
        "read": "Read",
        "grep": "Grep",
        "bash": "Bash",
        "glob": "Glob",
    }
    allowed_tools: list[str] = []
    for perm_key in ("read", "edit", "grep", "bash", "glob"):
        if perm_key in nested_perms:
            # Include if any rule allows (or asks) — Copilot doesn't distinguish
            perms = nested_perms[perm_key]
            if (
                any(v in ("allow", "ask") for v in perms.values())
                and perm_key in tool_map
            ):
                allowed_tools.append(tool_map[perm_key])
        elif perm_key in permissions and permissions[perm_key] in ("allow", "ask"):
            if perm_key in tool_map:
                allowed_tools.append(tool_map[perm_key])

    # Build Copilot-format frontmatter
    copilot_frontmatter = f'description: "{description}"\n'
    if mode == "primary":
        copilot_frontmatter += "user-invocable: true\n"
    else:
        copilot_frontmatter += "user-invocable: false\n"
    if allowed_tools:
        tools_str = ", ".join(allowed_tools)
        copilot_frontmatter += f"tools: [{tools_str}]\n"
    # Map task permissions to Copilot agents restriction
    if task_perms:
        allowed_agents = [k for k, v in task_perms.items() if v == "allow"]
        if allowed_agents:
            agents_str = ", ".join(allowed_agents)
            copilot_frontmatter += f"agents: [{agents_str}]\n"

    return f"---\n{copilot_frontmatter}---\n{body_md.strip()}\n"


def _copy_copilot_agents_to_github(
    target: Path, dry_run: bool = False
) -> tuple[int, int]:
    """Copy .copilot/agents/*.md → .github/agents/*.agent.md for Copilot discovery.

    Copilot reads custom agents from .github/agents/*.agent.md, not .copilot/agents/.
    Returns (new_count, updated_count).
    """
    copilot_agents_src = ROOT / ".copilot" / "agents"
    github_agents_dst = target / ".github" / "agents"

    if not copilot_agents_src.is_dir():
        return 0, 0

    new_count, updated_count = 0, 0
    github_agents_dst.mkdir(parents=True, exist_ok=True)

    for src_file in sorted(copilot_agents_src.glob("*.md")):
        dst_name = src_file.stem + ".agent.md"
        dst_file = github_agents_dst / dst_name

        if dry_run:
            exists = "[UPD]" if dst_file.exists() else "[NEW]"
            print(
                f"  [DRY] {exists} .github/agents/{dst_name} (from .copilot/agents/{src_file.name})"
            )
            new_count += 1
            continue

        source_text = src_file.read_text(encoding="utf-8")
        transformed = _transform_agent_frontmatter(source_text, src_file.stem)

        old_text = dst_file.read_text(encoding="utf-8") if dst_file.exists() else ""

        if old_text != transformed:
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            dst_file.write_text(transformed, encoding="utf-8")
            if old_text:
                print(f"  [UPD] .github/agents/{dst_name}")
                updated_count += 1
            else:
                print(f"  [NEW] .github/agents/{dst_name}")
                new_count += 1

    return new_count, updated_count


BLANK_MEMORY_MD = """# Memory Index

> Persistent cross-session memory for THIS project. The AI reads this at
> session start and writes to it via `/remember`. Starts blank — Solo-Code-
> CLI's own accumulated memory is never deployed into target projects.

## Project
- [project] (add your branch naming / workflow conventions here)

## Rules
- [rules] Load AGENTS.md for all behavior rules -> [[AGENTS.md]]

## Decisions
- (record key architectural decisions here as they happen)
"""

BLANK_PROJECT_CONVENTIONS_MD = """---
type: project
created: {timestamp}
updated: {timestamp}
---

# Project Conventions

> Blank template — fill in as this project's own conventions emerge.
> Do NOT copy Solo-Code-CLI's own conventions here; they belong to a
> different codebase.

## Git Workflow

- (define branch naming / commit conventions for this project)

## Code Style

- (define this project's code style rules)
"""

BLANK_DECISIONS_ARCHIVE_MD = """---
type: project
created: {timestamp}
---

# Decisions Archive

> Cold storage for decisions pruned out of `MEMORY.md` to stay under the
> `memory_gate` hard cap (8,000 chars). NOT loaded automatically into any
> session — unlike `MEMORY.md`, this file has no size limit and no
> auto-injection. Grep this file on demand for the "why" behind an old
> decision that `git log` alone makes hard to find.
>
> Workflow: when `MEMORY.md`'s Decisions section approaches the cap, MOVE
> (don't delete) the oldest/least-referenced entry here verbatim, then keep
> pruning until back under the WARN threshold (4,000 chars).

## Decisions

- (moved-out entries land here as this project's memory grows)
"""

# memory/ dirs that get a fresh blank template on scaffold/deploy instead of
# Solo-Code-CLI's own accumulated memory content (MEMORY.md, project-
# conventions.md, decisions-archive.md excluded from copy via EXCLUDE_FILES;
# written fresh here).
MEMORY_DIRS = [".kilo/memory", ".claude/memory", ".copilot/memory"]


def _write_blank_memory_templates(target: Path, dirs: list[str], dry_run: bool) -> int:
    """Write fresh blank MEMORY.md/project-conventions.md/decisions-archive.md
    into each deployed engine's memory/ dir, instead of letting Solo-Code-
    CLI's own accumulated project memory leak into the target project."""
    written = 0
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Only write memory for engines whose full root is deployed — a partial
    # path like ".claude/skills" (OpenCode's Claude-compatible skills source)
    # must not create a stray ".claude/memory/" the engine never reads.
    deployed_roots = set(dirs)
    for mem_rel in MEMORY_DIRS:
        engine_dir = mem_rel.split("/")[0]
        if engine_dir not in deployed_roots:
            continue
        mem_dir = target / mem_rel
        for fname, content in (
            ("MEMORY.md", BLANK_MEMORY_MD),
            (
                "project-conventions.md",
                BLANK_PROJECT_CONVENTIONS_MD.format(timestamp=timestamp),
            ),
            (
                "decisions-archive.md",
                BLANK_DECISIONS_ARCHIVE_MD.format(timestamp=timestamp),
            ),
        ):
            dst = mem_dir / fname
            if dst.exists():
                continue  # never overwrite existing project memory
            if dry_run:
                print(f"  [DRY] {mem_rel}/{fname} — would write blank template")
            else:
                mem_dir.mkdir(parents=True, exist_ok=True)
                dst.write_text(content, encoding="utf-8")
                print(f"  [NEW] {mem_rel}/{fname} — blank template")
            written += 1
    return written


def _shared_harness_paths(dirs: list[str]) -> list[str]:
    """Exact paths of harness files that land inside SHARED_DIRS.

    Derived from what the deploy actually ships, so the boundary a model
    reads in .harness.lock cannot drift from the files on disk.
    """
    paths: set[str] = set()
    for d in dirs:
        if d not in SHARED_DIRS:
            continue
        src = ROOT / d
        if not src.is_dir():
            continue
        for item in src.rglob("*"):
            if item.is_file() and should_copy(item):
                paths.add(item.relative_to(ROOT).as_posix())
    # .github/agents/*.agent.md are generated at deploy time from
    # .copilot/agents/, so they never appear in the source tree.
    copilot_agents = ROOT / ".copilot" / "agents"
    if ".github" in dirs and copilot_agents.is_dir():
        for f in copilot_agents.glob("*.md"):
            paths.add(f".github/agents/{f.stem}.agent.md")
    return sorted(paths)


def _root_shared_paths(root: Path | None = None) -> list[str]:
    """Every tracked file under SHARED_DIRS — the ROOT lock's ``[shared_files]``.

    ``_shared_harness_paths`` answers "what does a target project receive", which
    excludes the test suites and the harness's own dev tooling. This repo IS the
    harness, so its root lock declares everything git tracks in those directories.
    Regenerating the root lock through the deploy path therefore dropped 39
    boundaries (measured); this is the function that keeps them.
    """
    base = root or ROOT
    proc = subprocess.run(  # noqa: S603 — fixed argv
        ["git", "-C", str(base), "ls-files", *sorted(SHARED_DIRS)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files failed for {base}: {proc.stderr.strip()}")
    return sorted(set(proc.stdout.split()))


def _generate_harness_lock(
    target: Path,
    dry_run: bool = False,
    dirs: list[str] | None = None,
    *,
    root_mode: bool = False,
) -> None:
    """Generate .harness.lock with current timestamp and version.

    ``root_mode`` writes THIS repo's lock: every tracked file under the shared
    dirs, plus the note explaining why it is maintained by hand. Without it the
    lock describes a deployed target, whose shared list is a subset.
    """
    if dry_run:
        print("  [DRY] Would generate: .harness.lock")
        return

    # Read version from source pyproject.toml
    version = "3.6.0"
    src_pyproject = ROOT / "pyproject.toml"
    if src_pyproject.is_file():
        for line in src_pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped == 'dynamic = ["version"]':
                # hatch-vcs — use fallback
                continue
            if stripped.startswith("fallback-version"):
                version = stripped.split("=", 1)[1].strip().strip('"')
                break

    shared = (
        _root_shared_paths()
        if root_mode
        else _shared_harness_paths(dirs if dirs is not None else DIRS_ALL)
    )
    fingerprint_root = ROOT if root_mode else target
    if not root_mode:
        # A target is not the harness: it receives only what this deploy
        # actually shipped. Declaring a path the engine did not produce (e.g.
        # .github/agents/*.agent.md for a non-copilot engine) would make the
        # fingerprint demand a file that was never copied and abort the deploy
        # after the copy had already happened.
        shipped = [p for p in shared if (target / p).is_file()]
        absent = len(shared) - len(shipped)
        if absent:
            print(
                f"  [WARN] {absent} declared harness path(s) were not shipped "
                "to this target; excluded from .harness.lock"
            )
        shared = shipped
    mode = "root" if root_mode else "target"
    content_fingerprint = harness_content_fingerprint(shared, fingerprint_root)
    shared_block = "\n".join(f'    "{p}",' for p in shared)
    note = ROOT_SHARED_NOTE if root_mode else TARGET_SHARED_NOTE
    # `.agents` is a junction the IDE creates locally, so a target never receives
    # it and must not declare it — but this repo's lock must keep declaring it.
    local_only = ""
    if root_mode and LOCAL_ONLY_HARNESS_DIRS:
        local_only = "".join(f'    "{d}",\n' for d in sorted(LOCAL_ONLY_HARNESS_DIRS))
    # The dsh bridge is harness content that lives in the repo rather than an engine
    # dir. A target project never receives it, so only the root lock declares it.
    bridge_marker = '    "integrations",\n' if root_mode else ""

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = HARNESS_LOCK_TEMPLATE.format(
        version=version,
        timestamp=timestamp,
        mode=mode,
        engine_status=HARNESS_ENGINE_STATUS.format(version=version),
        shared_note=note,
        content_fingerprint=content_fingerprint,
        local_only_dirs=local_only,
        bridge_marker=bridge_marker,
        shared_files=shared_block,
    )
    lock_path = target / ".harness.lock"
    lock_path.write_text(content, encoding="utf-8", newline="\n")
    label = "root" if root_mode else "target"
    print(
        f"  [NEW] .harness.lock — generated ({label}, v{version}, "
        f"{len(shared)} shared paths)"
    )


# ═══════════════════════════════════════════════════════════════════════
# Core logic
# ═══════════════════════════════════════════════════════════════════════


def should_copy(path: Path) -> bool:
    """Check if a file/dir should be copied (not excluded)."""
    name = path.name
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return False
    if path.is_file() and name in EXCLUDE_FILES:
        return False
    # Harness test suites test THIS repo's tooling, never the target project.
    # Excluded by rule, not by an enumerated list that goes stale (see the
    # note in EXCLUDE_FILES).
    if path.is_file() and name.startswith("test_") and name.endswith(".py"):
        return False
    if (
        path.is_file()
        and path.parent.name in ("inbox", "outbox")
        and (name.endswith("-plan.md") or name.endswith("-report.md"))
    ):
        # Solo-Code-CLI's own accumulated Claude<->Gemini task handoffs
        # (.gemini/antigravity/handoff/{inbox,outbox}/) -- per-task history
        # specific to THIS repo's own development, not the target project's.
        # The protocol itself (README.md, .gitkeep) still deploys; only
        # accumulated *-plan.md/*-report.md instances are excluded.
        return False
    return not (path.is_file() and name.endswith(".pyc"))


def _merge_list_file(src: Path, dst: Path, dry_run: bool, base: Path) -> str:
    """Write `src` only when `dst` is absent; otherwise append just the missing lines.

    For line-oriented root files a project also legitimately owns - `.gitignore` today. A deploy
    cannot know which of a project's rules matter, and replacing the file silently dropped five of
    them once (measured: *.log, logs/, a project path, *.exe, .kilo/worktrees/). So the project's
    file is kept and the rules the harness depends on (`.env`, `.solocode/`, caches) are guaranteed
    present. Idempotent: a second deploy finds nothing missing and writes nothing.
    """
    rel = dst.relative_to(base)
    if not dst.is_file():
        if dry_run:
            return f"  [DRY] {rel} (written: target has none)"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return f"  [NEW] {rel}"

    # newline="" so a CRLF file is seen as CRLF: opening without it would translate the endings to LF
    # and the appended block would then change every line ending in the file (caught by a test).
    with dst.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read()
    existing = {ln.strip() for ln in text.splitlines()}
    with src.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        wanted = fh.read().splitlines()
    # Comment-only lines are documentation, not rules. Targets often carry the same comment with a
    # different dash character, and appending those would duplicate the file for no behavioural
    # gain - the marker line already explains why rules were added.
    missing = [
        ln
        for ln in wanted
        if ln.strip() and not ln.strip().startswith("#") and ln.strip() not in existing
    ]
    if not missing:
        return f"  [SKIP] {rel} - already has every harness rule"

    if dry_run:
        return f"  [DRY] {rel} - would append {len(missing)} missing harness rule(s)"

    eol = "\r\n" if "\r\n" in text else "\n"
    # a file that does not end in a newline needs one before the marker block starts
    eol_prefix = eol if text and not text.endswith(("\n", "\r")) else ""
    block = (
        eol_prefix
        + eol
        + "# --- added by the Solo-Code Harness deploy: the rules below were missing ---"
        + eol
        + eol.join(missing)
        + eol
    )
    with dst.open("a", encoding="utf-8", newline="") as fh:
        fh.write(block)
    return f"  [MRG] {rel} - appended {len(missing)} missing harness rule(s)"


def _write_if_absent(src: Path, dst: Path, dry_run: bool, base: Path) -> str:
    """Copy `src` only when `dst` does not exist; never replace a file the project owns.

    For structured root files (see PROJECT_OWNED_ROOT_FILES) there is no line-set merge to apply,
    so the honest move is to leave the project's copy alone and tell the operator it was kept.
    """
    rel = dst.relative_to(base)
    if not dst.is_file():
        if dry_run:
            return f"  [DRY] {rel} (written: target has none)"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return f"  [NEW] {rel}"

    try:
        same = dst.read_bytes() == src.read_bytes()
    except OSError:
        same = False
    if same:
        return f"  [SKIP] {rel} - identical to the harness copy"
    return f"  [KEEP] {rel} - target's own file kept (it differs from the harness copy)"


def copy_file(src: Path, dst: Path, dry_run: bool, base: Path | None = None) -> str:
    """Copy a single file. Returns status string.

    Args:
        src: Source file to copy.
        dst: Destination path.
        dry_run: If True, only print what would be done.
        base: Base path for display (defaults to dst.parent).
    """
    display = base or dst.parent
    rel = dst.relative_to(display) if display else dst.name

    if dry_run:
        return f"  [DRY] {rel}"

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.copy2(src, dst)
        return f"  [UPD] {rel}"
    else:
        shutil.copy2(src, dst)
        return f"  [NEW] {rel}"


def copy_tree(src: Path, dst: Path, dry_run: bool) -> tuple[int, int, list[str]]:
    """Recursively copy a directory tree. Returns (new, updated, skipped)."""
    new, updated, skipped = 0, 0, []

    if not src.is_dir():
        return 0, 0, [f"  [SKIP] {src} — not found"]

    for item in src.rglob("*"):
        if not should_copy(item):
            continue

        rel = item.relative_to(src)
        target = dst / rel

        if item.is_dir():
            if not dry_run:
                target.mkdir(parents=True, exist_ok=True)
        else:
            if dry_run:
                skipped.append(f"  [DRY] {target}")
                new += 1
            elif target.exists():
                shutil.copy2(item, target)
                updated += 1
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                new += 1

    return new, updated, skipped


def _run_command(cmd: list[str], cwd: Path, label: str, dry_run: bool) -> bool:
    """Run a shell command, return True on success."""
    if dry_run:
        print(f"  [DRY] $ {' '.join(cmd)}")
        return True

    print(f"  [{label}] Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=1800
        )
    except subprocess.TimeoutExpired:
        print(f"  [{label}] FAILED (timeout after 1800s)")
        return False
    if result.returncode != 0:
        print(f"  [{label}] FAILED (exit {result.returncode})")
        if result.stderr.strip():
            for line in result.stderr.strip().splitlines()[:10]:
                print(f"    {line}")
        return False
    if result.stdout.strip():
        for line in result.stdout.strip().splitlines()[:5]:
            print(f"    {line}")
    print(f"  [{label}] OK")
    return True


# ═══════════════════════════════════════════════════════════════════════
# Scaffold mode — create new project from scratch
# ═══════════════════════════════════════════════════════════════════════


def _print_opencode_skills_note(dirs: list[str]) -> None:
    """Explain the partial `.claude/` dir that the OpenCode engine ships.

    OpenCode loads the Claude-compatible `.claude/skills/` location natively,
    so `--engine opencode` ships it. In that case the target's `.claude/` is a
    skills directory only (no agents/hooks) — say so, so it is not mistaken
    for a full Claude Code setup.
    """
    if ".claude/skills" not in dirs:
        return
    print("\n  NOTE: .claude/skills/ ships with the OpenCode engine because OpenCode")
    print("  loads the Claude-compatible skills location. That makes .claude/ here a")
    print(
        "  PARTIAL copy (skills only, no agents/hooks) — not a full Claude Code setup."
    )


def scaffold(
    target: str,
    *,
    engine: str = "all",
    dry_run: bool = False,
    project_name: str | None = None,
    description: str = "A Solo-Code harness project with AI agent discipline.",
) -> int:
    """Create a brand-new project with full harness scaffolding.

    Steps:
      1. Create target directory (if missing)
      2. Initialize git repository
      3. Copy all harness files (root + engine dirs)
      4. Generate README.md
      5. Run post-scaffold validation
      6. Show next steps
    """
    target_path = Path(target).resolve()
    project_name = project_name or target_path.name

    # ── Step 0: Check target ─────────────────────────────────────
    if target_path.exists() and not target_path.is_dir():
        print(f"[ERROR] Target exists and is not a directory: {target_path}")
        return 1

    exists = target_path.exists()
    mode = "DRY RUN" if dry_run else "LIVE"

    print(f"{'=' * 60}")
    print(f"  Solo-Code Harness Scaffold ({mode})")
    print(f"{'=' * 60}")
    print(f"  Project : {project_name}")
    print(f"  Target  : {target_path}")
    print(f"  Engine  : {engine}")
    print()

    # ── Step 1: Create target directory ──────────────────────────
    if not dry_run and not exists:
        target_path.mkdir(parents=True, exist_ok=True)
        print(f"  [DIR] Created: {target_path}")
    elif dry_run and not exists:
        print(f"  [DRY] Would create: {target_path}")

    # ── Step 2: Copy harness files ───────────────────────────────
    if engine == "kilo":
        dirs = DIRS_KILO
    elif engine == "copilot":
        dirs = DIRS_COPILOT
    elif engine == "claude":
        dirs = DIRS_CLAUDE
    elif engine == "opencode":
        dirs = DIRS_OPENCODE
    elif engine == "codex":
        dirs = DIRS_CODEX
    else:
        dirs = DIRS_ALL

    total_new, total_upd, total_skip = 0, 0, 0

    print("--- Root Files ---")
    for f in ROOT_FILES:
        src = ROOT / f
        if not src.exists():
            print(f"  [SKIP] {f} — not found (source)")
            total_skip += 1
            continue
        if f in MERGE_ROOT_FILES:
            status = _merge_list_file(src, target_path / f, dry_run, base=target_path)
        elif f in PROJECT_OWNED_ROOT_FILES:
            status = _write_if_absent(src, target_path / f, dry_run, base=target_path)
        else:
            status = copy_file(src, target_path / f, dry_run, base=target_path)
        print(status)
        if "[NEW]" in status or "[DRY]" in status:
            total_new += 1
        elif "[UPD]" in status or "[MRG]" in status:
            total_upd += 1
        elif "[KEEP]" in status:
            total_skip += 1

    # Copy .gitignore last so it can't be overwritten by dir copies
    # Already handled in ROOT_FILES

    for d in dirs:
        src = ROOT / d
        name = d.rstrip("/").replace("\\", "/")
        print(f"\n--- {name}/ ---")
        dst = target_path / d
        n, u, skipped = copy_tree(src, dst, dry_run)

        for s in skipped[:5]:
            print(s)
        if len(skipped) > 5:
            print(f"  ... and {len(skipped) - 5} more files")

        total_new += n
        total_upd += u
        total_skip += len(skipped)

        if not dry_run:
            print(f"  Copied: {n} new, {u} updated")

    _print_opencode_skills_note(dirs)

    # ── Step 2.4: Generate .github/agents/ for Copilot ───────────
    if engine in ("all", "copilot"):
        print("\n--- .github/agents/ (Copilot) ---")
        cn, cu = _copy_copilot_agents_to_github(target_path, dry_run)
        total_new += cn
        total_upd += cu

    # ── Step 2.45: Blank memory templates (never leak our own memory) ──
    print("\n--- Project Memory (blank templates) ---")
    total_new += _write_blank_memory_templates(target_path, dirs, dry_run)

    # ── Step 2.46: Setup executor mode config ─────────────────
    print("\n--- .solocode/ (executor config) ---")
    solocode_dir = target_path / ".solocode"
    if not dry_run:
        solocode_dir.mkdir(exist_ok=True)

    # Copy executor config templates
    executor_config_src = ROOT / ".solocode" / "executor-config.json"
    executor_mode_src = ROOT / ".solocode" / "executor-mode"

    if executor_config_src.exists():
        status = copy_file(
            executor_config_src,
            solocode_dir / "executor-config.json",
            dry_run,
            base=target_path,
        )
        print(status)
        if "[NEW]" in status:
            total_new += 1

    if executor_mode_src.exists():
        status = copy_file(
            executor_mode_src, solocode_dir / "executor-mode", dry_run, base=target_path
        )
        print(status)
        if "[NEW]" in status:
            total_new += 1

    # ── Step 2.5: Generate .harness.lock ─────────────────────────
    print("\n--- .harness.lock ---")
    _generate_harness_lock(target_path, dry_run, dirs)
    total_new += 1

    # ── Step 3: Generate README.md (only if not exists) ──────────
    readme_path = target_path / "README.md"
    if not dry_run and not readme_path.exists():
        readme_content = README_TEMPLATE.format(
            project_name=project_name,
            description=description,
        )
        readme_path.write_text(readme_content, encoding="utf-8")
        print("\n  [NEW] README.md — generated")
        total_new += 1
    elif dry_run and not readme_path.exists():
        print("\n  [DRY] Would generate: README.md")

    # ── Step 4: Create tools/ directory for harness scripts ──────
    # The script itself is already copied via .github/ but we also
    # need deploy.py, generate_harness.py etc. in the new project.
    # Those live inside .github/scripts/ or tools/ — already handled
    # by the dir copies above.

    # ── Step 5: Git init ─────────────────────────────────────────
    git_dir = target_path / ".git"
    if not git_dir.exists():
        ok = _run_command(
            ["git", "init"],
            cwd=target_path,
            label="GIT",
            dry_run=dry_run,
        )
        if ok and not dry_run:
            # Set default branch to main
            _run_command(
                ["git", "checkout", "-b", "main"],
                cwd=target_path,
                label="GIT",
                dry_run=dry_run,
            )

    # ── Step 6: Create initial commit message ────────────────────
    if not dry_run and git_dir.exists():
        # Check if there's anything to commit
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(target_path),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            result = subprocess.CompletedProcess([], returncode=1, stdout="", stderr="")
        if result.stdout.strip():
            _run_command(
                ["git", "add", "."],
                cwd=target_path,
                label="GIT",
                dry_run=False,
            )
            _run_command(
                [
                    "git",
                    "commit",
                    "-m",
                    f"Initial scaffold: Solo-Code Harness ({engine} engine)\n\nCo-Authored-By: Solo-Code <admin@solo-code.com>",
                ],
                cwd=target_path,
                label="GIT",
                dry_run=False,
            )

    # ── Summary ─────────────────────────────────────────────────
    print()
    print("=" * 60)
    if dry_run:
        print(f"  DRY RUN — would scaffold project with {total_new} new files")
    else:
        print(
            f"  Scaffolded: {total_new} new, {total_upd} updated, {total_skip} skipped"
        )
    print("=" * 60)

    # ── Post-scaffold instructions ───────────────────────────────
    if not dry_run:
        print()
        print("  " + "=" * 56)
        print("  POST-SCAFFOLD SETUP")
        print("  " + "=" * 56)
        print()
        print(f"  cd {target_path}")
        print()
        print("  1. Fill in .env from .env.template (API keys)")
        print()
        print("  2. Run security scan:")
        print("     python .github/scripts/security_scan.py .")
        print()
        print("  3. Run full checklist:")
        print("     python .github/scripts/checklist.py .")
        print()
        print("  4. Open in your editor:")
        print("     code .")
        print()
        print("  Note: this is a runtime-only harness copy. Dev tooling for")
        print("  REGENERATING harness artifacts (deploy.py, garden.py,")
        print("  generate_harness.py) lives only in solo-code-io/solo-code-cli —")
        print("  not deployed here on purpose. Edit .kilo/ (source of truth)")
        print("  directly if you need to customize agents/skills/commands.")
        print()
        print("  " + "=" * 56)
        print("  Happy Solo-Coding!")
        print("  " + "=" * 56)

    return 0


# ═══════════════════════════════════════════════════════════════════════
# Deploy mode — copy harness to existing project
# ═══════════════════════════════════════════════════════════════════════


def _build_source_manifest(dirs: list[str]) -> set[str]:
    """Build a set of relative file paths (forward-slash) from source harness dirs."""
    manifest: set[str] = set()
    for d in dirs:
        src = ROOT / d
        if not src.is_dir():
            continue
        for item in src.rglob("*"):
            if not should_copy(item):
                continue
            if item.is_file():
                rel = item.relative_to(ROOT).as_posix()
                manifest.add(rel)
    # Add root files (relative to ROOT)
    for f in ROOT_FILES:
        src = ROOT / f
        if src.is_file():
            manifest.add(f.replace("\\", "/"))
    # .harness.lock is always regenerated — never stale
    manifest.add(".harness.lock")
    return manifest


def _cleanup_stale_files(
    target_path: Path,
    dirs: list[str],
    dry_run: bool,
) -> int:
    """Remove files the harness previously deployed but no longer ships.

    Two different rules apply, because target directories are not equal:

    * EXCLUSIVE_HARNESS_DIRS (.kilo, .claude, ...) are wholly owned by the
      harness — anything not in the current manifest is a stale leftover.
    * SHARED_DIRS (.github, .vscode, tools) are shared with the project. It
      keeps its own workflows, CODEOWNERS, dependabot config, editor settings
      and dev scripts there, so only explicitly RETIRED_SHARED_FILES may go.

    Returns number of stale files removed (or would-be-removed in dry_run).
    """
    source_manifest = _build_source_manifest(dirs)
    removed = 0

    # Collect target files inside harness dirs
    for d in dirs:
        dst = target_path / d
        if not dst.is_dir():
            continue
        shared = d in SHARED_DIRS
        for item in dst.rglob("*"):
            if item.is_file():
                rel = item.relative_to(target_path).as_posix()
                # SAFETY INVARIANT (shared dirs): the project owns most of
                # .github/.vscode/tools. Deleting "everything not in the
                # manifest" there would wipe its CI workflows and dev
                # scripts. Only names the harness itself shipped and has
                # since dropped are eligible.
                #
                # Checked BEFORE should_copy(): retired files are typically
                # retired *by being added to EXCLUDE_FILES*, which makes
                # should_copy() false — so the skip below would otherwise
                # make them permanently uncleanable.
                if shared:
                    if rel in RETIRED_SHARED_FILES:
                        if dry_run:
                            print(f"  [STALE] Would remove: {rel}")
                        else:
                            item.unlink()
                            print(f"  [STALE] Removed: {rel}")
                        removed += 1
                    continue
                # Skip files intentionally excluded from copy (personal
                # overrides, .pyc, runtime state) — they are never "stale".
                if not should_copy(item):
                    continue
                # Skip generated directories (not in source manifest)
                if rel.startswith(".github/agents/"):
                    continue
                if rel not in source_manifest:
                    if dry_run:
                        print(
                            f"  [STALE] Would remove: {d}/{item.relative_to(dst).as_posix()}"
                        )
                    else:
                        item.unlink()
                        print(
                            f"  [STALE] Removed: {d}/{item.relative_to(dst).as_posix()}"
                        )
                    removed += 1

    # Check root-level harness files for staleness.
    #
    # SAFETY INVARIANT: at the target root we may ONLY delete a file whose
    # name the harness itself has deployed at some point (current ROOT_FILES
    # or RETIRED_ROOT_FILES). A target project's root is full of files that
    # look "harness-ish" by extension — package.json, tsconfig.json,
    # docker-compose.yml, README.md — and they are NOT ours to remove.
    #
    # Do NOT reintroduce extension-based matching here. Doing so deleted
    # package.json/tsconfig.json/README.md from deployed projects and broke
    # their builds. See test_deploy.py::test_project_root_files_survive_deploy.
    for f_path in target_path.iterdir():
        if not f_path.is_file():
            continue
        name = f_path.name
        if name not in HARNESS_OWNED_ROOT_FILES:
            continue  # project-owned — never ours to delete
        if name in source_manifest:
            continue  # still shipped by the harness — expected
        if dry_run:
            print(f"  [STALE] Would remove root file: {name}")
        else:
            f_path.unlink()
            print(f"  [STALE] Removed root file: {name}")
        removed += 1

    return removed


def _uncommitted_changes(target: Path) -> list[str] | None:
    """Return uncommitted paths in target's git repo, or None if not a repo.

    Empty list means a clean tree. Any git failure is reported as None so a
    missing/broken git never blocks a deploy.
    """
    if not (target / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(target), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def _confirm_unsafe_deploy(target: Path, assume_yes: bool) -> bool:
    """Guard a LIVE deploy into a target with uncommitted work.

    Deploy writes ~485 files and can delete retired harness files. If the
    target has uncommitted changes there is no clean `git checkout` to undo
    it, so require an explicit --yes (or an interactive confirmation).
    """
    dirty = _uncommitted_changes(target)
    if dirty is None:
        print("  [WARN] Target is not a git repository — no undo if this goes wrong.")
        if assume_yes:
            return True
        return _ask_yes_no("Continue anyway?")
    if not dirty:
        return True  # clean tree — `git checkout` can undo everything

    print(f"\n  [WARN] Target has {len(dirty)} uncommitted change(s):")
    for line in dirty[:10]:
        print(f"    {line}")
    if len(dirty) > 10:
        print(f"    ... and {len(dirty) - 10} more")
    print("  Deploy writes many files and removes retired harness files.")
    print("  With a dirty tree you cannot cleanly `git checkout` to undo it.")
    print("  Commit/stash first, or re-run with --dry-run to preview.")
    if assume_yes:
        print("  Proceeding anyway (--yes).")
        return True
    return _ask_yes_no("Proceed anyway?")


def _ask_yes_no(question: str) -> bool:
    if not sys.stdin.isatty():
        print(f"  [ABORT] {question} — not a TTY; re-run with --yes to confirm.")
        return False
    try:
        return input(f"  {question} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print("\n  [ABORT] Cancelled.")
        return False


def deploy(
    target: str,
    *,
    engine: str = "all",
    dry_run: bool = False,
    assume_yes: bool = False,
) -> int:
    """Deploy harness to an existing target directory."""
    target_path = Path(target).resolve()

    if not target_path.exists():
        print(f"[ERROR] Target does not exist: {target_path}")
        return 1

    if not target_path.is_dir():
        print(f"[ERROR] Target is not a directory: {target_path}")
        return 1

    if not dry_run and not _confirm_unsafe_deploy(target_path, assume_yes):
        print("[ABORT] Deploy cancelled — nothing was written.")
        return 1

    # Rulebooks and the env template must stay current (a stale rulebook silently teaches the wrong
    # rules), so they are replaced - but a project may have added sections, and the operator deserves
    # to see that in the output rather than discover it in a diff. Snapshotted before the copy.
    watched_before = {
        name: (target_path / name).read_bytes()
        for name in ("AGENTS.md", "CLAUDE.md", ".env.template")
        if (target_path / name).is_file()
    }

    if engine == "kilo":
        dirs = DIRS_KILO
    elif engine == "copilot":
        dirs = DIRS_COPILOT
    elif engine == "claude":
        dirs = DIRS_CLAUDE
    elif engine == "opencode":
        dirs = DIRS_OPENCODE
    elif engine == "codex":
        dirs = DIRS_CODEX
    else:
        dirs = DIRS_ALL

    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"=== Solo-Code Harness Deploy ({mode}) ===")
    print(f"  Source : {ROOT}")
    print(f"  Target : {target_path}")
    print(f"  Engine : {engine}")
    print()

    total_new, total_upd, total_skip = 0, 0, 0

    print("--- Root Files ---")
    for f in ROOT_FILES:
        src = ROOT / f
        if not src.exists():
            print(f"  [SKIP] {f} — not found")
            total_skip += 1
            continue
        if f in MERGE_ROOT_FILES:
            status = _merge_list_file(src, target_path / f, dry_run, base=target_path)
        elif f in PROJECT_OWNED_ROOT_FILES:
            status = _write_if_absent(src, target_path / f, dry_run, base=target_path)
        else:
            status = copy_file(src, target_path / f, dry_run, base=target_path)
        print(status)
        if "[NEW]" in status or "[DRY]" in status:
            total_new += 1
        elif "[UPD]" in status or "[MRG]" in status:
            total_upd += 1
        elif "[KEEP]" in status:
            total_skip += 1

    for d in dirs:
        src = ROOT / d
        name = d.rstrip("/").replace("\\", "/")
        print(f"\n--- {name}/ ---")
        dst = target_path / d
        n, u, skipped = copy_tree(src, dst, dry_run)

        for s in skipped[:5]:
            print(s)
        if len(skipped) > 5:
            print(
                f"  ... and {len(skipped) - 5} more files (use --dry-run for full list)"
            )

        total_new += n
        total_upd += u
        total_skip += len(skipped)

        if not dry_run:
            print(f"  Copied: {n} new, {u} updated")

    _print_opencode_skills_note(dirs)

    # ── Step 2.4: Generate .github/agents/ for Copilot ───────────
    if engine in ("all", "copilot"):
        print("\n--- .github/agents/ (Copilot) ---")
        cn, cu = _copy_copilot_agents_to_github(target_path, dry_run)
        total_new += cn
        total_upd += cu

        # Clean up stale .github/agents/ files not in .copilot/agents/
        copilot_agents_src = ROOT / ".copilot" / "agents"
        github_agents_dir = target_path / ".github" / "agents"
        if copilot_agents_src.is_dir() and github_agents_dir.is_dir():
            expected_names = {
                f.stem + ".agent.md" for f in copilot_agents_src.glob("*.md")
            }
            stale_removed = 0
            for existing in sorted(github_agents_dir.glob("*.agent.md")):
                if existing.name not in expected_names:
                    if dry_run:
                        print(f"  [STALE] Would remove: .github/agents/{existing.name}")
                    else:
                        existing.unlink()
                        print(f"  [STALE] Removed: .github/agents/{existing.name}")
                    stale_removed += 1

    # ── Step 2.45: Blank memory templates (never leak our own memory) ──
    print("\n--- Project Memory (blank templates) ---")
    _write_blank_memory_templates(target_path, dirs, dry_run)

    # ── Step 2.5: Cleanup stale files ────────────────────────────
    print("\n--- Stale Cleanup ---")
    removed = _cleanup_stale_files(target_path, dirs, dry_run)
    if removed == 0:
        print("  No stale files found.")
    total_removed = removed

    # ── Generate .harness.lock ──────────────────────────────────
    print("\n--- .harness.lock ---")
    _generate_harness_lock(target_path, dry_run, dirs)
    total_new += 1

    print()
    print("=" * 60)
    if dry_run:
        print(
            f"  DRY RUN — would deploy {total_new} files, update {total_upd}, remove {total_removed} stale"
        )
    else:
        print(
            f"  Deployed: {total_new} new, {total_upd} updated, {total_skip} skipped, {total_removed} stale removed"
        )
    print("=" * 60)

    if not dry_run:
        # An upgrade from an earlier harness leaves its root documents behind, and this deploy must
        # not touch them: a target's SPEC.md and docs/ are classified as project-owned, so cleanup
        # may never delete them. Say so instead of silently leaving a stale specification in place.
        legacy = _legacy_harness_leftovers(target_path)
        if legacy:
            print()
            print("  NOTE: this target carries documents from an earlier harness:")
            for item in legacy:
                print(f"        {item}")
            print(
                "        They are project-owned now, so this deploy never removes them -"
            )
            print(
                "        delete them by hand if you want the target free of stale harness docs."
            )

        print()
        print("  Next steps in target project:")
        print(f"    cd {target_path}")
        print("    python .github/scripts/security_scan.py .")
        print("    python .github/scripts/checklist.py .")

        replaced = []
        if not dry_run:
            for name, before in watched_before.items():
                now = target_path / name
                if now.is_file() and now.read_bytes() != before:
                    replaced.append(name)
        if replaced:
            print()
            print("  NOTE: replaced root files that this target had its own copy of:")
            for name in replaced:
                print(f"        {name}")
            print(
                "        They are tracked in git, so `git diff <file>` shows what the project had."
            )
            print(
                "        If the project had added its own sections there, merge them back by hand."
            )

    return 0


# ═══════════════════════════════════════════════════════════════════════
# Interactive mode — ask user what to do
# ═══════════════════════════════════════════════════════════════════════


def interactive() -> int:
    """Run in interactive mode — ask user for details."""
    print("=" * 60)
    print("  Solo-Code Harness — Interactive Setup")
    print("=" * 60)
    print()

    # Ask mode
    while True:
        mode = input("Mode (scaffold/deploy): ").strip().lower()
        if mode in ("scaffold", "deploy"):
            break
        print("  Please enter 'scaffold' or 'deploy'.")

    # Ask target path
    while True:
        target = input("Target path: ").strip()
        if target:
            break
        print("  Path cannot be empty.")

    # Ask engine
    while True:
        engine = (
            input("Engine (all/kilo/copilot/claude/opencode) [all]: ").strip().lower()
        )
        if not engine:
            engine = "all"
            break
        if engine in ("all", "kilo", "copilot", "claude", "opencode"):
            break
        print("  Please enter 'all', 'kilo', 'copilot', 'claude', or 'opencode'.")

    # Ask dry-run
    dry_run_input = input("Dry run? (y/N): ").strip().lower()
    dry_run = dry_run_input in ("y", "yes")

    # Ask project name (only for scaffold)
    project_name = None
    description = "A Solo-Code harness project with AI agent discipline."
    if mode == "scaffold":
        name_input = input(f"Project name [{Path(target).name}]: ").strip()
        if name_input:
            project_name = name_input
        desc_input = input("Description (optional): ").strip()
        if desc_input:
            description = desc_input

    print()

    # Execute
    if mode == "scaffold":
        return scaffold(
            target,
            engine=engine,
            dry_run=dry_run,
            project_name=project_name,
            description=description,
        )
    else:
        return deploy(target, engine=engine, dry_run=dry_run)


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════


def _looks_like_path(value: str) -> bool:
    """Heuristic: does the string look like a filesystem path?"""
    # Contains path separators, or is a dot-relative path, or exists on disk
    if "/" in value or "\\" in value or value in (".", ".."):
        return True
    return bool(Path(value).exists())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deploy or scaffold Solo-Code Harness for new/existing projects.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/deploy.py scaffold /path/to/new-project\n"
            "  python tools/deploy.py deploy /path/to/existing-project\n"
            "  python tools/deploy.py /path/to/target         (auto-detect)\n"
            "  python tools/deploy.py                         (interactive)"
        ),
    )
    parser.add_argument(
        "args",
        nargs="*",
        help="[mode] [target] — mode (scaffold|deploy), target (path to project)",
    )
    parser.add_argument(
        "--engine",
        choices=["all", "kilo", "copilot", "claude", "opencode", "codex"],
        default="all",
        help="Which engine harness to deploy (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be copied without making changes",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Project name (scaffold mode only)",
    )
    parser.add_argument(
        "--description",
        type=str,
        default="A Solo-Code harness project with AI agent discipline.",
        help="Project description (scaffold mode only)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation prompt when deploying into a target with "
        "uncommitted changes (deploy mode only)",
    )
    parser.add_argument(
        "--root-lock",
        action="store_true",
        help="Regenerate THIS repo's .harness.lock (every tracked file in the "
        "shared dirs) and exit. Do not use the deploy path for it: a target "
        "receives a subset, so that path drops boundaries the root lock keeps.",
    )

    args = parser.parse_args()
    positional = args.args

    if args.root_lock:
        _generate_harness_lock(ROOT, dry_run=args.dry_run, root_mode=True)
        return 0

    # ── Interactive mode (no args) ───────────────────────────────
    if not positional:
        return interactive()

    # ── Parse positional arguments manually ──────────────────────
    # Patterns:
    #   "scaffold" /path/to/target   → mode=scaffold, target=/path/to/target
    #   "deploy"   /path/to/target   → mode=deploy,   target=/path/to/target
    #   /path/to/target              → auto-detect mode
    #   /path/to/target "scaffold"   → target first, mode second (we handle this too)

    mode = None
    target = None

    for pos in positional:
        if pos in ("scaffold", "deploy") and mode is None:
            mode = pos
        elif target is None:
            target = pos
        else:
            print(f"[WARN] Extra argument ignored: {pos}")

    # ── Auto-detect mode if only target provided ─────────────────
    if mode is None and target is not None:
        target_path = Path(target).resolve()
        if target_path.exists():
            print(f"[AUTO] Target exists — deploying to: {target_path}")
            mode = "deploy"
        else:
            print(f"[AUTO] Target does not exist — scaffolding: {target_path}")
            mode = "scaffold"

    if target is None:
        print("[ERROR] Target path is required.")
        return 1

    if mode == "scaffold":
        return scaffold(
            target,
            engine=args.engine,
            dry_run=args.dry_run,
            project_name=args.name,
            description=args.description,
        )
    else:
        return deploy(
            target, engine=args.engine, dry_run=args.dry_run, assume_yes=args.yes
        )


if __name__ == "__main__":
    sys.exit(main())
