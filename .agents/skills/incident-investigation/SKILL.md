---
name: incident-investigation
description: "Investigate a production incident, error spike, or log/data anomaly under time pressure -- find root cause before proposing a fix. Use when: production down, incident, outage, error spike, logs, investigate production, on-call, something broke in prod."
---

# Incident Investigation

Diagnose a live production problem (outage, error spike, anomalous data)
methodically, even under time pressure -- resist the urge to guess-and-patch.

## When to use this

- Something is broken in production right now (or was, recently)
- You need to read/query logs, metrics, or a data file to find what changed
- The user is on-call or reporting a live incident, not a routine bug report
- `debugging-and-error-recovery` is for a single reproducible bug; use this
  skill instead when the scope is "something is wrong in a live system and
  we don't yet know what."

## Process

### 1. Establish the timeline first

- When did it start? Correlate against recent deploys, config changes,
  traffic spikes, or upstream dependency changes
- Get the exact error signature (message, stack trace, status code) before
  theorizing about cause
- Confirm current impact/scope (all users? one region? one endpoint?)
  before deciding urgency

### 2. Read logs/metrics in plain terms before jumping to code

- Query logs for the error signature across the incident window
- Look for correlated signals: latency spikes, dependency errors, resource
  exhaustion (CPU/memory/disk/connections), rate limits
- Prefer structured queries (grep/filter by request ID, error class, time
  range) over scrolling raw logs

### 3. Form a hypothesis, then verify it against evidence

- State the suspected root cause explicitly before proposing a fix
- Verify it against the actual logs/metrics/data -- don't accept a plausible
  story that isn't confirmed by evidence
- If multiple hypotheses fit, find the one piece of evidence that
  distinguishes them, rather than picking the most likely-sounding one

### 4. Contain before you fully fix, if impact is ongoing

- If there's a safe, reversible mitigation (rollback, feature flag off,
  scale up, restart), consider recommending it to stop the bleeding while
  the full root-cause fix is still being worked out
- Never recommend a destructive action (data deletion, force-push,
  `rm -rf`, hard resets) as a mitigation -- see `permission-guard`

### 5. Fix the root cause, not just the symptom

- The mitigation from step 4 is not the fix -- confirm the actual code/config
  change once root cause is verified
- Add a regression test or alert that would have caught this earlier, when
  practical

### 6. Write it up

- Summarize: timeline, root cause, mitigation, fix, and one concrete
  follow-up to prevent recurrence
- Log the incident + resolution via `/remember` or the project's shared
  state so the next session (or engine) has this context

## Safety rules

- Never run destructive commands against production systems without
  explicit confirmation
- Never guess at root cause and ship a fix without confirming it against
  logs/evidence first
- Always state current blast radius/impact before recommending any action
