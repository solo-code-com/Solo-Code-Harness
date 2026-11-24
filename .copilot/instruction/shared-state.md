# Shared State — Cross-Engine Collaboration (Local-Only)

> Auto-loaded at session start. Mọi engine **đọc** được `.solocode/shared-state.db` (SQLite).
> Chỉ hook lifecycle hoặc một lệnh gọi `SharedState` tường minh mới **ghi** vào đó. Kilo có hook
> nhưng chỉ ghi `.kilo/state/*.json`; OpenCode, Copilot và Gemini không có lifecycle hook, nên
> session của chúng không để lại row nào.
> File này KHÔNG được commit vào git — chỉ tồn tại local trên máy đang chạy các engine.

## Cái gì đang thực sự chạy (snapshot 2026-09-26)

Đừng tin mô tả, hãy tin số đo — và **đo lại** bằng
`python tools/shared_state.py show` trước khi dùng số liệu. Số dưới đây chỉ là
một snapshot, sẽ cũ theo thời gian:

| Bảng | Rows (snapshot) | Ai ghi |
|---|---:|---|
| `session_log` | 1000 | **Tự động** — `pre_compact.py` (Claude) |
| `active_locks` | 1 | Writer lấy lock theo path; tự hết hạn sau `LOCK_TIMEOUT_HOURS` = 2 giờ |
| `features` | 0 | Không ai — dùng git log + `MEMORY.md` thay thế |
| `shared_memory_*` | 0 | Không ai — `MEMORY.md` đã làm việc này |

Một bản mô tả "Session Protocol (MANDATORY)" 9 bước từng nằm ở đây,
trong đó **0/9 bước thực sự được chạy**. Nó đã bị gỡ: một quy trình
bắt buộc mà không gì kiểm chứng chỉ dạy agent tin vào thứ không có thật.

### Session start / end — chỉ tự động ở engine có lifecycle hook

Claude Code có hook vòng đời và ghi vào **hai store khác nhau** (xem bảng dưới):
`session_start.py` đọc `.solocode/sessions.db` và bơm bối cảnh; `session_end.py`
ghi vào `.solocode/sessions.db`; `pre_compact.py` ghi checkpoint vào
`.solocode/shared-state.db` và nhắc ghi `.solocode/context-checkpoint.json`.
Kilo Code có hook vòng đời riêng nhưng chỉ ghi file JSON trong `.kilo/state/`,
không đụng tới hai store đó.

OpenCode, Copilot và Gemini **không** có lifecycle hook, nên phiên của chúng không
tự động được ghi lại: đừng giả định có dòng mới trong bảng session sau một phiên như
vậy. Nếu cần lưu, gọi `SharedState.add_session_entry` từ code —
`.claude/hooks/pre_compact.py` dòng 88 là ví dụ đang chạy.

### Hai SQLite store — đừng nhầm

Repo có hai DB SQLite local-only với tên dễ lẫn. Chúng phục vụ mục đích khác nhau
và **không** phải hai nguồn sự thật cho cùng một dữ liệu:

| Store | Bảng | Writer | Reader | Mục đích |
|---|---|---|---|---|
| `.solocode/shared-state.db` | `session_log`, `active_locks`, `shared_memory_*` | `pre_compact.py`, `acquire_lock` (locks) | `tools/shared_state.py` CLI, `garden.py`, `test_integration.py` | Nhật ký sự kiện đa engine + khoá file |
| `.solocode/sessions.db` | `sessions` | `.claude/hooks/session_start.py`, `session_end.py` (qua `session_persistence`) | `session_persistence.py`, `session_analytics.py`, `session_start.py` | Vòng đời phiên (start/end/duration/files/status) cho analytics |

Quyết định (2026-09-26): **giữ song song, ghi rõ vai trò**, không hợp nhất khi chưa
chứng minh consumer trùng — xem `docs/deepseek-harness-upgrade-acceptance.md`.

### Khi nào PHẢI dùng lock (còn giá trị)
`active_locks` thường rỗng; nó chỉ có row khi một writer đang giữ lock (hoặc khi
một lock rò rỉ chưa hết hạn). Trước khi giao một tác vụ **ghi** cho worker chạy
song song (Gemini/Antigravity sửa cùng cây thư mục), hãy lấy lock cho các file
trong phạm vi — xem `acquire_lock` ở phần API bên dưới. Đây là cơ chế chống ghi
đè duy nhất giữa các engine.

### `features` và `shared_memory_*` — schema còn, không dùng
Giữ lại để tương thích ngược (`garden.py` cảnh báo feature `in-progress`
quá 7 ngày nếu có ai ghi). Với dự án solo, dùng **git log** cho task và
**`MEMORY.md`** cho convention/gotcha/decision: chúng nằm trong repo,
agent đọc được, không cần đồng bộ. Chỉ dùng các bảng này nếu bạn thật
sự chạy nhiều engine song song và cần trạng thái chung.

## Nếu DB bị hỏng (corrupt)

Nếu `python tools/shared_state.py validate` báo lỗi, hoặc thao tác đọc/ghi báo `sqlite3.DatabaseError` — xoá file DB và để nó tự tái tạo schema rỗng ở lần chạy tiếp theo (KHÔNG còn nguồn migrate dự phòng từ `.opencode/state/` — đã gỡ ở v4.0.0; lịch sử feature/session trước đó sẽ mất nếu chưa backup):

```bash
cp .solocode/shared-state.db .solocode/shared-state.db.bak   # backup trước khi xoá, nếu còn dùng được
rm .solocode/shared-state.db .solocode/shared-state.db-wal .solocode/shared-state.db-shm
# SharedState() tự tạo schema rỗng ở lần mở kế tiếp — không cần script migrate riêng.
```

## CLI Quick Reference

```bash
python tools/shared_state.py show
python tools/shared_state.py features --status in-progress
python tools/shared_state.py sessions --limit 10
python tools/shared_state.py locks
python tools/shared_state.py validate
```

## Python API

```python
from tools.shared_state import SharedState

# Trường hợp dùng thật: khoá file trước khi giao việc GHI cho worker
# chạy song song, rồi trả khoá ngay sau khi xong.
with SharedState() as state:
    if state.acquire_lock("src/auth.py", engine="claude", model="sonnet", reason="Delegating edit to Gemini"):
        # ... thực hiện/uỷ quyền sửa file ...
        state.release_lock("src/auth.py", engine="claude")

# add_session_entry() do hook tự gọi — không cần gọi tay:
#   .claude/hooks/session_end.py, .claude/hooks/pre_compact.py
# set_feature_status() còn tồn tại nhưng không dùng ở repo này (xem trên).
```
