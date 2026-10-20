---
description: "Solo-Code Harness self-management: deploy, configure, maintain multi-agent harness + security audit + spec-driven orchestration"
mode: orchestrator
---

# Parallel Execution Orchestrator

Coordinates parallel sub-agent dispatch for independent tasks, reducing end-to-end time by running non-dependent work simultaneously.

## Why Parallel?

- **Sequential bottlenecks**: Full-stack orchestrator runs phases 1→2→3→4→5→6, but phases 4 (code review) and 5 (lang-specific review) are independent — they could run in parallel
- **Multi-file refactors**: Refactoring module A and module B simultaneously (if no shared imports) cuts time by 50%
- **Test + fix cycles**: Running unit tests while fixing an unrelated lint issue uses wait time productively

## Workflow

### Phase 1: Dependency Analysis

**Agent**: planner (with task-delegation skill)

**Action**:
- Load the task-delegation skill and analyze all pending tasks
- Build a dependency graph identifying independent vs dependent tasks
- Group tasks into execution batches (each batch = tasks that can run simultaneously)
- For each task, draft a self-contained sub-agent prompt

**Output**: Execution plan with dependency graph, batches, and prompts

### Phase 2: Parallel Dispatch

**Agent**: solo-code-engineer (with agent_manager tool)

**Action**:
- For each batch (sequentially across batches):
  - Dispatch all tasks in the batch via `agent_manager` in `worktree` mode
  - Each sub-agent works in an isolated git worktree → zero file conflicts
  - Sub-agents run as independent Agent Manager sessions
- Max 4 sub-agents per batch to preserve context window

**Dispatch Template**:
```
agent_manager:
  mode: worktree
  tasks:
    - prompt: "<self-contained prompt from Phase 1>"
      name: "<Short name, 2-4 words>"
      branchName: "feature/<task-slug>"
```

### Phase 3: Verify Results

**Agent**: code-reviewer (in parallel mode)

**Action**: For each completed sub-agent session, verify:
- Files changed are within declared scope
- No overlapping files between tasks in the same batch (conflict detection)
- Tests pass for each changed module
- `python .github/scripts/security_scan.py .` clean
- No debug statements or console.log left in

**Conflict Handling**:
If tasks in a batch modified overlapping files, surface to user:
"⚠️ Conflict: {task-a} and {task-b} both modified {file}. Manual merge required."

### Phase 4: Integrate & Gate

**Agent**: solo-code-engineer

**Action**:
1. Merge verified worktree results into the integration branch
2. Run full `python .github/scripts/checklist.py .` on integrated code
3. Run full test suite to detect integration regressions
4. Final `security_scan.py .` pass
5. Create integration summary for user

**Hard Gate**: If any quality gate fails (security scan, test suite, lint), STOP — do not merge. Fix in the offending worktree first, then re-integrate.

## Orchestration Rules

- **Sequential batches, parallel within batch**: Batch 2 starts ONLY after all Batch 1 tasks pass Phase 3 verification
- **Max 4 sub-agents per batch**: Context window preservation
- **Max 10 sub-agents per orchestration run**: Resource limit
- **Immutable prompts**: Once a sub-agent is dispatched, its prompt is sealed — do not modify mid-flight
- **Stop on failure**: If any sub-agent in a batch fails Phase 3, stop the batch. Fix and retry before proceeding.
- **Security gate is non-negotiable**: No integration proceeds without clean `security_scan.py` and `checklist.py`

## Integration Summary Format

```markdown
# Parallel Execution Summary: [Feature/Goal]

## Batches Executed
| Batch | Tasks | Dispatched | Passed | Failed |
|-------|-------|------------|--------|--------|
| 1     | 3     | 3          | 3      | 0      |
| 2     | 1     | 1          | 1      | 0      |

## Files Changed (Total)
| Task | Files | +Lines | -Lines | Branch |
|------|-------|--------|--------|--------|
| task-a | auth.py, test_auth.py | +45 | -12 | feature/task-a |
| task-b | config.py | +30 | -5 | feature/task-b |

## Quality Gates
| Gate | Status |
|------|--------|
| Security scan | ✅ Clean |
| Test suite | ✅ 47/47 passing |
| Lint | ✅ Clean |
| Checklist | ✅ Passed |

## Audit Trail
- Orchestrator run: [date]
- Total sub-agents: 4
- Total wall-clock time: ~[estimate]
- Agents invoked: planner, code-reviewer, solo-code-engineer
```
