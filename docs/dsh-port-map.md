# dsh Port Map — Ánh xạ 49 package của DeepSeek Harness sang Solo-Code-CLI

> Bước A1 của `docs/dsh-port-plan.md`.
> Ngày: 2026-08-15
> Nguồn: `deepseek-harness-master/packages/` (49 nhóm, 226 npm package — đã kiểm kê trực tiếp).

---

## Cách đọc

Bốn trạng thái:

| Trạng thái | Nghĩa |
|---|---|
| **Đã port** | Có bản tương đương trong Solo-Code (đầy đủ hoặc rút gọn) |
| **Đáng port tiếp** | Capability seam mà Solo-Code còn thiếu, có lộ trình (A2/A3) |
| **Đọc để học** | Không port code — chỉ lấy rationale (loop hygiene, state, acceptance) |
| **Bỏ qua** | Đặc thù kiến trúc Cordis/TypeScript của dsh, không có bản đối chiếu Python |

> Ghi chú trung thực: các dòng "Bỏ qua" phân loại theo tên nhóm + mô tả trong `packages/AGENTS.md` gốc, chưa đọc README từng package. Lý do "đặc thù dsh" là hợp lệ vì bản thân kiến trúc Cordis/effect system đã nằm ngoài phạm vi port (§8 kế hoạch).

---

## 1. Bảng ánh xạ 49 package

### 1.1 Đã port (4)

| Group | Bản port trong Solo-Code | Độ trung thực |
|---|---|---|
| `core` | `tools/agent_scope.py` (scope), `tools/session_registry.py` (session), guard hooks | **Rút gọn** — scope thiếu parent-chain, event routing, runtime invariant |
| `session` | `tools/session_registry.py` | Rút gọn — SQLite cơ bản, không projection/title/telemetry |
| `guard` | `.kilo/hooks/pre-tool-use/*`, `edit: deny` trong agent OpenCode | Đầy đủ cho nhu cầu hiện tại |
| `hooks` | `.kilo/hooks/`, `.opencode/plugins/harness/` | Đầy đủ (Kilo hooks + OpenCode plugin) |

### 1.2 Đáng port tiếp (5)

| Group | Lộ trình | Lý do |
|---|---|---|
| `subagent` | A2 — runtime subagent (interface, chưa refactor) | Gap lớn nhất: Solo-Code delegate qua CLI ngoài, chưa có registry in-process |
| `compaction` | A3 — `tools/compaction.py` | Solo-Code chỉ có checkpoint hook thụ động, chưa có prune |
| `skill` | Chưa xếp lịch | Solo-Code có `.kilo/skill/` nhưng cơ chế khác (file tĩnh vs registry+provider) |
| `shell` | Chưa xếp lịch | Solo-Code gọi `subprocess.run` trực tiếp, chưa có capability seam cho bash |
| `subprocess` | Chưa xếp lịch | Tương tự `shell` — subprocess capability + process-tree provider |

### 1.3 Đọc để học (8)

Rationale giá trị cao, không port code:

| Group | Bài học đáng lấy |
|---|---|
| `context` | Request-context: phân tách context per-request, tránh leak giữa agent |
| `plan` | Plan mode là logged state, không phải biến trạng thái ngầm |
| `todo` | todo_write tool: snapshot toàn danh sách thay vì patch từng item |
| `workflow` | Workflow capability + child-agent start/end pairing |
| `interaction` | Approval/permission: ask-user flow, command runtime |
| `goal` | Durable goal snapshot giữ source attribution + revision |
| `feedback` | Feedback loop — liên quan trực tiếp postmortem 0003 |
| `spill` | Spill-file truncation — xử lý output quá dài (liên quan A3 pruner) |

### 1.4 Bỏ qua (32)

Đặc thù kiến trúc Cordis/TS hoặc không sinh lời cho Solo-Code Python:

| Group | Lý do bỏ qua |
|---|---|
| `acp` | ACP protocol server — không dùng ACP trong Solo-Code |
| `api` | Remote BFF + Typert RPC gateway — đặc thù dsh web |
| `attachment` | Attachment capability — không áp dụng |
| `boot` | App-bin glue — đặc thù dsh CLI |
| `bundle` | dsh profile patch-layer bundles — đặc thù dsh |
| `client` | Client-side — đặc thù dsh |
| `code-runtime` | RunCode bridge — đặc thù dsh |
| `credentials` | Credential-reference provider — Solo-Code dùng env trực tiếp |
| `e2b` | E2B sandbox POC — đặc thù dsh, đòi hỏi hạ tầng E2B |
| `examples` | Demo bundles — không port |
| `extensions` | Extension points — đặc thù dsh |
| `fs` | Filesystem capability + policy — Solo-Code dùng pathlib trực tiếp, policy khác |
| `host` | Host — đặc thù dsh |
| `identity` | Anonymous identity — không áp dụng |
| `jobs` | Job runtime (background tasks) — đặc thù dsh |
| `llm` | LLM capability + DeepSeek providers — Solo-Code dùng Kilo/OpenCode CLI, không tự gọi LLM |
| `lsp` | Language-server capability — không áp dụng |
| `mcp` | MCP server — Solo-Code có `.mcp.json` riêng |
| `preset` | Per-session agent composition — đặc thù dsh |
| `runtime-diagnostics` | Runtime invariants companion — đặc thù dsh |
| `sandbox` | Sandbox (landlock/bwrap/seatbelt) — đặc thù OS, không áp dụng Windows |
| `schedule` | Schedule — không áp dụng |
| `sdk` | JSON-RPC protocol + TS client — đặc thù dsh |
| `session-query` | Session query — đặc thù dsh |
| `settings` | User-settings capability — Solo-Code dùng `.solocode/` config riêng |
| `storage` | Storage — đặc thù dsh |
| `terminal` | Persistent terminal sessions — đặc thù dsh |
| `test-support` | Dev/test infra — đặc thù dsh |
| `typert` | Type graph generator — đặc thù TS |
| `util` | Zero-dependency utilities (TS) — đặc thù TS |
| `web` | Web capability (search/fetch) — đặc thù dsh web |
| `workspace` | Workspace — đặc thù dsh |

---

## 2. Bài học từ postmortem (4/4 đã đọc)

| # | Bài học | Link nguồn |
|---|---|---|
| 0001 | **Coverage 100% không bằng feature hoạt động.** Test phải chạy qua real load path + real call topology. Hai bug độc lập cùng núp sau một error string; chỉ real-Loader e2e không cần key mới bắt được. | `deepseek-harness-master/docs/postmortem/0001-acp-default-export-drops-inject.md` |
| 0002 | **Snapshot refresh là sản xuất fixture, không phải review đúng/sai.** Missing tool phải có assertion semantic độc lập với expected output; nếu không, refresh sẽ "hợp pháp hóa" regression. | `deepseek-harness-master/docs/postmortem/0002-js-expression-disabled-filesystem-tools.md` |
| 0003 | **HTTP 200 / build success / boot manifest là các fact khác nhau.** Acceptance phải chỉ đích danh origin và quan sát thay đổi TẠI đó — replacement service không chứng minh trang cũ đã đổi. | `deepseek-harness-master/docs/postmortem/0003-web-agent-gui-feedback-loop.md` |
| 0004 | **Attribution process cần conjunction of independent evidence.** Shared prefix không phải protocol; adapter phải giữ structured failure của seam bên dưới thay vì thay bằng generic category gần nhất. | `deepseek-harness-master/docs/postmortem/0004-landlock-partial-notice-misclassified-child-failures.md` |

→ Ảnh hưởng trực tiếp đến Solo-Code: bài học 0001 khớp đúng lỗi self-test ghi DB production vừa fix (test không chạy qua real path); 0002 khớp lỗi coverage-budget rỗng (ratchet không thực sự hoạt động); 0004 khớp nguyên tắc "adapter phải giữ structured failure" khi port subagent (tách `evidence` khỏi `summary`).

## 3. Bài học từ architecture notes (4/4 đã đọc)

| Note | Bài học | Link nguồn |
|---|---|---|
| capability-seams (2026-06-13) | **Seam = 3 vai trò (Definition/Provider/Consumer) là một thể hoàn chỉnh.** Đừng split preemptively — 1 provider + 1 consumer thì giữ 1 package cho đến khi provider thứ 2 xuất hiện. | `deepseek-harness-master/.agents/notes/implemented/architecture/2026-06-13-capability-seams.md` |
| agent-scope-contexts (2026-07-08) | **Registration origin quyết định cả visibility lẫn cleanup.** Scope là flat, không inherit; security là non-goal. | `deepseek-harness-master/.agents/notes/implemented/architecture/2026-07-08-agent-scope-contexts.md` |
| scoped-layers-store (2026-07-12) | **Undo phải được collect TRƯỚC khi notify callback chạy** (callback throw → rollback); disposer phải là exact function trả về từ effect. | `deepseek-harness-master/.agents/notes/implemented/architecture/2026-07-12-scoped-layers-store.md` |
| package-invariant-runtime-contracts (2026-07-19) | **Runtime invariant phải là quan hệ giữa các observation theo thời gian/cấu trúc**, không phải assert method-presence. Package không có quan hệ liên tục thì ghi `No runtime invariant:` rõ ràng. | `deepseek-harness-master/.agents/notes/implemented/architecture/2026-07-19-package-invariant-runtime-contracts.md` |

→ Ảnh hưởng trực tiếp: note capability-seams **xác nhận** quyết định của Claude Code rằng A2 chỉ làm interface, hoãn refactor `opencode_delegate.py` (chỉ 1 provider). Note scoped-layers-store cho biết `agent_scope.py` hiện tại thiếu đúng 2 điều: undo-before-notify và exact-disposer identity.

---

## 4. Kết luận A1

- 49/49 package có trạng thái (4 đã port, 5 đáng port, 8 đọc để học, 32 bỏ qua).
- 8 bài học có link nguồn cụ thể (4 postmortem + 4 architecture notes).
- Lộ trình tiếp: **A2** (runtime subagent — interface, tách `evidence`/`summary`) → **A3** (`tools/compaction.py`).
