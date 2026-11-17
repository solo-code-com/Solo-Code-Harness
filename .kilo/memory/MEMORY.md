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
- [gotcha] `antigravity_delegate.py --allow-tools` (read-only) chay song song voi worker dang ghi cay se bao
  "read-only violation" cho chinh cac file nguoi khac sua - snapshot khong quy duoc chu. Chi chay review agy
  khi cay da dung yen.

## Decisions
- [decision] Router co ledger (2026-11-12): `tools/router.py` phan loai task bang bang rule (6 loai,
  khong goi LLM), tra ve `{task_type, brain, worker, model, reason, budget, evidence}` va ghi
  `.solocode/route-decisions.jsonl` khi `--dry-run`. Brain ghi `rules` cho moi route cho den khi
  `--probe` duoc noi; brain fallback khai trong `routing_brains` (`openrouter/jev-router` ->
  `opencode-go/claude-haiku-5-5`), khong bao gio la executor. `opencode_delegate.py` doc
  `default_agent` tu `ROOT/opencode.json` (khong phai cwd) truoc khi cap `--auto`, nen thieu `--agent`
  thi worker khong con quyen ghi. `gemini-decision-1` la decision API (`outputTokenLimit: 1`,
  `POST /v1beta/decisions`), khong phai chat model.

- [measurement] WSL fence + worker Linux (2026-10-24/25, B4 pha 1):
  `tools/wsl_sandbox.sh` **fail-open** nếu gọi ngoài `unshare -Urm` - đã sửa: exit 69,
  có `WSL_SANDBOX_ALLOW_DEGRADED=1`. Gọi đúng: chặn `/mnt/c` và `$HOME` (cả `mkdir`),
  `/tmp` riêng, DNS+HTTPS sống; fence 0.14s ấm (cold ~14s). Đã chạy được worker thật
  trong fence: `@opencode/cli` cài vào `~/.local` (không cần sudo), `run --standalone`
  với `HOME`/XDG trỏ vào workspace + key qua `WSLENV` - echo 8.5s ấm (21s cold), ghi
  1 file 56.5s, state 28 file nằm trong workspace. Còn chặn: binary Windows
  (`opencode.exe`, `kilo.exe`, `cmd.exe`) không chạy được trong fence.
- [decision] Giu `.kilo/` lam goc sinh (2026-11-16): van sinh `.claude`, `.opencode`, `.copilot`,
  `.gemini`. Do: 1.684 lan ".kilo" tren 250 file (~778 ".kilo/" tren 179); garden neo
  `engines[".kilo"]`; deploy co `DIRS_KILO`. Phan bien doc lap (agy): KEEP, don rename vao
  breaking release.
- [decision] Noi gate hook-reference, do hau qua (2026-11-16): `check_hook_references` tra `[]`
  khi `hooks.json` vang. Do 4 ca: vang 1->0; thieu script / trung id / JSON hong giu 1->1.
  Mat bat xoa nham; file duoc git track nen xoa hien trong diff.
- [decision] `select_provider` hoi router (2026-11-16): ca 5 worker cung capability
  `{read,bash}` nen `min(capable, key=rank)` luon tra opencode. Khong co `--worker` thi goi
  `router.route()` + `provider_id_for_router_worker`; survey->gemini, simple_edit->opencode;
  loi route fall-through.
- [decision] Tu choi contract `worker-response/1` (2026-11-16): Codex verify in-memory.
  `"event" in obj` sai voi opencode/kilo (dung key `type`) va bo sot terminal
  `{"event":"result"}` cua antigravity -> run thanh cong bi bao loi. Khong co field cho
  partial output; trung `SubagentResult` (`subagent_seam.py:59`).
- [decision] Evidence loi giu ca hai kenh (2026-11-16): `subagent_cli.py` gop `err` + `out`
  (cat 2000) thay vi `err or out`; antigravity/codex in fragment timeout ra stdout nen truoc
  do bi nuot.
- [decision] Checklist Observability het false PASS (2026-11-16): 5 dong tro artifact chi
  `.kilo/hooks/*` sinh ra. `.kilo/state/` la ban copy tu `D:\Project\Solo-Code-Harness`
  (9 file, noi dung 6/2026, mtime 10/2026) -> da xoa; `.gitkeep` duoc track de
  `eval_harness` `is_dir()` khong do (thieu dir -> context 11/12).
- [decision] Ignore rule `.solocode` (2026-11-16): `.solocode/` loai ca thu muc nen moi
  negation chet -> `.solocode/*`. `executor-config.json` track lai (deploy doc lam template
  sau `if exists()`); `executor-mode` giu ignore vi noi dung `off` va deploy copy vao moi
  target. Guard: `test_repo_ignore_rules.py`, mutation-checked.
