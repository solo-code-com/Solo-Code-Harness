# Roadmap: Adopt Claude Code vanilla updates into g-harness

## Context for the executor (DeepSeek)

You are executing part of a plan to bring **native Claude Code v2.1.318→325 features**
into the **Solo-Code Harness** (`g-harness`). The harness is a multi-engine wrapper
around Claude Code. This roadmap is the source of truth for what to change.

### Critical constraints (READ BEFORE EDITING)

1. **Harness boundary — every file you touch is harness, not project code.**
   Allowed paths to edit are listed per-task below. Do not edit anything outside
   those lists. If you think you need to, stop and report.

2. **Cross-engine parity is mandatory.** `.kilo/` is the source of truth.
   Any change to `.claude/` must be mirrored to `.kilo/` first, then verified by
   `python tools/garden.py`. A drift failure from garden.py after a deliberate
   sync is EXPECTED and must be fixed by completing the sync — it is not a bug
   in your change.

3. **Verify every claim with a command.** Do not write a claim you did not run a
   command for. Use this table in your final report:

   | Claim | Command run | Output |
   |------|-------------|--------|

4. **Predict the red gate.** If a correct change makes a check fail, say so
   explicitly ("that failure is the expected outcome") so the reviewer does not
   "fix" what was intentional.

5. **Match existing conventions** in the file you edit (comment density, naming,
   style). Do not reformat code you were not asked to touch.

6. **Do not edit protected linter/formatter config** (`.ruff.toml`,
   `eslint.config.js`) without explicit instruction. None of the tasks below
   require it.

### How phases are delegated

This roadmap has **3 phases**. Each phase is independently delegatable. Run
`/verify` (or `make check`) after each phase. The 6 gates:

1. `ruff check .` — Python lint
2. `python tools/check_lint_budget.py` — ratchet for S/BLE families
3. `python tools/validate_schemas.py` — frontmatter schema validation
4. `python tools/garden.py` — cross-engine parity / drift detection
5. `python -m pytest tools/ -q` — full test suite (auto-discovers all
   `tools/test_*.py`)
6. `python .github/scripts/security_scan.py .` — secret scan

### Engine status (v4.3.1)

- `.opencode/` — first-class primary agent engine (OpenCode v2). Generated from
  `.kilo/instruction/*.md` by `tools/opencode_engine.py`.
- `.copilot/` — manually kept in parity with `.kilo/`, verified by garden.py.
- `.claude/` — Claude engine config. The `.claude/` you edit is regenerated from
  `.kilo/` by `tools/generate_harness.py --harness claude`, so the `.kilo/`
  change is the source and `.claude/` follows.

---

## Phase 1 — Simplifications (safe, replace custom with native)

**Goal:** Reduce maintenance by adopting native equivalents for static safety
rules, hook reliability, and instruction scoping. Lowest risk, do first.

### Task 1.1 — Add static `permissions.deny` entries

**Files allowed to edit:**
- `.claude/settings.json`
- `.kilo/kilo.jsonc` (mirror — source of truth)
- `.copilot/` equivalent if it carries permissions (check first)

**Spec:**
The current `.claude/settings.json` `permissions.deny` array contains only
`Read(./.env)`, `Read(./.env.*)`, `Read(./secrets/**)`. Add static deny rules
for destructive Bash patterns that `guard.py` currently catches with regex.
Native deny is enforced by the client regardless of Claude's decision, so it
is stronger than a hook round-trip for these static cases.

Add these entries to the `permissions.deny` array (keep existing ones):
```
"Bash(rm -rf *)",
"Bash(rm -rf /*)",
"Bash(git push --force *)",
"Bash(git push -f origin main*)",
"Bash(git push -f origin master*)",
"Bash(git reset --hard*)",
"Bash(DROP TABLE*)"
```

**Do NOT remove `guard.py` or its hook wiring.** guard.py still handles dynamic
cases: secret detection in file content, executor-mode gating, protected-config
edit blocking. Native deny cannot do those. This task only adds the static
layer; guard.py remains the dynamic layer.

**Verify:**
- `python -c "import json; json.load(open('.claude/settings.json'))"` — valid JSON
- Confirm the new deny entries are present and the existing Read rules survive
- `python tools/garden.py` — if it reports drift on `.claude/settings.json`,
  that is EXPECTED until the `.kilo/` mirror is updated. Complete the mirror and
  re-run garden.py; it must pass.

**Red gate (expected):** garden.py drift until `.kilo/kilo.jsonc` (or the
equivalent settings source) is updated with the same deny rules.

### Task 1.2 — Add `onFailure: "block"` to critical hooks

**Files allowed to edit:**
- `.claude/settings.json`
- `.kilo/` mirror (source of truth)

**Spec:**
The guard.py and security_post.py hooks currently fail-open: if the hook
script crashes, the action proceeds. Add `"onFailure": "block"` to those two
hook handlers so a hook crash blocks the action instead (fail-closed). Requires
v2.1.295+; the harness runs 2.1.325 so this is safe.

In `.claude/settings.json`, the `PreToolUse` matcher for `Bash|Edit|Write|MultiEdit`
has one hook (guard.py). Add `"onFailure": "block"` to that hook object:
```json
{
  "type": "command",
  "command": "python \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/guard.py\"",
  "onFailure": "block"
}
```

The `PostToolUse` matcher for `Bash` has one hook (security_post.py). Add the
same field:
```json
{
  "type": "command",
  "command": "python \"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/security_post.py\"",
  "onFailure": "block"
}
```

Do NOT add `onFailure: "block"` to the advisory hooks (quality_gate.py,
prose_quality.py, memory_gate.py) — those are advisory format checks and should
not block on crash. Leave them as-is.

**Verify:**
- `python -c "import json; json.load(open('.claude/settings.json'))"` — valid JSON
- Confirm `onFailure: "block"` present on guard.py and security_post.py only
- `python tools/garden.py` — must pass after `.kilo/` mirror updated

### Task 1.3 — Move path-scoped instructions to `.claude/rules/`

**Files allowed to edit:**
- Create `.claude/rules/typescript.md`, `.claude/rules/database.md`,
  `.claude/rules/security.md`
- Move content FROM `.claude/instruction/rules-typescript.md`,
  `.claude/instruction/rules-database.md`,
  `.claude/instruction/security-patterns.md` INTO the new rules files
- Delete the old instruction files once content is moved
- Mirror in `.kilo/` (source of truth) and `.copilot/`

**Spec:**
Claude Code now supports `.claude/rules/*.md` with `paths:` frontmatter. A
rule with `paths` only loads into context when Claude reads/writes a matching
file. This saves context vs. loading every instruction file every session.

Move these three instruction files (which are only relevant for specific file
types) into path-scoped rules:

1. `.claude/instruction/rules-typescript.md` → `.claude/rules/typescript.md`
   Add frontmatter:
   ```markdown
   ---
   paths:
     - "**/*.ts"
     - "**/*.tsx"
   ---
   ```
   Then the existing content below the frontmatter.

2. `.claude/instruction/rules-database.md` → `.claude/rules/database.md`
   Add frontmatter:
   ```markdown
   ---
   paths:
     - "**/migrations/**"
     - "**/*.sql"
     - "**/models/**"
     - "**/schema*/**"
   ---
   ```

3. `.claude/instruction/security-patterns.md` → `.claude/rules/security.md`
   Add frontmatter:
   ```markdown
   ---
   paths:
     - "**/auth/**"
     - "**/api/**"
     - "**/middleware/**"
     - "**/credentials/**"
   ---
   ```

**KEEP these instruction files in `.claude/instruction/`** (they are always
relevant, do not scope them):
- `harness-boundaries.md`
- `custom-framework-rules.md`
- `rules-git.md`
- `shared-state.md`
- `rules-database.md` — wait, no. This is being moved. Remove from this keep-list.
  Final keep-list: `harness-boundaries.md`, `custom-framework-rules.md`,
  `rules-git.md`, `shared-state.md`.

**IMPORTANT:** Before deleting the old `.claude/instruction/*.md` files, check
how `tools/generate_harness.py` discovers instruction files. If it hardcodes
the instruction dir, moving files to `.claude/rules/` may break regeneration.
Read `tools/generate_harness.py` and `tools/claude_engine.py` first. If the
generator does not support `.claude/rules/`, you must add support there too,
or keep the files in `.kilo/instruction/` and have the generator emit them to
`.claude/rules/`. Report what you find and how you handled it.

**Verify:**
- `python tools/validate_schemas.py` — frontmatter schema must pass for new
  rule files. If it fails, that may be a schema gap (rules are a new path);
  report the exact error.
- `python tools/garden.py` — drift EXPECTED until `.kilo/` and `.copilot/`
  mirrors are complete.
- Read back one new rule file and confirm frontmatter + content intact.

**Red gate (expected):** garden.py drift (fix by mirroring), possibly
validate_schemas.py if it does not know `.claude/rules/` (report error).

### Task 1.4 — Run `/doctor prompt-audit` baseline (read-only)

**Files allowed to edit:** NONE. This task is read-only.

**Spec:**
Claude Code v2.1.283+ has `/doctor prompt-audit` which checks CLAUDE.md and
`.claude/` instruction files for outdated references, dead file paths, and
conflicting instructions. This is a read-only audit that proposes edits but does
not apply them.

This task is for the **human reviewer / orchestrator**, not the executor, since
it requires an interactive Claude Code session. Document it here as a Phase 1
action item: run `/doctor prompt-audit` once, capture the report, and file any
high-priority findings as a follow-up. The executor should SKIP this task and
note "deferred to orchestrator — requires interactive session" in its report.

---

## Phase 2 — High-value extensions (add capability, medium risk)

**Goal:** Add native capabilities the harness currently lacks or implements
with heavy custom code. Do after Phase 1 gates pass.

### Task 2.1 — Document dynamic-workflow routing policy in CLAUDE.md

**Files allowed to edit:**
- `CLAUDE.md` (the Claude engine rulebook)
- `AGENTS.md` (cross-engine root — source of truth, mirror)
- Create `.claude/skills/dynamic-workflow-orchestration/SKILL.md`

**Spec:**
Claude Code now has a native **Workflow tool** that spawns subagents in-process
via a script (see `agent()`, `parallel()`, `pipeline()`, `phase()` primitives).
This overlaps with but does not replace the harness's CLI delegation to
Antigravity/Kilo/OpenCode.

Update the **"Choosing a worker engine (routing table)"** section of CLAUDE.md
to split the orchestration lane clearly:

- **Native Workflow tool** (in-process subagents): use for codebase audits,
  migrations that fit in Claude's context window, cross-checked research,
  adversarial verification of findings. These benefit from structured output
  and in-process coordination. They cost Claude tokens but avoid a round-trip
  to an external CLI.
- **CLI delegation** (Antigravity/Kilo/OpenCode): KEEP for cross-engine
  perspective (a different model catches different things), work that exceeds
  Claude's context window, and UI verification.

Add a row to the routing table:
```
| Broad audit/research fitting Claude's window | Native Workflow tool | In-process, structured output, no round-trip |
```

Then create the skill `.claude/skills/dynamic-workflow-orchestration/SKILL.md`
documenting three patterns with minimal examples (do not write full scripts,
just the shape):
1. **Fan-out read**: parallel readers over subsystems → structured map
2. **Adversarial verify**: find → independent verify pass → synthesize
3. **Pipeline migration**: discover sites → transform each (worktree isolation)
   → verify

Add a guardrail note: "Do not use the Workflow tool for trivial or mechanical
edits — it spawns subagents and costs tokens. Use it for genuinely parallel or
adversarial work. Solo work stays in the main loop."

**Verify:**
- `python tools/validate_schemas.py` — SKILL.md frontmatter must pass
- `python tools/garden.py` — drift EXPECTED until AGENTS.md + `.kilo/` mirrored
- `ruff check .` — n/a (markdown only) but run anyway to confirm no incidental
  Python changes
- Read back the updated CLAUDE.md routing table and confirm the new row and
  the split between native Workflow vs CLI delegation is unambiguous.

### Task 2.2 — Enable tool search for large MCP tool sets (RESEARCH FIRST)

**Files allowed to edit:**
- `.claude/settings.json` (pending research)
- `.kilo/` mirror

**Spec:**
The harness loads several MCP servers (memory, playwright, context7, ide,
sequential_thinking) whose tools all occupy context every session. Claude Code
now supports **tool search** — on-demand tool discovery so the context window
is not flooded.

BEFORE editing settings, you MUST research the exact settings key. Fetch
`https://code.claude.com/docs/en/settings-reference.md` and find the key that
enables tool search (likely under a `toolSearch` or similar key, or an MCP
config option). If the documentation does not expose a settings key for it,
SKIP this task and report "tool search is auto-enabled / not settings-driven —
no change needed".

If a settings key exists, add it to `.claude/settings.json` with the
documented default, mirror to `.kilo/`, and verify:

**Verify:**
- `python -c "import json; json.load(open('.claude/settings.json'))"`
- `python tools/garden.py` — must pass after mirror

**Red gate (expected):** None if the key is correctly named per docs.

### Task 2.3 — Auto memory policy in CLAUDE.md

**Files allowed to edit:**
- `CLAUDE.md`
- `AGENTS.md` (mirror)

**Spec:**
Claude Code now has native **auto memory** — Claude accumulates learnings
across sessions automatically, stored per-repo. The harness already has a
custom file-based memory system (`memory/` dir + `MEMORY.md` index, documented
in the system prompt). These two systems can coexist but need a clear policy to
avoid drift.

Add a short section to CLAUDE.md under the existing memory documentation:

```
## Native auto memory (v2.1.x)

Claude Code now writes its own auto memory per repository. The harness's
custom memory system (`memory/` dir + `MEMORY.md`) and native auto memory
serve different purposes and coexist:

- **Native auto memory**: preferences and corrections Claude picks up
  interactively. Leave it ON (default). Do not duplicate these into custom
  memory files.
- **Custom memory (`memory/`)**: structured project facts — ongoing goals,
  non-obvious constraints, reference pointers — that are not derivable from
  code or git history. A human or Claude deliberately writes these.

Rule: if a learning is a personal preference or a correction, let native auto
memory hold it. If it is a project fact with a stable structure (goal,
constraint, external pointer), write it to custom memory. Run
`/doctor prompt-audit` quarterly to catch drift between the two.
```

**Verify:**
- `python tools/garden.py` — drift EXPECTED until AGENTS.md mirrored
- Read back the new CLAUDE.md section and confirm it matches the spec above

---

## Phase 3 — Research-gated extensions (do last, may defer)

**Goal:** Capabilities that need external research or infra before they can
land. Each task starts with a research gate; if the gate fails, defer and
report.

### Task 3.1 — Channels for CI event integration (RESEARCH GATED)

**Research gate (do first):** Determine whether a public, usable MCP server
that emits channels (CI build failures, PR events) exists. Fetch
`https://code.claude.com/docs/en/channels.md` and
`https://code.claude.com/docs/en/channels-reference.md`. If no off-the-shelf
server exists and self-hosting one is required, DEFER this task and report
"channels require self-hosted MCP server — deferred, effort too high for this
pass".

**Files allowed to edit (only if research gate passes):**
- `.mcp.json`
- `.vscode/mcp.json`
- Create `.claude/skills/ci-event-triage/SKILL.md`
- `.kilo/` mirror

**Spec (if gate passes):**
Configure the channel MCP server in `.mcp.json` per the docs. Create a skill
documenting the event-handling pattern: receive CI failure event → read the
failing log → triage root cause → propose fix. Keep the skill short; it is a
trigger pattern, not a full runbook.

**Verify:**
- `python tools/validate_schemas.py`
- `python tools/garden.py`
- Send a simulated test event per the server's docs and confirm it lands in a
  session (if a session is available; otherwise document the test command)

### Task 3.2 — Advisor tool for hard decisions (RESEARCH GATED)

**Research gate:** Fetch
`https://code.claude.com/docs/en/settings-reference.md` and find the settings
key that enables the advisor tool and configures the advisor model. If the
advisor tool is not settings-driven (e.g., auto-invoked by the model), SKIP
and report.

**Files allowed to edit (only if research gate passes):**
- `.claude/settings.json`
- `.kilo/` mirror
- `CLAUDE.md` (add a note in the routing table that architectural/security/
  product decisions can invoke the advisor tool)

**Spec (if gate passes):**
Enable the advisor tool with a mid-tier advisor model (do not default to the
most expensive model; pick the documented default). Add a one-line note to the
CLAUDE.md routing table's "Architecture / product / security decisions" row:
"Neither — do it here. The native advisor tool may be invoked for a second
opinion; it pairs the main model with a stronger advisor model."

**Verify:**
- `python -c "import json; json.load(open('.claude/settings.json'))"`
- `python tools/garden.py`

---

## Execution order

```
Phase 1: 1.1 → 1.2 → 1.3 → (1.4 deferred to orchestrator)
  → run /verify
Phase 2: 2.1 → 2.3 → 2.2
  → run /verify
Phase 3: 3.1 (research gate) → 3.2 (research gate)
  → run /verify
```

## Done when

- `/verify` (all 6 gates) passes after each phase
- `python tools/garden.py` shows no drift between `.kilo/`, `.claude/`,
  `.copilot/`
- Every task's "Verify" commands have been run and output recorded in the
  report table
- No project source code was modified (all edits are within the harness
  boundary lists above)
- The final report lists, per task: status (done / deferred / blocked),
  files written (with paths), the claim/command/output table, and any red
  gates hit and how they were resolved

## Final report format

Return a single markdown report with:

1. Per-task status table
2. Files written (exact paths, verified by reading back)
3. Claim / Command / Output table (no claim without a command)
4. Red gates hit and resolution
5. Deferred tasks and why
6. Anything unexpected (generator behavior, schema gaps, doc mismatches)
