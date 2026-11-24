#!/usr/bin/env python3
"""
session_persistence.py — SQLite-based session tracking system.

Records agent session lifecycles into .solocode/sessions.db (local-only,
gitignored via the existing `.solocode/` rule). Each session row captures
start/end timestamps, the git branch + commit at start, how many files were
changed, the closing status, and an arbitrary JSON metadata blob.

Session IDs follow the harness convention from .kilo/hooks/session/session-start.js:
engine-provided IDs (KILO_SESSION_ID / CLAUDE_SESSION_ID) or a
`session-<epoch-millis>` fallback.

Usage:
    python tools/session_persistence.py --self-test          # validate against a temp DB
    python tools/session_persistence.py --list [--limit N]
    python tools/session_persistence.py --get SESSION_ID
    python tools/session_persistence.py --search [--branch B] [--status S]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / ".solocode" / "sessions.db"
DEFAULT_STATUS = "active"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    start_time TEXT NOT NULL,
    end_time TEXT,
    branch TEXT,
    commit_hash TEXT,
    files_changed INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    metadata TEXT
)
"""


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ── Core Functions ───────────────────────────────────────────────────────────


def init_db(path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the sessions SQLite database.

    Creates the parent directory and the ``sessions`` table on first run.
    Uses WAL journal mode so concurrent readers/writers do not block each
    other, matching the pattern in tools/shared_state.py.

    Args:
        path: Path to the SQLite database file. Defaults to the module-level
            ``DB_PATH`` (``.solocode/sessions.db``). Pass an explicit path in
            tests to avoid touching production data.

    Returns:
        An open :class:`sqlite3.Connection` with the schema guaranteed to exist.

    Raises:
        sqlite3.Error: If the database cannot be opened or the schema cannot
            be created (e.g. the parent path is a file, or the file is corrupt).
    """
    resolved = path if path is not None else DB_PATH
    resolved.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(resolved, isolation_level=None, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA_SQL)
    except sqlite3.Error as exc:
        raise sqlite3.Error(
            f"Failed to initialize sessions database at {resolved}: {exc}"
        ) from exc
    return conn


def record_session_start(
    session_id: str, branch: str, commit: str, *, path: Path | None = None
) -> None:
    """Record the start of a session.

    Inserts a new row with ``status`` = ``active`` and the current UTC time
    as ``start_time``. Re-inserting an existing session id fails with a
    primary-key conflict, so callers must use unique ids.

    Args:
        session_id: Unique session identifier (see module docstring for format).
        branch: Git branch the session started on (``"unknown"`` if unavailable).
        commit: Short commit hash the session started on (``"unknown"`` if unavailable).

    Raises:
        sqlite3.Error: If the write fails (including a duplicate ``session_id``,
            which surfaces as a ``sqlite3.IntegrityError``).
    """
    conn = init_db(path)
    try:
        conn.execute(
            """
            INSERT INTO sessions (id, start_time, branch, commit_hash, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, _now(), branch, commit, DEFAULT_STATUS),
        )
    finally:
        conn.close()


def record_session_end(
    session_id: str, files_changed: int, status: str, *, path: Path | None = None
) -> None:
    """Record the end of a session.

    Sets ``end_time``, ``files_changed``, and ``status`` on the row created
    by :func:`record_session_start`. Missing sessions are reported instead of
    silently ignored so a stray end-record does not mask a lost start.

    Args:
        session_id: Session id passed to :func:`record_session_start`.
        files_changed: Number of files modified during the session.
        status: Closing status (e.g. ``"completed"``, ``"blocked"``, ``"interrupted"``).

    Raises:
        ValueError: If ``session_id`` does not exist in the database.
        sqlite3.Error: If the update itself fails.
    """
    conn = init_db(path)
    try:
        existing = conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(
                f"Cannot end unknown session '{session_id}'. "
                "Call record_session_start() before record_session_end()."
            )
        conn.execute(
            """
            UPDATE sessions
            SET end_time = ?, files_changed = ?, status = ?
            WHERE id = ?
            """,
            (_now(), files_changed, status, session_id),
        )
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sessions row to a plain dict, decoding the JSON metadata blob.

    Args:
        row: A row returned by the sessions queries.

    Returns:
        Dict with keys matching the table columns; ``metadata`` is a parsed
        dict (or ``{}`` when the column is NULL/unparseable).
    """
    result = dict(row)
    raw = result.get("metadata")
    if raw:
        try:
            result["metadata"] = json.loads(raw)
        except json.JSONDecodeError:
            result["metadata"] = {}
    else:
        result["metadata"] = {}
    return result


def list_sessions(limit: int = 10, *, path: Path | None = None) -> list[dict[str, Any]]:
    """List the most recent sessions, newest first.

    Args:
        limit: Maximum number of sessions to return. Must be >= 1.

    Returns:
        List of session dicts ordered by ``start_time`` descending.

    Raises:
        ValueError: If ``limit`` is less than 1.
    """
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")
    conn = init_db(path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, start_time, end_time, branch, commit_hash,
                   files_changed, status, metadata
            FROM sessions ORDER BY start_time DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(row) for row in rows]


def get_session(session_id: str, *, path: Path | None = None) -> dict[str, Any] | None:
    """Fetch a single session by id.

    Args:
        session_id: Session id previously passed to :func:`record_session_start`.
        path: Path to the SQLite database file. Defaults to ``DB_PATH``.

    Returns:
        The session dict, or ``None`` if no such session exists.
    """
    conn = init_db(path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, start_time, end_time, branch, commit_hash,
                   files_changed, status, metadata
            FROM sessions WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row is not None else None


def search_sessions(
    branch: str | None = None, status: str | None = None, *, path: Path | None = None
) -> list[dict[str, Any]]:
    """Search sessions by git branch and/or closing status.

    Filters are combined with AND; omitted filters match everything. Both
    match with exact equality, so pass the exact branch name / status value.

    Args:
        branch: Git branch to filter on, or ``None`` for any branch.
        status: Session status to filter on, or ``None`` for any status.

    Returns:
        List of matching session dicts ordered by ``start_time`` descending.
    """
    clauses: list[str] = []
    params: list[str] = []
    if branch is not None:
        clauses.append("branch = ?")
        params.append(branch)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = init_db(path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(  # noqa: S608
            f"""
            SELECT id, start_time, end_time, branch, commit_hash,
                   files_changed, status, metadata
            FROM sessions {where} ORDER BY start_time DESC
            """,
            tuple(params),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(row) for row in rows]


# ── Self-Test ────────────────────────────────────────────────────────────────


def run_self_test() -> bool:
    """Exercise every public function against a throwaway temporary database.

    Creates its own DB under the system temp dir so the real
    ``.solocode/sessions.db`` is never touched. Verifies insert, end-record,
    list ordering, exact lookup, and branch/status filtering.

    Returns:
        True if all checks pass, False otherwise.
    """
    print("Running self-test...")

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sessions.db"

        # init_db must be idempotent
        conn = init_db(db)
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()
        if "sessions" not in tables:
            print("[FAIL] schema not created", file=sys.stderr)
            return False

        try:
            record_session_start("sess-test-1", "main", "abc1234", path=db)
            record_session_start("sess-test-2", "feature/x", "def5678", path=db)
        except sqlite3.Error as exc:
            print(f"[FAIL] record_session_start: {exc}", file=sys.stderr)
            return False

        # End only the first session
        try:
            record_session_end("sess-test-1", 3, "completed", path=db)
        except (ValueError, sqlite3.Error) as exc:
            print(f"[FAIL] record_session_end: {exc}", file=sys.stderr)
            return False

        # Ending an unknown session must raise
        try:
            record_session_end("sess-ghost", 0, "completed", path=db)
            print("[FAIL] expected ValueError for unknown session", file=sys.stderr)
            return False
        except ValueError:
            pass

        listed = list_sessions(limit=5, path=db)
        if len(listed) != 2:
            print(f"[FAIL] list_sessions returned {len(listed)} rows", file=sys.stderr)
            return False
        if listed[0]["id"] != "sess-test-2":
            print(
                f"[FAIL] list_sessions not newest-first: {listed[0]['id']}",
                file=sys.stderr,
            )
            return False

        got = get_session("sess-test-1", path=db)
        if got is None:
            print(
                "[FAIL] get_session returned None for existing session", file=sys.stderr
            )
            return False
        if got["status"] != "completed" or got["files_changed"] != 3:
            print(f"[FAIL] get_session fields wrong: {got}", file=sys.stderr)
            return False
        if get_session("sess-ghost", path=db) is not None:
            print(
                "[FAIL] get_session should be None for unknown session", file=sys.stderr
            )
            return False

        by_branch = search_sessions(branch="feature/x", path=db)
        if len(by_branch) != 1 or by_branch[0]["id"] != "sess-test-2":
            print(f"[FAIL] search by branch: {by_branch}", file=sys.stderr)
            return False

        by_status = search_sessions(status="completed", path=db)
        if len(by_status) != 1 or by_status[0]["id"] != "sess-test-1":
            print(f"[FAIL] search by status: {by_status}", file=sys.stderr)
            return False

        if search_sessions(branch="main", status="completed", path=db) != by_status:
            print("[FAIL] combined branch+status search", file=sys.stderr)
            return False

        if search_sessions(path=db) != listed:
            print("[FAIL] unfiltered search should match list", file=sys.stderr)
            return False

    print("[OK] All self-tests passed!")
    return True


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Solo-Code session tracking (SQLite, local-only)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--self-test", action="store_true", help="Validate against a temp database"
    )
    group.add_argument("--list", action="store_true", help="List recent sessions")
    group.add_argument(
        "--get", metavar="SESSION_ID", help="Show a single session by id"
    )
    group.add_argument(
        "--search",
        action="store_true",
        help="Search sessions (combine with --branch/--status)",
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="Max sessions for --list (default 10)"
    )
    parser.add_argument("--branch", type=str, help="Branch filter for --search")
    parser.add_argument("--status", type=str, help="Status filter for --search")

    args = parser.parse_args()

    if args.self_test:
        return 0 if run_self_test() else 1

    if args.list:
        try:
            sessions = list_sessions(limit=args.limit)
        except ValueError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 1
        for s in sessions:
            print(
                f"  {s['id']} [{s['status']}] {s['start_time'][:19]} "
                f"{s['branch'] or '-'} @ {s['commit_hash'] or '-'} "
                f"files={s['files_changed']}"
            )
        return 0

    if args.get:
        session = get_session(args.get)
        if session is None:
            print(f"[FAIL] Session not found: {args.get}", file=sys.stderr)
            return 1
        print(json.dumps(session, indent=2, default=str))
        return 0

    if args.search:
        if args.branch is None and args.status is None:
            print("Error: --search requires --branch and/or --status", file=sys.stderr)
            return 1
        sessions = search_sessions(branch=args.branch, status=args.status)
        for s in sessions:
            print(
                f"  {s['id']} [{s['status']}] {s['start_time'][:19]} "
                f"{s['branch'] or '-'} @ {s['commit_hash'] or '-'}"
            )
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
