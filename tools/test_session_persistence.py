"""Tests for tools/session_persistence.py"""

import pytest

from tools.session_persistence import (
    get_session,
    init_db,
    list_sessions,
    record_session_end,
    record_session_start,
    search_sessions,
)


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "sessions.db"
    init_db(p)
    return p


# ── init_db ────────────────────────────────────────────────────────────────────


def test_init_db_creates_file(tmp_path):
    p = tmp_path / "s.db"
    conn = init_db(p)
    conn.close()
    assert p.exists()


def test_init_db_idempotent(tmp_path):
    p = tmp_path / "s.db"
    init_db(p).close()
    init_db(p).close()  # second call must not raise


# ── record_session_start ───────────────────────────────────────────────────────


def test_record_session_start(db):
    record_session_start("sess-1", "main", "abc1234", path=db)
    sessions = list_sessions(limit=10, path=db)
    assert len(sessions) == 1
    assert sessions[0]["id"] == "sess-1"
    assert sessions[0]["branch"] == "main"


def test_record_session_start_multiple(db):
    record_session_start("sess-1", "main", "aaa", path=db)
    record_session_start("sess-2", "feat/x", "bbb", path=db)
    sessions = list_sessions(limit=10, path=db)
    assert len(sessions) == 2


# ── record_session_end ─────────────────────────────────────────────────────────


def test_record_session_end_updates_status(db):
    record_session_start("sess-1", "main", "abc", path=db)
    record_session_end("sess-1", files_changed=3, status="completed", path=db)
    s = get_session("sess-1", path=db)
    assert s["status"] == "completed"
    assert s["files_changed"] == 3


def test_record_session_end_unknown_session(db):
    # record_session_end raises ValueError for unknown sessions
    with pytest.raises(ValueError, match="unknown session"):
        record_session_end(
            "ghost-session", files_changed=0, status="completed", path=db
        )


# ── get_session ────────────────────────────────────────────────────────────────


def test_get_session_found(db):
    record_session_start("sess-1", "main", "abc", path=db)
    s = get_session("sess-1", path=db)
    assert s is not None
    assert s["id"] == "sess-1"


def test_get_session_not_found(db):
    assert get_session("does-not-exist", path=db) is None


# ── list_sessions ──────────────────────────────────────────────────────────────


def test_list_sessions_empty(db):
    assert list_sessions(path=db) == []


def test_list_sessions_respects_limit(db):
    for i in range(5):
        record_session_start(f"sess-{i}", "main", f"commit{i}", path=db)
    sessions = list_sessions(limit=3, path=db)
    assert len(sessions) == 3


def test_list_sessions_returns_dicts(db):
    record_session_start("sess-1", "main", "abc", path=db)
    sessions = list_sessions(path=db)
    assert isinstance(sessions[0], dict)
    assert "id" in sessions[0]


# ── search_sessions ────────────────────────────────────────────────────────────


def test_search_sessions_by_branch(db):
    record_session_start("sess-1", "main", "a", path=db)
    record_session_start("sess-2", "feat/x", "b", path=db)
    results = search_sessions(branch="feat/x", path=db)
    assert len(results) == 1
    assert results[0]["branch"] == "feat/x"


def test_search_sessions_by_status(db):
    record_session_start("sess-1", "main", "a", path=db)
    record_session_start("sess-2", "main", "b", path=db)
    record_session_end("sess-1", files_changed=1, status="completed", path=db)
    results = search_sessions(status="completed", path=db)
    assert all(r["status"] == "completed" for r in results)


def test_search_sessions_no_filter_returns_all(db):
    record_session_start("sess-1", "main", "a", path=db)
    record_session_start("sess-2", "feat/x", "b", path=db)
    results = search_sessions(path=db)
    assert len(results) == 2


def test_search_sessions_empty_result(db):
    record_session_start("sess-1", "main", "a", path=db)
    results = search_sessions(branch="nonexistent-branch", path=db)
    assert results == []


# ── isolation — separate DBs don't interfere ──────────────────────────────────


def test_separate_dbs_isolated(tmp_path):
    db1 = tmp_path / "a.db"
    db2 = tmp_path / "b.db"
    init_db(db1)
    init_db(db2)
    record_session_start("sess-only-in-a", "main", "x", path=db1)
    assert list_sessions(path=db2) == []
    assert len(list_sessions(path=db1)) == 1
