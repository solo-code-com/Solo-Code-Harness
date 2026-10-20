# Design Intent — DO NOT REMOVE

## What This Harness Is

Solo-Code Harness is a multi-layer quality and safety system for AI coding agents. It is NOT a generic agent config — every rule, skill, hook, and gate exists because a specific failure mode was observed in real usage.

## Architecture Layers

```
AGENTS.md        → Behavior rules, anti-hallucination, prose quality, harness boundaries
.harness.lock    → Boundary manifest — which files are harness vs project code
.kilo/instruction/ → Language-specific rules (Python, TS, Git, DB)
.kilo/skill/     → 53 domain skills (code-review, debugging, testing...)
.kilo/agents/    → 15 specialized agents with handoff chains
.kilo/hooks/     → 20 lifecycle hooks (bash validation, secret scan, learning...)
.kilo/hookify/   → MD-config rules engine (user-customizable policies)
.kilo/prompts/   → Structured workflow templates (code review, feature dev...)
tools/           → Harness utilities (deploy, generate, garden, harness_config) + tests (guard, hooks, engine)
.github/scripts/ → Verification gates (security_scan, checklist, check_skips, eval_harness, security-allowlist)
.contracts/      → Sub-agent status contracts (DeerFlow pattern)
kilo.jsonc       → Permission model (bash allow/deny, task allow/deny)
```

## Why Each Layer Exists

| Layer | Prevents |
|-------|----------|
| Anti-hallucination rules (A1-A6) | Model invents APIs, libraries, non-existent params, loops forever |
| Harness Boundaries (AGENTS.md + .harness.lock) | AI confuses harness files with project code, modifies rules instead of fixing bugs |
| Prose quality rules (9-16) | AI-tell patterns: clichés, filler phrases, handwavy claims |
| Hierarchical config (harness_config.py) | Config scattered across .env, hardcoded values — no per-project overrides |
| 7-category gate guard | rm -rf, force push, SQL injection, disk destruction |
| Hookify MD engine | User needs custom policies without editing JS |
| Continual learning | Agent forgets everything between sessions |
| Ralph Loop | Agent stops work early, doesn't iterate |
| 4-agent parallel review | Single-agent review misses bugs |
| 15 specialized agents | Generalist agent makes mistakes in domain-specific work |
| Deploy/scaffold (deploy.py) | Manual copy-paste errors when replicating harness to new projects |
| Security allow-list | "Dangerous" calls (subprocess, os.environ) lack auditable justifications — scan false negatives |
| No-skips policy (check_skips.py) | Test coverage silently degrades via unconditional skip/skipif markers |

## Modification Policy

1. Before modifying any rule, ask: "Which failure mode does this prevent?"
2. If you cannot answer, do not modify.
3. Run `python .github/scripts/checklist.py .` after any change.
4. All gates must stay green.

## Key Metrics

| Metric | Target |
|--------|--------|
| Security scan | 0 findings |
| Ruff lint | 0 errors |
| Check skips | 0 unauthorized skips |
| Harness eval | 59/59 pass |
| Guard tests | 26/26 pass |
| Gate guard | 0 false blocks |
| Hook uptime | 100% (all stdin.resume present) |
