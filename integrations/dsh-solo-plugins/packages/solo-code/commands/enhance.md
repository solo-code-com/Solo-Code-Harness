---
description: Safely enhance or refactor existing code without breaking functionality.
---
# /enhance — Safe Code Enhancement

## Purpose
Add features to or refactor existing code without regressions.

## Behavior
1. **Understand current behavior** — Read the code. What does it do now?
2. **Identify change points** — Exactly which lines need to change?
3. **Make minimal changes** — Smallest diff that achieves the goal
4. **Preserve existing tests** — All existing tests must still pass
5. **Add new tests** — Cover the new behavior
6. **Verify** — Run the full test suite

## Rules
- Read before edit — always
- Never rewrite the whole file — targeted edits only
- If a refactor grows beyond 3 files, stop and propose a separate PR
- Back up complex logic before simplifying
