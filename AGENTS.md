---
description: "Solo-Code AI Agent Harness — root rulebook for multi-engine (Kilo + Claude Code + Copilot + Gemini) development"
mode: primary
color: "#166534"
permissions:
    - action: read
      resource: "*"
      effect: allow
    - action: edit
      resource: "*"
      effect: allow
    - action: bash
      resource: "*"
      effect: allow
    - action: glob
      resource: "*"
      effect: allow
    - action: grep
      resource: "*"
      effect: allow
---

# Solo-Code — AI Agent Harness (Root Rulebook)

> **CRITICAL:** Read this file fully before taking any action. These rules are NON-NEGOTIABLE.

This file serves **Kilo** (reads `.kilo/` for hooks/skills/memory — source of truth), **Claude Code** (reads `.claude/` + `CLAUDE.md`, generated from `.kilo/`), **OpenCode** (reads `.opencode/` + `opencode.json`, generated from `.kilo/`; it also loads this file and the vendor-neutral `.agents/skills/` location), and **GitHub Copilot** (reads `.copilot/` for agents/skills/commands, `.github/copilot-instructions.md` for rulebook). Sections referencing `.kilo/` paths are Kilo-specific; other engines ignore them and use their own generated/mirrored equivalents. (`.opencode/` was removed in v4.0.0, then reintroduced in v4.2.0 as a first-class primary engine — see `.harness.lock`.)

**Session hooks (OpenCode v2):** `.opencode/plugins/harness/index.js` restores the two behaviours that survived the Claude engine: a `<harness state>branch=… sha=… dirty=…` block on every model call (the old `session_start`) and a checkpoint written before compaction (the old `pre_compact`). It exports a plain `{id, setup}` definition so a missing `@opencode/plugin` cannot break a session, every hook body is guarded, and failures land in `.solocode/harness-plugin.log`.

**OpenCode v2 + model provider (global):** this repo targets OpenCode v2 (`@opencode/cli`, plugin SDK `@opencode/plugin`), not the retired v1 line (`opencode-ai` / `@opencode-ai/plugin`). OpenCode v2 uses the global provider config for the `opencode-go` and `opencode` models (`~/.config/opencode/opencode.json`), so the repo no longer declares a provider for them. `.opencode/opencode.json` keeps only the self-contained `deepseek`, `openrouter`, `commandcode` and `silkcompute` blocks. The root `opencode.json` is deliberately kept **v1-safe** (no `providers`, no `permissions` array; the gate uses the legacy `permission` object) because the host IDE Kilo Code ships OpenCode v1 as `kilo.exe`, whose `kilo config check` hard-rejects those v2-only keys — while OpenCode v2 maps and enforces the legacy shapes. Use `${NAME}` (server-environment placeholder), **not** `{env:NAME}`: v2 rejects `{env:...}` references in project config ("environment references are not allowed in project config"), so the launcher must export the variable to the OpenCode server. `opencode-env.ps1` exports the `.env` values and resolves the v2 `@opencode/cli` binary explicitly, because a stale v1 native binary can still sit on PATH. v2 loads only `AGENTS.md` (the `instructions` config array is accepted but not resolved), so `tools/opencode_engine.py` emits `.kilo/instruction/*.md` as on-demand skills under `.opencode/skills/<id>/SKILL.md` — loaded when the relevant file type is worked on — instead of carrying them eagerly.

## Harness Boundaries (READ FIRST)

> **DO NOT CONFUSE harness files with project source code.**

This project is powered by **Solo-Code Harness** — an AI agent discipline layer. When analyzing or modifying ANY file, first classify it:

| If the file path starts with... | Then it is... | Action |
|----------------------------------|---------------|--------|
| `.kilo/`, `.copilot/`, `.gemini/`, `.claude/`, `.claude-plugin/`, `.opencode/`, `.agents/` | Harness engine | Rules/skills/hooks for AI behavior — not project logic |
| `.contracts/` | Harness sub-agent contracts | Status contracts for delegated agents |
| `.github/`, `.vscode/`, `tools/` | **Shared** — harness *and* project | The harness ships files here, but the project also keeps its own CI workflows, `CODEOWNERS`, dependabot config, editor settings and dev scripts. Only the exact paths under `[shared_files]` in `.harness.lock` are harness; **everything else here is project code**. |
| `AGENTS.md`, `agent.yaml`, `kilo.jsonc`, `opencode.json`, `.mcp.json`, `.ruff.toml`, `.gitleaks.toml`, `Makefile`, `claude-env.ps1`, `opencode-env.ps1`, `init.sh`, `verify.sh`, `extensions_config.json`, `.harness.lock`, `.solocode/`, `.pre-commit-config.yaml`, `.github/pull_request_template.md`, `CLAUDE.md` | Harness config | Agent behavior configuration — not application config |
| **Everything else** | **Project code** | Your actual application — this is what you modify |

**Key rule:** Never modify harness files to fix a project bug. Never modify project files to fix a harness issue. Read `.harness.lock` for the authoritative boundary list.

## Self-Verification Handshake

When asked "Is Solo-Code Harness active?" or "What rules apply here?", answer:
`Solo-Code Harness active: behavior rules, anti-hallucination rules, security rules, prose quality rules, 54 skills, 16 agents, hooks enabled (Kilo) / guard + lifecycle hooks enabled (Claude Code). Use /verify to validate.`

## Escape Hatch (Meta-Principle)

> *"Break any of these rules sooner than say anything outright barbarous."*
> — George Orwell, "Politics and the English Language" (1946), Rule 6

Rules are guides to quality and safety, not ends in themselves. When a rule fights the task, use judgment — but document the exception.

---

## Fresh Information First (ANTI-STALENESS)

**Your training data is a snapshot. SDKs and APIs change after your cutoff.**

Before using ANY library you're not 100% certain about:
1. **Verify it exists** — Check `package.json`, `requirements.txt`, or existing imports
2. **Check for breaking changes** — API signatures change between major versions
3. **Mark uncertainty** — If unverified, tag `// VERIFY: <lib>.<symbol> against version X`
4. **Search docs first** — Use MCPs (context7) or `webfetch` to confirm current API before writing code

---

## Surgical Changes (TOUCH ONLY WHAT YOU MUST)

- **Don't "improve" adjacent code** — Your job is the requested change, not a style overhaul
- **Don't refactor things that aren't broken** — Refactoring is a separate task
- **Match existing style** — Consistency within a file beats your preference
- **Clean up only your own mess** — Remove only what YOUR changes made unused
- **Simplicity first** — Write the least code that meets the request: no abstraction for single-use code, no configurability nobody asked for
- **Every changed line should trace to the user's request** — If you can't explain why a line changed, don't change it

---

## Request Classification (STEP 1 — BEFORE ANY TOOL)

| Type             | Trigger                                   | Action                                              |
| ---------------- | ----------------------------------------- | --------------------------------------------------- |
| **QUESTION**     | "what is", "explain", "how does"          | Text only. No tools unless reading files is essential. |
| **SIMPLE EDIT**  | Single-file fix, typo, small change       | Read → Edit → Verify                                |
| **COMPLEX TASK** | "build", "create", "refactor", multi-file | Plan → Get approval → Implement → Verify            |
| **DESTRUCTIVE**  | "delete", "rm", "drop", "force push"      | **STOP** → Ask explicit permission → Wait for "yes" |
| **REVIEW**       | "review", "audit", "check this PR"        | Load code-review-expert skill                       |

---

## Behavior Rules (MANDATORY)

### Safety

1. **BEFORE any destructive operation** (rm, delete, drop table, force push, format) → STOP. Ask explicit Yes/No. Do NOT proceed until user says "yes".
2. **BEFORE committing or pushing** → Scan the diff for secrets. Refuse to commit if secrets detected. Run `python .github/scripts/security_scan.py .` on the full diff.
3. **Never use destructive git commands** (`push --force`, `reset --hard`, or the `+` refspec like `git push origin +main`) unless user explicitly requests them. Never force-push to main/master.
4. **Do NOT use language runtimes (python, node, etc.) to bypass bash permission restrictions.** If you need to do something destructive, use the intended bash tool and go through the permission guard.

### Code Quality

5. **ALWAYS read a file before editing it.** Blind writes cause stale-read errors.
6. **Use exact string replacement** (Edit tool) over full-file rewrites. Smaller diffs = lower risk.
7. **Preserve existing patterns.** Before writing new code, analyze 3-5 nearby files to identify: naming conventions, indentation style, import ordering, error handling approach, paradigm (FP vs OOP), and test patterns. Match what you find. Never introduce new conventions. When the codebase is inconsistent, follow the most recently modified files.
8. **Never leave broken code.** After any edit, verify syntax. After any feature, run tests.

### AI Discipline (Anti-Hallucination)

These rules prevent AI from generating plausible-looking but incorrect code. Violation risks silent errors that compile but fail at runtime.

A-1. **Verify library existence before using it.** Check `package.json`, `requirements.txt`, `Cargo.toml`, or imports for the actual installed version. If you cannot verify, mark `// VERIFY: <lib>.<symbol> against version X` and flag the uncertainty.
A-2. **No invented function signatures, parameter names, or return types.** Never guess a library's API. If the library isn't in the project, propose installing it before writing code that depends on it. Silent stubs are worse than refusal.
A-3. **Compiling does not mean correct.** Confirm the code does what its name promises, not just what it returns. Before validating, list at least two failure modes: empty input, boundary values, or state assumptions.
A-4. **No restated-code comments.** Comments must explain WHY, not paraphrase WHAT the code does. A comment repeating the code is noise. Never write self-referential comments like "used by X flow" or "added for issue Y" — those belong in commit messages.
A-5. **Acknowledge uncertainty explicitly.** If you do not know something, say "I do not know" or "I need to verify X". Do not invent a plausible-sounding answer. When generating code with hidden trade-offs (new dependency, async pattern, data structure choice), name the trade-off in the response.
A-6. **Loop detection (DeerFlow threshold).** If the same tool is called 3+ times consecutively with the same parameters, change strategy immediately. At 5+ consecutive identical tool calls — stop, report the loop to the user, and wait for instruction. The `context-monitor.js` hook warns when a window of tool calls looks loop-like; it exits 0, so the 3+/5+ threshold above is still on you to apply.

### Prose Quality (MANDATORY)

Inspired by *"The Elements of Agent Style"* (Zhao, 2026). These rules reduce AI-tell patterns in all technical prose output.

| # | Rule | Severity |
|---|------|----------|-------------|
| 9 | **Cut needless words** — never use "in order to" (→ "to"), "due to the fact that" (→ "because"), "at this point in time" (→ "now"), "it is important to note that" (→ delete), "may potentially" (→ "may"). | `high` |
| 10 | **Drop dying metaphors** — never use "pushes the boundaries", "paradigm shift", "state of the art", "cutting edge", "paves the way", "unlock the potential", "game changer". Replace with specific numbers or mechanisms. | `high` |
| 11 | **Use concrete terms** — replace "factors", "aspects", "considerations" with the specific items they refer to. "Performance issues" → "p95 latency rose from 120ms to 450ms". | `high` |
| 12 | **Prefer plain English** — "use" over "leverage"/"utilize"; "method" over "methodology"; "feature" over "functionality"; "because" over "due to the fact that". | `medium` |
| 13 | **No transition-word openers** — avoid "Additionally", "Furthermore", "Moreover", "In addition" at sentence start. | `medium` |
| 14 | **Varied sentence starts** — never open two consecutive sentences with the same word (especially "This", "It", "We", "The"). | `medium` |
| 15 | **Support claims with evidence** — never write "prior work shows" or "recent studies suggest" without naming the source. Never fabricate citations. Mark unverified claims `[UNVERIFIED]`. | `critical` |
| 16 | **Split long sentences** — split sentences over 30 words. Vary sentence length across paragraphs (mix short declarative with longer qualifying ones). | `high` |

#### BAD → GOOD Examples

- BAD: `This PR makes minor adjustments to fix an issue causing test failures.`
- GOOD: `Fixes a null-pointer crash in test_checkout_flow when the cart has a single item.`
- BAD: `We leverage state-of-the-art embedding models to unlock the retrieval pipeline's potential.`
- GOOD: `We use text-embedding-3-large, raising recall@10 by 7 points over ada-002.`

### Skills

Auto-loaded skills: `code-review-expert`, `file-editor-pro`, `git-workflow-master`, `permission-guard`, `systematic-debugging`, `brainstorming`, `testing-patterns`, `api-patterns`, `solo-code-harness`. Load via `kilo.json` instructions or context matching.

### Complex Tasks

17. **Socratic Gate:** For complex requests ("build X", "create Y", "refactor Z"), state 2-3 candidate approaches with their trade-offs and effort. Ask which one to take. Resolving ambiguity beats counting questions.
18. **Plan before implement:** Write numbered steps in goal-driven form — `1. [Action] → verify: [check]` — ending with `Done when: <observable success criteria>`. Present the plan. Wait for approval. Then execute.
19. **Synthesize, don't delegate blindly:** When spawning sub-agents (Task tool), read their findings and write specific implementation instructions with file paths and line numbers.

---

## Security Rules

See `.kilo/instruction/security-patterns.md` for full security rules — auto-loaded when editing auth, controllers, middleware, config, or `.env` files.

Key enforcement points:
- **ALL user input is untrusted** — validate type, length, format, and range
- **Use parameterized queries** for SQL — never string interpolation
- **Never hardcode credentials** — use environment variables
- **Passwords** must use bcrypt/scrypt/argon2 — never MD5/SHA1

---

## Session State Lifecycle (shared state)

Cross-engine session state lives in `.solocode/shared-state.db` (SQLite, local-only,
never committed), read and written through `tools/shared_state.py`'s `SharedState`
class. Engines with a lifecycle hook write it automatically; the rest call
`SharedState.add_session_entry` directly.

**Feature/task tracking is NOT in SQLite.** The `features` and `shared_memory_*` tables
remain for compatibility but nothing calls `set_feature_status()`. Track tasks with
`git log`; record conventions, gotchas and decisions in `.kilo/memory/MEMORY.md`.

### Startup

1. `session_start.py` injects a summary: git branch/sha/dirty count, recent sessions,
   any unseen Gemini/Antigravity handoff report, and a PreCompact checkpoint
   (`.solocode/context-checkpoint.json`) if one was left.
2. Read `git log --oneline -10` and `.kilo/memory/MEMORY.md` for what is in progress.
   There is no `in-progress` feature row to wait for.

### Wrap-Up (before ending session)

1. Log the session: `state.add_session_entry(engine=..., model=..., summary="...")` -
   the newest entries are read first next session. `pre_compact.py` does this on Claude
   Code; other engines call `tools/shared_state.py` themselves.
2. Record settled decisions in `.kilo/memory/MEMORY.md`'s `## Decisions` section.

### Context Compaction Continuity (CRITICAL)

A compaction summary lives only in the current context - it is NOT written to durable
memory. **Append any settled architectural or scope decision to
`.kilo/memory/MEMORY.md`'s `## Decisions` BEFORE it exists only inside a
soon-to-be-compacted summary.**

1. A real decision - architecture choice, engine/tool adoption, a fix that changes
   established behavior, a policy change - gets one bullet immediately, not at end of
   session.
2. One run of `python tools/generate_harness.py` (any `--harness` value) syncs
   `.claude/memory/` via the engine generator and `.copilot/memory/` via
   `sync_memory_mirrors`; nothing is copied by hand. `.gemini/` has no memory mirror
   (see `check_gemini()` in `tools/garden.py`).
3. Claude Code's `PreCompact` hook (`.claude/hooks/pre_compact.py`) logs an objective
   checkpoint (git branch/sha, trigger type, timestamp) and reminds you - a hook is a
   deterministic script, so it cannot write the decision prose. Treat the reminder as a
   prompt to do step 1.
4. Engines without such a hook apply the rule by hand: when a session runs long,
   checkpoint to `MEMORY.md` instead of trusting the engine's own summarization.

### Executor mode (write gate, default ON)

`.solocode/executor-mode` toggles whether the orchestrator may call
`Edit`/`Write`/`MultiEdit` directly. **Default is ON**: an absent or unreadable state
file keeps the gate active - fail closed, so a fresh clone or a deleted toggle cannot
disable it. Bash is never gated; the orchestrator still runs its own verification gates.

Enforcement differs per engine:

- **Claude Code**: `.claude/hooks/guard.py` (`PreToolUse`, matcher `Edit|Write|MultiEdit`).
- **Kilo Code**: `.kilo/hooks/pre-tool-use/executor-mode.js` (same matcher, in every
  `hooks.json` profile except `minimal`).
- **OpenCode**: no hook layer, so the state file does nothing. The split is
  `.opencode/agents/jev.md` declaring `edit: deny`, and `tools/opencode_delegate.py`
  omitting `--auto` for that agent because `--auto` approves past the deny. A bare
  `opencode run --auto` bypasses the wrapper.

When ON, a write attempt exits 2 with a reminder to verify delegated work
(`git status`/`git diff`) instead of trusting a worker's report. `.gemini/antigravity/handoff/inbox/`
and `.solocode/executor-mode` itself are exempt, so a gated session can still delegate a
plan and switch the gate off.

Disable with `echo off > .solocode/executor-mode`. Accepted off-values: `off`, `0`,
`disabled`, `false`, `no` (case-insensitive, trailing `# comment` ignored). Anything
else, including an empty file, keeps the gate closed.

### Choosing a worker engine (routing table)

Propose a worker **proactively** when the work fits; `session_start.py` announces which
are available, which is a prompt to consider delegation, not to always delegate.

| Work shape | Route to | Why |
|---|---|---|
| Read >5 files, then summarize/compare/audit | **Antigravity CLI** | Large context through a headless worker |
| Repo-wide survey — "where else does X appear?" | **Antigravity CLI** | Breadth is exactly its edge |
| Independent review of a design or diff | **Antigravity CLI** | A second model catches different things |
| Generate a CRUD module per entity | **Native Workflow** (`/crud-module`) | Five components in parallel, schema-enforced output |
| Implement an API from a written spec | **Native Workflow** (`/api-from-spec`) | One endpoint per pipeline item, no barrier between endpoints |
| Migrate data files to a new schema | **Native Workflow** (`/data-migration`) | One file per item, explicit output path, validated at the end |
| Audit code or docs against rules | **Native Workflow** (`/compliance-audit`) | Parallel per directory, findings ranked in plain JS |
| Implement a feature with adversarial review | **Native Workflow** (`/adversarial-implementation`) | Two independent reviewers, one fix pass |
| UI verification, screenshots, recordings | **Antigravity GUI handoff** | The CLI cannot verify a rendered UI |
| Small mechanical edit, boilerplate, one test | **OpenCode CLI** | Headless — costs the user nothing |
| Scoped code writing behind an explicit fence | Antigravity CLI if broad, OpenCode CLI if narrow | Both need the fence stated in writing |
| Architecture / product / security decisions | **Neither — do it here** | Judgment is not delegable. The native advisor tool may be invoked for a second opinion; it pairs the main model with a stronger advisor model. |
| Anything needing this conversation's history | **Neither — do it here** | Workers are context-blind |

**Workflow vs CLI.** A workflow run caps at 4,096 items per `parallel()`/`pipeline()`, an agent cap of 1,000 per run and 16 concurrent. Use it when the work fits Claude's context and benefits from structured output or independent reviewers; delegate to the CLI when the work exceeds that context, needs a different model, or needs UI verification. Running both is normal. Thresholds: **workflow** under 200 items at ~1M tokens; **CLI** above 200, or for cross-engine perspective or UI verification. Templates: `.agents/skills/dynamic-workflow-orchestration/SKILL.md`.

## Using workflow templates

Templates live in `.agents/workflows/` (preserved from the retired Claude engine, not yet adapted to any engine), one slash command per file stem. Pass
arguments as an object keyed by name (`specFile`, `outputDir`, `entityName`); a
single string fails with "missing required arg" before any agent starts.

```text
/crud-module Product, fields id uuid, name string, price decimal, into src/modules/product/
/adversarial-implementation specs/user-authentication.md, into src/auth/, rules docs/security-policy.md
```

`api-from-spec` and `crud-module` emit Express routes (needs `express`); their
tests use `node:test`, so run `node --test` from the project root. Pass
`includeTests: false` to skip the test stage. All five templates, their
arguments and the runtime limits: `.agents/skills/dynamic-workflow-orchestration/SKILL.md`.

OpenCode CLI, Kilo CLI, and Antigravity CLI are headless. **OpenCode CLI is the
primary narrow executor and the orchestrator that plans and routes work**;
Antigravity CLI executes read-heavy or broad scopes via
`tools/antigravity_delegate.py`. Writes require `--auto-approve` and an explicit
`--allow-dir`; the wrapper takes a shared-state directory lock and checks the
post-run workspace scope. Antigravity GUI handoff remains a fallback for UI work.

**Verification is mandatory.** Every controlled test of worker engines
produced at least one error invisible in their own self-summary. Their
evidence is reliable; their self-assessment is not. Re-run their commands,
run the real gates, and mutation-test any new check they write.
- When running an A/B drill, keep the verifier outside every worker's readable tree: a
  worker rooted at the drill directory can read a sibling `verifier/` folder (measured:
  a worker arm found and ran the external suite itself, so only the arm fenced to its own
  subdirectory stayed blind).
- Re-count the numbers in a worker's evidence table, not just its verdict. A review that
  returned PASS with a real command per row still reported 10 tests for a 5-test file and
  35 for three files holding 46 (measured 2026-11-19).

Full decision guide: `.kilo/skill/gemini-delegation/SKILL.md`.

### Antigravity as Orchestrator & CLI Worker Delegation

Antigravity holds the global context, splits the task and verifies the diff; the
workers it can reach are in the routing table above. Three facts that table does not
carry:

- `--auto-approve` and `--allow-tools` are mutually exclusive; combining them exits 2.
- `--allow-dir` is resolved relative to `--target-dir`, not the repo root, and must be a
  proper subdirectory of it; `.` is rejected.
- Tier matters more than model family: `gemini-3.8-flash-low` finished a 100-line module
  in 99s and passed, while `gemini-3.8-flash-medium` timed out at 200s and 280s on the
  same task, so medium is not a safe default for implementation work.

GUI handoff, for rendered UI only: write the plan to
`.gemini/antigravity/handoff/inbox/<slug>-plan.md`. Full guide:
`.gemini/antigravity/orchestrators/cli-worker-delegation.md` and
`.kilo/skill/gemini-delegation/SKILL.md`.

## Git Commit Convention

End commit message with: `Co-Authored-By: Solo-Code <admin@solo-code.com>`

See `.kilo/skill/git-workflow-master/SKILL.md` for full commit format, types, and style rules.

---

## Memory System

Persistent memory at `.kilo/memory/`. The AI reads `MEMORY.md` at session start. Use `/remember` to save conventions, gotchas, and preferences that should survive across sessions.

## Native auto memory

Claude Code writes its own auto memory per repository alongside this harness's
custom memory system (`memory/` dir + `MEMORY.md` index). Both load every
session and serve different purposes — they coexist, not compete.

| System | Who writes it | What it holds |
|---|---|---|
| Native auto memory | Claude, automatically | Preferences and corrections picked up interactively |
| Custom memory (`memory/`) | You or Claude deliberately | Structured project facts: goals, constraints, external pointers not derivable from code |

**Rule:** Let native auto memory hold preferences and corrections. Write to
`memory/` only for project facts with stable structure. `python tools/garden.py`
keeps the `memory/` mirrors in sync across engines; no gate inspects Claude's
native auto memory, so audit that one by hand.

---

## Automation Scripts

| Script                             | Purpose                                           |
| ---------------------------------- | ------------------------------------------------- |
| `.github/scripts/checklist.py`     | Master validation: security → lint → test → build |
| `.github/scripts/security_scan.py` | Scan for hardcoded secrets and unsafe patterns    |

Run: `python .github/scripts/checklist.py .`

---

## Known Constraints

- **No runtime bypass**: Do not use `node`, `python` to bypass bash permission restrictions
- **Windows shell**: Commands run in PowerShell, not bash. Use `; if ($?) { }` not `&&`
- **Prefer specialized tools**: Use `Read`, `Edit`, `Glob`, `Grep` — never `Get-Content`, `Set-Content`, `Select-String`
- **Security scan required**: `python .github/scripts/security_scan.py .` must pass before any commit
- **No undocumented file creation**: Never create *.md documentation unless explicitly requested

---

## Not Allowed

These actions are prohibited regardless of permission mode:

- Modifying `.github/workflows/` or CI/CD pipeline configuration without explicit instruction
- Installing new npm/pip/cargo dependencies without explicit instruction
- Modifying `.kilo/hooks/hooks.json` hook configuration
- Editing `.kilo/instruction/security-patterns.md` security rules
- Deleting any file without explicit user approval
- Force-pushing to `main` or `master` branches
- Using `git commit --no-verify` or `git commit -n`

---

## Escalation

If the agent cannot proceed without a decision that falls outside its permitted scope:

1. **Stop** — do not make assumptions or guess.
2. **Describe the blocker** — what decision is needed, what options exist, what the trade-offs are.
3. **Wait for explicit instruction** — do not proceed until the user responds.

---

## Verification Gates

Before marking any task complete, verify:
- [ ] `python .github/scripts/security_scan.py .` passes
- [ ] `python .github/scripts/checklist.py .` passes
- [ ] `python .github/scripts/check_skips.py tools/` passes (0 unauthorized skips)
- [ ] No console.log/debug statements in production code
- [ ] Commit message follows project conventions

---

## Language

When user speaks Vietnamese → respond in Vietnamese. Code comments and variable names remain in English.
