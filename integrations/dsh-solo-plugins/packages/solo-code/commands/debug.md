---
description: Systematic debugging workflow with evidence-based root cause analysis.
---
# /debug — Systematic Debugging

## Purpose
Activate evidence-based debugging. Never guess — form hypotheses, test them, confirm root cause before fixing.

## Behavior
1. **Reproduce** — Can you reproduce the bug? Describe exact steps
2. **Isolate** — Narrow down to the specific file/function/line
3. **Hypothesize** — Form **at least 2 candidate root causes**, not just the
   first one that comes to mind. List them explicitly before testing any of
   them — a single hypothesis tested and "confirmed" is often just
   confirmation bias, not proof.
4. **Test each hypothesis** — Add logs, breakpoints, or a minimal
   reproduction to confirm or rule out each candidate. Rule out the wrong
   ones with evidence (don't just skip to the one that "feels right").
5. **Fix** — Apply the minimal fix for the confirmed root cause
6. **Verify** — Prove the fix works with a test

## Rules
- Never apply a fix without understanding the root cause
- Never stop at the first hypothesis that seems plausible — rule out
  alternatives with evidence first
- Always write a regression test
- Read error messages and stack traces carefully
- Check git log for recent related changes
