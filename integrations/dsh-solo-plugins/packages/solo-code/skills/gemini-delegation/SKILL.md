---
name: gemini-delegation
description: Routes read-heavy work, broad scoped changes, and independent review to Gemini through the Antigravity headless CLI, selecting gemini-3.8-flash high/medium/low by task complexity. Use GUI inbox/outbox handoff only when a rendered UI needs human verification.
---

# Gemini/Antigravity Delegation — Headless Worker

## Overview

The orchestrator invokes `agy.exe` through `tools/antigravity_delegate.py`.
Read-only tasks are the default. A write requires both `--auto-approve` and an
explicit `--allow-dir`; the wrapper takes a directory lock, prepends a guardrail,
and rejects a post-run scope violation. That check is an **audit of `--target-dir`
only** — it cannot see writes outside it and cannot undo a write, and
`--auto-approve` still passes `--dangerously-skip-permissions` to `agy.exe`.
`--allow-dir` is the fence; this is not a sandbox. Review the diff and rerun all
evidence: the worker report is not verification.

The payoff is real and measured. On a repo-wide audit (2026-07-26) Gemini read
25 files / 194 KB (~49,600 tokens) while this orchestrator spent ~2,500 tokens
writing the brief and verifying the result — roughly **95% saving, ~20x
leverage**. That ratio is the entire reason the mechanism exists.

## Making it effective — measured (2026-10-07)

Three `--allow-tools` runs on broad tasks all **timed out at 400s**, while
generation-only runs finished in **18–32s**. Cause: with tools, the worker spends
its budget exploring the tree (one repo held a 2 GB `node_modules`), and every tool
call adds a permission round-trip.

**Protocol that works — the orchestrator reads, Gemini reasons:**

| # | Rule | Why |
|---|---|---|
| 1 | The orchestrator gathers the files and inlines the needed excerpts in the prompt. Pass **no** `--allow-tools`. | Tool exploration is what times out |
| 2 | One question per call; keep the prompt ≤ ~1 page. | Long prompts drift and slow the model |
| 3 | Pass the prompt as a **single line**. PowerShell mangles multi-line arguments, and an embedded `"` makes argparse split the prompt into extra args. | Measured failure: `unrecognized arguments` |
| 4 | Use `--allow-tools` only when the task truly must run commands; down it with `--target-dir` narrowed to the smallest directory and list the exact commands. | Avoids traversing a huge tree |
| 5 | Timeout: 180s for generation-only, 900s for a tool-using run. | 400s was too short for tool runs, needlessly long for generation |
| 6 | On timeout resume with `--continue-latest` (or `--conversation <id>`). | The wrapper prints the id only on success |

Measured: generation-only 18.2s and 31.6s; `--allow-tools` 400s timeout (×2).

## Why not the SDK or IDE chat command

Two dead ends, both verified — do not re-investigate without new information:

- **`google-antigravity` Python SDK**: headless, but authenticates only via
  `GEMINI_API_KEY` or Vertex + ADC (confirmed by reading its `models.py`).
  There is no OAuth path, so it **cannot reuse the IDE's Gemini Pro login**.
  Using it would bill a separate API account rather than the existing plan.
- **`antigravity-ide chat -m ask|edit|agent`**: exists, but only sends the
  prompt into the GUI. Exit 0, empty stdout, no readable result.

Use `agy.exe --print --output-format stream-json` instead. It returns structured
events and works without the GUI.

## When to Delegate

Strong fit — this is where the leverage is:

| Task shape | Why Gemini |
|---|---|
| Read >5 files, then summarize/compare | Its context is cheap; ours is not |
| Repo-wide audit ("where else does X appear?") | Breadth beats depth here |
| Independent review of a design or diff | A second model catches different things |
| UI verification / screenshots / recordings | The orchestrator cannot do this at all |
| Scoped code writing behind an explicit fence | Proven workable — see the fencing rule below |

Poor fit:

- Anything needing this conversation's history — the handoff file is the
  **only** context Gemini receives.
- Architecture and product decisions. Those stay with the orchestrator.
- Small mechanical edits — use Kilo CLI instead; it is headless and does not
  cost the user a relay step.

Rule of thumb: if the task is small enough that Kilo CLI can do it, **use Kilo CLI**.
Gemini earns its relay cost only on breadth.

## Maximizing Antigravity CLI use

Prefer Antigravity CLI (`tools/antigravity_delegate.py`) for every read-heavy,
broad, or independent task. Concretely, always route these to it:

- reading **>5 files** to summarize, compare, or audit
- repo-wide surveys: "where else does X appear?", "list every caller of Y"
- independent review of a design, diff, or PR
- running the project's read-only gates (`garden`, `ruff`, `check_skips`) and
  reporting the raw output — use `--allow-tools` so the commands can run
- scoped code writing behind an explicit `--allow-dir --auto-approve` fence

Keep in the orchestrator: anything needing this conversation's history, and
architecture / product / security decisions.

### Can this be forced?

**No hard enforcement exists.** The orchestrator chooses which tool to call, and
no hook intercepts "the agent read 6 files directly" to block it. The available
levers are all soft:

| Lever | Kind | Where |
|---|---|---|
| Routing rules ("use Antigravity CLI for X") | advisory | `AGENTS.md`, this skill |
| Session-start availability announcement | reminder | `.claude/hooks/session_start.py` |
| `opencode.json` permission `ask` on the wrapper | prompt, not a block | `opencode.json` |
| Wrapper `--allow-dir` / lock / scope audit | enforced *if a run happens* | `tools/antigravity_delegate.py` |

To make delegation near-certain without a hard gate:
1. Keep the routing rules imperative (they already are).
2. Name the trigger in the task itself — e.g. *"delegate this audit to agy"*.
3. Pass `--allow-tools` so a read-only audit can actually run greps and tests.

A blocking hook ("deny Read on the Nth file unless delegated") was **not** added:
it would fight the Escalation and Surgical-Changes rules and break legitimate
large in-orchestrator work. Treat maximization as a policy plus a prompt, not a
gate.

## Invocation

```powershell
# Read-only task
python tools/antigravity_delegate.py "<task>" --model gemini-3.8-flash-medium

# Read-only task that needs tools (greps, tests, git status), no write scope
python tools/antigravity_delegate.py "<task>" --allow-tools --model gemini-3.8-flash-medium

# Write task: narrow directory scope and explicit auto approval
python tools/antigravity_delegate.py "<task>" --allow-dir src --auto-approve --model gemini-3.8-flash-high
```

### Tool permissions and silent failures

A plain read-only run cannot answer the `command` permission prompt headless, so
`run_command` is auto-denied and agy still returns `status: SUCCESS`. The wrapper
therefore inspects `denied_actions` and per-tool `ERROR` events: a denied run
exits **5**, an empty-output run exits **2**, and an error that looks like an
exhausted quota exits **6** (see the auth section), never a silent success. Use
`--allow-tools` for read/execute work without granting a write scope (no
directory lock, no scope audit); it is mutually exclusive with `--auto-approve`,
`--allow-dir`, and `--no-guardrail`. For a least-privilege alternative, add a
`permissions.allow` rule in `~/.gemini/antigravity-cli/settings.json` (e.g.
`"allow": ["command(python tools/garden.py)"]`) — see the auth section below.

### Choose the model by task complexity

Gemini 3.8 Flash exposes three reasoning levels as distinct model ids. All three
draw the same account quota, so spend the cheapest level that fits:

| Complexity | Model id | Use for |
|---|---|---|
| Low | `gemini-3.8-flash-low` | Mechanical, single-file, little reasoning |
| Medium (default) | `gemini-3.8-flash-medium` | Multi-file summarize/compare, scoped refactor |
| High | `gemini-3.8-flash-high` | Independent design review, subtle bug hunt |

### Authentication, quota, and account rotation

Per the [Antigravity CLI auth docs](https://antigravity.google/docs/cli/install/),
`agy` stores a **token profile in the OS keyring** (Windows Credential Manager)
and signs in silently when one exists; otherwise it opens a browser OAuth flow.
There is **no per-run account flag**, so exactly one account is active at a time.

- **Switch account:** run `/logout` in the CLI (purges the keyring profile), then
  start `agy` again and sign in with the other account. The wrapper inherits the
  active profile on its next run.
- **Check quota:** `/usage` (alias `/quota`) — a TUI panel only; there is no CLI
  flag for it.
- Plan-quota exhaustion makes delegated runs fail. Rotation is manual and serial,
  so do not start an unattended batch that will cross a quota boundary.

**Headless alternative — Gemini API key.** For unattended/CI runs, set
`modelProvider` to `gemini` in `~/.gemini/antigravity-cli/settings.json` and
export `GEMINI_API_KEY`. The CLI reads only that variable (`GOOGLE_API_KEY` and
`.env` are ignored), and `/logout` has no effect in this mode. Point at a custom
endpoint with `GOOGLE_GEMINI_BASE_URL`. This bills the API key instead of the plan
quota — the documented answer when no browser is available.

**Other `settings.json` keys that matter** (`~/.gemini/antigravity-cli/settings.json`):

- `"permissions": {"allow": ["command(git)"], "deny": ["command(rm -rf)"]}` —
  fine-grained command allow/deny; the least-privilege alternative to
  `--allow-tools`.
- `toolPermission`: `request-review` (default), `proceed-in-sandbox`,
  `always-proceed`, `strict`. Prefer the wrapper's per-run `--allow-tools` over
  the global `always-proceed`.
- `useG1Credits` (external builds): spend personal AI credits once plan quota is
  exhausted.
- `enableTerminalSandbox` (default `false`): OS containment (`AppContainer` on
  Windows) for agent shell commands. `agy --sandbox` enables it per run.
- `allowNonWorkspaceAccess` (default `false`): leave false; the wrapper's
  `--add-dir` is the workspace fence.

The wrapper prints the `conversation=<id>` line on every run. Resume an
interrupted task after re-auth with:

```powershell
python tools/antigravity_delegate.py "<follow-up>" --conversation <id>
# or, to resume the most recent conversation
python tools/antigravity_delegate.py "<follow-up>" --continue-latest
```

Use `.gemini/antigravity/handoff/` only as a GUI fallback for UI verification.

## Writing the Brief — Four Rules Learned From Failures

Each of these fixed an observed, reproduced failure. They are not style
preferences.

**1. Fence the scope, and name the expected failure.** Gemini follows an
explicit fence well, but "don't break things" is too vague to act on. When a
correct result will make a gate go red, say so — otherwise it will helpfully
"fix" the drift it was told only to detect:

```markdown
- **Do NOT fix any of the drifts you find.** You will almost certainly make
  `python tools/garden.py` start FAILING, because there are real stale counts
  in the repo right now. **That failure is the expected, correct outcome.**
```

**2. Demand evidence, never self-assessed confidence.** A "Confident? Y/N"
column came back 22-for-22 "Yes" — including on a finding that was wrong. The
column carried zero information. Require the command and its output instead:

```markdown
| Claim | Command run | Output (trimmed) |
Do not write a claim you did not actually run a command for.
```

**3. Say explicitly which files may be touched — including the brief.** The
handoff README permits updating `status:` in the plan file; a brief saying
"modify nothing but the report" contradicts it. Resolve it in the brief:
*"Do NOT edit this file. Leave `status: pending`."*

**4. State the ground truth measurement, not the number.** Tell it how to
measure (`ls .kilo/skill | wc -l`), never what the answer should be. A brief
that leaks the expected answer cannot detect that the answer changed.

## After Delegating — Verify 100%, Always

Both controlled tests produced errors that were invisible in Gemini's own
summary and surfaced only under verification:

- Audit run: 1 of 22 findings was factually wrong, marked "Confident: Yes".
- Code run: shipped 2 false positives while self-reporting "unsure about:
  nothing".

The operating rule that follows: **its evidence tables are reliable; its
self-assessment is not.** Never let a self-report substitute for a check.

1. Re-run every command in its evidence table yourself. Spot-checking is not
   enough — the wrong row looked exactly like the right ones.
2. Run the project's real gates (`make check`, `python tools/garden.py`,
   targeted pytest, `security_scan.py`).
3. For new checks, **mutation-test them**: break the thing the check guards
   and confirm the check goes red. A check that cannot fail is not a check.
4. For new code, re-derive the ground truth independently before accepting
   any list of "problems found" — false positives are the failure mode.

## Concurrent-Edit Safety

Gemini edits the same working tree at the same time as this session. Before
delegating anything that **writes**:

- The wrapper takes a `tools/shared_state.py` directory lock; do not bypass it.
- Name the writable directory with `--allow-dir` and forbid everything else.
- Never delegate a write that overlaps a file being edited in this session.

Gemini also keeps its own artifacts outside the repo at
`~/.gemini/antigravity-ide/brain/<session-uuid>/` (`*.md` + `.metadata.json`,
plus `.system_generated/logs/transcript.jsonl`). Those are machine-readable and
useful for auditing what it actually did — but they are **not** the handoff
channel. The report file in `outbox/` is.

## Anti-patterns

| Don't | Do Instead |
|-------|------------|
| Delegate a small mechanical edit to Gemini | Use Kilo CLI — headless, no relay cost to the user |
| Trust its "Confident: Yes" / "unsure about: nothing" | Re-run its evidence commands yourself |
| Write a brief without an explicit writable-file list | Fence the scope; name every path it may touch |
| Put the expected answer in the brief | Give the measurement command, not the number |
| Let a correct-but-red gate look like failure | Predict the red gate in the brief and call it correct |
| Delegate a write without `--allow-dir --auto-approve` | Use the wrapper's required scope and explicit opt-in |
| Try the SDK or `antigravity-ide chat` for a headless run | Use `agy.exe` through the wrapper |
