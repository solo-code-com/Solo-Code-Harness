---
name: dynamic-workflow-orchestration
description: "Orchestrate broad, parallel, or adversarial tasks using the native Workflow tool. Use when: codebase audit, parallel research, adversarial verify, pipeline migration, fan-out subagents, structured output from many files."
license: MIT
---

> **Claude Code only.** Kilo, OpenCode, and Copilot do not have the Workflow tool - use CLI delegation (`tools/antigravity_delegate.py`, `tools/opencode_delegate.py`) instead.

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
