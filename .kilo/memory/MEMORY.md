# Memory Index

High-signal context for session start. History belongs in `decisions-archive.md`.

## Project
- Branches: `feature/[slug]` or `fix/[slug]`. `AGENTS.md` is the rulebook; `.harness.lock` defines boundaries.
- `.kilo/` is the source of truth; `.opencode/` and `.agents/skills` are generated, `.gemini/` parity-checked. No Claude Code, Codex or Copilot engine remains (removed 2026-11-24).

## Rules
- Read before editing; surgical changes; verify syntax and tests.
- User input is untrusted: parameterize SQL, keep secrets in env. Ask before destructive actions, dependency installs, CI edits or deletion.
- Executor mode is ON by default; run the security scan before committing.

## Stack
- Python 3.10+ stdlib for `tools/`; pytest and ruff are dev tools. Kilo hooks are Node 18+. Pre-commit pins ruff 0.9.1.
- `.solocode/shared-state.db` holds `active_locks` and `session_log` only; the four legacy memory tables are removed when an older file is opened. This file is the memory; git log is the task history.
- `tools/portfolio.py` is a derived index of the sibling projects in the folder above
  this checkout: `sync` mirrors the scanned roots into `.solocode/portfolio.db`, `status`
  compares each project lock fingerprint with this repo, `failures` and `usage` read the
  per-arm logs. Delete the database and `sync` rebuilds it.

## Verify
`security_scan.py .` - `checklist.py .` - `check_skips.py tools/` - `garden.py` - `pytest tools` - `prose_lint.py`

## Gotchas
- TOML bare keys must precede the first table header.
- Cap is 8,000 characters, measured in characters. Move pruned text to `decisions-archive.md` verbatim.
- Never run two pytest processes; `.pytest_temp` races on Windows.
- Windows console is cp1252: use `_make_streams_encoding_safe`; `python -c` in PowerShell crashes the same way.
- The Kilo arm needs `kilo_server_manager.py ensure`; otherwise exit 7.
- `agy --allow-tools` reports another writer's edits as its own; run it only when the tree is idle.
- A Windows junction is not a symlink: use `workspace_snapshot.is_reparse_point`, never walk one.
- argv caps a brief at 32767 characters; the wrappers stop at 30,000.
- A bloated `.solocode` slows the suite: 866s with 2.8 GB of restore snapshots and 300k files, 290s after `retention --apply` and `--purge`. Run retention before blaming the tests.
- Import tools as `tools.<name>`; a bare import elsewhere creates a second module object.
- `.harness.lock` must be STAGED with the code: pre-commit stashes unstaged changes, so an unstaged lock reverts to the old fingerprint and garden-drift fails. Order: `git add <code>`, `deploy.py --root-lock`, `git add .harness.lock`.
- Pinned ruff 0.9.1 rejects `isinstance(x, (int, float))` (UP038) even when the local ruff passes it: run `pre_commit run ruff --files ...` before committing.
- `Out-File -Encoding utf8` (PS 5.1) writes a BOM and `git commit -F` keeps it in the subject; write the message file with Python instead.

## Decisions
- 2026-11-26: Work item 3 (add `$ARGUMENTS`/`argument-hint` to the 15 `.kilo/command/`
  files) is DROPPED, decision C. Kilo is barely used here, and churn in a file no engine reads
  buys nothing: OpenCode V2 already appends non-empty arguments after a blank line when a
  template has no placeholder, so `/test auth.py` works today. `subtask:` stays untouched for
  the same reason -- it is a dead field in a copied file, not a deprecated one needing a rename.
- 2026-11-26: Pi (earendil-works/pi) evaluated as a reference, not adopted. Only two
  of its practices were portable: keep head+tail when truncating tool output, and enforce
  the Agent Skills frontmatter caps. MEASURED facts behind that: `parse_frontmatter` returns
  `|-` for a YAML block scalar, so a naive 1024-char check passes every skill and enforces
  nothing (2 of 54 skills use block scalars). The Kilo CLI binary is the OpenCode runtime
  (`kilo.exe --help` logs `process_role=main ... opencode`); its VS Code extension reads
  `.kilo/skills/`, `.kilo/setup-script` and `.kilo/worktrees/` only, never `.kilo/command/`.
  Therefore `.kilo/command/` is this harness's own convention: `$ARGUMENTS` reaches OpenCode
  (verified in the V2 docs) but nothing proves Kilo expands it, and `subtask:` is carried by
  nothing but the copy. Keep command frontmatter a harness convention, not an engine contract.
- 2026-11-25: Working split: Gemini analyses the problem and writes the code by default;
  DeepSeek takes the hard code and the adversarial review. A review covers features
  (reachable, wired, tested, documented) as well as code. SQLite stays: it is the only
  engine that supports concurrent writers for the lock table, and the index is 370 rows.
- 2026-11-25: Hydrogen only (`medium` floor, `high` for accuracy); 3.8-flash retired, no downgrade. Kilo arm model is `opencode-go/deepseek-v4.1-flash`.
- 2026-11-25: Cascade opencode-go -> commandcode -> kilo, 1800s budget, never on a changed tree or a policy refusal.
- 2026-11-25: `--worktree` on all wrappers (exit 7 if dirty); byte-exact restore; protected-state fingerprint; credential-path brief filter; retention archives `.solocode` and never touches receipts.
- 2026-11-25: Skill proposals: an agent writes `.solocode/skill-proposals/<slug>` with a canary; promotion is manual; garden warns at 90 days.
- 2026-11-24: P5 (no network control) accepted, reasoning in `tools/win_sandbox.py`. Regenerate the lock with `deploy.py --root-lock` after `git add`.
- 2026-11-23: FreeModel removed; tier gate `tools/tier_gate.py`, missing marker = `client`.
- 2026-11-26: Routing adviser: `gemini-decision-1` primary, Jev backup, both advisory only.
  A low-confidence answer, an undeclared worker or a safety `noul` above 0.85 stops at `worker=none`
  instead of falling back to the rule table: measured, the cases the model doubted were exactly the ones
  whose right answer was `none`. Real cost from a top-up: 0.48 dong per call, about 1:34 against a paid
  delegation. The free-tier keys work but are not used (one prepaid key, no rotation).
- 2026-11-26: Open before wiring `--decide` into the wrappers: the safety question is miscalibrated
  (0.57 for a read-only survey, 0.91 for `find the file that defines pid_alive`), so the gate blocks benign
  work. Measure it on the labelled set; if it does not separate benign from destructive, drop the gate and
  keep the hard deny list plus confidence.
