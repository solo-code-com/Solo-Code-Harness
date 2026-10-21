# Solo-Code CLI

AI coding agent harness — rules, skills, hooks, and verification gates for disciplined Solo-Code engineering.

Engine support: **Kilo Code** (`.kilo/`, source of truth — all other engine artifacts are generated from or kept in parity with it), **Claude Code** (`.claude/` + `CLAUDE.md`, orchestrator, generated from `.kilo/`), **OpenCode** (`.opencode/` + `opencode.json`, generated from `.kilo/`, reintroduced in v4.2.0 as a primary engine and worker CLI), **GitHub Copilot** (`.copilot/`, manually kept in parity with `.kilo/`), **Gemini/Antigravity** (`.gemini/`), **Codex CLI** (`.codex/` + `codex-env.ps1`).

> **v4.2.0:** OpenCode engine reintroduced as a first-class primary agent engine (removed in v4.0.0 as a 100%-parity mirror with no unique capability, then brought back once OpenCode v1.18+'s stable native format made near-identity regeneration from `.kilo/` cheap). Full history in `.kilo/memory/MEMORY.md` → "Decisions".

> **Canonical repository:** <https://github.com/solo-code-com/Solo-Code-Harness> (MIT). The engine
> version `4.3.1` is stamped in `.harness.lock`, `agent.yaml` and `pyproject.toml`; the
> specification keeps its own document version (`SPEC.md`, currently `4.4.0`) — two axes, not drift.

## Quick Start

```bash
# Launch Claude Code (orchestrator) — see claude-env.ps1
./claude-env.ps1

# Regenerate the Claude engine from .kilo/ source
python tools/generate_harness.py --harness claude

# Run all quality gates
make check

# Single gates
make test              # Harness tests
make security-scan     # Secret detection
make validate          # Schema validation (.kilo/ agents + skills)
make garden            # Drift detection (.kilo <-> .claude / .copilot)

# Integration tests (Copilot structure + shared state)
python tools/test_integration.py

# No-skips test policy
python .github/scripts/check_skips.py tools/
```

## Scaffold & Deploy

Use `tools/deploy.py` to replicate this harness into new or existing projects.
Deploy is **runtime-only**: it copies what a target project needs to actually
*run* the AI-CLI harness (agents/skills/commands/hooks/config), never
Solo-Code-CLI's own dev tooling (`deploy.py`, `garden.py`, `generate_harness.py`,
`test_*.py`), meta docs (`SPEC.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
`SECURITY.md`), CI workflows, or this repo's own accumulated memory — target
projects get fresh blank memory templates instead.

### Scaffold a new project

```bash
# Create a new project from scratch with full harness
python tools/deploy.py scaffold /path/to/new-project

# Custom project name and description
python tools/deploy.py scaffold /path/to/new-project --name my-app --description "My app"

# Kilo-only engine
python tools/deploy.py scaffold /path/to/new-project --engine kilo

# Copilot-only engine
python tools/deploy.py scaffold /path/to/new-project --engine copilot
```

Scaffold creates the directory, copies engine configs (Kilo + Claude Code + Copilot + Gemini, runtime-only — see "Deploy is runtime-only" note above), generates `.gitignore` and `README.md`, runs `git init`, and prints post-setup instructions.

### Deploy to an existing project

```bash
# Copy harness into an existing project directory
python tools/deploy.py deploy /path/to/existing-project

# Dry run — preview changes without copying
python tools/deploy.py deploy . --dry-run
```

### Auto-detect

```bash
# Auto-detect: scaffold if target missing, deploy if exists
python tools/deploy.py /path/to/target
```

### Interactive mode

```bash
# No arguments → interactive setup wizard
python tools/deploy.py
```

---

## Structure

| Directory | Purpose |
|---|---|
| `.claude/` | **Orchestrator** — Claude Code: agents (15), skills (53), commands (15, incl. `ship`), instruction (10), guard + lifecycle hooks + `settings.json`; rulebook `CLAUDE.md` at root. Generated from `.kilo/` via `tools/generate_harness.py --harness claude`. |
| `.copilot/` | GitHub Copilot: agents (15), skills (53), commands (15), instruction (10), memory (4). Manually kept in parity with `.kilo/`; checked (not generated) by `tools/garden.py`. |
| `.kilo/` | **Source of truth** — Kilo Code: agents (15), skills (53), commands (15, incl. `ship`), hooks, memory, instruction. Edit here first. |
| `.gemini/` | Gemini/Antigravity: agents (15), skills (53), commands (12), knowledge. Manually kept in parity with `.kilo/`; checked by `tools/garden.py`. |
| `.codex/` | Codex CLI integration: README, session tracking, verified write guard (`tools/codex_guard.py`), and launcher `codex-env.ps1`. |
| `.github/` | Shared scripts: `security_scan.py`, `checklist.py`, `check_skips.py`, `eval_harness.py`, `boundary_audit.py`, `security-allowlist.txt` + `copilot-instructions.md`, `prompts/` |
| `tools/` | Generator (`generate_harness.py`, `claude_engine.py`), validator, drift detector (`garden.py`), integration tests, `shared_state.py` (runtime dep of Claude session hooks) |
| `.vscode/` | VS Code settings + MCP config for Copilot |

## Model Providers & the 4-Role Pipeline

Four providers are generated into `.opencode/opencode.json` — `commandcode`, `freemodel`,
`deepseek`, `openrouter` — with **104 model ids** declared in `tools/opencode_engine.py`
(commandcode 94, freemodel 3, deepseek 2, openrouter 5). Their base URLs come from `.env`, and
only `freemodel`'s template appends a path (`${OPENAI_BASE_URL}/v1`); the others are interpolated
verbatim, so nothing may strip a trailing `/v1` from them.
`tools/check_model_catalog.py` checks the ids against `GET /models` (plus `--probe`), and probes
`POST /systemone` for the Jev decision model, which `/models` never lists; ids are never taken
from memory.

Work is routed through four roles, declared in `.kilo/agents/jev.md`:

| Role | Engine | Notes |
|---|---|---|
| **Jev** — routes only | agent on `openrouter/jev-router` | `edit: deny`; the wrapper launches it without `--auto` so the deny binds |
| **Codex** — briefs and verification | `gpt-6.1-sol`, effort `medium` | `--model` / `--effort` override; launched by `codex-env.ps1` |
| **DeepSeek V4.1 Flash** — implementation | OpenCode CLI (`tools/opencode_delegate.py`) or the direct API arm (`tools/model_api.py`) | one-shot: 2.3 s trivial, 22 s one file; 503 above ~38 K-token prompts |
| **Gemini 3.8 Flash** — fast and broad work | Antigravity CLI, tiers `low`/`medium`/`high` (`tools/antigravity_delegate.py`) | independent review, repo-wide survey |

Architecture, product and security judgment is not delegated: workers are context-blind.

**A worker's output is a claim, not a result.** In controlled tests each worker produced at least
one error invisible in its own summary — a wrong finding marked "Confident: Yes", two false
positives reported as "unsure about: nothing". Their evidence is reliable; their self-assessment is
not, so the orchestrator re-runs the commands and the real gates before trusting a change.

## Confinement (WSL sandbox)

Workers that need to run code in a loop can be fenced with `tools/wsl_sandbox.sh` (wired as
`tools/model_api.py --sandbox wsl`): a private `/tmp` tmpfs, the workspace bound at `/tmp/work`,
every Windows drive overlaid with its own tmpfs, and `/home` + `/root` read-only. `/mnt/wsl*` stays
visible on purpose — hiding it kills DNS. The Windows ACL restricted-token path was measured and
rejected: it breaks TLS (`SEC_E_NO_CREDENTIALS`), so no worker that calls an API can run inside it.

Measured: two commands inside the fence in 10.9 s (pytest 9.1.1, ruff 0.17.1), writes outside the
workspace refused, TLS 200. Agreed but not built: a docker fence (daemon not running) and dsh
inside WSL to use its bwrap sandboxes, which are inert on Windows.

## DeepSeek Harness Port (Tier A)

Ports architecture patterns from the upstream reference
[`deepseek-harness-master/`](docs/dsh-port-map.md) (226 npm packages, MIT) as
capability seams — Service Definition / Provider / Consumer — without pulling in
Cordis or the TypeScript runtime.

| Module | Role | Ports from |
|---|---|---|
| `tools/agent_scope.py` | Scoped tool registry (global + per-agent shadow + dispose) | `core/scope` |
| `tools/subagent_seam.py` | Subagent Service Definition (`SubagentRequest`/`Result`/`Runtime` Protocol), splits evidence from self-assessment, fail-loud capability gating (`capabilities`/`supported_capabilities()`/`check_capabilities()`), closed-union `stop_reason` | `subagent` |
| `tools/compaction.py` | Byte/char budget policy | `compaction` |
| `tools/compaction_pruner.py` | Prunes oversized tool results to a byte budget (standalone tool, not a hook) | `compaction-tool-result-pruner` |

Decision record: [`docs/dsh-port-plan.md`](docs/dsh-port-plan.md) (scope),
[`docs/dsh-port-map.md`](docs/dsh-port-map.md) (49-package map + postmortem
lessons). Tier B/C (running the dsh SDK/runtime as a third executor) is blocked:
the runtime's single-file executable is a Linux/macOS-only non-goal, and
`deepseek-v4-pro` is already reachable via OpenCode CLI + CommandCode — no
harness change required.

## Shared State (Cross-Engine, Local-Only)

Hai file SQLite local-only, đều nằm trong `.solocode/` (đã bị `.gitignore` chặn):

- **`.solocode/shared-state.db`** — nhật ký sự kiện đa engine + khoá file:
  - **`session_log`** — mỗi session được ghi lại: engine, model, files changed, verification (giữ tối đa 1000 dòng gần nhất); ghi tự động bởi `pre_compact.py` (Claude) và `codex_session.py` (Codex).
  - **`active_locks`** — ngăn 2 engine sửa cùng 1 file cùng lúc (tự hết hạn sau 2 giờ); do worker lấy/trả qua `acquire_lock`/`release_lock`.
  - **`features`**, **`shared_memory_*`** — schema còn để tương thích ngược nhưng **không dùng** (không hook/engine nào gọi `set_feature_status()`). Task tracking dùng `git log` + `.kilo/memory/MEMORY.md`.
- **`.solocode/sessions.db`** — vòng đời phiên (start/end/duration/files/status) cho analytics; ghi bởi `.claude/hooks/session_start.py` / `session_end.py` qua `tools/session_persistence.py`.

```bash
python tools/shared_state.py show
python tools/shared_state.py locks
python tools/session_persistence.py --list
```

Bản đồ hai store và lý do tách vai trò: [`.kilo/instruction/shared-state.md`](.kilo/instruction/shared-state.md); biên bản nghiệm thu: [`docs/deepseek-harness-upgrade-acceptance.md`](docs/deepseek-harness-upgrade-acceptance.md).

## Gates

| Gate | Command | What it checks |
|---|---|---|
| Lint | `ruff check .` | Python code style |
| Schema | `make validate` | `.kilo/` agent + skill frontmatter validity |
| Drift | `make garden` | `.kilo` ↔ `.claude` (generated) / `.copilot` / `.gemini` (manual parity) |
| Document truth | `make garden` | Counts, cited paths, documented CLI flags, enforcement claims and skill references must match reality — see [SPEC.md §7.2.1](SPEC.md) |
| Harness Tests | `make test` | Generator + shared-state (`tools/test_*.py`) |
| Integration | `python tools/test_integration.py` | Copilot structure + shared state schema |
| Security | `make security-scan` | Hardcoded secrets |
| Boundary | `python .github/scripts/boundary_audit.py .` | No project files leaked into harness dirs |
| No-Skips | `python .github/scripts/check_skips.py tools/` | Unauthorized test skips (skip/skipif without reason) |
| Stdlib-only | `make stdlib-only` | `tools/` and `.github/scripts/` import nothing outside the standard library (SPEC section 4) |
| Text hygiene | `make text-hygiene` | Tracked sources end with a newline, carry no trailing space, and contain no CRLF |
| Bypass claims | `make bypass-claims` | A documented `*_BYPASS` / `*_OVERRIDE` name must be read by code, not only by prose |
| Prose budget | `make prose` | Long sentences, semicolons and em dashes may not grow past their recorded baseline |
| Ruff pin | `make lint-pin` | Runs the ruff version the commit hook pins, so make check and a commit cannot disagree |
| Gate probe | `python tools/gate_probe.py` | REPORT, never blocks: which gate catches which known defect, with caught, missed and no-gate outcomes |
| Review receipt | `python tools/review_receipt.py verify` | A staged change over 5 files and 120 lines needs a receipt recorded after /review; the gate blocks, the advisory hook above only measures |

## Slash Commands (Kilo + Claude Code)

| Command | Purpose |
|---|---|
| `/verify` | Run all verification gates |
| `/plan` | Delegate to planner agent |
| `/decide` | Delegate to architect agent |
| `/ship` | Pre-launch checklist |
| `/commit` | Create conventional commit |
| `/debug` | Systematic debugging workflow |

## Copilot Setup (VS Code)

```bash
# Open project in VS Code — Copilot auto-loads:
#   .github/copilot-instructions.md   (rulebook)
#   .github/prompts/*.prompt.md       (chat commands)
#   .vscode/settings.json             (Copilot config)
#   .vscode/mcp.json                  (MCP servers)
```

**Copilot Chat Commands** — type `#` in Copilot Chat and select:

| Command | Purpose |
|---|---|
| `verify` | Run all verification gates |
| `plan` | Create an implementation plan |
| `decide` | Architectural decision record |
| `ship` | Pre-launch checklist |
| `commit` | Create conventional commit |
| `debug` | Systematic debugging workflow |

## Claude Code Setup

Claude Code loads the harness natively from generated artifacts:

```bash
# Regenerate the Claude engine from .kilo/ source
python tools/generate_harness.py --harness claude
```

| Artifact | Path | Purpose |
|---|---|---|
| Rulebook | `CLAUDE.md` | Auto-loaded project memory (boundaries + rules) |
| Subagents (15) | `.claude/agents/*.md` | Invoke via Task tool or by name |
| Skills (53) | `.claude/skills/<name>/SKILL.md` | Auto-discovered capabilities |
| Slash commands (15) | `.claude/commands/*.md` | `/verify`, `/plan`, `/decide`, `/ship`, `/debug`, `/commit`, … |
| Guard hook | `.claude/hooks/guard.py` + `.claude/settings.json` | `PreToolUse` — blocks destructive commands, secret leaks, protected-config edits |
| Quality-gate hook | `.claude/hooks/quality_gate.py` | `PostToolUse` (Edit/Write) — advisory ruff/prettier/biome/gofmt format check |
| Memory-gate hook | `.claude/hooks/memory_gate.py` | `PostToolUse` (Edit/Write) — caps `.claude/memory/*.md` size (WARN 4k / **hard-block 8k** chars) so memory never silently bloats every session's context; Python port of Kilo's `memory-manager.js` |
| Security-post hook | `.claude/hooks/security_post.py` | `PostToolUse` (Bash) — scans `git diff` for secrets after commit/push |
| Pre-compact hook | `.claude/hooks/pre_compact.py` | `PreCompact` — logs a git-state checkpoint to shared-state and reminds Claude to persist any settled decision to `.kilo/memory/MEMORY.md` before context is summarized/cleared |
| Session hooks | `.claude/hooks/session_start.py`, `session_end.py` | `SessionStart`/`SessionEnd` — load git + cross-engine context; log session lifecycle to `.solocode/sessions.db` via `session_persistence.py` |

The guard hook is a stdlib-only Python port of the Kilo `gate-guard.js`/`secret-scan.js`
lifecycle hooks (44 destructive patterns + 23 secret patterns + protected config
files). It blocks a tool call by returning a `PreToolUse` deny decision and exit
code 2. The memory-gate hook can also hard-block (exit code 2) when a memory
file exceeds 8,000 chars. The remaining PostToolUse/Session hooks are advisory
(always exit 0, never block) — together bringing Claude Code to enforcement
parity with the Kilo engine. `garden.py`'s memory-drift check also diffs
`.claude/memory/*.md` content against `.kilo/memory/` (the source of truth),
not just filenames, so a silently out-of-sync mirror is always caught.

```bash
# Test the guard + lifecycle hook suites
python -m pytest tools/test_claude_guard.py tools/test_claude_hooks.py -q
```

### Launch Claude Code via FreeModel

`claude-env.ps1` loads `.env`, normalizes the gateway URL, and launches Claude Code
with profile-based behavior:

- `gateway` (default): FreeModel / third-party gateways via `ANTHROPIC_API_KEY` + `ANTHROPIC_BASE_URL`
- `native`: prefer `ANTHROPIC_API_KEY` / `apiKeyHelper` if present
- `kilo`: distinct profile name for IDE-integrated Kilo workflows

Every profile runs in full mode by default — hooks, skills, auto-memory and
`CLAUDE.md` auto-discovery are all active. Pass `--bare` explicitly to opt into
the reduced mode (which re-adds `CLAUDE.md` discovery via `--add-dir .` but
still skips hooks and auto-memory).

```powershell
# 1. Copy the template and fill your key
Copy-Item .env.template .env
#    then edit .env → set ANTHROPIC_API_KEY=<your-key>

# 2. Launch Claude Code with the default gateway profile
./claude-env.ps1

# 3. Optional profiles
./claude-env.ps1 --profile native
./claude-env.ps1 --profile kilo

# Pass-through args still work:
./claude-env.ps1 --help
```

`.env.template` ships with 3 FreeModel VIP tiers (`cc.freemodel.dev`, `api-cc.freemodel.dev`, `cc-t2.freemodel.dev`)
alongside a `COMMANDCODE_API_KEY` entry shared with Kilo CLI. Your real `.env` is gitignored and never deployed. Full mode is the default for every profile, so hooks and auto-memory stay active; pass `--bare` explicitly if a future provider swap needs the reduced mode.

## Gemini/Antigravity workers (headless CLI + GUI fallback)

Antigravity runs headless through `tools/antigravity_delegate.py`, which wraps
`agy.exe --print --output-format stream-json`. Read-only is the default; a write
requires both `--auto-approve` and an explicit `--allow-dir`, and the wrapper
takes a shared-state directory lock and audits the post-run scope. A plain
read-only run cannot answer the `command` permission prompt, so pass
`--allow-tools` when an audit must run greps or tests; the wrapper then fails
loudly (exit 5) if a tool was auto-denied instead of reporting a silent success.

Pick the model by task complexity — `gemini-3.8-flash-low|medium|high`. Inspect
the binary and its models with `python tools/antigravity_probe.py`.

The GUI inbox/outbox protocol in `.gemini/antigravity/handoff/` stays as a
fallback when a *rendered* UI needs human verification: Claude writes a plan to
`handoff/inbox/<slug>-plan.md`, you relay one line to Antigravity, and
`.claude/hooks/session_start.py` auto-detects the report at the next session
start. Full protocol: `.gemini/antigravity/handoff/README.md`.

### Which worker gets which job

Claude Code proposes a worker on its own when the shape fits — you should
not have to remember these exist. `session_start.py` announces each
engine's availability at session start.

| Work shape | Route to | Why |
|---|---|---|
| Read >5 files, then summarize/compare/audit | **Antigravity CLI** | Large context through a headless worker |
| Repo-wide survey — "where else does X appear?" | **Antigravity CLI** | Breadth is exactly its edge |
| Independent review of a design or diff | **Antigravity CLI** | A second model catches different things |
| UI verification, screenshots, recordings | **Antigravity GUI handoff** | The CLI cannot verify a rendered UI |
| Small mechanical edit, boilerplate, one test | **OpenCode CLI** | Headless — costs you nothing |
| Scoped code writing behind an explicit fence | **Antigravity CLI** (broad) / **OpenCode CLI** (narrow) | Both need the fence in writing |
| Architecture / product / security decisions | **Neither** | Judgment is not delegable |
| Anything needing the session's history | **Neither** | Workers are context-blind |

OpenCode CLI, Kilo CLI, and Antigravity CLI are all headless, so Claude delegates
directly. Antigravity CLI handles read-heavy and broad scopes; OpenCode CLI is
the narrow executor (reasoning depth + cache tracking); Kilo CLI is fallback. The
GUI handoff remains only for rendered-UI work. This routing cannot be
hard-enforced — no hook blocks a direct multi-file read — so it is a policy plus
the session-start reminder, not a gate.

**Everything every worker returns is verified.** In controlled tests each
shipped an error that was invisible in its own summary — a wrong finding
marked "Confident: Yes", and two false positives reported as "unsure
about: nothing". Their evidence is reliable; their self-assessment is not.
Full guide: `.kilo/skill/gemini-delegation/SKILL.md`.

## MCP Servers

| Server | Status | Purpose |
|---|---|---|
| `context7` | Active | Live library documentation lookup |
| `playwright` | Disabled | Browser E2E testing |

---

# Solo-Code CLI — Tiếng Việt

Bộ harness (dây cương) cho AI coding agent — rules, skills, hooks và verification gates dành cho kỹ thuật Solo-Code có kỷ luật.

Hỗ trợ engine: **Kilo Code** (`.kilo/`, nguồn gốc — mọi engine khác được sinh ra từ đây hoặc giữ song song), **Claude Code** (`.claude/` + `CLAUDE.md`, điều phối, sinh từ `.kilo/`), **OpenCode** (`.opencode/` + `opencode.json`, sinh từ `.kilo/`, tái bổ sung ở v4.2.0 làm engine chính và worker CLI), **GitHub Copilot** (`.copilot/`, giữ song song thủ công với `.kilo/`), **Gemini/Antigravity** (`.gemini/`).

> **v4.2.0:** tái bổ sung engine OpenCode làm engine chính (đã gỡ ở v4.0.0 vì là bản mirror 100% không có giá trị riêng, sau đó đưa lại khi định dạng native ổn định của OpenCode v1.18+ giúp sinh lại từ `.kilo/` gần như đồng nhất, chi phí thấp). Lịch sử đầy đủ trong `.kilo/memory/MEMORY.md` → "Decisions".

## Bắt đầu nhanh

```bash
# Mở Claude Code (điều phối) — xem claude-env.ps1
./claude-env.ps1

# Sinh lại engine Claude từ nguồn .kilo/
python tools/generate_harness.py --harness claude

# Chạy tất cả quality gates
make check

# Gates riêng lẻ
make test              # Harness tests
make security-scan     # Phát hiện secret trong code
make validate          # Schema validation (.kilo/ agents + skills)
make garden            # Drift detection (.kilo <-> .claude / .copilot)

# Integration tests (cấu trúc Copilot + shared state)
python tools/test_integration.py

# No-skips test policy
python .github/scripts/check_skips.py tools/
```

## Scaffold & Deploy

Dùng `tools/deploy.py` để nhân bản harness này vào dự án mới hoặc dự án có sẵn.
Deploy chỉ mang theo **phần runtime cần để chạy harness** (agents/skills/commands/
hooks/config) — không mang theo công cụ dev của Solo-Code-CLI (`deploy.py`,
`garden.py`, `generate_harness.py`, `test_*.py`), tài liệu nội bộ (`SPEC.md`,
`CONTRIBUTING.md`...), CI workflows, hay bộ nhớ tích luỹ của chính repo này —
dự án đích nhận memory template rỗng để tự ghi quy ước riêng.

### Scaffold — tạo dự án mới

```bash
# Tạo dự án mới kèm full harness
python tools/deploy.py scaffold /path/to/new-project

# Tuỳ chỉnh tên và mô tả
python tools/deploy.py scaffold /path/to/new-project --name my-app --description "My app"

# Chỉ engine Kilo
python tools/deploy.py scaffold /path/to/new-project --engine kilo

# Chỉ engine Copilot
python tools/deploy.py scaffold /path/to/new-project --engine copilot
```

Scaffold tạo thư mục, copy config engine (Kilo + Claude Code + Copilot + Gemini, chỉ phần runtime), sinh `.gitignore` và `README.md`, chạy `git init`, và in hướng dẫn post-setup.

### Deploy — copy harness vào dự án có sẵn

```bash
# Copy harness vào dự án có sẵn
python tools/deploy.py deploy /path/to/existing-project

# Dry run — xem trước thay đổi mà không copy thật
python tools/deploy.py deploy . --dry-run
```

### Auto-detect

```bash
# Tự động scaffold nếu target chưa tồn tại, deploy nếu đã tồn tại
python tools/deploy.py /path/to/target
```

### Interactive mode

```bash
# Không đối số → mở wizard thiết lập tương tác
python tools/deploy.py
```

---

## Cấu trúc thư mục

| Thư mục | Mục đích |
|---|---|
| `.claude/` | **Điều phối** — Claude Code: agents (15), skills (53), commands (15, gồm `ship`), instruction (10), guard + lifecycle hooks + `settings.json`; rulebook `CLAUDE.md` ở root. Sinh từ `.kilo/` qua `tools/generate_harness.py --harness claude`. |
| `.copilot/` | GitHub Copilot: agents (15), skills (53), commands (15), instruction (10), memory (4). Giữ song song thủ công với `.kilo/`; `tools/garden.py` chỉ kiểm tra, không tự sinh. |
| `.kilo/` | **Nguồn gốc** — Kilo Code: agents (15), skills (53), commands (15, gồm `ship`), hooks, memory, instruction. Sửa ở đây trước tiên. |
| `.gemini/` | Gemini/Antigravity: agents (15), skills (53), commands (12), knowledge. Manually kept in parity with `.kilo/`; checked by `tools/garden.py`. |
| `.github/` | Script dùng chung: `security_scan.py`, `checklist.py`, `check_skips.py`, `eval_harness.py`, `boundary_audit.py` + `copilot-instructions.md`, `prompts/` |
| `tools/` | Generator (`generate_harness.py`, `claude_engine.py`), validator, drift detector (`garden.py`), integration tests, `shared_state.py` (runtime dep của Claude session hooks) |
| `.vscode/` | VS Code settings + MCP config cho Copilot |

> **Kho chính thức:** <https://github.com/solo-code-com/Solo-Code-Harness> (MIT). Version engine
> `4.3.1` đóng dấu ở `.harness.lock`, `agent.yaml`, `pyproject.toml`; SPEC giữ version tài liệu
> riêng (`SPEC.md`, hiện `4.4.0`) — hai trục, không phải drift.

## Provider model & pipeline 4 vai trò

Bốn provider được sinh vào `.opencode/opencode.json` — `commandcode`, `freemodel`, `deepseek`,
`openrouter` — với **104 model id** (commandcode 94, freemodel 3, deepseek 2, openrouter 5) khai
trong `tools/opencode_engine.py`. Base URL lấy từ `.env`, và **chỉ** template của `freemodel` thêm
đường dẫn (`${OPENAI_BASE_URL}/v1`); các cái còn lại dùng nguyên văn, nên không được cắt `/v1` của
chúng. `tools/check_model_catalog.py` đối chiếu id với `GET /models` (kèm `--probe`), và probe
`POST /systemone` cho Jev decision model — model này không hề xuất hiện trong `/models`; id không
bao giờ lấy từ trí nhớ.

Việc được định tuyến qua 4 vai trò, khai ở `.kilo/agents/jev.md`:

| Vai trò | Engine | Ghi chú |
|---|---|---|
| **Jev** — chỉ định tuyến | agent trên `openrouter/jev-router` | `edit: deny`; wrapper chạy không `--auto` để deny có hiệu lực |
| **Codex** — ra đề bài + thẩm định | `gpt-6.1-sol`, effort `medium` | đổi bằng `--model` / `--effort`; chạy qua `codex-env.ps1` |
| **DeepSeek V4.1 Flash** — code | OpenCode CLI (`tools/opencode_delegate.py`) hoặc arm API trực tiếp (`tools/model_api.py`) | one-shot: 2,3 s việc nhỏ, 22 s một file; 503 khi prompt trên ~38K token |
| **Gemini 3.8 Flash** — việc nhanh, rộng | Antigravity CLI, tier `low`/`medium`/`high` (`tools/antigravity_delegate.py`) | review độc lập, khảo sát toàn repo |

Quyết định kiến trúc, sản phẩm, bảo mật không được giao: worker không có lịch sử hội thoại.

**Kết quả worker là lời khai, không phải kết quả.** Trong các test có kiểm soát, mỗi worker đều
tạo ra ít nhất một lỗi mà bản tự thuật của nó không thấy — một kết luận sai vẫn ghi "Confident:
Yes", hai dương tính giả báo là "unsure about: nothing". Bằng chứng của họ đáng tin; tự đánh giá
thì không — nên điều phối viên tự chạy lại lệnh và các gate thật trước khi tin.

## Fence (sandbox WSL)

Worker cần chạy vòng lặp có thể bị quây bằng `tools/wsl_sandbox.sh` (nối vào
`tools/model_api.py --sandbox wsl`): `/tmp` riêng dạng tmpfs, workspace bind ở `/tmp/work`, mỗi ổ
Windows phủ tmpfs riêng, `/home` + `/root` chỉ đọc. `/mnt/wsl*` cố ý để lộ — che nó là chết DNS.
Đường restricted-token bằng ACL Windows đã đo và loại: nó phá TLS (`SEC_E_NO_CREDENTIALS`), nên
worker gọi API không chạy trong đó được.

Đã đo: hai lệnh trong fence hết 10,9 s (pytest 9.1.1, ruff 0.17.1), ghi ra ngoài workspace bị từ
chối, TLS 200. Chưa làm: fence docker (daemon chưa chạy) và chạy dsh trong WSL để dùng sandbox
bwrap (vô tác dụng trên Windows).

## DeepSeek Harness Port (Tầng A)

Port các mẫu kiến trúc từ nguồn tham khảo
[`deepseek-harness-master/`](docs/dsh-port-map.md) (226 npm package, MIT) dưới
dạng capability seam — Service Definition / Provider / Consumer — mà không kéo
theo Cordis hay TypeScript runtime.

| Module | Vai trò | Port từ |
|---|---|---|
| `tools/agent_scope.py` | Scoped tool registry (global + shadow theo agent + dispose) | `core/scope` |
| `tools/subagent_seam.py` | Service Definition cho subagent (`SubagentRequest`/`Result`/`Runtime` Protocol), tách evidence khỏi self-assessment, fail-loud capability gating (`capabilities`/`supported_capabilities()`/`check_capabilities()`), `stop_reason` closed-union | `subagent` |
| `tools/compaction.py` | Budget policy theo byte/ký tự | `compaction` |
| `tools/compaction_pruner.py` | Cắt kết quả tool quá dài theo byte budget (tool độc lập, không phải hook) | `compaction-tool-result-pruner` |

Ghi chép quyết định: [`docs/dsh-port-plan.md`](docs/dsh-port-plan.md) (phạm vi),
[`docs/dsh-port-map.md`](docs/dsh-port-map.md) (bản đồ 49 package + bài học
postmortem). Tầng B/C (chạy dsh SDK/runtime làm executor thứ ba) bị chặn:
binary đóng gói của runtime chỉ hỗ trợ Linux/macOS (Windows là non-goal), và
`deepseek-v4-pro` đã gọi được trực tiếp qua OpenCode CLI + CommandCode — không
cần đổi harness.

## Verification Gates

| Gate | Lệnh | Kiểm tra |
|---|---|---|
| Lint | `ruff check .` | Python code style |
| Schema | `make validate` | Frontmatter agent + skill trong `.kilo/` |
| Drift | `make garden` | `.kilo` ↔ `.claude` (sinh tự động) / `.copilot` / `.gemini` (giữ song song thủ công) |
| Document truth | `make garden` | Số đếm, path trích dẫn, flag CLI, tuyên bố "chặn" và tên skill phải khớp thực tế — xem [SPEC.md §7.2.1](SPEC.md) |
| Harness Tests | `make test` | Generator + shared-state (`tools/test_*.py`) |
| Integration | `python tools/test_integration.py` | Cấu trúc Copilot + schema shared state |
| Security | `make security-scan` | Secret hardcode |
| Boundary | `python .github/scripts/boundary_audit.py .` | Không có file dự án lẫn vào thư mục harness |
| No-Skips | `python .github/scripts/check_skips.py tools/` | Skip/skipif không lý do |
| Stdlib-only | `make stdlib-only` | `tools/` và `.github/scripts/` không import gì ngoài standard library (SPEC mục 4) |
| Text hygiene | `make text-hygiene` | File nguồn kết thúc bằng newline, không có khoảng trắng cuối dòng, không có CRLF |
| Bypass claims | `make bypass-claims` | Tên `*_BYPASS` / `*_OVERRIDE` được ghi trong tài liệu phải được code đọc, không chỉ trong văn xuôi |
| Prose budget | `make prose` | Câu dài, dấu chấm phẩy và em dash không được tăng quá baseline đã ghi |
| Ruff pin | `make lint-pin` | Chạy đúng bản ruff mà commit hook pin, để make check và commit không nói khác nhau |
| Gate probe | `python tools/gate_probe.py` | BÁO CÁO, không chặn: gate nào bắt được defect nào, với kết quả caught, missed, no-gate |
| Review receipt | `python tools/review_receipt.py verify` | Thay đổi đã stage vượt 5 file và 120 dòng cần receipt ghi sau /review; cổng này chặn, còn hook advisory bên trên chỉ đo |

## Slash Commands (Kilo + Claude Code)

| Lệnh | Chức năng |
|---|---|
| `/verify` | Chạy tất cả verification gates |
| `/plan` | Giao việc cho planner agent |
| `/decide` | Giao việc cho architect agent |
| `/ship` | Pre-launch checklist |
| `/commit` | Tạo conventional commit |
| `/debug` | Quy trình debug có hệ thống |

## Claude Code Setup

```bash
# Sinh lại engine Claude từ nguồn .kilo/
python tools/generate_harness.py --harness claude
```

Guard hook là bản port stdlib-only Python từ `gate-guard.js`/`secret-scan.js` của
Kilo (33 mẫu lệnh nguy hiểm + 15 mẫu secret + danh sách file config được bảo vệ).
Hook mới `memory_gate.py` (`PostToolUse`, bản port của `memory-manager.js`)
chặn cứng (exit 2) khi `.claude/memory/*.md` vượt 8.000 ký tự, tránh memory
phình to âm thầm và tốn context mỗi phiên. Các PostToolUse/Session hook còn
lại chỉ mang tính khuyến nghị (luôn exit 0) — đưa Claude Code lên ngang hàng
enforcement với Kilo. `PreCompact` hook (`pre_compact.py`) ghi checkpoint
git-state vào shared-state và nhắc lưu quyết định đã chốt vào
`.kilo/memory/MEMORY.md` trước khi context bị nén/tóm tắt. `garden.py` nay
cũng so khớp nội dung (không chỉ tên file) giữa `.claude/memory/` và
`.kilo/memory/` (nguồn gốc sự thật) để bắt drift âm thầm.

```bash
# Test bộ guard + lifecycle hook
python -m pytest tools/test_claude_guard.py tools/test_claude_hooks.py -q
```

### Chạy Claude Code qua FreeModel

```powershell
Copy-Item .env.template .env
#    rồi sửa .env → ANTHROPIC_API_KEY=<key-của-bạn>
./claude-env.ps1
```

## Worker Gemini/Antigravity (CLI headless + GUI dự phòng)

Antigravity chạy headless qua `tools/antigravity_delegate.py`, wrapper của
`agy.exe --print --output-format stream-json`. Mặc định read-only; muốn ghi phải
có cả `--auto-approve` và `--allow-dir`, wrapper lấy directory lock và audit
scope sau khi chạy. Run read-only thường không trả lời được prompt quyền
`command`, nên truyền `--allow-tools` khi cần chạy grep/test; khi đó wrapper fail
rõ ràng (exit 5) nếu tool bị auto-deny thay vì báo "thành công" giả.

Chọn model theo độ phức tạp — `gemini-3.8-flash-low|medium|high`. Kiểm tra binary
và model bằng `python tools/antigravity_probe.py`.

Giao thức GUI inbox/outbox trong `.gemini/antigravity/handoff/` vẫn là dự phòng
khi cần người xác minh UI đã render: Claude ghi plan vào
`handoff/inbox/<slug>-plan.md`, bạn chuyển 1 dòng cho Antigravity, và
`.claude/hooks/session_start.py` tự phát hiện report ở phiên Claude kế tiếp.
Chi tiết: `.gemini/antigravity/handoff/README.md`.

### Giao việc cho ai

Claude Code tự đề xuất worker khi gặp việc phù hợp — bạn không cần nhớ là
chúng tồn tại. `session_start.py` thông báo engine nào đang sẵn sàng ở đầu
mỗi phiên.

| Dạng công việc | Giao cho | Lý do |
|---|---|---|
| Đọc >5 file rồi tóm tắt/so sánh/rà soát | **Antigravity CLI** | Ngữ cảnh lớn qua worker headless |
| Khảo sát toàn repo — "chỗ nào khác dùng X?" | **Antigravity CLI** | Bề rộng là thế mạnh của nó |
| Review độc lập một thiết kế hoặc diff | **Antigravity CLI** | Model khác bắt được lỗi khác |
| Kiểm chứng UI, chụp màn hình, quay video | **Antigravity GUI handoff** | CLI không kiểm được UI đã render |
| Sửa cơ học nhỏ, boilerplate, một test | **OpenCode CLI** | Headless — không tốn công bạn |
| Viết code có scope rõ ràng | **Antigravity CLI** (rộng) / **OpenCode CLI** (hẹp) | Cả hai đều cần fence bằng văn bản |
| Quyết định kiến trúc / sản phẩm / bảo mật | **Không giao** | Phán đoán không ủy quyền được |
| Việc cần lịch sử hội thoại của phiên | **Không giao** | Worker đều mù ngữ cảnh |

OpenCode CLI, Kilo CLI và Antigravity CLI đều chạy headless nên Claude ủy quyền
trực tiếp. Antigravity CLI lo việc đọc nhiều/bề rộng; OpenCode CLI là executor
hẹp (chiều sâu suy luận + cache tracking); Kilo CLI là dự phòng. GUI handoff chỉ
còn cho việc UI đã render. Routing này **không ép cứng được** — không hook nào
chặn việc đọc nhiều file trực tiếp — nên nó là chính sách + nhắc nhở ở session
start, không phải gate.

**Mọi kết quả từ mọi worker đều được kiểm chứng.** Trong các bài test có
kiểm soát, mỗi bên đều trả về ít nhất một lỗi mà chính bản tóm tắt của nó
không hề lộ ra — một phát hiện sai bị đánh dấu "Confident: Yes", và hai
false positive kèm câu "không có gì không chắc". Bằng chứng nó đưa ra thì
đáng tin; phần nó tự đánh giá thì không.

## MCP Servers

| Server | Trạng thái | Chức năng |
|---|---|---|
| `context7` | Đang chạy | Tra cứu tài liệu thư viện trực tiếp |
| `playwright` | Tắt | Browser E2E testing |

## License / Giấy phép

MIT — see [`LICENSE`](LICENSE). / MIT — xem file [`LICENSE`](LICENSE).
