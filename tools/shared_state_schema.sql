-- tools/shared_state_schema.sql
-- Schema tài liệu cho .solocode/shared-state.db (SQLite, local-only, KHÔNG track git).
-- Bảng thực tế được tạo tự động bởi tools/shared_state.py::SCHEMA_SQL khi mở kết nối lần đầu.
-- File này chỉ để đọc hiểu — sửa schema thật thì sửa trong tools/shared_state.py.
-- Four legacy tables (features, shared_memory_conventions, shared_memory_gotchas, shared_memory_decisions) are removed when SharedState opens an older database.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- keys hợp lệ: version, updated_at, updated_by_engine, updated_by_model

CREATE TABLE IF NOT EXISTS session_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,     -- ISO 8601 UTC
    engine        TEXT NOT NULL CHECK (engine IN ('kilo','opencode','copilot','gemini')),
    model         TEXT NOT NULL,
    session_id    TEXT,
    summary       TEXT NOT NULL,
    files_changed TEXT,              -- JSON array dạng text
    commits       TEXT,              -- JSON array dạng text
    verification  TEXT               -- JSON object dạng text
);
CREATE INDEX IF NOT EXISTS idx_session_log_ts ON session_log(timestamp DESC);

CREATE TABLE IF NOT EXISTS active_locks (
    path       TEXT PRIMARY KEY,
    engine     TEXT NOT NULL,
    model      TEXT NOT NULL,
    session_id TEXT,
    locked_at  TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    reason     TEXT
);
