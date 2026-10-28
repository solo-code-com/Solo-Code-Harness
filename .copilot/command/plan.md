---
description: Generate a structured implementation plan and checklist before writing code.
subtask: true
---

# /plan — Implementation Planning

## Purpose
Generate a detailed implementation plan before writing any code. Forces thinking through architecture, dependencies, and risks upfront.

## Behavior
1. **Analyze requirements** — What exactly needs to be built?
2. **Identify affected files** — List specific files that will be created/modified
3. **Break down into tasks** — Ordered steps, each paired with the check that proves it
4. **Flag risks** — What could go wrong? Dependencies? Edge cases?
5. **Estimate effort** — Per task

## Output Format
```markdown
## Plan: [Feature/Task]

### Affected Files
| File | Action | Purpose |
|------|--------|---------|
| src/... | CREATE | ... |
| src/... | MODIFY | ... |

### Tasks (Goal-Driven)
1. [ ] [Step] — description → verify: [command or observable check]
2. [ ] [Step] — description → verify: [command or observable check]
3. [ ] [Step] — description → verify: [command or observable check]

**Done when:** <observable success criteria>

### Risks
- Risk 1: ... (mitigation)
- Risk 2: ... (mitigation)

### Verification
- [ ] Tests pass
- [ ] Manual check: ...
```
