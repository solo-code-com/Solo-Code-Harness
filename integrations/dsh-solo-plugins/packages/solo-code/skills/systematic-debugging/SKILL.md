---
name: systematic-debugging
description: "Diagnose bugs, test failures, unexpected behavior, and errors before proposing fixes. Use when: debug, bug, error, failure, unexpected, broken, not working, investigate, root cause."
license: MIT
---

# Systematic Debugging

Random fixes waste time and create new bugs. Quick patches mask underlying issues.

## The Iron Law

**NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST.** If you haven't completed Phase 1, you cannot propose fixes. Symptom fixes are failure.

---

## Phase 0 — Build a feedback loop

**This is the skill.** Everything else in this document is mechanical follow-through. If you have a **tight** pass/fail signal for the bug — one that goes **red** on *this* bug — you will find the cause. Bisection, hypothesis-testing, and instrumentation all just consume it. Without one, no amount of staring at code will save you.

Spend disproportionate effort here. Be aggressive. Be creative. Refuse to give up.

### Ways to construct a feedback loop (try in this order)

1. **Failing test** at whatever seam reaches the bug — unit, integration, or e2e.
2. **Curl / HTTP script** against a running dev server.
3. **CLI invocation** with a fixture input, diffing stdout against a known-good snapshot.
4. **Headless browser script** (Playwright / Puppeteer) — drives the UI, asserts on DOM/console/network.
5. **Replay a captured trace.** Save a real network request / payload / event log to disk; replay through the code path in isolation.
6. **Throwaway harness.** Spin up a minimal subset of the system (one service, mocked deps) exercising the bug code path with a single function call.
7. **Property / fuzz loop.** If the bug is "sometimes wrong output", run 1000 random inputs and look for the failure mode.
8. **Differential loop.** Run the same input through old-version vs new-version (or two configs) and diff outputs.

### Tighten the loop

Treat the loop as a product. Once you have *a* loop, **tighten** it:

- **Make it faster** — cache setup, skip unrelated init, narrow test scope.
- **Make the signal sharper** — assert on the specific symptom, not "didn't crash".
- **Make it more deterministic** — pin time, seed RNG, isolate filesystem, freeze network.

A 30-second flaky loop is barely better than no loop. A 2-second deterministic one is a debugging superpower.

### Completion criterion — a tight, red-capable loop

Phase 0 is done when you can name **one command** (a script path, test invocation, or curl) that you have **already run at least once**, and that meets all four:

- [ ] **Red-capable** — drives the actual bug code path and asserts the **user's exact symptom**. Not "runs without erroring" — it must catch *this specific bug*.
- [ ] **Deterministic** — same verdict each run. For flaky bugs: a pinned, high reproduction rate is acceptable.
- [ ] **Fast** — seconds, not minutes.
- [ ] **Agent-runnable** — you can run it unattended without a human in the loop.

**Warning:** If you catch yourself reading code to build a theory before this command exists, **stop**. Jumping straight to a hypothesis without a red-capable loop is the exact failure this skill prevents. No red command, no Phase 1.

---

## Phase 1: Root Cause Investigation

**BEFORE attempting ANY fix:**

1. **Read Error Messages Carefully** — Don't skip warnings. Note line numbers, file paths, stack traces.
2. **Reproduce Consistently** — Exact steps. Every time? If not → gather more data, don't guess.
3. **Check Recent Changes** — Git diff, commits, new dependencies, config changes, environment differences.
4. **Multi-Component Evidence Gathering** — When system has multiple components (API → service → DB), add diagnostic logging at EACH boundary. Log what data enters/exits each component. Run once to isolate WHERE it breaks.
5. **Trace Data Flow** — Where does the bad value originate? What called this with the bad value? Keep tracing up. Fix at source, not at symptom.

---

## Phase 2: Pattern Analysis

1. **Find Working Examples** — Locate similar working code in the same codebase.
2. **Compare Against References** — Read reference implementation completely, understand pattern before applying.
3. **Identify Differences** — What's different between working and broken? List every difference, don't assume it "can't matter."
4. **Understand Dependencies** — What components, settings, config, environment does this need?

---

## Phase 3: Hypothesis and Testing

1. **Form Single Hypothesis** — State clearly: "X is the root cause because Y." Be specific.
2. **Test Minimally** — SMALLEST possible change, one variable at a time.
3. **Verify Before Continuing** — Did it work? Yes → Phase 4. No → Form NEW hypothesis. Don't add more fixes.
4. **When You Don't Know** — Say "I don't understand X." Don't pretend. Ask for help.

---

## Phase 4: Implementation

1. **Create Failing Test Case** — Simplest reproduction. MUST have before fixing.
2. **Implement Single Fix** — ONE change at a time. No "while I'm here" improvements.
3. **Verify Fix** — Test passes? No other tests broken? Issue actually resolved?
4. **If Fix Doesn't Work** — Count attempts. If < 3: return to Phase 1. **If ≥ 3: STOP. Question the architecture.** Each fix revealing new problems in different places = architectural problem, not a bug.

---

## Red Flags — STOP and Return to Phase 1

If you catch yourself thinking any of these, you're guessing, not debugging:

- "Quick fix for now, investigate later"
- "Just try changing X and see if it works"
- "It's probably X, let me fix that"
- "I don't fully understand but this might work"
- "Skip the test, I'll manually verify"
- "Add multiple changes, run tests"
- Proposing solutions before tracing data flow
- "One more fix attempt" (when already tried 2+)
- Each fix reveals new problem in different place

**3+ failed fixes = Question the architecture, not the fix.**

---

## Quick Reference

| Phase             | Key Activities                                         | Success Criteria            |
| ----------------- | ------------------------------------------------------ | --------------------------- |
| 0. Feedback Loop  | Build tight, red-capable repro command                 | Command is fast, deterministic, catches the bug |
| 1. Root Cause     | Read errors, reproduce, check changes, gather evidence | Understand WHAT and WHY     |
| 2. Pattern        | Find working examples, compare                         | Identify differences        |
| 3. Hypothesis     | Form theory, test minimally                            | Confirmed or new hypothesis |
| 4. Implementation | Create test, fix, verify                               | Bug resolved, tests pass    |

---

If root cause analysis reveals an **architectural issue** (no good test seam, tangled callers, hidden coupling), use skill `improve-codebase-architecture` to scan for deepening opportunities. Do this after the fix is in — you have more information then.
