# Claude Code <-> Gemini/Antigravity Handoff Protocol

Antigravity IDE has no headless/scriptable CLI (verified: `antigravity-ide.cmd
--help` only exposes GUI window/diff/extension-management flags, no
prompt-execution subcommand). This means Claude Code cannot invoke Gemini
directly the way it invokes Kilo CLI (`Kilo CLI run ...`) -- a human still has to
open Antigravity and give Gemini one instruction. This protocol minimizes
that manual step to "read file X, write your report to file Y" instead of
copy-pasting full plan/result text back and forth through chat.

## Directories

- `inbox/` -- Claude Code writes a structured plan file here for Gemini to
  execute. Committed to git (durable record of what was delegated and why).
- `outbox/` -- Gemini/Antigravity writes its report/artifact here when done.
  Committed to git (durable record of what came back).

## Naming convention

Both sides use the same slug for a given task:

```
inbox/<slug>-plan.md
outbox/<slug>-report.md
```

`<slug>` = short kebab-case task name, e.g. `refactor-auth-module`.

## Plan file format (inbox/<slug>-plan.md)

```markdown
---
slug: <slug>
created: <ISO date>
from: claude
status: pending
---

# Task

<one paragraph: what needs to be done and why>

## Context

<relevant files/背景, links to code, constraints>

## Expected report format

<what Claude needs back to continue: e.g. "list of files changed + a
summary of the approach taken" or "a design doc with 2-3 options
compared">
```

## Report file format (outbox/<slug>-report.md)

Gemini/Antigravity should write back in whatever format fits the task, but
should always include a frontmatter header so the handoff is traceable:

```markdown
---
slug: <slug>
completed: <ISO date>
from: gemini
---

<report content>
```

## Who may edit what

The plan file is **written by Claude Code and read-only for Gemini**,
including its `status:` field. An earlier version of this README implied
Gemini should flip `status: pending` -> `done`; that contradicted the briefs,
which fence Gemini to writing only the report. The report file's existence
in `outbox/` *is* the completion signal — a duplicate status flag can only
disagree with it.

| File | Claude Code | Gemini |
|---|---|---|
| `inbox/<slug>-plan.md` | writes | reads only — do not edit, leave `status: pending` |
| `outbox/<slug>-report.md` | reads | writes |
| everything else | per the brief | only paths the brief names explicitly |

Restate this fence in every brief. Gemini follows an explicit written scope
well, but does not infer one from this README.

## Workflow

1. Ask Claude Code to draft a plan for a task you want to delegate to Gemini.
2. Claude Code writes `.gemini/antigravity/handoff/inbox/<slug>-plan.md` and
   logs a `shared-state` entry marking the delegation (see
   `tools/shared_state.py`).
3. Open Antigravity, tell Gemini: *"Read
   `.gemini/antigravity/handoff/inbox/<slug>-plan.md` and follow it. Write
   your report to `.gemini/antigravity/handoff/outbox/<slug>-report.md`."*
4. Next session (or on request) the orchestrator lists `outbox/` and reads any
   new report.

## Why inbox/outbox is separate from `knowledge/`

`.gemini/antigravity/knowledge/` is a **static** corpus (project conventions,
security patterns, anti-hallucination rules) indexed by `metadata.json` --
background rules Gemini always has loaded, not a per-task inbox. Mixing
transient task handoffs into that folder would make the static knowledge
base harder to audit. Keep the two concerns separate.
