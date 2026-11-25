#!/usr/bin/env python3
"""Unit tests for tools/retention.py."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from tools import retention  # noqa: E402


def _make_session(root: Path, name: str, mtime: float) -> Path:
    path = root / retention.SESSIONS_DIR / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": name}) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _make_restore(root: Path, name: str, mtime: float) -> Path:
    snap = root / retention.RESTORE_DIR / name
    snap.mkdir(parents=True, exist_ok=True)
    data = snap / "manifest.json"
    data.write_text(json.dumps({"run_id": name}) + "\n", encoding="utf-8")
    os.utime(data, (mtime, mtime))
    os.utime(snap, (mtime, mtime))
    return snap


def test_dry_run_changes_nothing(tmp_path: Path, capsys) -> None:
    """1. plan and CLI without --apply leave every file intact and create no archive."""
    base_time = time.time() - 1000
    sessions = [_make_session(tmp_path, f"s{i}", base_time + i * 10) for i in range(4)]
    restores = [_make_restore(tmp_path, f"r{i}", base_time + i * 10) for i in range(4)]
    usage = tmp_path / ".solocode" / "kilo-usage.jsonl"
    usage.write_text("\n".join(f"line-{i}" for i in range(10)) + "\n", encoding="utf-8")

    planned = retention.plan(
        tmp_path, keep_restore=1, keep_sessions=1, keep_usage_lines=3
    )
    assert len(planned["restore"]) == 3
    assert len(planned["sessions"]) == 3
    assert len(planned["usage"]) == 1

    code = retention.main(
        [
            "--root",
            str(tmp_path),
            "--keep-restore",
            "1",
            "--keep-sessions",
            "1",
            "--keep-usage-lines",
            "3",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "restore" in out
    assert "sessions" in out
    assert "usage" in out

    for path in sessions:
        assert path.is_file()
    for snap in restores:
        assert snap.is_dir()
    assert len(usage.read_text(encoding="utf-8").splitlines()) == 10
    assert not (tmp_path / retention.ARCHIVE_DIR).exists()


def test_reviews_and_task_receipts_are_never_planned(tmp_path: Path) -> None:
    """2. Old files in reviews and task-receipts are never planned and survive apply."""
    old_time = time.time() - (365 * 86400)
    review_file = tmp_path / ".solocode" / "reviews" / "review-1.json"
    review_file.parent.mkdir(parents=True, exist_ok=True)
    review_file.write_text('{"verdict": "pass"}\n', encoding="utf-8")
    os.utime(review_file, (old_time, old_time))

    receipt_file = tmp_path / ".solocode" / "task-receipts" / "receipt-1.json"
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_file.write_text('{"status": "VERIFIED"}\n', encoding="utf-8")
    os.utime(receipt_file, (old_time, old_time))

    _make_session(tmp_path, "old-session", old_time)

    planned = retention.plan(
        tmp_path, keep_restore=0, keep_sessions=0, keep_usage_lines=0
    )
    all_planned = (
        planned["restore"]
        + planned["sessions"]
        + [item["path"] for item in planned["usage"]]
    )
    assert all("reviews" not in entry for entry in all_planned)
    assert all("task-receipts" not in entry for entry in all_planned)

    retention.apply(tmp_path, planned)
    assert review_file.is_file()
    assert receipt_file.is_file()


def test_keeps_newest_sessions(tmp_path: Path) -> None:
    """3. Five session files with distinct mtimes: keep 2 newest, archive other 3."""
    base = 1_700_000_000.0
    files = [_make_session(tmp_path, f"sess-{i}", base + i * 60) for i in range(5)]

    planned = retention.plan(tmp_path, keep_sessions=2)
    assert len(planned["sessions"]) == 3

    retention.apply(tmp_path, planned)

    for old_file in files[:3]:
        assert not old_file.exists()
    for newest_file in files[3:]:
        assert newest_file.is_file()

    archived = list((tmp_path / retention.ARCHIVE_DIR / "sessions").rglob("*.json"))
    assert sorted(p.name for p in archived) == [
        "sess-0.json",
        "sess-1.json",
        "sess-2.json",
    ]


def test_keeps_newest_restore_snapshots(tmp_path: Path) -> None:
    """4. Five restore snapshots with distinct mtimes: keep 2 newest, archive other 3."""
    base = 1_700_000_000.0
    snaps = [_make_restore(tmp_path, f"snap-{i}", base + i * 60) for i in range(5)]

    planned = retention.plan(tmp_path, keep_restore=2)
    assert len(planned["restore"]) == 3

    retention.apply(tmp_path, planned)

    for old_snap in snaps[:3]:
        assert not old_snap.exists()
    for newest_snap in snaps[3:]:
        assert newest_snap.is_dir()

    archived_manifests = list(
        (tmp_path / retention.ARCHIVE_DIR / "restore").rglob("manifest.json")
    )
    assert sorted(p.parent.name for p in archived_manifests) == [
        "snap-0",
        "snap-1",
        "snap-2",
    ]


def test_usage_log_rotation_keeps_the_tail(tmp_path: Path) -> None:
    """5. Usage log with 10 lines, keep 5: live keeps last 5, archive keeps first 5."""
    usage = tmp_path / ".solocode" / "opencode-usage.jsonl"
    usage.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'{{"seq": {i}}}\n' for i in range(10)]
    usage.write_text("".join(lines), encoding="utf-8")

    sizes = retention.usage_size(tmp_path)
    assert sizes == {"opencode-usage.jsonl": usage.stat().st_size}

    planned = retention.plan(tmp_path, keep_usage_lines=5)
    assert len(planned["usage"]) == 1
    assert planned["usage"][0]["keep_lines"] == 5
    assert planned["usage"][0]["move_lines"] == 5

    retention.apply(tmp_path, planned)

    live_lines = usage.read_text(encoding="utf-8").splitlines(keepends=True)
    assert live_lines == lines[5:]

    archived_logs = list(
        (tmp_path / retention.ARCHIVE_DIR / "usage").rglob("opencode-usage.jsonl")
    )
    assert len(archived_logs) == 1
    archived_lines = (
        archived_logs[0].read_text(encoding="utf-8").splitlines(keepends=True)
    )
    assert archived_lines == lines[:5]


def test_purge_deletes_only_inside_the_archive(tmp_path: Path) -> None:
    """6. Purge older_than_days=1 removes old archive entry and keeps recent & live files."""
    now = time.time()
    old_mtime = now - (5 * 86400)
    recent_mtime = now - 60

    archive_sessions = tmp_path / retention.ARCHIVE_DIR / "sessions"
    old_batch = archive_sessions / "20200101T000000-000000Z"
    old_batch.mkdir(parents=True, exist_ok=True)
    old_entry = old_batch / "old.json"
    old_entry.write_text("{}\n", encoding="utf-8")
    os.utime(old_entry, (old_mtime, old_mtime))
    os.utime(old_batch, (old_mtime, old_mtime))

    recent_batch = archive_sessions / "20261125T000000-000000Z"
    recent_batch.mkdir(parents=True, exist_ok=True)
    recent_entry = recent_batch / "recent.json"
    recent_entry.write_text("{}\n", encoding="utf-8")
    os.utime(recent_entry, (recent_mtime, recent_mtime))
    os.utime(recent_batch, (recent_mtime, recent_mtime))

    # An old live file outside archive must never be touched by purge.
    old_review = tmp_path / ".solocode" / "reviews" / "keep-me.json"
    old_review.parent.mkdir(parents=True, exist_ok=True)
    old_review.write_text("{}\n", encoding="utf-8")
    os.utime(old_review, (old_mtime, old_mtime))

    removed = retention.purge(tmp_path, older_than_days=1)
    assert len(removed) == 1
    assert not old_batch.exists()
    assert recent_entry.is_file()
    assert old_review.is_file()


def test_protected_dirs_refused_even_when_asked(tmp_path: Path) -> None:
    """7. apply refuses plans naming reviews or task-receipts, moving nothing."""
    review_file = tmp_path / ".solocode" / "reviews" / "r1.json"
    review_file.parent.mkdir(parents=True, exist_ok=True)
    review_file.write_text("{}\n", encoding="utf-8")

    receipt_file = tmp_path / ".solocode" / "task-receipts" / "t1.json"
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_file.write_text("{}\n", encoding="utf-8")

    sess = _make_session(tmp_path, "s1", time.time() - 100)

    bad_plans = [
        {
            "reviews": [".solocode/reviews/r1.json"],
            "sessions": [".solocode/sessions/s1.json"],
        },
        {
            "restore": [".solocode/reviews/r1.json"],
            "sessions": [".solocode/sessions/s1.json"],
        },
        {"sessions": [".solocode/task-receipts/t1.json"]},
    ]
    for bad in bad_plans:
        with pytest.raises(ValueError):
            retention.apply(tmp_path, bad)

    assert review_file.is_file()
    assert receipt_file.is_file()
    assert sess.is_file()
    assert not (tmp_path / retention.ARCHIVE_DIR).exists()


def test_cli_json_apply_and_purge(tmp_path: Path, capsys) -> None:
    """8. CLI --purge without --apply previews only; --apply --purge deletes old archive."""
    now = time.time()
    _make_session(tmp_path, "s-old", now - 200)
    _make_session(tmp_path, "s-new", now - 10)

    old_archive = tmp_path / retention.ARCHIVE_DIR / "sessions" / "old-batch"
    old_archive.mkdir(parents=True, exist_ok=True)
    old_file = old_archive / "gone.json"
    old_file.write_text("{}\n", encoding="utf-8")
    old_mtime = now - (10 * 86400)
    os.utime(old_file, (old_mtime, old_mtime))
    os.utime(old_archive, (old_mtime, old_mtime))

    code_dry = retention.main(
        [
            "--root",
            str(tmp_path),
            "--keep-sessions",
            "1",
            "--purge",
            "1",
            "--json",
        ]
    )
    assert code_dry == 0
    dry_payload = json.loads(capsys.readouterr().out)
    assert dry_payload["dry_run"] is True
    assert len(dry_payload["purge_plan"]) == 1
    assert old_archive.exists()
    assert (tmp_path / retention.SESSIONS_DIR / "s-old.json").is_file()

    code_apply = retention.main(
        [
            "--root",
            str(tmp_path),
            "--keep-sessions",
            "1",
            "--apply",
            "--purge",
            "1",
            "--json",
        ]
    )
    assert code_apply == 0
    apply_payload = json.loads(capsys.readouterr().out)
    assert apply_payload["dry_run"] is False
    assert apply_payload["applied"]["counts"]["sessions"] == 1
    assert len(apply_payload["purged"]) == 1
    assert not old_archive.exists()
    assert not (tmp_path / retention.SESSIONS_DIR / "s-old.json").exists()
    assert (tmp_path / retention.SESSIONS_DIR / "s-new.json").is_file()
