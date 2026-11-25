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
- Import tools as `tools.<name>`; a bare import elsewhere creates a second module object.

## Decisions
- 2026-11-25: Hydrogen only (`medium` floor, `high` for accuracy); 3.8-flash retired, no downgrade. Kilo arm model is `opencode-go/deepseek-v4.1-flash`.
- 2026-11-25: Cascade opencode-go -> commandcode -> kilo, 1800s budget, never on a changed tree or a policy refusal.
- 2026-11-25: `--worktree` on all wrappers (exit 7 if dirty); byte-exact restore; protected-state fingerprint; credential-path brief filter; retention archives `.solocode` and never touches receipts.
- 2026-11-25: Skill proposals: an agent writes `.solocode/skill-proposals/<slug>` with a canary; promotion is manual; garden warns at 90 days.
- 2026-11-24: P5 (no network control) accepted, reasoning in `tools/win_sandbox.py`. Regenerate the lock with `deploy.py --root-lock` after `git add`.
- 2026-11-23: FreeModel removed; tier gate `tools/tier_gate.py`, missing marker = `client`.
