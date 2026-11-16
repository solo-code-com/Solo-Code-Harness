---
name: dynamic-workflow-orchestration
description: "Orchestrate broad, parallel, or adversarial tasks using the native Workflow tool. Use when: codebase audit, parallel research, adversarial verify, pipeline migration, fan-out subagents, structured output from many files."
license: MIT
---

> **Claude Code only.** Kilo, OpenCode, and Copilot do not have the Workflow tool - use CLI delegation (`tools/antigravity_delegate.py`, `tools/opencode_delegate.py`) instead.

# Dynamic Workflow Orchestration

Use the native Workflow tool (not CLI delegation) when a task is genuinely
parallel or adversarial and fits within Claude's context window. CLI delegation
stays for cross-engine perspective, context exceeding Claude's window, or UI
verification.

## Five template patterns

Templates live in `.claude/workflows/` and run as slash commands, one per file
stem: `/crud-module`, `/api-from-spec`, `/data-migration`, `/compliance-audit`,
`/adversarial-implementation`. The script reads its input from the `args`
global, and `args` must be an object, not the sentence typed after the command:
passed as one string, a run fails with "missing required arg". Name the keys
explicitly when you ask, and if that error appears, ask for the arguments as an
object. When `args` is omitted the script sees `undefined`.

The `api-from-spec` and `crud-module` templates emit Express routes, so the
target project needs `express` installed. Their tests use the built-in
`node:test` runner, which needs no install. Run them from the project root with
`node --test`; Node rejects a directory argument. Pass `includeTests: false` to
skip the test stage: three agents per endpoint instead of four, and no test files.

### /crud-module

**When.** A project grows one five-file module per entity (model, repository,
service, api, tests) and the shape barely changes between entities.

**Invoke.** `/crud-module` with `entityName`, `entitySchema` (an object of
per-field rules) and optional `outputDir`, which defaults to `src/`. A missing
name, or a schema that is not a plain object, throws before any agent runs.

**Primitives.** `phase('Generate')`, then `parallel()` over the five
components, each callback one `agent()` whose `schema` demands `path`, `exports`
and `summary`. `phase('Verify')` then spends one agent on the linter and the
type check.

**Outcome.** Five files written concurrently, filtered with `.filter(Boolean)`,
then a verification object with `ok` and `issues`. The verify agent reports
problems and edits nothing.

### /api-from-spec

**When.** The endpoints already exist as a written spec and each one needs a
route, a handler, validation and tests. Pass `includeTests: false` when you
only want the scaffold.

**Invoke.** `/api-from-spec` with `specFile`, optional `outputDir` (default
`src/api/`) and optional `maxEndpoints` (default 40).

**Primitives.** `phase('Parse')` reads the spec once into an endpoint list.
`pipeline()` then runs one callback per endpoint, and each callback awaits
route, then handler, then validation, then tests in that order, so one
endpoint's four agents never interleave with another's. With
`includeTests: false` the tests step is skipped and each endpoint costs three
agents instead of four.

**Outcome.** One route, handler, validation and test set per selected endpoint,
then a `Verify` agent that runs the checks this run required, which means the
suite plus the contract check unless tests were skipped. A spec above 4096
endpoints throws instead of truncating silently. The agent budget is the tighter
limit: 249 endpoints with tests, 332 without.

### /data-migration

**When.** Many files carry one old shape and move to one new shape, and each
file can be migrated on its own.

**Invoke.** `/data-migration` with `sourcePattern` (a glob), `targetSchema` (an
object) and `outputDir`, which is required here and has no default, plus
optional `maxFiles` (default 50).

**Primitives.** `phase('Discover')` lists the matching files. `pipeline()` gives
each file its own callback, and each callback owns one input file and one
explicit output path: validate the source, transform it to the target shape,
then write only the output path.

**Outcome.** One migrated file per source file, then a `Verify` agent that
checks only the paths this run produced against `targetSchema`.

### /compliance-audit

**When.** A rules file exists and several directories have to be checked against
it, with findings ranked so the critical ones surface first.

**Invoke.** `/compliance-audit` with `rulesFile`, `targetDirs` (a non-empty
array) and optional `auditTypes`, which defaults to `['all']`.

**Primitives.** `phase('Load')` parses the rules once, then `parallel()` runs
one audit agent per directory in `phase('Audit')`. The merge is plain JavaScript
in `phase('Synthesize')`: a severity map, a `flatMap`, one `sort`. No agent is
spent on merging.

**Outcome.** A flat findings array ordered critical, high, medium, low, plus one
`log()` line giving the count against the number of directories that answered.

### /adversarial-implementation

**When.** An implementation is risky enough that one reviewer would miss a
whole class of defect, and a single fix pass is cheaper than review rounds.

**Invoke.** `/adversarial-implementation` with `specFile`, `targetDir` and
optional `businessRulesFile`. Without that last one the business-rules reviewer
derives the rules from the spec.

**Primitives.** `phase('Implement')` writes from the spec, `phase('Review')`
runs `parallel()` with two independent reviewers, one for security and one for
business rules, and `phase('Fix')` spends exactly one agent on every issue at
once. Do not fan the fixes out in parallel over the same directory: two agents
would edit the same file.

**Outcome.** `{ status: 'passed' }` when neither reviewer reports anything,
otherwise one fix pass over the named files and `{ status: 'fixed' }`.

## Script surface and limits

The only globals are `agent()`, `parallel()`, `pipeline()`, `phase()`, `log()`
and `args`. Nothing accounts for tokens or spend: there is no such object, and
there is no `workflow` function for nesting one script inside another. A script
that reaches for either throws at run time. `agent()` takes `schema`, `label`
and `stallMs`.

The runtime limits:

- 4096 items per `parallel()` or `pipeline()` call
- 1000 agents per run
- 16 concurrent by default
- no `import()` in the script
- `Date.now()`, `Math.random()` and `new Date()` throw
- no mid-run user input

`agent()` resolves to `null` when the run is stopped, so a fan-out ends with
`.filter(Boolean)` before anything reads the result.

## When to use templates vs inline scripts vs CLI

**Use a template** when the pattern repeats across projects, when the result
must be structured, or when the work needs independent reviewers.

**Write an inline script** for a one-off task that will not recur, even a small
one: fewer files to keep in step.

**Use CLI delegation** (`tools/antigravity_delegate.py`,
`tools/opencode_delegate.py`) when you need a different model, when the work
exceeds Claude's context, or for UI verification.
