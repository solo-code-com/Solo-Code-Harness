---
description: Record a design or implementation decision in IMPLEMENT.md. Track deviations from the plan.
---
# /decide — Record a Decision

## Purpose
Capture a significant decision made during implementation. Appends to `IMPLEMENT.md` in the project root. Used to maintain an audit trail of why choices were made.

## When to use
- Choosing between two technical approaches (e.g., library A vs library B)
- Deviating from the original plan documented in PLAN.md
- Resolving an open question that arose during implementation
- Documenting a trade-off that future maintainers need to understand

## Behavior
1. Identify the decision point and the options considered
2. Record what was chosen, what was rejected, and why
3. If this deviates from PLAN.md, note the deviation and update the plan
4. Append the decision as a new entry in `IMPLEMENT.md`

## Output format

Appends to `IMPLEMENT.md`:

```
### YYYY-MM-DD HH:MM — [Brief decision title]

**What happened:**
[Context that led to this decision]

**Decision:**
[What was chosen and why. What was rejected and why.]

**Deviation from plan:**
[Yes/No — if yes, what changed and has PLAN.md been updated]

**Next:**
[What follows from this decision]
```

## Rules
- IMPLEMENT.md is append-only — never edit past entries
- Every decision must explain not just what, but why
- If the decision contradicts the plan, update PLAN.md's milestones or scope
- Keep entries concise — 1 paragraph per section is usually enough
