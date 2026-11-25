#!/usr/bin/env python3
"""
shared_state.py — Cross-engine shared state reader/writer (SQLite backend).

Single source of truth at .solocode/shared-state.db (LOCAL ONLY — not committed to git,
directory already excluded via .gitignore). The database keeps the delegation lock table
and the session log; project knowledge lives in .kilo/memory/MEMORY.md and in the git
log, not here.
("opencode" remains a VALID_ENGINES value: OpenCode was reintroduced as a
primary engine in v4.2.0, and historical rows still name it that way.)

Why SQLite instead of JSON + manual file locking:
  - Each mutation (acquire_lock, add_session_entry, ...) runs inside its own
    `BEGIN IMMEDIATE` transaction. SQLite's own OS-level locking (WAL mode) guarantees
    isolation between concurrent processes — no "load whole file, mutate in memory,
    save at the end" pattern that would lose concurrent writes from another engine.
  - No custom msvcrt/fcntl code needed; SQLite already handles cross-platform locking.

Usage:
    from tools.shared_state import SharedState

    with SharedState() as state:
        if state.acquire_lock("src/auth.py", engine="copilot", model="deepseek-chat"):
            state.add_session_entry(engine="copilot", model="deepseek-chat", summary="Fixed bug in auth")
            state.release_lock("src/auth.py", engine="copilot")
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / ".solocode" / "shared-state.db"
LOCK_TIMEOUT_HOURS = 2
MAX_SESSION_LOG_ROWS = 1000
VALID_ENGINES = ("kilo", "opencode", "claude", "copilot", "gemini", "codex")


# Windows resolves paths case-insensitively, so two workers that spell the same
# directory differently must still collide on one lock key.
_CASE_INSENSITIVE_PATHS = sys.platform == "win32"


def _fold_case(value: str) -> str:
    """Lowercase a lock key on a case-insensitive filesystem."""
    return value.lower() if _CASE_INSENSITIVE_PATHS else value


def _normalise_lock_path(path: str) -> str:
    """Return a portable relative lock key without changing directory markers."""
    if path.startswith("dir:"):
        value = path[4:].replace("\\", "/").strip("/")
        value = "." if value in ("", ".") else value
        return _fold_case("dir:" + value)
    return _fold_case(path.replace("\\", "/").strip("/"))


def _directory_lock_path(path: str) -> str:
    """Encode directory locks in the existing path-keyed table."""
    normalised = _normalise_lock_path(path)
    return normalised if normalised.startswith("dir:") else "dir:" + normalised


def _lock_paths_conflict(left: str, right: str) -> bool:
    """Return whether file or directory lock keys overlap in the workspace."""
    left = _normalise_lock_path(left)
    right = _normalise_lock_path(right)
    left_is_dir = left.startswith("dir:")
    right_is_dir = right.startswith("dir:")
    left_path = left[4:] if left_is_dir else left
    right_path = right[4:] if right_is_dir else right
    if left_is_dir and left_path == ".":
        left_path = ""
    if right_is_dir and right_path == ".":
        right_path = ""
    if not left_is_dir and not right_is_dir:
        return left_path == right_path
    if left_is_dir and right_is_dir:
        if not left_path or not right_path:
            return True
        return (
            left_path == right_path
            or left_path.startswith(right_path + "/")
            or right_path.startswith(left_path + "/")
        )
    directory, file_path = (
        (left_path, right_path) if left_is_dir else (right_path, left_path)
    )
    if not directory:
        return True
    return file_path == directory or file_path.startswith(directory + "/")


def pid_alive(pid: int) -> bool:
    """Whether a process id is still running, conservatively.

    Windows has no POSIX pid probe: os.kill(pid, 0) would call TerminateProcess
    there, so liveness is asked through the Win32 API instead. Any answer that
    cannot be obtained counts as alive, because reclaiming a live lock is worse
    than waiting for the TTL.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            # 5 is access denied, which can still mean a live process.
            return ctypes.get_last_error() == 5
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


#: Tables an older database still carries. Nothing ever wrote them, and they are
#: removed on open so the file cannot suggest a memory store that does not exist.
_LEGACY_TABLES = (
    "features",
    "shared_memory_conventions",
    "shared_memory_gotchas",
    "shared_memory_decisions",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    engine        TEXT NOT NULL,
    model         TEXT NOT NULL,
    session_id    TEXT,
    summary       TEXT NOT NULL,
    files_changed TEXT,
    commits       TEXT,
    verification  TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_log_ts ON session_log(timestamp DESC);
CREATE TABLE IF NOT EXISTS active_locks (
    path       TEXT PRIMARY KEY,
    engine     TEXT NOT NULL,
    model      TEXT NOT NULL,
    session_id TEXT,
    locked_at  TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    reason     TEXT,
    pid        INTEGER
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Số lần thử lại + độ trễ khi khởi tạo DB lần đầu bị "database is locked".
# Cần thiết vì PRAGMA/executescript trong __init__ chạy TRƯỚC bất kỳ
# transaction BEGIN IMMEDIATE nào, nên có thể va chạm khi 2 engine cùng
# tạo file .db mới lần đầu gần như đồng thời (đã tái hiện được bug này
# bằng test_concurrent_lock_acquire trước khi có retry).
_INIT_RETRY_ATTEMPTS = 10
_INIT_RETRY_DELAY_SECONDS = 0.1


class SharedState:
    """Cross-engine shared state manager backed by SQLite (local-only)."""

    def __init__(self, path: Path | None = None):
        self.path = path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        self._init_schema_with_retry()

    def _init_schema_with_retry(self) -> None:
        """Run PRAGMA + schema setup, retrying on transient 'database is locked'.

        This happens outside any BEGIN IMMEDIATE transaction, so it is not
        covered by SQLite's own busy_timeout the same way writes are —
        retry manually instead of letting the first attempt crash the caller.
        """
        import time

        last_error: sqlite3.OperationalError | None = None
        for attempt in range(_INIT_RETRY_ATTEMPTS):
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._conn.executescript(SCHEMA_SQL)
                cols = {
                    row[1]
                    for row in self._conn.execute(
                        "PRAGMA table_info(active_locks)"
                    ).fetchall()
                }
                if "pid" not in cols:
                    self._conn.execute(
                        "ALTER TABLE active_locks ADD COLUMN pid INTEGER"
                    )
                for table in _LEGACY_TABLES:
                    self._conn.execute(f"DROP TABLE IF EXISTS {table}")
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                    raise
                last_error = e
                time.sleep(_INIT_RETRY_DELAY_SECONDS * (attempt + 1))
        raise last_error  # type: ignore[misc]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SharedState:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _transaction(self):
        """Context manager: BEGIN IMMEDIATE ... COMMIT/ROLLBACK."""
        return _ImmediateTransaction(self._conn)

    def _expire_locks(self) -> None:
        self._conn.execute("DELETE FROM active_locks WHERE expires_at <= ?", (_now(),))

    def _reclaim_dead_holders(self) -> int:
        """Delete lock rows whose recorded pid is no longer running."""
        rows = self._conn.execute(
            "SELECT path, pid FROM active_locks WHERE pid IS NOT NULL"
        ).fetchall()
        dead = [path for path, pid in rows if not pid_alive(int(pid))]
        for path in dead:
            self._conn.execute("DELETE FROM active_locks WHERE path = ?", (path,))
        return len(dead)

    def _prune_session_log(self) -> None:
        # Sắp xếp phụ theo `id DESC` để có thứ tự xác định (deterministic) khi
        # 2 dòng có cùng `timestamp` (2 engine ghi trong cùng 1 giây) — tránh
        # xoá nhầm dòng mới do ORDER BY chỉ theo timestamp không ổn định.
        self._conn.execute(
            """
            DELETE FROM session_log WHERE id NOT IN (
                SELECT id FROM session_log ORDER BY timestamp DESC, id DESC LIMIT ?
            )
            """,
            (MAX_SESSION_LOG_ROWS,),
        )

    # ── Session Log ──────────────────────────────────────────

    def add_session_entry(
        self,
        *,
        engine: str,
        model: str,
        summary: str,
        session_id: str = "",
        files_changed: list[str] | None = None,
        commits: list[str] | None = None,
        verification: dict[str, bool] | None = None,
    ) -> None:
        if engine not in VALID_ENGINES:
            raise ValueError(f"Invalid engine: {engine}")
        with self._transaction():
            self._conn.execute(
                """
                INSERT INTO session_log
                    (timestamp, engine, model, session_id, summary, files_changed, commits, verification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now(),
                    engine,
                    model,
                    session_id,
                    summary,
                    json.dumps(files_changed or []),
                    json.dumps(commits or []),
                    json.dumps(verification or {}),
                ),
            )
            self._prune_session_log()

    def get_recent_sessions(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT timestamp, engine, model, session_id, summary, "
            "files_changed, commits, verification FROM session_log "
            "ORDER BY timestamp DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "timestamp": r[0],
                "engine": r[1],
                "model": r[2],
                "session_id": r[3],
                "summary": r[4],
                "files_changed": json.loads(r[5]),
                "commits": json.loads(r[6]),
                "verification": json.loads(r[7]),
            }
            for r in rows
        ]

    # ── File Locking ─────────────────────────────────────────

    def acquire_lock(
        self,
        path: str,
        *,
        engine: str,
        model: str,
        session_id: str = "",
        reason: str = "",
    ) -> bool:
        """Atomically acquire a lock. Returns False if another engine already holds it."""
        path = _normalise_lock_path(path)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=LOCK_TIMEOUT_HOURS)
        with self._transaction():
            self._expire_locks()
            self._reclaim_dead_holders()
            rows = self._conn.execute(
                "SELECT path, engine, session_id FROM active_locks"
            ).fetchall()
            if any(
                (owner != engine or owner_session != session_id)
                and _lock_paths_conflict(path, existing)
                for existing, owner, owner_session in rows
            ):
                return False  # Held by a different engine — conflict
            self._conn.execute(
                """
                INSERT INTO active_locks (path, engine, model, session_id, locked_at, expires_at, reason, pid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    engine = excluded.engine, model = excluded.model,
                    session_id = excluded.session_id, locked_at = excluded.locked_at,
                    expires_at = excluded.expires_at, reason = excluded.reason,
                    pid = excluded.pid
                """,
                (
                    path,
                    engine,
                    model,
                    session_id,
                    now.isoformat(),
                    expires.isoformat(),
                    reason,
                    os.getpid(),
                ),
            )
            return True

    def acquire_directory_lock(
        self,
        path: str,
        *,
        engine: str,
        model: str,
        session_id: str = "",
        reason: str = "",
    ) -> bool:
        """Lock a directory, conflicting with every locked path it contains."""
        return self.acquire_lock(
            _directory_lock_path(path),
            engine=engine,
            model=model,
            session_id=session_id,
            reason=reason,
        )

    def release_lock(self, path: str, *, engine: str, session_id: str = "") -> None:
        """Release a lock held by ``engine``.

        Keys are compared after normalisation so a row written by an earlier
        (non-normalising) build is still releasable. When ``session_id`` is
        given, only that session's row is deleted: a stale worker must not evict
        a newer holder of the same engine and path.
        """
        target = _normalise_lock_path(path)
        with self._transaction():
            rows = self._conn.execute(
                "SELECT path, engine, session_id FROM active_locks"
            ).fetchall()
            stale = [
                stored
                for stored, owner, owner_session in rows
                if owner == engine
                and _normalise_lock_path(stored) == target
                and (not session_id or owner_session == session_id)
            ]
            for stored in stale:
                self._conn.execute("DELETE FROM active_locks WHERE path = ?", (stored,))

    def release_directory_lock(
        self, path: str, *, engine: str, session_id: str = ""
    ) -> None:
        """Release a directory lock acquired by :meth:`acquire_directory_lock`."""
        self.release_lock(
            _directory_lock_path(path), engine=engine, session_id=session_id
        )

    def get_active_locks(self) -> list[dict[str, Any]]:
        self._expire_locks()
        rows = self._conn.execute(
            "SELECT path, engine, model, session_id, locked_at, expires_at, reason FROM active_locks"
        ).fetchall()
        return [
            {
                "path": r[0],
                "locked_by": {"engine": r[1], "model": r[2], "session_id": r[3]},
                "locked_at": r[4],
                "expires_at": r[5],
                "reason": r[6],
            }
            for r in rows
        ]

    # ── Meta / Health ────────────────────────────────────────

    def touch_meta(self, *, engine: str, model: str) -> None:
        now = _now()
        with self._transaction():
            for k, v in (
                ("version", "1.0.0"),
                ("updated_at", now),
                ("updated_by_engine", engine),
                ("updated_by_model", model),
            ):
                self._conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (k, v),
                )

    def integrity_check(self) -> list[str]:
        """PRAGMA integrity_check — returns [] if the DB file is healthy."""
        rows = self._conn.execute("PRAGMA integrity_check").fetchall()
        return [] if rows == [("ok",)] else [r[0] for r in rows]


class _ImmediateTransaction:
    """`BEGIN IMMEDIATE` context manager — real cross-process write isolation."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __enter__(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE")

    def __exit__(self, exc_type: object, *_rest: object) -> bool:
        if exc_type is None:
            self._conn.execute("COMMIT")
        else:
            self._conn.execute("ROLLBACK")
        return False


# ── CLI ────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Solo-Code Shared State Manager (SQLite, local-only)"
    )
    sub = parser.add_subparsers(dest="command")

    show_parser = sub.add_parser("show", help="Display current shared state summary")
    sess_parser = sub.add_parser("sessions", help="Show recent session log")
    sess_parser.add_argument("--limit", type=int, default=5)
    sub.add_parser("locks", help="Show active file locks")
    sub.add_parser("validate", help="Run PRAGMA integrity_check")

    args = parser.parse_args()
    with SharedState() as state:
        if args.command == "show" or args.command is None:
            print(f"Sessions: {len(state.get_recent_sessions(limit=10_000_000))}")
            print(f"Active locks: {len(state.get_active_locks())}")
        elif args.command == "sessions":
            for s in state.get_recent_sessions(args.limit):
                print(f"  [{s['engine']}] {s['timestamp'][:16]} — {s['summary'][:80]}")
        elif args.command == "locks":
            for lock in state.get_active_locks():
                print(
                    f"  {lock['path']} — locked by {lock['locked_by']['engine']} ({lock['locked_by']['model']})"
                )
        elif args.command == "validate":
            errors = state.integrity_check()
            if errors:
                for e in errors:
                    print(f"  ERROR: {e}")
                sys.exit(1)
            print("  OK — valid")
