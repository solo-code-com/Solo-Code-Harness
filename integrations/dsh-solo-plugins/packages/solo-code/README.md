# @solo-code/dsh-solo-code

One DeepSeek Harness plugin carrying Solo-Code's operating discipline: an
approval policy on the tool seam, a fail-closed write gate, and four guidance
skills. The DSH kernel is not modified.

## What it contributes

| Seam | Conflict | Effect |
|---|---|---|
| `tools/pre-execute` | `ask` / `deny` / `allow` | Destructive shell commands, dependency changes, network calls, and git publication need approval; credential files (`.env`, `*.pem`, `*.key`, `*.p12`, `*.pfx`) cannot be written. |
| `tools/pre-execute` (`writeGate`) | `deny` | When enabled and `executor-mode` is not off, direct `write`/`edit` calls are denied so the work goes to a delegate. |
| `skills.register` | runtime skills | `solo-plan`, `solo-debug`, `solo-review`, `solo-verify`. |

## Config

| Key | Default | Meaning |
|---|---|---|
| `writeGate` | `false` | Enable the executor-mode write gate. |
| `executorModeFile` | `.solocode/executor-mode` | State file, resolved against the process working directory unless absolute. |
| `askOnWrites` | `false` | Ask for approval on every file write, not just gated ones. |
| `askOnDependencyChanges` | `true` | Ask on `npm`/`pnpm`/`pip`/`uv`/`cargo` install, add, remove. |
| `askOnNetwork` | `true` | Ask on `curl`, `wget`, `Invoke-WebRequest`, `Invoke-RestMethod`. |
| `askOnGitPublish` | `true` | Ask on `git commit`, `git push`. |
| `askOnDelegation` | `true` | Ask on `mcp__solo__delegate`, because a worker can modify files. |
| `denyCredentialWrites` | `true` | Deny writes to credential-file paths. |

The executor-mode file fails closed: a missing or unreadable file means ON, and
only `off`, `0`, `disabled`, `false`, `no` (case-insensitive) open the gate. The
gate covers the filesystem write tools; shell writes remain the approval
policy's job.

## MCP tool layer

`mcp/server.py` is a stdlib-only MCP server (stdio, newline-delimited JSON-RPC 2.0)
that the bundle mounts through `@deepseek-ai/dsh-mcp-client` as server name
`solo`. DSH exposes its tools as `mcp__solo__<tool>`:

| Tool | What it does |
|---|---|
| `security_scan` | Runs `.github/scripts/security_scan.py` over a path inside the workspace. |
| `shared_state` | Reads `.solocode/shared-state.db` views: `show`, `features`, `sessions`, `locks`, `memory`, `validate`. |
| `model_catalog` | Compares provider model catalogs; `probe` calls one model per entry. |
| `delegate` | Runs one worker engine (`opencode`, `kilo`, `dsh`) with a self-contained brief and returns its evidence. |
| `route` | Routes a task by type (`simple`→opencode, `complex`/`review`→codex, `verify`→dsh), returns a claim/command/output evidence table, and appends the decision to `.solocode/dsh-routing.jsonl`. `dry_run` returns the decision without spawning. |
| `gate` | Runs one gate: `lint`, `lint_budget`, `schemas`, `drift`, `tests`, `secrets`, or `all` (`checklist --fast`). |

The server enforces its own fences: argv lists only, no shell, workspace root as
cwd, path arguments confined to the root, timeouts capped at 900 s, and no
write-enabling flags passed to delegates. The bundle patch pins the interpreter
and root as absolute paths, so it is machine-local by design.

## Skill pack

The package ships the Solo-Code harness assets copied from `.claude/` and
projects them onto the DSH skills seam through one provider:

| Source | Entries | Invocation | Name |
|---|---|---|---|
| `skills/<name>/SKILL.md` | 53 | model + user | the skill's own name |
| `commands/<name>.md` | 15 | user only | `cmd-<name>` |
| `agents/<name>.md` | 15 | user only | `agent-<name>` |

DSH has no file-based command or agent format: `commands.register` hands the UI
a handler, not a model message, so a Solo-Code command template has no native
equivalent. Projecting it as a user-invocable skill keeps its text reachable
without inventing a second mechanism. Set `skillPack: 'core'` to load only the
four built-in guidance skills and keep the catalog small.

## Install

This package is a DSH bundle (`dsh.bundle.patch`). Install it into a profile
with the Plugin Manager, passing this package directory, then confirm the
`solo-code` row is active. It carries no dependencies and needs no build.

## Verify

```sh
node --check index.js
node --test test/
```

The policy tests need no network, model key, or harness boot.

## Route B: run from source

The `deepseek-harness-master` checkout can mount this bundle without packaging the
desktop app, which reaches host seams a profile plugin cannot. Run both steps in a
normal shell, not inside a sandboxed agent shell:

```powershell
./scripts/enable-source-profile.ps1   # mount the bundle in the `headless` profile
./scripts/source-run.ps1              # compose from source and print the rows
```

The `desktop` profile is rejected by the source CLI: it is managed exclusively by
the Electron app. `enable-source-profile.ps1` therefore edits
`%USERPROFILE%\.dsh\profiles\headless` instead, drops the retired
`solo-policy`/`solo-guidance` link dependencies, adds this bundle, and relinks.
`source-run.ps1` installs the checkout's missing `node_modules` links when needed,
composes `headless`, and prints the Solo-Code rows.

The source tree is `0.2.2-alpha.2` while the packaged app is `0.2.1-rc.5`: tasks
run from source write sessions in the newer format, which the older app may not
read.
