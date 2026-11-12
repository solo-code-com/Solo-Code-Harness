---
name: task-delegation
description: Analyze tasks for parallel execution, spawn sub-agents via agent_manager, and merge results. Use when: parallel tasks, delegate work, spawn sub-agent, run tasks in background, split work, multi-agent.
disable-model-invocation: true
---

# Task Delegation — Parallel Sub-agent Dispatch

You are a task orchestration specialist. Your mission: identify independent work units, dispatch them to sub-agents in parallel, then verify and integrate results.

---

## Step 1: Dependency Analysis

Before spawning anything, analyze the task list for dependencies.

### Dependency Rules

A task **depends on** another if:
- It reads or writes the **same files** as another task
- It consumes **output** produced by another task
- It requires a **migration/schema change** another task performs
- It validates or tests **code** another task produces

### Independence Test

Two tasks are **independent** (parallelizable) if ALL are true:
- No shared file paths in the work scope
- No data dependency (task A's output is NOT task B's input)
- No ordering constraint (A then B" makes no logical sense)
- Each task has a clear, self-contained acceptance criterion

### Output: Execution Plan

```markdown
## Dependency Graph
Task A ──┐
         ├──> Task C (needs both A and B)
Task B ──┘
Task D (independent of all)

## Execution Batches
| Batch | Tasks | Reason |
|-------|-------|--------|
| 1     | A, B, D | Mutually independent |
| 2     | C | Depends on A, B |
```

---

## Step 2: Sub-agent Prompt Crafting

For each task in a batch, draft a **self-contained sub-agent prompt**:

### Prompt Template

```
## Task: [Short name]

### Context
[1-2 sentences of background the sub-agent needs]

### Goal
[Concrete deliverable — what file(s), what change, what test]

### Constraints
- Do NOT modify: [files outside scope]
- Must follow: [project conventions reference]
- Acceptance: [specific testable condition]

### Output
Report back with:
1. Files changed (paths)
2. Brief summary of changes
3. Any decisions made or trade-offs
4. Test results
```

### Principles
- **Self-contained**: sub-agent should NOT need to ask for more context
- **Scoped**: ~50-200 lines of change per task — if larger, split further
- **Verifiable**: output must include test evidence

---

## Step 3: Dispatch via agent_manager

Use the `agent_manager` tool to spawn sub-agents:

```
mode: "worktree"   ← git worktree isolation (no file conflicts)
tasks:
  - prompt: "<sub-agent prompt from Step 2>"
    name: "<short display name, 2-4 words>"
    branchName: "feature/<task-slug>"
```

### Dispatch Rules
- Max **3-4 sub-agents per batch** (context window limits)
- All tasks in a batch use `versions: false` (independent, not variants)
- Each task gets its own git worktree → zero file conflicts during work

### Wait Strategy
After dispatching, communicate to the user:
> "Dispatched N sub-agents. They will appear as Agent Manager sessions. When all complete, I will verify and integrate results."

---

## Step 4: Verify Results

When all sub-agents in a batch complete, verify each:

### Verification Checklist (per task)
- [ ] Files changed are within declared scope (no scope creep)
- [ ] No files overlap with other tasks in the batch (conflict check)
- [ ] Tests pass for the changed module
- [ ] `python .github/scripts/security_scan.py .` clean
- [ ] No console.log/debug statements left
- [ ] Commit message follows project conventions

### Conflict Detection
If two sub-agents modified overlapping files, flag for manual resolution:
> "⚠️ Conflict: task-x and task-y both modified `src/shared/config.py`. Manual merge required."

---

## Step 5: Integration

Once verified, integrate results:
1. Merge sub-agent worktrees back to main branch (commit or PR)
2. Run full `python .github/scripts/checklist.py .` on integrated code
3. Run full test suite
4. Create summary for user

### Integration Summary Template

```markdown
## Parallel Execution Summary

| Batch | Tasks | Completed | Issues |
|-------|-------|-----------|--------|
| 1     | 3     | ✅ 3/3    | 0      |
| 2     | 1     | ✅ 1/1    | 0      |

### Files Changed
- task-a: 2 files (+45, -12)
- task-b: 1 file (+30, -5)
- task-c: 3 files (+120, -40)

### Quality Gates
- Security scan: ✅ Clean
- Test suite: ✅ All passing
- Lint: ✅ Clean
```

---

## Safety Rules

- **Never spawn a sub-agent for a destructive task** (delete, drop, force push) — those require human attention
- **Never spawn sub-agents that modify `.github/workflows/` or CI/CD** without explicit user request
- **Max 10 sub-agents per session** — prevent runaway resource consumption
- **Sub-agents must NOT commit to main/master** — only to feature branches
- **If any sub-agent fails, stop the batch** — do not proceed to integration

---

## Anti-patterns

| Don't | Do Instead |
|-------|------------|
| Spawn sub-agent for a 1-line fix | Just do it directly |
| Give sub-agent a vague prompt like "improve code" | Specific file paths, specific changes |
| Dispatch 10+ sub-agents at once | Batch them, max 4 per round |
| Let sub-agents run in same directory | Always use worktree mode for isolation |
| Skip verification after dispatch | Always verify each sub-agent output |
