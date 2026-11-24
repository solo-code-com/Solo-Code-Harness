#!/usr/bin/env python3
"""Tests for tools/shared_state.py — SQLite-backed cross-engine shared state."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from tools import shared_state
from tools.shared_state import SharedState


def test_empty_state():
    """New SharedState with no prior data returns empty state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            assert state.get_features() == []
            assert state.get_active_locks() == []


def test_set_feature_status():
    """Setting feature status persists and retrieves correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            state.set_feature_status(
                "feat-001",
                "in-progress",
                engine="copilot",
                model="deepseek-chat",
                evidence="PR #42 merged",
            )
        with SharedState(path) as state2:
            feat = state2.get_feature("feat-001")
            assert feat is not None
            assert feat["status"] == "in-progress"
            assert feat["owner"]["engine"] == "copilot"
            assert feat["owner"]["model"] == "deepseek-chat"
            assert feat["evidence"] == "PR #42 merged"


def test_update_existing_feature():
    """Updating an existing feature changes status, not creates duplicate."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            state.set_feature_status(
                "feat-001", "not-started", engine="kilo", model="claude"
            )
            state.set_feature_status(
                "feat-001", "completed", engine="opencode", model="gpt-4o"
            )
            features = state.get_features()
            assert len(features) == 1
            assert features[0]["status"] == "completed"
            assert features[0]["owner"]["engine"] == "opencode"


def test_acquire_lock():
    """Acquiring a lock marks the file as locked."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            ok = state.acquire_lock(
                "src/auth.py", engine="copilot", model="deepseek", reason="Fixing bug"
            )
            assert ok is True
            locks = state.get_active_locks()
            assert len(locks) == 1
            assert locks[0]["path"] == "src/auth.py"
            assert locks[0]["locked_by"]["engine"] == "copilot"


def test_lock_conflict():
    """Two different engines cannot lock the same file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            assert (
                state.acquire_lock("src/auth.py", engine="kilo", model="deepseek")
                is True
            )
            assert (
                state.acquire_lock("src/auth.py", engine="copilot", model="gpt-4o")
                is False
            )


def test_directory_lock_conflicts_with_contained_file_lock():
    """A delegated directory scope cannot overlap another engine's file edit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            assert (
                state.acquire_directory_lock("src", engine="gemini", model="flash")
                is True
            )
            assert (
                state.acquire_lock("src/auth.py", engine="copilot", model="gpt-4o")
                is False
            )


def test_file_lock_blocks_containing_directory_lock():
    """Directory locking also detects a pre-existing nested file lock."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            assert (
                state.acquire_lock("src/auth.py", engine="copilot", model="gpt-4o")
                is True
            )
            assert (
                state.acquire_directory_lock("src", engine="gemini", model="flash")
                is False
            )


def test_root_directory_lock_conflicts_with_all_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            assert (
                state.acquire_directory_lock(
                    ".", engine="gemini", model="flash", session_id="a"
                )
                is True
            )
            assert (
                state.acquire_lock(
                    "src/auth.py", engine="copilot", model="gpt", session_id="b"
                )
                is False
            )


def test_same_engine_different_sessions_conflict():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            assert (
                state.acquire_directory_lock(
                    "src", engine="gemini", model="flash", session_id="a"
                )
                is True
            )
            assert (
                state.acquire_directory_lock(
                    "src", engine="gemini", model="flash", session_id="b"
                )
                is False
            )


def test_stale_session_cannot_release_newer_lock():
    """A worker whose lock expired must not evict the newer holder's lock."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            assert (
                state.acquire_directory_lock(
                    "src", engine="gemini", model="m", session_id="old"
                )
                is True
            )
            with state._transaction():
                state._conn.execute(
                    "UPDATE active_locks SET expires_at = '1970-01-01T00:00:00+00:00'"
                )
            assert (
                state.acquire_directory_lock(
                    "src", engine="gemini", model="m", session_id="new"
                )
                is True
            )

            state.release_directory_lock("src", engine="gemini", session_id="old")

            locks = state.get_active_locks()
            assert [lock["locked_by"]["session_id"] for lock in locks] == ["new"]


def test_release_accepts_legacy_unnormalised_key():
    """A row stored before key normalisation still releases with the same raw key."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            with state._transaction():
                state._conn.execute(
                    "INSERT INTO active_locks (path, engine, model, session_id, locked_at,"
                    " expires_at, reason) VALUES (?, 'claude', 'm', '', '2026-01-01T00:00:00+00:00',"
                    " '2099-01-01T00:00:00+00:00', 'legacy')",
                    ("src\\a.py",),
                )

            state.release_lock("src\\a.py", engine="claude")

            assert state.get_active_locks() == []


def test_case_folding_treats_slash_and_case_variants_as_one_lock(monkeypatch):
    """On a case-insensitive filesystem, spelling differences still collide."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        monkeypatch.setattr(shared_state, "_CASE_INSENSITIVE_PATHS", True)
        with SharedState(path) as state:
            assert (
                state.acquire_directory_lock("Src", engine="gemini", model="flash")
                is True
            )
            assert (
                state.acquire_lock("src/auth.py", engine="copilot", model="gpt")
                is False
            )


def test_case_folding_is_off_on_case_sensitive_filesystems(monkeypatch):
    monkeypatch.setattr(shared_state, "_CASE_INSENSITIVE_PATHS", False)
    assert shared_state._lock_paths_conflict("dir:src", "Src/a.py") is False


def test_release_lock():
    """Releasing a lock removes it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            state.acquire_lock("src/auth.py", engine="kilo", model="claude")
            assert len(state.get_active_locks()) == 1
            state.release_lock("src/auth.py", engine="kilo")
            assert len(state.get_active_locks()) == 0


def test_add_session_entry():
    """Session entries are ordered newest first."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            state.add_session_entry(
                engine="copilot",
                model="deepseek-chat",
                summary="Fixed auth bug",
                files_changed=["src/auth.py"],
                verification={"security_scan": True},
            )
            state.add_session_entry(
                engine="kilo",
                model="claude-sonnet",
                summary="Refactored database layer",
                files_changed=["src/db.py"],
            )
            sessions = state.get_recent_sessions(limit=5)
            assert len(sessions) == 2
            assert sessions[0]["engine"] == "kilo"
            assert sessions[1]["engine"] == "copilot"


def test_add_shared_memory():
    """Conventions, gotchas, and decisions are stored correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            state.add_convention(
                "branch_naming", "feature/<slug>", engine="copilot", model="gpt-4o"
            )
            state.add_gotcha(
                "ruff config in .ruff.toml only, not pyproject.toml",
                engine="kilo",
                model="claude",
            )
            state.add_decision(
                "Use SQLite for shared state",
                "Chose SQLite over JSON+manual locking for real cross-process transaction isolation",
                rationale="stdlib only, BEGIN IMMEDIATE gives real write isolation",
                engine="copilot",
                model="gpt-4o",
            )
            mem = state.get_shared_memory()
            assert len(mem["conventions"]) == 1
            assert len(mem["gotchas"]) == 1
            assert len(mem["decisions"]) == 1
            assert mem["conventions"][0]["key"] == "branch_naming"


def test_integrity_check():
    """A fresh database always passes integrity_check."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        with SharedState(path) as state:
            assert state.integrity_check() == []


def test_concurrent_lock_acquire():
    """Two threads racing to lock the same path — exactly one must win, no crash."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "shared-state.db"
        # Khởi tạo schema TRƯỚC khi spawn thread — tránh race điều kiện lúc tạo
        # file .db lần đầu (PRAGMA/executescript ngoài transaction BEGIN IMMEDIATE),
        # để phần concurrent chỉ còn kiểm tra đúng 1 thứ: tranh chấp acquire_lock.
        SharedState(path).close()

        results: list[bool] = []
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def worker(engine: str) -> None:
            try:
                with SharedState(path) as state:
                    # timeout để không bao giờ treo vô hạn nếu thread kia lỗi
                    # trước khi tới barrier — biến hang thành fail rõ ràng.
                    barrier.wait(timeout=10)
                    results.append(
                        state.acquire_lock("src/shared.py", engine=engine, model="test")
                    )
            except Exception as e:  # noqa: BLE001 — test must observe any crash, not hide it
                errors.append(e)

        t1 = threading.Thread(target=worker, args=("kilo",))
        t2 = threading.Thread(target=worker, args=("copilot",))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not t1.is_alive(), (
            "Thread 'kilo' did not finish within timeout — possible deadlock"
        )
        assert not t2.is_alive(), (
            "Thread 'copilot' did not finish within timeout — possible deadlock"
        )
        assert errors == [], (
            f"No exception should be raised during lock contention, got: {errors}"
        )
        assert sorted(results) == [False, True], (
            f"Exactly one thread should win the lock, got: {results}"
        )
