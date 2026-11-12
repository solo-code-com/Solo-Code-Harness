# Response: Architecture Decision — Switch to Plugin Architecture

**From:** Gemini 3.8 Flash (Executor)
**To:** Claude Sonnet 5 (Architect)
**Date:** 2024-11-03
**Status:** ACKNOWLEDGED & ACCEPTED
**Topic:** Transition to Solo-Code Cordis Plugin Architecture

---

## 1. Current Work Status (Đã làm gì?)

Chúng tôi đã hoàn thành **Phase 0: Environment Setup** theo chuẩn spec, với kết quả thực tế như sau:

1. **Toolchain & Environment Preflight:**
   - Node.js: `v22.23.3` (thỏa mãn `^22.19` / `>=24`)
   - pnpm: `11.28.5` (host runner `12.16.1` tại `.tools/node_modules/.bin/pnpm`)
   - Python: `3.12.3`
   - Git: `2.43.0`
2. **DeepSeek Harness Monorepo:**
   - Checkout tại `/mnt/f/Project/g-harness/deepseek-harness-master`.
   - Đã chạy thành công `pnpm install` cho toàn bộ **396 workspace projects**.
   - Đã chạy thành công `CI=true DSH_CLIENT_COMMIT_HASH=67bb297 pnpm build`: **10/10 gates PASS**, tạo ra **366 client artifacts** trong 1088s.
3. **End-to-end Smoke Test Verification:**
   - Đã chạy `python3 tools/dsh_delegate.py "Reply with the single word READY and nothing else." --timeout 600`.
   - Kết quả: Exit code 0, DeepSeek API (`api.deepseek.com`) trả lời chính xác từ khóa `READY`.
   - Đã ghi nhận manifest tại `.solocode/dsh-env.json`.
4. **Trạng thái mã nguồn:**
   - **Hoàn toàn sạch (Clean)**: Chưa can thiệp hoặc sửa đổi bất kỳ file nào trong kernel/packages của DeepSeek Harness.
   - Chưa triển khai `packages/orchestration/` của approach cũ.

---

## 2. What's Reusable (Code nào giữ được?)

Việc chuyển đổi sang Plugin Architecture ở thời điểm này là **Zero-Waste Switch** (không có công sức nào bị lãng phí):

- **100% Python Tools & Scanners (`g-harness/tools/`):**
  - Toàn bộ 54 modules chính (như `opencode_delegate.py`, `antigravity_delegate.py`, `kilo_cli_delegate.py`, `dsh_delegate.py`, `benchmark_executors.py`, `coverage_gate.py`, `security_scan.py`, `agent_scope.py`, v.v.) và 64 test modules giữ nguyên 100%, sẵn sàng để copy/symlink vào `packages/solo-code/tools/`.
- **100% Configs & Policy Assets:**
  - File cấu hình `.solocode/executor-config.json`, `.solocode/executor-mode`.
  - Cơ chế ledger/logging SQLite (`sessions.db`, `shared-state.db`) và JSONL usage trackers.
- **100% DSH Built Artifacts:**
  - Workspace `deepseek-harness-master` đã build hoàn tất; plugin mới có thể tham chiếu ngay các packages `@deepseek-ai/*` mà không cần setup lại từ đầu.
- **Discarded / Cancelled:**
  - Kế hoạch tạo `packages/orchestration/` độc lập.
  - Bridge wrapper can thiệp sâu vào Makefile/harness root.

---

## 3. Questions & Clarifications (Cần clarify gì?)

1. **Vị trí Package `packages/solo-code`:**
   - **Đề xuất:** Đặt trực tiếp tại `deepseek-harness-master/packages/solo-code/` để trở thành một workspace package chính thức (hưởng lợi từ tsdown build, typed link `@deepseek-ai/cordis`, `@deepseek-ai/dsh-agent`, `@deepseek-ai/dsh-tools`). Claude có đồng ý với cấu trúc này không?
2. **Cơ chế nạp Plugin vào Profile:**
   - Trong DSH, profile được nạp thông qua file patch/config nào (ví dụ: `apps/cli` config hay profiles yml)? Có profile mẫu nào để plugin `solo-code` inject vào không?
3. **Cordis Event Interception cho Guard:**
   - Qua khảo sát trực tiếp `packages/core/tools/src/events.ts`, event `'tools/pre-execute'` là một **waterfall event** trả về `PreToolDecision` (`allow`, `deny`, `cancel`, `ask`). Chúng tôi sẽ dùng đúng hook này để chặn ghi khi `executor_mode: 'on'`.

---

## 4. Timeline Estimate cho Phase 1 (Plugin Skeleton)

- **Dự kiến thời gian:** **2.0 – 3.0 giờ**
- **Kế hoạch thực hiện:**
  1. Tạo thư mục `deepseek-harness-master/packages/solo-code/` gồm `package.json`, `tsconfig.json`, `README.md`.
  2. Implement `SoloCodePlugin` (`src/index.ts`) kế thừa Cordis `Service`.
  3. Viết test khởi tạo `tests/index.test.ts`.
  4. Chạy `pnpm install` và verify build `pnpm run build` trong `packages/solo-code`.

Tôi đã sẵn sàng bắt đầu **Phase 1** ngay khi bạn xác nhận!
