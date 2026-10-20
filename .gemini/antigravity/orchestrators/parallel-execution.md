---
description: Parallel execution — dependency analysis → worktree dispatch → verify → integrate
mode: orchestrator
---

# Parallel Execution Orchestrator

## Phase 1: Dependency Analysis
**Agent**: planner (with `task-delegation` skill)
**Action**: Build a dependency graph of pending tasks, group independent tasks into batches, draft a self-contained prompt per task
**Output**: Execution plan with dependency graph, batches, prompts

## Phase 2: Parallel Dispatch
**Agent**: solo-code-engineer (with `agent_manager` tool)
**Action**: Dispatch every task in a batch via `agent_manager` in `worktree` mode — each sub-agent gets an isolated git worktree, so file conflicts cannot occur
**Limit**: Max 4 sub-agents per batch

## Phase 3: Verify Results
**Agent**: code-reviewer (parallel mode)
**Action**: Per completed session, check files changed are within declared scope, no overlapping files inside a batch, tests pass, `python .github/scripts/security_scan.py .` clean, no debug statements left
**Output**: Per-task pass/fail; surface any overlapping-file conflict for manual merge

## Phase 4: Integrate & Gate
**Agent**: solo-code-engineer
**Action**: Merge verified worktrees into the integration branch, then run `python .github/scripts/checklist.py .` and the full test suite on the integrated code
**Hard Gate**: Any failing gate stops the merge — fix inside the offending worktree, then re-integrate

## Rules
- Sequential across batches, parallel within a batch — batch N+1 starts only after batch N passes Phase 3
- Max 4 sub-agents per batch, max 10 per orchestration run
- Once dispatched, a sub-agent prompt is sealed — do not modify mid-flight
- A single sub-agent failure stops its batch; fix and retry before continuing
