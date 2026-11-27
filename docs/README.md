# Solo-Code-CLI — Tài Liệu Dự Án (Documentation Hub)

> **Cổng tra cứu trung tâm** hệ thống hoá toàn bộ tài liệu kỹ thuật, kiến trúc, kế hoạch porting, nhật ký kiểm toán (audit) và các báo cáo review chéo giữa các AI engine (Claude Code, Kilo, OpenCode CLI, Antigravity IDE, Codex).

---

## 1. Bản Đồ Tổng Quan & Trạng Thái Hiệu Lực

Các tài liệu trong `docs/` được phân thành 3 nhóm trạng thái:
- **`Active` (Hiệu lực liên tục):** Tài liệu hướng dẫn, nguyên tắc kỹ thuật và checklist áp dụng trực tiếp cho quá trình phát triển mã nguồn hiện tại.
- **`Reference` (Tra cứu kỹ thuật):** Kế hoạch kiến trúc, bản đồ tích hợp và dữ liệu benchmark dùng để đối chiếu khi mở rộng hệ thống.
- **`Historical Archive` (Hồ sơ lịch sử):** Báo cáo nghiệm thu, biên bản bàn giao, nhật ký audit và kết quả review chéo theo từng mốc phát triển.

### Ma Trận Tài Liệu (Documentation Matrix)

| Tài liệu | Danh mục | Mốc thời gian | Tác giả / Engine | Trạng thái | Tóm tắt mục đích |
|---|---|---|---|---|---|
| [`defensive-patterns.md`](defensive-patterns.md) | Kiến trúc & Tiêu chuẩn | 2026-08-14 | Solo-Code Team | `Active` | Bộ quy tắc lập trình phòng thủ: concurrency, lifecycle, subprocess, teardown. |
| [`capability-seams.md`](capability-seams.md) | Kiến trúc & Tiêu chuẩn | 2026-08-14 | Solo-Code Team | `Active` | Phân lập ranh giới Service Definition / Provider / Consumer theo Seam pattern. |
| [`how-harness-works.md`](how-harness-works.md) | Kiến trúc & Tiêu chuẩn | 2026-11-27 | Solo-Code Team | `Active` | Một trang mô tả cỗ máy: ba vai trò, cây file sinh tự động, ranh giới và fingerprint, hai cổng ghi, hàng rào worker, chuỗi gate và state sống qua phiên. |
| [`code-review-checklist.md`](code-review-checklist.md) | Quy trình & Kiểm soát | 2026-08-14 | Solo-Code Team | `Active` | Checklist tiêu chuẩn phục vụ rà soát mã nguồn trước khi tích hợp/merge. |
| [`antigravity-telemetry-hook.md`](antigravity-telemetry-hook.md) | Vận hành & Công cụ | 2026-09-21 | Antigravity IDE | `Reference` | Hướng dẫn tắt hook telemetry local để khắc phục lỗi parse quoting `telemetry_hook_bundle.js`. |
| [`dsh-port-plan.md`](dsh-port-plan.md) | Kế hoạch Porting | 2026-08-15 | Kilo & Claude Code | `Reference` | Kế hoạch khai thác DeepSeek Harness: chốt 3 tầng phạm vi (A1-A3). |
| [`dsh-port-map.md`](dsh-port-map.md) | Kế hoạch Porting | 2026-08-15 | Kilo | `Reference` | Ánh xạ 49 nhóm / 226 packages DeepSeek Harness sang Python CLI và 8 bài học postmortem. |
| [`executor-benchmark-2026-09-04.md`](executor-benchmark-2026-09-04.md) | Đánh giá & Benchmark | 2026-09-04 | Claude Code | `Reference` | Đánh giá chi phí & năng lực code của các model: GLM-5, Qwen 3.6/3.7, DeepSeek V4. |
| [`upgrade-summary.md`](upgrade-summary.md) | Lịch sử nâng cấp | 2026-08-14 | Claude & OpenCode | `Historical Archive` | Tổng kết đợt đại tu hệ thống v3 (12 commits). **?? g?p 2026-10-16** v?o [`archive-harness-upgrade-review-and-audit-reports-2026.md`](archive-harness-upgrade-review-and-audit-reports-2026.md) ? file n?y nay l? con tr?. |
| [`evaluation-report.md`](evaluation-report.md) | Lịch sử nâng cấp | 2026-08-15 | Kilo | `Historical Archive` | Báo cáo đánh giá độc lập về 12 commit nâng cấp từ DeepSeek Harness. |
| [`work-report-2026-09-16.md`](work-report-2026-09-16.md) | Lịch sử nâng cấp | 2026-09-16 | Kilo | `Historical Archive` | Báo cáo nâng cấp Phase 1 sau đợt rà soát `open-code-review`. |
| [`audit-2026-08-15.md`](audit-2026-08-15.md) | Kiểm định & Sức khỏe | 2026-08-15 | Claude Code | `Historical Archive` | Đợt audit toàn diện repo sau upgrade lớn. **?? g?p 2026-10-16** v?o [`archive-harness-upgrade-review-and-audit-reports-2026.md`](archive-harness-upgrade-review-and-audit-reports-2026.md) ? file n?y nay l? con tr?. |
| [`audit-2026-09-04.md`](audit-2026-09-04.md) | Kiểm định & Sức khỏe | 2026-09-04 | Gemini & Claude | `Historical Archive` | Xác nhận chất lượng dự án đạt 100% test gate. **?? g?p 2026-10-16** v?o [`archive-harness-upgrade-review-and-audit-reports-2026.md`](archive-harness-upgrade-review-and-audit-reports-2026.md) ? file n?y nay l? con tr?. |
| [`audit-2026-09-11.md`](audit-2026-09-11.md) | Kiểm định & Sức khỏe | 2026-09-11 | OpenCode CLI | `Historical Archive` | Rà soát và chuẩn hoá tính nhất quán giữa cấu hình harness, docs và test. |
| [`handoff-2026-09-11.md`](handoff-2026-09-11.md) | Bàn giao & Quy trình | 2026-09-11 | OpenCode CLI | `Historical Archive` | Biên bản bàn giao phiên làm việc và yêu cầu Claude Code phân tích độc lập. |
| [`deepseek-cross-review-antigravity.md`](deepseek-cross-review-antigravity.md) | Cross-Review | 2026-08-28 | Orchestrator | `Historical Archive` | Đề cương yêu cầu review vòng 1 cho tích hợp `agy.exe` headless worker. |
| `codex-antigravity-cross-review-report.md` (removed 2026-11-23, Codex engine decommissioned) | Cross-Review | 2026-08-28 | Codex | `Historical Archive` | Báo cáo kết quả rà soát Antigravity headless worker (danh mục phát hiện F1–F11). |
| [`deepseek-cross-review-antigravity-round2.md`](deepseek-cross-review-antigravity-round2.md) | Cross-Review | 2026-08-29 | Orchestrator | `Historical Archive` | Đề cương review vòng 2 về tính ổn định, concurrency và an toàn subprocess. |
| [`review-open-code-review-2026-09-16.md`](review-open-code-review-2026-09-16.md) | Cross-Review | 2026-09-16 | Kilo | `Historical Archive` | Phân tích bài học từ `open-code-review` và đề xuất nâng cấp hệ thống harness. |
| [`deepseek-harness-upgrade-review-plan.md`](deepseek-harness-upgrade-review-plan.md) | Cross-Review | 2026-09-26 | ChatGPT | `Historical Archive` | Đề cương phản biện kế hoạch đánh giá & nâng cấp harness (validator generic 52/100). **?? g?p 2026-10-16** v?o [`archive-harness-upgrade-review-and-audit-reports-2026.md`](archive-harness-upgrade-review-and-audit-reports-2026.md) ? file n?y nay l? con tr?. |
| [`deepseek-harness-upgrade-review-response.md`](deepseek-harness-upgrade-review-response.md) | Cross-Review | 2026-09-26 | Kilo (DeepSeek) | `Historical Archive` | Phản biện vòng 1: 13 finding validator, mâu thuẫn state, giới hạn adapter. **?? g?p 2026-10-16** v?o [`archive-harness-upgrade-review-and-audit-reports-2026.md`](archive-harness-upgrade-review-and-audit-reports-2026.md) ? file n?y nay l? con tr?. |
| [`deepseek-harness-upgrade-review-followup.md`](deepseek-harness-upgrade-review-followup.md) | Cross-Review | 2026-09-26 | ChatGPT | `Historical Archive` | Phản hồi vòng 2 + kế hoạch chốt 6 bước triển khai. **?? g?p 2026-10-16** v?o [`archive-harness-upgrade-review-and-audit-reports-2026.md`](archive-harness-upgrade-review-and-audit-reports-2026.md) ? file n?y nay l? con tr?. |
| [`deepseek-harness-upgrade-review-response-round2.md`](deepseek-harness-upgrade-review-response-round2.md) | Cross-Review | 2026-09-26 | Kilo (DeepSeek) | `Historical Archive` | Xác nhận 6 điểm, chọn Phương án B, bổ sung 3 omission (hai store, lock rò rỉ, drift). **?? g?p 2026-10-16** v?o [`archive-harness-upgrade-review-and-audit-reports-2026.md`](archive-harness-upgrade-review-and-audit-reports-2026.md) ? file n?y nay l? con tr?. |
| [`deepseek-harness-upgrade-acceptance.md`](deepseek-harness-upgrade-acceptance.md) | Nghiệm thu | 2026-09-27 | Kilo (DeepSeek) | `Active` | Biên bản nghiệm thu: baseline vs sau, mutation test, gate cuối. |
| [`lifecycle-matrix-verification.md`](lifecycle-matrix-verification.md) | Nghiệm thu | 2026-09-27 | Kilo (DeepSeek) | `Reference` | Ma trận lifecycle cross-engine kèm bằng chứng tĩnh (phân biệt live chưa chạy). |
| [`deepseek-harness-upgrade-final-review.md`](deepseek-harness-upgrade-final-review.md) | Cross-Review | 2026-09-27 | ChatGPT/Gemini | `Historical Archive` | Báo cáo thống nhất: hiệu chỉnh cuối và xác nhận gate. **?? g?p 2026-10-16** v?o [`archive-harness-upgrade-review-and-audit-reports-2026.md`](archive-harness-upgrade-review-and-audit-reports-2026.md) ? file n?y nay l? con tr?. |
| [`work-report-2026-09-27.md`](work-report-2026-09-27.md) | Lịch sử nâng cấp | 2026-09-27 | Kilo | `Historical Archive` | Nhật ký phiên nâng cấp harness (6 bước, gate, dọn dẹp). |
| [`archive-harness-upgrade-review-and-audit-reports-2026.md`](archive-harness-upgrade-review-and-audit-reports-2026.md) | Cross-Review | 2026-10-16 | Jev (?i?u ph?i) + Gemini | `Historical Archive` | G?p 8 b?o c?o review/audit 2026 (upgrade-summary, audit-08-15, audit-09-04, 5 file cross-review) ? n?i dung verbatim. |

---

## 2. Chi Tiết Các Nhóm Tài Liệu

### Nhóm 1: Tiêu Chuẩn Kỹ Thuật & Kiến Trúc Cốt Lõi (`Active`)
Nhóm tài liệu quy định phong cách lập trình, thiết kế mô-đun và bảng kiểm an toàn cho codebase:
- [`defensive-patterns.md`](defensive-patterns.md): Đúc kết các bài học phòng vệ xương máu từ sự cố thực tế. Cần đọc kỹ khi viết code liên quan đến quản lý tiến trình con (subprocess), lifecycle, mutex/lock, IO bất đồng bộ hoặc teardown.
- [`capability-seams.md`](capability-seams.md): Kiến trúc Service Definition / Provider / Consumer, giúp tách biệt các năng lực của agent và cho phép mocking/testing cô lập.
- [`code-review-checklist.md`](code-review-checklist.md): Danh mục kiểm tra chi tiết theo các khía cạnh: Security, Error Handling, Concurrency, Shell Scripting trên môi trường Windows/POSIX.
- [`how-harness-works.md`](how-harness-works.md): Trang tổng quan một trang về cách harness vận hành — vai trò, file sinh tự động, ranh giới, fence worker, chuỗi gate và state bền vững. Đọc trước khi tra sâu vào `SPEC.md`.

### Nhóm 2: Khai Thác & Chuyển Giao DeepSeek Harness (`Reference`)
Tài liệu định hướng việc tiếp nhận và chuyển ngữ hệ thống harness từ nguồn DeepSeek (Node.js/npm) sang Python:
- [`dsh-port-plan.md`](dsh-port-plan.md): Xác định chiến lược porting theo 3 tầng (Scope A chốt thực hiện, Scope B/C chặn vì khác biệt môi trường runtime).
- [`dsh-port-map.md`](dsh-port-map.md): Bản đồ phân loại 49 nhóm gói thư viện (226 npm packages) kèm 8 bài học postmortem đắt giá về kiểm thử, snapshot refresh và phân loại lỗi.

### Nhóm 3: Benchmark & Cấu Hình Môi Trường (`Reference`)
- [`executor-benchmark-2026-09-04.md`](executor-benchmark-2026-09-04.md): Thử nghiệm thực tế chi phí, tốc độ và tỷ lệ thành công của các mô hình LLM làm executor (Qwen, GLM, DeepSeek V4).
- [`antigravity-telemetry-hook.md`](antigravity-telemetry-hook.md): Xử lý xung đột lỗi quoting khi Antigravity IDE chạy các lệnh headless với hook telemetry.

### Nhóm 4: Báo Cáo Nghiệm Thu & Nâng Cấp Hệ Thống (`Historical Archive`)
- [`upgrade-summary.md`](upgrade-summary.md): Dấu mốc đại tu ngày 2026-08-14 với 12 commits hoàn thành mục tiêu tái cấu trúc.
- [`evaluation-report.md`](evaluation-report.md): Báo cáo phản biện độc lập của Kilo đánh giá chất lượng 12 commit nói trên.
- [`work-report-2026-09-16.md`](work-report-2026-09-16.md): Báo cáo triển khai Phase 1 tích hợp các khuyến nghị từ dự án `open-code-review`.

### Nhóm 5: Chuỗi Nhật Ký Kiểm Định Sức Khỏe Dự Án (Audit Trail)
Theo dõi tiến trình củng cố chất lượng và dọn dẹp drift cấu hình:
1. [`audit-2026-08-15.md`](audit-2026-08-15.md): Audit sau đợt nâng cấp DeepSeek, chỉ ra các điểm drift về số dòng và phantom file refs.
2. [`audit-2026-09-04.md`](audit-2026-09-04.md): Xác nhận 100% test gate vượt qua (sự phối hợp giữa Gemini IDE và Claude Code).
3. [`audit-2026-09-11.md`](audit-2026-09-11.md): Khắc phục 10 lỗi không nhất quán về config harness, documentation và mock scripts.
4. [`handoff-2026-09-11.md`](handoff-2026-09-11.md): Handoff ghi nhận commit `953ae69` và chuyển giao quyền kiểm định sang Claude Code.

### Nhóm 6: Cross-Review Đa AI Engine (Multi-Engine Assurance)
Hồ sơ đối chiếu và kiểm tra chéo giữa các tác tử AI độc lập:
- Bộ tài liệu review Antigravity Headless Worker:
  - Yêu cầu ban đầu: [`deepseek-cross-review-antigravity.md`](deepseek-cross-review-antigravity.md)
  - Kết quả rà soát F1–F11: `codex-antigravity-cross-review-report.md` (removed 2026-11-23, Codex engine decommissioned)
  - Yêu cầu review vòng 2: [`deepseek-cross-review-antigravity-round2.md`](deepseek-cross-review-antigravity-round2.md)
- Rà soát công cụ bên ngoài:
  - [`review-open-code-review-2026-09-16.md`](review-open-code-review-2026-09-16.md): Báo cáo thẩm định bộ công cụ `open-code-review`.

---

## 3. Hướng Dẫn Tra Cứu Nhanh Cho AI & Kỹ Sư

```
   ┌──────────────────────────────────────────────────────────────┐
   │                    BẠN CẦN LÀM GÌ?                           │
   └──────────────────────────────┬───────────────────────────────┘
                                  │
         ┌────────────────────────┼────────────────────────┐
         ▼                        ▼                        ▼
  [Viết / Sửa Code]        [Nghiên Cứu / Mở Rộng]   [Review / Debug Lỗi]
         │                        │                        │
         ├─ defensive-patterns.md ├─ dsh-port-plan.md      ├─ code-review-checklist.md
         └─ capability-seams.md   ├─ dsh-port-map.md       ├─ chuỗi audit (2026-08/09)
                                  └─ executor-benchmark.md └─ cross-review reports
```

1. **Khi bắt đầu viết code hoặc sửa đổi logic:**
   - Đọc trước [`defensive-patterns.md`](defensive-patterns.md) để tránh các bẫy lỗi về concurrency/subprocess.
   - Tuân thủ ranh giới dịch vụ trong [`capability-seams.md`](capability-seams.md).
2. **Khi chuẩn bị mở PR hoặc review:**
   - Dùng checklist tại [`code-review-checklist.md`](code-review-checklist.md) để tự đánh giá.
3. **Khi cần tìm hiểu quyết định kiến trúc trong quá khứ:**
   - Tra cứu [`dsh-port-map.md`](dsh-port-map.md) và các báo cáo audit tương ứng.
