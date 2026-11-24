---
name: improve-codebase-architecture
description: "Scan a codebase for deepening opportunities — refactors that turn shallow modules into deep ones — and present ranked candidates. Use when: architecture review, codebase feels like a ball of mud, too many small modules, hard to test, reduce coupling, improve maintainability."
license: MIT
---

# Improve Codebase Architecture

Scan a codebase for **deepening** opportunities — refactors that turn shallow modules into deep ones (many behaviours behind a small interface). Use vocabulary from skill `codebase-design`: module, interface, depth, seam, adapter, leverage, locality. Do not drift into "component", "service", or "boundary".

---

## 1. Explore

**Scope before you scan.** Deepening pays off on code that keeps changing, so decide *where* to look first:

- If the user named a module, subsystem, or pain point, take it and skip the inference below.
- Otherwise run `git log --oneline -n 200 --name-only` and rank paths by change frequency — those hot spots go first. If changes are scattered with no clear hot spot, widen the net.

1. Read `CONTEXT.md` if it exists (created by skill `domain-modeling`) to learn the project's shared language, plus any ADRs in `docs/decisions/` covering the area you are touching.
2. Use Agent `subagent_type=Explore` to scan the codebase. Record friction signals:
   - **Shallow modules** — interface nearly as complex as the implementation (many public methods for little behaviour)
   - **Module hopping** — must read 3+ files to understand one concept
   - **Coupling across seams** — changes in one module force changes in callers
   - **Hard-to-test** — test requires complex setup or mocks reaching deep into internals
3. Run the **deletion test** for each candidate: "If I delete this module, does the complexity vanish or reappear across callers?" Vanishes = pass-through (delete it). Reappears = earning its keep (keep, maybe deepen).

---

## 2. Present candidates (Markdown — not HTML)

Output a ranked list in Markdown. Each candidate follows this format:

```
### Candidate: <file/area>

**Files:** `<path>`, `<path>`

**Problem:** The module is shallow — callers must know X details to use it.

**Solution:** Merge modules A and B, expose a small interface, hide complexity inside.

**Benefits:**
- Locality: fix one place instead of N callers
- Leverage: N call sites exercise the same implementation
- Testability: test through the new interface, delete old unit tests

**Before→After:**
- Before: caller calls processItem(), validateItem(), formatItem() separately
- After: caller calls process(item) — validation and formatting are internal

**Strength:** Strong | Worth exploring | Speculative
```

Rank candidates by **locality gain** × **leverage gain**. End with a **Top recommendation**.

**ADR conflicts:** if a candidate contradicts an existing ADR in `docs/decisions/`, surface it only when the friction is real enough to justify reopening that decision, and mark it in the candidate (`Contradicts ADR-007 — worth reopening because …`). Do not list every refactor an ADR forbids.

---

## 3. Grilling loop

After presenting candidates, ask: "Which candidate should we explore first?" Do not propose detailed interfaces yet.

Once the user picks a candidate:
1. Use skill `interview-me` (upgraded with grilling cadence) to walk through the design tree one decision at a time.
2. Update `CONTEXT.md` inline (via `domain-modeling`) when new terms crystallise.
3. If the user rejects a candidate for a **load-bearing** reason, offer to record it: "Want me to record this as an ADR so future architecture reviews do not re-suggest it?" Use skill `documentation-and-adrs`. Only offer when a future explorer would need that reason to avoid re-proposing the same refactor — skip ephemeral ("not worth it right now") and self-evident ones.

---

## Dependency categories (for assessing candidates)

| Category | Description | Deepenable? | Test strategy |
|---|---|---|---|
| **In-process** | Pure computation, no I/O | Always | Test through new interface directly |
| **Local-substitutable** | Has local test stand-in (PGLite, in-memory FS) | Yes, if stand-in exists | Test with stand-in |
| **Remote but owned** | Your own services across network | Yes, with port/adapter | In-memory adapter in tests |
| **True external** | Third-party services (Stripe, Twilio) | Yes, with mock | Inject mock at seam |

**Seam discipline:** One adapter = hypothetical seam. Two = real seam. Do not introduce a port unless at least two adapters are justified.

**Internal vs external seams:** A deep module can have internal seams for its own tests — do not expose them through the interface.

When deepening: **replace, don't layer.** Old unit tests on shallow modules become waste once tests at the deepened module's interface exist — delete them.
