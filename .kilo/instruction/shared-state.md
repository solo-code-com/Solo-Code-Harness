# Shared State — Cross-Engine Collaboration (Local-Only)

> Auto-loaded at session start. `.solocode/shared-state.db` là SQLite DB local-only (đã gitignore), tự tạo ở lần dùng đầu tiên.
> File này KHÔNG được commit vào git — chỉ tồn tại local trên máy đang chạy các engine.

## Mục đích và schema hiện tại

`.solocode/shared-state.db` hiện chỉ lưu đúng hai thứ: `active_locks` và `session_log` (cùng bảng nội bộ `meta`).
Bốn bảng cũ (`features`, `shared_memory_conventions`, `shared_memory_gotchas`, `shared_memory_decisions`) đã bị xoá khỏi schema và sẽ bị gỡ bỏ khỏi file DB cũ mỗi khi module mở file.

### Tri thức dự án nằm ở đâu

Tri thức dự án **không** nằm trong SQLite. Quy tắc, gotcha và quyết định nằm trong `.kilo/memory/MEMORY.md`, còn lịch sử tác vụ nằm trong `git log`.
Lý do: mọi engine đều đọc được file mà không cần tích hợp riêng, trong khi DB trước đây có bên ghi nhưng không có bên đọc.

## Ai đang ghi vào đâu (trạng thái thực tế)

Đừng tin mô tả tĩnh — chạy `python tools/shared_state.py show` để đo lại trạng thái thực tế trên máy:

| Bảng | Ai ghi | Ghi chú |
|---|---|---|
| `active_locks` | Ba delegation wrapper qua `acquire_lock` và `acquire_directory_lock` | Khoá tự hết hạn sau `LOCK_TIMEOUT_HOURS = 2` giờ; row có ghi `pid` chủ sở hữu sẽ bị thu hồi ngay khi process đó không còn chạy |
| `session_log` | Không có gì tự động ghi hiện nay | Còn 1000 row lịch sử do Claude Code và Codex ghi trước khi cả hai engine bị gỡ vào ngày 2026-11-24; gọi `SharedState.add_session_entry` từ code khi một lượt chạy cần để lại row |

### Hai SQLite store — đừng nhầm

Repo có hai DB SQLite local-only với vai trò tách biệt:

| Store | Bảng | Writer | Reader | Mục đích |
|---|---|---|---|---|
| `.solocode/shared-state.db` | `active_locks`, `session_log` | Các delegation wrapper (locks) và mọi lệnh gọi `add_session_entry` tường minh | `tools/shared_state.py` CLI, `garden.py`, `test_integration.py` | Khoá đường dẫn chống ghi đè đa engine và nhật ký phiên thủ công |
| `.solocode/sessions.db` | `sessions` | `tools/session_registry.py` qua `record_start` và `record_finish`, được gọi bởi các delegation wrapper của OpenCode, Kilo và Antigravity | Session analytics và chính các wrapper | Ghi nhận lượt chạy đã thoát bình thường, timeout hay thất bại |

## Khi nào cần dùng lock

Trước khi giao một tác vụ ghi cho worker chạy song song với một writer khác, hãy lấy lock cho các đường dẫn trong phạm vi và trả lock ngay khi xong. Đây là cơ chế chống ghi đè duy nhất giữa các engine.
Delegation wrapper đã tự lấy directory lock sẵn, nên lock thủ công dùng cho các công việc chạy ngoài wrapper.

## Nếu DB bị hỏng (corrupt)

Nếu `python tools/shared_state.py validate` báo lỗi, hoặc thao tác đọc/ghi báo `sqlite3.DatabaseError` — xoá file DB và để nó tự tái tạo rỗng ở lần mở tiếp theo (không có nguồn migrate dự phòng; dữ liệu hiện tại sẽ mất nếu chưa backup):

```bash
cp .solocode/shared-state.db .solocode/shared-state.db.bak   # backup trước khi xoá, nếu còn dùng được
rm .solocode/shared-state.db .solocode/shared-state.db-wal .solocode/shared-state.db-shm
# SharedState() tự tạo schema rỗng ở lần mở kế tiếp — không cần script migrate riêng.
```

## CLI Quick Reference

```bash
python tools/shared_state.py show
python tools/shared_state.py sessions --limit 10
python tools/shared_state.py locks
python tools/shared_state.py validate
```

## Python API

```python
from tools.shared_state import SharedState

# Trường hợp dùng thật: khoá file với engine opencode trước khi uỷ quyền ghi,
# rồi trả khoá ngay sau khi xong.
with SharedState() as state:
    if state.acquire_lock('src/auth.py', engine='opencode', model='deepseek-chat', reason='Delegating write'):
        try:
            # ... thực hiện hoặc uỷ quyền sửa file ...
            pass
        finally:
            state.release_lock('src/auth.py', engine='opencode')

# add_session_entry() là lệnh gọi thủ công, dùng các keyword argument: engine, model, summary.
```
