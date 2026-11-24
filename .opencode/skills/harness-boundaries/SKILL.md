---
name: "Harness Boundary Rules"
description: "**CRITICAL:** File này là một phần của Solo-Code Harness infrastructure. Nó tồn tại để bảo vệ các agent khỏi nhầm lẫn giữa code harness và code dự án."
---

# Harness Boundary Rules

> **CRITICAL:** File này là một phần của Solo-Code Harness infrastructure. Nó tồn tại để bảo vệ các agent khỏi nhầm lẫn giữa code harness và code dự án.

## Tại sao cần file này?

Khi triển khai bộ harness vào một dự án mới (`python tools/deploy.py deploy ./target-project`), các thư mục `.kilo/`, `.copilot/`, `.gemini/`, `.claude/`, `.opencode/`, `.contracts/` được copy nguyên khối vào project đích (dạng runtime-only, không mang theo dev tooling của Solo-Code-CLI). Riêng `.github/`, `.vscode/`, `tools/` là **thư mục dùng chung** — harness chỉ đặt thêm file vào đó, dự án vẫn giữ file riêng của mình. Các file như `AGENTS.md`, `kilo.jsonc`, `opencode.json` cũng được copy. (`.opencode/` từng bị gỡ ở v4.0.0, rồi được đưa trở lại thành engine chính ở v4.2.0 — xem `.harness.lock`.)

**Agent có thể nhầm lẫn theo CẢ HAI chiều:** thấy file harness trong project đích và tưởng là code dở dang của dự án; hoặc thấy CI workflow / script dev của chính dự án nằm trong `.github/`, `tools/` rồi tưởng là harness và không dám đụng. Tra `[shared_files]` trong `.harness.lock` để biết chính xác file nào là harness.

## Boundary Markers

### 1. `.harness.lock` — marker chính

File này tồn tại ở gốc project. Nếu có mặt → project được deploy bởi Solo-Code Harness. Đọc nó để biết chính xác thư mục/file nào là harness.

### 2. `.solocode/` — marker phụ

Thư mục này là marker dành riêng cho harness, chứa config nội bộ (không phải code dự án).

### 3. Harness Boundary Table

| Nếu file/thư mục... | Thì nó là... | Hành động |
|---------------------|-------------|----------|
| Bắt đầu bằng `.kilo/`, `.gemini/`, `.opencode/`, `.agents/` | **Harness engine** | KHÔNG sửa, KHÔNG phân tích như code dự án. Lưu ý: `.kilo/` là **generation root** — nguồn mà mọi engine khác (`.claude/`, `.copilot/`, `.gemini/`, `.opencode/`) được sinh ra từ đó, không phải phụ thuộc runtime vào ứng dụng Kilo. |
| Bắt đầu bằng `.contracts/` | **Harness contracts** | Sub-agent status contracts |
| Bắt đầu bằng `.github/`, `.vscode/`, `tools/` | **DÙNG CHUNG** — harness *và* dự án | Harness có đặt file ở đây, nhưng dự án CŨNG sở hữu file riêng (CI workflow, `CODEOWNERS`, dependabot, cấu hình editor, script dev). Chỉ các đường dẫn liệt kê trong `[shared_files]` của `.harness.lock` là harness; **mọi file khác ở đây là code dự án** — đọc/sửa bình thường. |
| Là `AGENTS.md`, `kilo.jsonc`, `opencode.json`, `.mcp.json`, `.ruff.toml`, `.gitleaks.toml`, `Makefile`, `verify.sh`, `extensions_config.json`, `.harness.lock`, `.solocode/`, `.pre-commit-config.yaml`, `.github/pull_request_template.md`, `agent.yaml`, `pyproject.toml`, `eslint.config.mjs` | **Harness config** | File cấu hình agent — không phải config dự án |
| **Tất cả các file/thư mục khác** | **Project code** | Đây là code của dự án thực — được phép sửa |

## Quy tắc bắt buộc

1. **KHÔNG BAO GIỜ sửa file harness để fix bug dự án.**
2. **KHÔNG BAO GIỜ sửa file dự án để fix vấn đề harness.**
3. **Luôn đọc `.harness.lock` trước khi phân tích codebase.**
4. **Nếu bạn thấy file trong danh sách harness bị lỗi, báo cáo — đừng tự sửa.**
5. **Khi triển khai harness vào dự án mới, tất cả file harness được copy từ nguồn — không tự tạo.**

## Trường hợp đặc biệt: `Deploy` vs `Init`

- **Deploy:** Harness được copy từ repo nguồn vào project đích → các file harness đã tồn tại từ trước, không phải do dự án tạo ra.
- **Init:** Dự án mới được tạo → có thể có file trùng tên với harness (như `.gitignore`, `Makefile`) — đó là project config, không phải harness.

## Tự kiểm tra (Self-Check)

Khi phân tích bất kỳ file nào trong project, hãy tự hỏi:

> "File này có thể đã được copy vào từ Solo-Code Harness không?"

Nếu câu trả lời là "có thể" → kiểm tra `.harness.lock` trước khi hành động.
