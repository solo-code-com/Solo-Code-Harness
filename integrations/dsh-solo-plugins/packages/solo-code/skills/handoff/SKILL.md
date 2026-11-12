---
name: handoff
description: "Compact the current conversation into a handoff document so a fresh agent or next session can continue. Use when: hand off, end of session, context getting full, switch agents, pause work, write a handoff."
license: MIT
---

# Handoff

Write a handoff document summarising the current conversation so a fresh agent can continue the work.

---

## Storage location

Write to **`.solocode/session-handoff.md`** (overwrite). `.solocode/` is
this harness's local state directory — gitignored, already used by the
hooks (`shared-state.db`, `context-checkpoint.json`). Do not save to
temporary OS directories, and do not commit handoffs.

---

## Document structure

```markdown
# Session Handoff

> Last update: <date> — <one-line summary>

## Current Objective

- Goal: <one-line>
- Current status: <which step / phase>
- Branch / commit: <reference>

## Completed This Session

- [x] <item with key result>

## Verification Evidence

| Check | Command | Result |
|---|---|---|
| <check name> | `<command>` | PASS/FAIL |

## Blockers / Risks

- <anything blocking or risky>

## Suggested skills

- <skill name> — <why the next session needs it>

## Next Steps

1. <ordered start-up steps>
```

---

## Rules

- **Do not duplicate** content already captured in plans, ADRs, specs, or feature_list. Reference them by path instead.
- **Redact** API keys, passwords, PII. Mark redacted sections `[REDACTED]`.
- **Suggested skills section** — recommend skills the next session should invoke (e.g. `incremental-implementation`, `systematic-debugging`).
- If the user described the next session's goal, tailor the document to that focus.

---

## Relationship with progress.md

| File | Behaviour |
|---|---|
| **`session-handoff.md`** | Overwritten each session — clean start |
| **`progress.md` (`.solocode/progress.md`)** | Append-only log across sessions |

Do not merge them. Handoff = fresh state; progress = history.
