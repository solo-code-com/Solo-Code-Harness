# Phase 2 Brief: Dynamic-workflow routing + Auto-memory policy

## Who you are and what this is

You are DeepSeek, executing Phase 2 of a plan to integrate Claude Code vanilla
updates into the **Solo-Code Harness** (`g-harness`). Phase 1 is already done
and verified (gates green). This brief is self-contained — no reconnaissance
needed.

---

## Critical constraints (READ BEFORE EDITING)

1. **Harness boundary.** Every file you touch is harness, not project code. The
   allowed paths are listed per-task. Edit nothing outside them.

2. **Cross-engine parity is mandatory.** `.kilo/` is the source of truth.
   Edit `.kilo/` first, then mirror to `.claude/`. Then run `python tools/garden.py`.
   A `lock_stale` drift is EXPECTED until you run `python tools/deploy.py --root-lock`
   at the very end. Do not run `--root-lock` until all tasks are complete.

3. **Verify every claim with a command.** No claim without output. Use this
   table in your report:

   | Claim | Command run | Output |
   |-------|-------------|--------|

4. **Style rules — read before touching AGENTS.md or CLAUDE.md:**
   - `AGENTS.md` uses **em-dashes** (`—`) in table cells, not `--`
   - `CLAUDE.md` uses `--` (double hyphen) in prose, not em-dashes
   - Match the surrounding style exactly

5. **Skill frontmatter format** (confirmed from existing skills):
   ```
   ---
   name: <kebab-case>
   description: "One sentence. Use when: trigger keywords."
   license: MIT
   ---
   ```
   No other frontmatter keys. `license: MIT` is required.

6. **Do not edit protected config** (`.ruff.toml`, `eslint.config.js`, etc.).
   None of the tasks below require it.

---

## Ground truth (do not re-derive, do not query)

**Skill counts — currently 53 in every engine:**
- `.claude/skills/` — 53 dirs (verified: `ls .claude/skills/ | wc -l`)
- `.kilo/skill/` — 53 dirs
- `.copilot/skill/` — 53 dirs (assumed in parity; garden.py enforces)
- `.gemini/` — 53 skills

**After Task 2.1, the count becomes 54.** You must update every "53" reference
to "54" in the files listed below. Exact locations:

| File | Line | Current text |
|------|------|-------------|
| `AGENTS.md` | 50 | `...prose quality rules, 53 skills, 16 agents...` |
| `CLAUDE.md` | 32 | `...prose quality rules, 53 skills, 16 agents...` |
| `CLAUDE.md` | 38 | `- **Skills (53)** in \`.claude/skills/\`` |
| `README.md` | 101 | `...skills (53)...` (`.claude/` row, EN table) |
| `README.md` | 102 | `...skills (53)...` (`.copilot/` row, EN table) |
| `README.md` | 103 | `...skills (53)...` (`.kilo/` row, EN table) |
| `README.md` | 104 | `...skills (53)...` (`.gemini/` row, EN table) |
| `README.md` | 296 | `\| Skills (53) \|` |
| `README.md` | 506 | `...skills (53)...` (`.claude/` row, VI table) |
| `README.md` | 507 | `...skills (53)...` (`.copilot/` row, VI table) |
| `README.md` | 508 | `...skills (53)...` (`.kilo/` row, VI table) |
| `README.md` | 509 | `...skills (53)...` (`.gemini/` row, VI table) |
| `.github/copilot-instructions.md` | 5 | `...and 53 specialized skills.` |
| `.github/copilot-instructions.md` | 260 | `...53 specialized skills in \`.copilot/skill/\`...` |
| `.gemini/antigravity/AGENTS.md` | 26 | `...53 skills, 16 agents...` |
| `.kilo/memory/harness-design-intent.md` | 13 | `→ 53 domain skills` |
| `.claude/memory/harness-design-intent.md` | 13 | `→ 53 domain skills` |
| `.copilot/memory/harness-design-intent.md` | 13 | `→ 53 domain skills` |
| `integrations/dsh-solo-plugins/packages/solo-code/README.md` | 60 | `\| 53 \|` |

Before editing any line, read the file to confirm the line number is still
correct (line numbers can shift if something above changed). Do not update a
line that already says "54".

**AGENTS.md routing table** (lines 262–271) uses em-dashes in cells:
```
| Read >5 files, then summarize/compare/audit | **Antigravity CLI** | Large context through a headless worker |
```

**CLAUDE.md routing table** (lines 157–167) uses `--` in prose paragraphs
below the table:
```
Kilo CLI, OpenCode CLI, and Antigravity CLI are headless. Use
`tools/antigravity_delegate.py` for broad scopes; writes require both
`--allow-dir` and `--auto-approve`.
```

---

## Task 2.1 — Add dynamic-workflow routing + new skill

### Files allowed to edit

- `.kilo/skill/` — create `.kilo/skill/dynamic-workflow-orchestration/SKILL.md`
- `.claude/skills/` — create `.claude/skills/dynamic-workflow-orchestration/SKILL.md`
- `.kilo/instruction/custom-framework-rules.md` — add one routing row
- `.claude/instruction/custom-framework-rules.md` — mirror
- `AGENTS.md` — add one routing row + update skill count 53→54
- `CLAUDE.md` — add one routing row + update skill count 53→54
- `README.md` — update skill counts 53→54 (12 occurrences listed above)
- `.github/copilot-instructions.md` — update skill count 53→54 (2 lines)
- `.gemini/antigravity/AGENTS.md` — update skill count 53→54 (1 line)
- `.kilo/memory/harness-design-intent.md` — update skill count 53→54
- `.claude/memory/harness-design-intent.md` — update skill count 53→54
- `.copilot/memory/harness-design-intent.md` — update skill count 53→54
- `integrations/dsh-solo-plugins/packages/solo-code/README.md` — update 53→54

### Spec: routing row

Add this row to the routing tables in **both** `AGENTS.md` and `CLAUDE.md`.
In `AGENTS.md` the table uses em-dashes in cells, in `CLAUDE.md` the table
prose uses `--`. The row itself uses `|` separators; just add it after the
"Independent review" row (3rd data row) in both files:

```
| Broad audit/research fitting Claude's context | **Native Workflow tool** | In-process subagents, structured output, no CLI round-trip |
```

Same row goes into `.kilo/instruction/custom-framework-rules.md` and its
`.claude/instruction/` mirror if those files contain a routing table. If they
do not contain a routing table, skip that edit and note it in the report.

### Spec: new skill file

Create `.kilo/skill/dynamic-workflow-orchestration/SKILL.md` with this exact
content:

```markdown
---
name: dynamic-workflow-orchestration
description: "Orchestrate broad, parallel, or adversarial tasks using the native Workflow tool. Use when: codebase audit, parallel research, adversarial verify, pipeline migration, fan-out subagents, structured output from many files."
license: MIT
---

# Dynamic Workflow Orchestration

Use the native Workflow tool (not CLI delegation) when a task is genuinely
parallel or adversarial and fits within Claude's context window. CLI delegation
stays for cross-engine perspective, context exceeding Claude's window, or UI
verification.

## Three patterns

### 1. Fan-out read
Spawn parallel readers over subsystems, collect structured output, synthesize.

```
phase('Understand')
const results = await parallel([
  () => agent('Read src/auth/... and return a JSON summary of auth flows', { schema: AUTH_SCHEMA }),
  () => agent('Read src/payments/... and return a JSON summary of payment flows', { schema: PAY_SCHEMA }),
])
```

### 2. Adversarial verify
Find findings, then spawn an independent verifier per finding.

```
phase('Find')
const findings = await agent('Audit all API handlers for missing input validation', { schema: FINDINGS_SCHEMA })
phase('Verify')
const verified = await parallel(findings.map(f => () => agent(`Verify this finding: ${f.summary}`, { schema: VERIFY_SCHEMA })))
```

### 3. Pipeline migration
Discover sites, transform each in an isolated worktree, verify.

```
phase('Discover')
const sites = await agent('List every file that imports the old logger', { schema: SITES_SCHEMA })
phase('Migrate')
await pipeline(sites, (site) => agent(`Migrate ${site} to new logger`, { isolation: 'worktree' }))
```

## Guardrail

Do NOT use the Workflow tool for trivial or mechanical single-file edits —
it spawns subagents and costs tokens. Solo work in the main loop.

## When to use CLI delegation instead

- Task needs a different model (Antigravity/Gemini for a second opinion)
- Context exceeds Claude's window (repo-wide survey of >200 files)
- UI verification (rendered output, screenshots)
```

Then mirror the same file to `.claude/skills/dynamic-workflow-orchestration/SKILL.md`
(identical content).

### Verify (Task 2.1)

```
ls .kilo/skill/dynamic-workflow-orchestration/SKILL.md
ls .claude/skills/dynamic-workflow-orchestration/SKILL.md
python tools/validate_schemas.py 2>&1| tail -5
# Expect: Files checked: 71 / Errors: 0  (was 69 before Phase 1 added 2 rules)
python tools/garden.py 2>&1 | grep -E "Total drift|lock_stale"
# Expect: Total drift issues: 1  (lock_stale only — not yet fixed)
grep "54" CLAUDE.md | head -5
grep "54 skills" AGENTS.md
```

**Red gate (expected):** `lock_stale` drift from garden.py. This is intentional
and clears with `--root-lock` at the end. Do not fix it now.

---

## Task 2.2 — Tool search

**SKIP.** The settings reference contains no `toolSearch` or equivalent key.
DeepSeek confirmed this in Run 1. No files to edit.

---

## Task 2.3 — Auto-memory policy in CLAUDE.md and AGENTS.md

### Files allowed to edit

- `CLAUDE.md` — add new section
- `AGENTS.md` — add mirrored section (use em-dashes in prose where CLAUDE.md
  uses `--`; match surrounding style)

### Spec

Add a new section to `CLAUDE.md` immediately after the
`## Git Commit Convention` section (around line 148). Insert a blank line
before and after:

```markdown
## Native auto memory

Claude Code writes its own auto memory per repository alongside this harness's
custom memory system (`memory/` dir + `MEMORY.md` index). Both load every
session and serve different purposes -- they coexist, not compete.

| System | Who writes it | What it holds |
|---|---|---|
| Native auto memory | Claude, automatically | Preferences and corrections picked up interactively |
| Custom memory (`memory/`) | You or Claude deliberately | Structured project facts: goals, constraints, external pointers not derivable from code |

**Rule:** Let native auto memory hold preferences and corrections. Write to
`memory/` only for project facts with stable structure. Run
`/doctor prompt-audit` quarterly to catch drift between the two.
```

Mirror equivalent content to `AGENTS.md`. Find the section that describes the
harness memory system in `AGENTS.md` (search for `memory/` or `MEMORY.md`)
and add the same policy table after it, using `AGENTS.md`'s surrounding style
(em-dashes in prose, same table format).

### Verify (Task 2.3)

```
grep -n "Native auto memory" CLAUDE.md
grep -n "Native auto memory" AGENTS.md
python tools/validate_schemas.py 2>&1 | tail -3
ruff check . 2>&1 | tail -3
```

---

## End-of-phase cleanup

After both tasks are done, run:

```bash
python tools/deploy.py --root-lock
```

This regenerates `.harness.lock` with the new fingerprint. Then run all 6 gates:

```bash
ruff check .
python tools/check_lint_budget.py
python tools/validate_schemas.py
python tools/garden.py
python -m pytest tools/test_deploy.py -q
python .github/scripts/security_scan.py .
```

**Expected after `--root-lock`:**
- `garden.py`: `Total drift issues: 0`
- `test_deploy.py`: all pass (the `test_root_lock_is_reproducible_from_the_generator` failure from Phase 1 clears)

**Red gates that should NOT appear:** any garden drift other than `lock_stale`
pre-`--root-lock`. If you see skill parity drift, instruction drift, or agent
drift — that is a real error, stop and report.

---

## Final report format

Return a single markdown report with:

1. Per-task status table (Task | Status | Files written)
2. Claim / Command / Output table (every claim needs a command)
3. Red gates hit and resolution
4. Skipped tasks and why
5. Any unexpected findings (line numbers shifted, file missing, style mismatch)
6. Gate results after `--root-lock` (all 6 commands + output tails)
