# Kế hoạch (đã duyệt) — Khai thác `deepseek-harness-master` cho Solo-Code-CLI

> Tác giả: Kilo
> Đã đánh giá: Claude Code (orchestrator)
> Ngày: 2026-08-15
> Trạng thái: **Tầng A đã xong. Tầng B/C tưởng bị chặn (2026-08-15) — ĐÃ HẾT CHẶN (2026-10-06), xem §0.1**

---

## 0. Kết luận phạm vi

Tầng B/C (adopt dsh SDK / runtime) **không thực thi** — bị chặn bởi hai sự thật đã xác minh, không phải ý kiến:

| Rào cản | Trạng thái thực tế | Hệ quả |
|---|---|---|
| Q3 — `DEEPSEEK_API_KEY` riêng | **NOT SET** | B/C chết không cần bàn thêm |
| Q4 — Node | `v22.12.0`, dsh cần `^22.19 \|\| >=24` | **Thiếu** — build runtime sẽ fail |
| Q4 — pnpm | `10.33.0` | Thỏa (điểm duy nhất đạt) |

**Chỉ làm tầng A**: A1 (bản đồ ánh xạ + đọc notes/postmortem gộp chung) → A2 (subagent interface, chưa refactor) → A3 (compaction pruner, sau khi chốt injection point).

---

## 0.1 Trạng thái 2026-10-06 (đo lại — §0 KHÔNG còn đúng)

| Rào cản cũ | §0 ghi (2026-08-15) | Đo lại (2026-10-06) | Còn chặn? |
|---|---|---|---|
| Q3 — `DEEPSEEK_API_KEY` | NOT SET | có trong `g-harness/.env` | ❌ Hết |
| Q4 — Node | `v22.12.0` | **`v22.23.2`** (thỏa `^22.19 \|\| >=24`) | ❌ Hết |
| Q4 — pnpm | `10.33.0` | `11.7.0` | ❌ Hết |

**Hệ quả:** tầng B (chạy `dsh` như worker thứ ba qua subprocess) nay **khả thi**.
Điều kiện §A2 cho the runtime subagent ("chỉ refactor khi có ≥2 provider") cũng
đã đủ. Đề xuất: thêm `tools/dsh_delegate.py` (đối xứng `antigravity_delegate.py`).

**Vẫn giữ nguyên:** KHÔNG merge code DSH/Cordis vào harness — chỉ gọi subprocess
(SPEC §0.1.1: "config trỏ xuống native, không clone native"). DSH giữ ở
`reference/deepseek-harness-master/`.

---

## 1. Trả nợ kỹ thuật — TÌNH TRẠNG ĐÃ CẬP NHẬT

Claude Code nói "A0 đã xong". Xác minh trực tiếp cho thấy **đúng về logic, nhưng fix chưa commit**. Bảng dưới là trạng thái thật tại thời điểm 2026-08-15:

| Bug | Nội dung | Trạng thái |
|---|---|---|
| #1 | `session_persistence.py` default-arg `DB_PATH` | ✅ Đã fix (`path: Path \| None = None`), **chưa commit** |
| #2 | `session_persistence.py` dead code dòng 356–358 | ✅ Đã fix (dedent), **chưa commit** |
| #3 | `session_analytics.py` self-test đọc DB thật | ✅ **Vừa fix** (Kilo, session này) — dùng `path=` + temp DB, assert dữ liệu cụ thể. **Chưa commit** |
| #4 | `coverage-budget.json` rỗng | ✅ Đã chạy `--update` (45 file), **chưa commit** |
| — | DB rác `sess-test-*` | ✅ Đã sạch (chỉ còn 3 dòng thật) |

**Hành động bắt buộc trước A1:** commit toàn bộ working tree hiện tại (`session_persistence.py`, `session_analytics.py`, `coverage_gate.py`, `coverage-budget.json`, `upgrade-summary.md`, `kilo_cli_delegate.py` + file mới `agent_scope.py`, `evaluation-report.md`, `dsh-port-plan.md`) để A1 chạy trên nền git sạch. Không được để fix "đã xong" nằm ngoài git.

---

## 2. Tầng A — Lộ trình đã rút gọn

### A1 — Bản đồ ánh xạ + ghi nhận bài học (0.5 ngày)

**Gộp A4 vào đây.** Đọc notes/postmortem song song khi viết bản đồ, ghi bài học trực tiếp vào `dsh-port-map.md` — không làm bước đọc riêng.

Deliverable: `docs/dsh-port-map.md`

- Bảng **49 package** → 4 trạng thái: Đã port / Đáng port / Bỏ qua / Đọc để học.
- Chỉ điền phần đã có khung: §1.2 kế hoạch cũ đã xác định 3 nhóm trọng điểm (subagent, compaction, skill), §8 đã loại ~15–20 package (Cordis/typert/ACP/e2b/website/native). Không khảo sát lại từ đầu.
- **4 postmortem** (`0001`–`0004`) + **4 architecture notes** (agent-scope, capability-seams, scoped-layers-store, package-invariant-runtime-contracts) → ghi 1 dòng bài học + link nguồn ngay vào bản đồ.
- Mỗi hàng: 1 câu lý do, không bỏ trống trạng thái.

**Nghiệm thu:** `dsh-port-map.md` tồn tại; 49 package đều có trạng thái; ≥8 bài học có link nguồn cụ thể.

### A2 — Subagent interface (1 ngày, CHƯA refactor)

Claude Code đúng: `opencode_delegate.py` không có bug đã biết, và `SubagentRuntime` Protocol chỉ có giá trị khi có ≥2 provider. Hiện chỉ có 1 provider (CLI) → **chỉ viết interface, không thêm abstraction layer vô dụng**.

Deliverable: the runtime subagent

- Service Definition: `SubagentRequest`, `SubagentResult`, `SubagentRuntime` (Protocol).
- Trường bắt buộc từ bài học dsh: tách `evidence` khỏi `summary` (worker evidence đáng tin, self-assessment thì không).
- `--self-test` chạy được.
- **Không** refactor `opencode_delegate.py` — chỉ làm khi chốt provider thứ hai (dsh SDK từ tầng B, hiện bị chặn).

**Nghiệm thu:** `python -m pytest tools/test_opencode_delegate.py -q` pass; `opencode_delegate.py` không đổi; `pytest tools/ -q` xanh.

### A3 — Compaction pruner (2–3 ngày, SAU khi chốt injection point)

Claude Code nêu đúng lỗ hổng: injection point của pruner chưa rõ.

**Quyết định đã chốt (Kilo đề xuất):** pruner là **standalone tool** đặt ở `tools/`, **không phải hook**.

Lý do (nguyên tắc harness boundary):
- Nếu là PostToolUse hook → phải đặt ở `.kilo/hooks/` (harness config) → **vi phạm nguyên tắc không sửa file harness để làm việc project**.
- Pruner dùng chung cho nhiều engine (Kilo/Claude/OpenCode), không phải đặc thù một engine → tool trong `tools/` đúng chỗ.
- Wire vào luồng thật (hook) chỉ khi nào có yêu cầu rõ ràng từ orchestrator, qua đúng file harness, không làm trong phạm vi này.

Deliverable: `tools/compaction.py` (budget policy) theo ngưỡng).

**Không port** phần LLM-summarize của `compaction-basic` (đòi hỏi gọi model trong-process).

**Nghiệm thu:** `--self-test` pass; pruner giữ byte/token limit đúng — test cả input rỗng, vượt ngưỡng 1 byte, multibyte.

---

## 3. Nghiệm thu toàn cục (mọi bước)

- [ ] `python tools/<file> --self-test` pass (mọi tool mới)
- [ ] `python -m pytest tools/ -q` xanh, không skip trái phép
- [ ] `python .github/scripts/security_scan.py .` — 0 issue
- [ ] `python .github/scripts/checklist.py .` pass
- [ ] `python .github/scripts/check_skips.py tools/` — 0 skip trái phép
- [ ] Không `console.log`/debug statement trong production code
- [ ] Commit theo convention (`Co-Authored-By: Solo-Code <admin@solo-code.com>`)
- [ ] Không sửa file harness để fix lỗi project

---

## 4. Ước lượng

| Bước | Ước lượng | Phụ thuộc |
|---|---|---|
| Commit dọn dẹp (bắt buộc) | 0.25 ngày | — |
| A1 (bản đồ + bài học) | 0.5 ngày | nền git sạch |
| A2 (subagent interface) | 1 ngày | A1 |
| A3 (compaction pruner) | 2–3 ngày | A1 + chốt injection point (đã chốt: tool) |

Tổng tầng A: **~4–5 ngày**.

---

## 5. Ngoài phạm vi

- Không port Cordis / effect system / typert / ACP / web UI / website / e2b / native(landlock).
- Không đổi executor (OpenCode CLI) — Q2 trả lời "giữ nguyên", Q3/Q4 chặn B/C.
- Không sửa file harness (`.kilo/`, `.opencode/`, `.github/scripts/`) — trừ commit dọn dẹp các fix đã nằm trong `tools/` (project code).
- Không tạo tài liệu mới ngoài `dsh-port-map.md`.

---

## 6. Quyết định đã chốt (Q1–Q5)

| # | Câu hỏi | Kết quả |
|---|---|---|
| Q1 | Làm tầng A? | Có — bỏ A0 (đã fix, cần commit), gộp A4→A1 |
| Q2 | OpenCode CLI hạn chế gì? | Không — giữ nguyên |
| Q3 | `DEEPSEEK_API_KEY` riêng? | **NOT SET** → B/C chặn |
| Q4 | Node/pnpm? | Node `v22.12.0` (thiếu `^22.19`), pnpm `10.33.0` (đạt) → B/C chặn |
| Q5 | `agent_scope.py` nâng parent-chain? | Giữ rút gọn — đủ cho A2 |
