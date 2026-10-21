# Memory Index

High-signal context loaded at session start. Detailed history belongs in
`decisions-archive.md`; keep this file below the 8,000-character gate.

## Project
- [project] Branches: `feature/[task-slug]` or `fix/[bug-slug]`.
- [project] `AGENTS.md` is the root rulebook; `.harness.lock` defines boundaries.
- [project] `.kilo/` is source of truth. Claude and OpenCode are generated from
  it; Copilot/Gemini are parity-checked.

## Rules
- [rules] Read before editing; make surgical changes; verify syntax and tests.
- [rules] User input is untrusted. Parameterize SQL; keep credentials in env vars.
- [rules] Ask before destructive actions, dependency installs, CI changes, or
  deletion.
- [rules] Use `permission-guard`; executor mode is ON by default. Run the
  security scan before committing.

## Tech Stack
- [tech] Python 3.10+ stdlib runtime for `tools/` and `.github/scripts/`;
  pytest/ruff are dev tools. Kilo hooks use Node.js 18+.
- [tech] Ruff config: `.ruff.toml`; secrets: `.gitleaks.toml`.
- [tech] Codex CLI 0.160.0 (npm global) is a harness consumer: it reads
  `AGENTS.md` natively, so no `.codex/` engine mirror is generated. Launcher
  `codex-env.ps1`, metering `tools/codex_usage.py`.
- [tech] SQLite shared state: `.solocode/shared-state.db`. Only `session_log`
  actually has rows; `features` and `shared_memory_*` exist but are unused —
  git log plus this file cover task tracking and conventions. `codex` is a
  valid engine name in `tools/shared_state.py`.

## Verification
- [verify] Full Codex gate: `python tools/codex_verify.py`.
- [verify] Individual gates: security scan, schema validation, garden,
  no-skips, pytest.
- [verify] Codex has no repository hook API, so use `tools/codex_guard.py` for
  destructive-command, secret, and file-lock preflight checks, or run
  verified writes with `tools/codex_guard.py --write`.

## Gotchas
- [gotcha] Bare Codex does not load `.env`; always run via the launcher.
- [gotcha] Codex gateway needs `code_mode.enabled = false`,
  `unified_exec = false`, `wire_api = "responses"`, and a full-access sandbox.
- [gotcha] Codex base URLs do not interpolate `${VAR}`; the launcher passes a
  `-c model_providers.<id>.base_url=...` override instead.
- [gotcha] Codex CLI internal exec policy blocks `rm -f` / `-Force` patterns;
  under `approval_policy = "never"` this aborts process creation. Avoid `-Force`
  in PowerShell, or use `python tools/codex_guard.py --write`.
- [gotcha] TOML bare keys must precede the first table header.
- [gotcha] Keep loaded memory under 8,000 chars. MOVE pruned material into
  `decisions-archive.md` verbatim — never silently delete it.
- [gotcha] `.pytest_temp` cleanup can race on Windows; rerun pytest if needed.
- [gotcha] `check_lint_budget` honours `.gitignore` only when the tree is a git
  repo. Without `.git/`, ruff still scans `.pytest_temp*/` and the count overshoots
  the budget (measured 49 vs 45 on the pristine tree). Run `git init` before
  trusting the gate.
- [gotcha] `jev.md` khai `edit: deny` nhung KHONG bind suot dau phien
  (`opencode.json:12` noi `edit: ask`) - hai tang lech nhau. Phien chay agent
  Jev phai sua file qua `tools/*_delegate.py`, khong qua edit truc tiep.
- [gotcha] Kilo can `python tools/kilo_server_manager.py ensure` truoc moi
  phien dung arm Kilo; thieu buoc do = 100% fail voi `kilo CLI exit code 1`
  (do duoc: 325 loi, 14 ngay, server chet tu 16/10).
- [gotcha] Script trong `tools/` in output ra console Windows cp1252 se crash
  neu output co ky tu ngoai bang ma - dung `_make_streams_encoding_safe`
  (`codex_delegate.py` tung thieu, crash o U+2192).
- [gotcha] Khong chay 2 pytest song song: dung chung `.pytest_temp` - test do
  gia (da gap 2 lan trong phien 2026-10-20).
- [gotcha] Kilo arm cần `python tools/kilo_server_manager.py ensure` trước mỗi
  phiên dùng nó. Thiếu bước đó = 100% fail với `kilo CLI exit code 1` (đo: 325
  lỗi trong 14 ngày, server chết từ 16/10). Wrapper nay preflight + exit 7.
- [gotcha] Script in output ra console Windows cp1252 sẽ crash nếu output chứa
  ký tự ngoài bảng mã (U+2192). Dùng `_make_streams_encoding_safe`; `python -c`
  trong PowerShell cũng dính lỗi này.
- [gotcha] Ba bẫy tooling: (a) không chạy 2 pytest song song — chung
  `.pytest_temp`, test đỏ giả; (b) pre-commit pin ruff 0.9.1 (`make lint-pin`),
  ruff trên PATH mới hơn bỏ sót UP038; (c) hook ruff exclude `.kilo/` và cả 3
  mirror skill — sửa script trong skill thì format cả 4 bản.
- [gotcha] Cap `memory_gate` đo bằng KÝ TỰ (`len`), không phải byte. `$s.Length`
  của PowerShell trên file UTF-8 không BOM đếm sai (8182 vs 7671) — đo bằng python.

## Decisions
- [decision] CommandCode model ids are verified against
  `GET {COMMANDCODE_BASE_URL}/models`, never from memory. Each consumer declares models
  separately: `_PROVIDER_MODELS` in `tools/opencode_engine.py` (OpenCode uses the harness
  key `commandcode/deepseek-v4-pro`), `.vscode/settings.json` (Copilot), and
  `~/.config/kilo/kilo.jsonc` (Kilo uses the full upstream id
  `commandcode/deepseek/deepseek-v4-pro`). `context_length` comes from the response, not
  the file. (2026-09-28; catalog 80 model on 2026-10-16)
- [decision] Pipeline 4 vai trò (2026-10-16): Jev (agent trên **model JEV**
  `openrouter/jev-router`) điều phối; **Codex** ra đề bài + **verify** (`gpt-6.1-sol`,
  effort `medium`, đổi bằng `--model`/`--effort`); **DeepSeek V4.1 Flash** (OpenCode CLI,
  + `dsh`) code sâu; **Gemini 3.8 Flash** (Antigravity CLI) việc nhanh nhẹ. Fallback:
  Codex không ổn định → DeepSeek viết brief + thẩm định lại Gemini. Jev `edit: deny` và
  wrapper chạy agent này **không `--auto`** để deny có hiệu lực. Gotcha: timeout mặc định
  của wrapper opencode/agy là **120s** → job dài bị giết oan (đã nâng 900/600); `.ps1` tải
  về bị mark-of-the-web → phải `Unblock-File`. Catalog **80 model / 4 provider**.
- [decision] Parity launcher ↔ delegate về biến môi trường (2026-10-16): chỉ cắt `/v1` ở biến mà
  config sinh ra **thêm** `/v1` — hiện chỉ `OPENAI_BASE_URL`; `COMMANDCODE_BASE_URL` và
  `DEEPSEEK_BASE_URL` dùng **nguyên văn**. Cắt `/v1` của commandcode từng làm mọi call delegate
  hỏng (A/B cùng call: `Unknown error` → `OK` 8.2s). `tools/test_env_file.py` suy luật **từ chính**
  `.opencode/opencode.json`, nên thêm provider có template `/v1` là test đỏ ngay. Ghi chú: launcher
  xuất **mọi** key `.env` bằng vòng lặp chung nên nó và `env_file.py` đã ngang nhau — audit chỉ grep
  `$env:NAME =` sẽ báo thiếu biến oan.
- [decision] Sua cac loi im lang (2026-10-20): 3 agent thieu `description` nen
  SPEC 7.1 nay duoc enforce; `boundary_audit` bat `--strict` va sua bug bo qua
  theo path tuyet doi (repo duoi thu muc ten build/dist/venv lam ca audit cam);
  gate `test_hook_exit_claims.py` moi bat 2 claim sai that (`quality-gate.js`,
  `hookify-engine.js`); xoa 3 claim bypass khong ton tai. Kilo: 327/350 loi chi
  vi server chet -> them preflight exit 7 va co `--ensure-server`; bo `requests`
  (dependency pip duy nhat trong `tools/`). Antigravity nay ghi
  `.solocode/antigravity-usage.jsonl`; `failure_ledger.py` moi gop moi nguon van
  hanh va co `--sweep` da du an.
- Harness content fingerprint (2026-10-21): .harness.lock records `content_fingerprint`,
  a sha256 over the sorted (path, content) pairs of [shared_files], BOM stripped and
  CRLF->LF. A `[harness] mode` is recorded too. `root` is enforced: a harness edit
  without `python tools/deploy.py --root-lock` fails garden. `target` is advisory, so
  garden prints a WARN and does not fail, because customising harness files there is
  legitimate. A lock with no `mode` is treated as root. A target lock declares only the
  files deploy actually shipped. `failure_ledger --sweep` reports per-project fingerprint
  match.
