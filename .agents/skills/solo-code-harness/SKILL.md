---
name: solo-code-harness
description: "Deploy, configure, and maintain the Solo-Code multi-agent harness across Copilot, Claude, Gemini, and Kilo. Use when: deploy harness, setup copilot, setup claude, setup gemini, setup kilo, configure AI agents, install AI harness, solo-code setup, initialize project."
license: MIT
---

# Solo-Code Harness — Multi-Agent AI Coding Framework

You are an expert on the Solo-Code Harness framework. This project provides discipline, safety, and consistency rules for AI coding agents across four platforms: GitHub Copilot (VS Code), Claude Code, Gemini, and Kilo.

---

## Architecture

The harness lives in four platform-specific directories at the project root:

| Platform | Directory | Auto-loads |
|----------|-----------|------------|
| GitHub Copilot / Cursor | `.github/` | `copilot-instructions.md` + PreToolUse hooks |
| Claude Code | `.claude/` | `CLAUDE.md` |
| Gemini | `.gemini/antigravity/` | `AGENTS.md` + skills |
| Kilo | `.kilo/` + `kilo.json` + `AGENTS.md` | `AGENTS.md`, `command/`, `agent/`, `skill/` |

Each harness includes: rulebook, skills (8-9), agents (2), memory, automation scripts.

---

## Deployment

### Quick Deploy (copy-to-project)
Copy this entire repository's root contents (excluding `.git/`, `.github/workflows/`, etc.) into a target project. Each AI tool auto-discovers its respective config directory.

### Deploy Kilo harness only
Copy to target project:
```
AGENTS.md
kilo.json
.kilo/
```

### Deploy Copilot harness only
Copy to target project:
```
.github/
.vscode/
```

---

## Kilo Harness Components

The `.kilo/` directory is auto-discovered by Kilo. No additional config needed.

### Agents (`.kilo/agents/`)
| Agent | Mode | Purpose |
|-------|------|---------|
| `orchestrator.md` | subagent | Breaks down complex tasks, coordinates parallel sub-agents |
| `solo-code-engineer.md` | subagent | Template agent for project customization |

### Commands (`.kilo/command/`)
| Command | Purpose |
|---------|---------|
| `/plan` | Implementation planning with affected files, tasks, risks |
| `/verify` | Prove code works by building, running, and testing |
| `/brainstorm` | Structured idea exploration with 3+ options |
| `/debug` | Evidence-based root cause analysis before fixes |
| `/test` | Test generation and execution (AAA pattern) |
| `/create` | Feature scaffolding from scratch |
| `/enhance` | Safe refactoring of existing code |
| `/remember` | Save project conventions to persistent memory |

### Skills (`.kilo/skill/`)
| Skill | When to load |
|-------|-------------|
| `code-review-expert` | Reviewing code, PRs, diffs; security audits |
| `file-editor-pro` | Editing files, refactoring, precise changes |
| `git-workflow-master` | Committing, pushing, PRs, git safety |
| `permission-guard` | Destructive operations, credentials, config |
| `systematic-debugging` | Debugging, errors, unexpected behavior |
| `brainstorming` | Designing features, architecture decisions |
| `testing-patterns` | Writing tests, TDD, coverage |
| `api-patterns` | API design (REST/GraphQL/tRPC) |

---

## Customization Guide

### For a new project
1. Copy the harness into your project
2. Customize `.kilo/agents/solo-code-engineer.md` with your project's tech stack, architecture map, and conventions
3. Update `.kilo/memory/project-conventions.md` with your project's specific rules
4. Adjust `kilo.json` permissions if your project needs different bash command access

### Adding a new skill
Create `.kilo/skill/<name>/SKILL.md` with YAML frontmatter:
```yaml
name: your-skill-name
description: "When to use this skill"
```
The skill body can use any markdown format Kilo supports.

### Adding a new command
Create `.kilo/command/<name>.md` with YAML frontmatter:
```yaml
description: What the command does
subtask: true
```
The command name is the filename (minus `.md`), invoked via `/name`.

---

## Security Model (Defense in Depth)

The harness uses three layers of protection:

1. **Permission layer** (`kilo.json`) — Deny patterns evaluated before allow patterns. Destructive git/rm/DB/Windows-PowerShell operations blocked at the bash level.
2. **Behavioral layer** (`AGENTS.md`) — Rules instructing the AI: no destructive commands without permission, scan for secrets before commit, no runtime bypass of bash permissions.
3. **Enforcement layer** (skills) — `permission-guard` halts execution for dangerous operations; `git-workflow-master` enforces secret scanning with `security_scan.py` before commits.

---

## Cross-Harness Consistency

When editing shared concepts (behavior rules, security rules, project conventions), update ALL four harnesses:
- `.github/copilot-instructions.md`
- `CLAUDE.md`
- `.gemini/antigravity/AGENTS.md`
- `AGENTS.md` (Kilo root)
