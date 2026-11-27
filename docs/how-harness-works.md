# How the harness works

One page describing the machinery: the roles that run it, the generated files, the
boundaries, the fences around a worker, the verification stack, and what state survives
a session. It describes what exists and adds no rules. For rules see `AGENTS.md`.

## 1. What runs this, and who may write

Three roles (`SPEC.md` section 0.2):

| Role | Who | What |
|---|---|---|
| Orchestrator | agent `solocode-orchestrator` | holds the context, splits the task, dispatches workers, verifies the diff |
| Deep code | DeepSeek via the OpenCode CLI; `dsh` for DeepSeek-native work | implements the brief |
| Quick work | Gemini via the Antigravity CLI | lookups and read-heavy passes; its tokens are free |

The orchestrator holds the context, splits the task, dispatches, and verifies the diff.
Its agent config declares `edit: deny`, enforced at runtime rather than by convention:
`tools/opencode_delegate.py` omits `--auto` for that agent. The same engines double as
independent reviewers: any worker can take a second pass over another worker's output, and
a wide change records that pass as a receipt.

A worker is either an API call or a CLI. `tools/model_api.py` speaks a provider's chat
API directly with a fenced tool loop. `tools/opencode_delegate.py`,
`tools/kilo_cli_delegate.py`, `tools/antigravity_delegate.py` and `tools/dsh_delegate.py`
each drive a CLI.

## 2. Where the source lives and what is generated from it

`.kilo/` is the source of truth. `tools/generate_harness.py` emits:

- `.opencode/`: agents, commands, and instruction-derived skills
- `.agents/skills/`: the vendor-neutral skills copy OpenCode reads
- the `.gemini/antigravity/` mirrors

A hand edit to any of these is wiped on the next run. `tools/opencode_engine.py` owns
the Kilo-to-OpenCode transform.

## 3. What the boundaries are and who checks them

`.harness.lock` declares which paths are harness: the `[boundaries]` dirs, plus
`[shared_files]` inside the shared dirs. It also holds a `content_fingerprint` over
those paths. `tools/garden.py` compares the fingerprint and fails on drift.

A change to a harness file must regenerate the lock with
`python tools/deploy.py --root-lock`, and the order matters: after `generate_harness.py`,
never before.

## 4. How a worker is fenced, and what happens when it fails

Two gates control writes.

The first applies to the orchestrator: `.solocode/executor-mode` toggles whether it may
call `Edit`/`Write`/`MultiEdit`. Default is ON, and a missing or unreadable file leaves
it on, so a fresh clone fails closed. Kilo Code enforces it with
`.kilo/hooks/pre-tool-use/executor-mode.js`. OpenCode has no hook layer, which is why its
split is an agent-level `edit: deny` plus the wrapper's missing `--auto`. Bash is never
gated, so the orchestrator still runs its own verification.

The second applies to a worker. A wrapper that can write captures the tree first and
prints a snapshot id. `tools/restore.py apply <id> --yes` restores every file byte for
byte, including uncommitted work. `--worktree` builds a tree from HEAD and refuses a
dirty one. Two writers run at once only when each has its own tree: both wrappers
compare a snapshot of the whole workspace, so a concurrent writer looks like this run's
violation.

## 5. What verifies a change before it lands

Two command gates:

| Scope | Command |
|---|---|
| Local | `make check` |
| CI | the gates written out in `.github/workflows/ci.yml` |

`make check` runs ruff, the lint budget, the stdlib-only gate, text hygiene, bypass
claims, the prose budget, schema validation, garden, and the full pytest suite.
`.github/scripts/checklist.py` bundles a secret scan, ruff, the boundary audit, harness
eval, schema validation and pytest behind one command, but CI does not call it: the
workflow runs those gates as separate steps. `tools/coverage_gate.py` ratchets per-file
coverage.

A change wider than five files or 120 lines requires a review receipt from
`tools/review_receipt.py`. The receipt hashes the **staged index**, not the working tree:
`git diff --cached` supplies the file list and the patch. Stage the change, then record;
an unstaged edit or an untracked file does not move the hash, and re-staging one does.

## 6. What state survives a session, and what is disposable

`.solocode/shared-state.db` (SQLite, local-only) holds a session log and a lock table.
`.solocode/sessions/`, `restore/`, `reviews/`, `task-receipts/` and `*-usage.jsonl` are
local evidence; `tools/retention.py` archives aged entries rather than deleting them.
`.kilo/memory/MEMORY.md` holds decisions that must survive a session.
`.harness-tmp/tool-output/` is disposable scratch, swept on every write.

Two invariants are worth naming here.

`.harness-tier` holds `client`, `personal` or `public`, resolves upward from the working
directory, and defaults to `client`, the strictest, when absent, so a new project fails
closed.

`truncate_output` in `tools/model_api.py` keeps both ends of an oversized tool result and
spills the whole text to `.harness-tmp/tool-output/` inside the repository, because
`read_file` refuses anything outside it. It sweeps that directory on write, caps the
label, and holds `len(result) <= budget`. The read path passes `spill=False`: a read that
spilled would point the model at a new file on every hop.

The seam pattern that lets a provider swap without touching consumers is documented in
`docs/capability-seams.md`: a Service Definition (interface plus registry key), Providers
that register implementations, and Consumers that resolve through the registry. Consumer
and Provider never import each other; both import only the definition.

## Where to read next

- `SPEC.md`: the authoritative specification for every mechanism above.
- `AGENTS.md`: the root rulebook; behavior rules, not architecture.
- `docs/capability-seams.md`: the definition/provider/consumer pattern in full.
- `docs/defensive-patterns.md`: the guard patterns the wrappers and gates rely on.
