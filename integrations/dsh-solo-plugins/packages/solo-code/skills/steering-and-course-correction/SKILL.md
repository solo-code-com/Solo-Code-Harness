---
name: steering-and-course-correction
description: "Correct an agent's direction mid-task without derailing progress -- when it scopes too broadly, misreads intent, or drifts from the actual goal. Use when: wrong direction, off track, not what I meant, too much, scope creep, redo, narrow down, that's not it."
---

# Steering and Course Correction

Redirect an in-progress task cleanly when the agent has gone off-track --
without discarding useful partial work or restarting from zero.

## When to use this

- The agent is editing/exploring far more than the task needed (scope creep)
- The agent misunderstood the actual goal and is solving the wrong problem
- A correction mid-conversation needs to permanently change direction, not
  just patch the next message
- The user says some version of "that's not what I meant" / "stop, too much"

## Process

### 1. Stop and diagnose before acting further

Before making any more changes, identify precisely where the drift started:
- Which instruction was misread, or which assumption was wrong?
- Was the scope wrong (too broad/narrow) or the approach wrong (right goal,
  wrong method)?
- Don't guess -- if ambiguous, ask one clarifying question instead of
  continuing to iterate blindly.

### 2. Decide what to keep vs. discard

- **Keep**: work that is still valid under the corrected understanding
  (e.g., a bug fix found along the way, a util that's still needed)
- **Discard or revert**: work that only made sense under the wrong
  assumption -- don't leave it half-applied
- State explicitly what you're keeping/reverting and why, so the user can
  veto before you proceed

### 3. Narrow (or redirect) the scope explicitly

- Restate the corrected, narrower goal in one sentence before continuing
- If the task was too broad, cut it down to the smallest slice that
  satisfies the actual ask
- If the approach was wrong, name the new approach before writing code

### 4. Apply the correction as a durable change, not a one-off patch

- A correction given once should hold for the rest of the session -- don't
  regress to the old behavior on the next similar request
- If the correction reveals a gap in project rules/instructions (AGENTS.md,
  a skill, a command), consider logging it via `/remember` or updating the
  relevant instruction file so future sessions don't repeat the mistake

### 5. Confirm before continuing at scale

- After the first corrected step, pause and confirm it matches intent
  before applying the same pattern across many files/tasks
- Prefer one small verified step over many steps built on an unverified
  correction

## Safety rules

- Never silently discard work without saying so
- Never expand scope again right after being told to narrow it
- If the correction contradicts an explicit earlier instruction, surface
  the conflict instead of picking one side silently
