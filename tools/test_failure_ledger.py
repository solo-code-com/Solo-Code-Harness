"""Tests for tools/failure_ledger.py"""

import json
import sqlite3

from tools.failure_ledger import collect, main, sweep

SHARED_STATE_SCHEMA = """
CREATE TABLE session_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT NOT NULL,
    engine           TEXT NOT NULL,
    model            TEXT NOT NULL,
    session_id       TEXT,
    summary          TEXT NOT NULL,
    features_touched TEXT,
    files_changed    TEXT,
    commits          TEXT,
    verification     TEXT
)
"""

SESSIONS_SCHEMA = """
CREATE TABLE sessions (
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


def _seed_repo(root):
    """Write a fixture repo: 3 session_log rows, 2 sessions, 2 usage, 1 governance."""
    solocode = root / ".solocode"
    solocode.mkdir(parents=True)

    conn = sqlite3.connect(solocode / "shared-state.db")
    conn.execute(SHARED_STATE_SCHEMA)
    conn.executemany(
        "INSERT INTO session_log (timestamp, engine, model, session_id, summary,"
        " features_touched, files_changed, commits, verification)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "2026-10-01T10:00:00+00:00",
                "kilo",
                "kilo/model-a",
                "s1",
                "clean run",
                "[]",
                "[]",
                "[]",
                "{}",
            ),
            (
                "2026-10-01T11:00:00+00:00",
                "opencode",
                "opencode/model-b",
                "s2",
                "crashed run",
                "[]",
                "[]",
                "[]",
                "{}",
            ),
            (
                "2026-10-01T12:00:00+00:00",
                "codex",
                "codex/model-c",
                "s3",
                "gate failed",
                "[]",
                "[]",
                "[]",
                json.dumps({"error": "security scan failed"}),
            ),
        ],
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(solocode / "sessions.db")
    conn.execute(SESSIONS_SCHEMA)
    conn.executemany(
        "INSERT INTO sessions (id, start_time, end_time, branch, commit_hash,"
        " files_changed, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "s1",
                "2026-10-01T10:00:00+00:00",
                "2026-10-01T10:10:00+00:00",
                "main",
                "abc1234",
                3,
                "completed",
            ),
            (
                "s2",
                "2026-10-01T11:00:00+00:00",
                "2026-10-01T11:05:00+00:00",
                "fix/x",
                "def5678",
                1,
                "failed",
            ),
        ],
    )
    conn.commit()
    conn.close()

    (solocode / "opencode-usage.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-10-01T11:00:05+00:00",
                        "model": "opencode/model-b",
                        "elapsed_s": 5.0,
                        "usage": None,
                        "cost": None,
                        "error": "provider timeout",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-10-01T12:05:00+00:00",
                        "model": "opencode/model-b",
                        "elapsed_s": 9.0,
                        "usage": {"total_tokens": 1200},
                        "cost": 0.002,
                        "error": None,
                    }
                ),
                "not json at all",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    logs = root / ".kilo" / "logs"
    logs.mkdir(parents=True)
    (logs / "governance-events.jsonl").write_text(
        json.dumps(
            {
                "eventType": "approval_requested",
                "payload": {"severity": "high"},
                "timestamp": "2026-10-01T13:00:00.000Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def test_report_counts(tmp_path):
    root = _seed_repo(tmp_path)
    report = collect(root)

    assert report["sessions"]["total"] == 3
    assert report["sessions"]["session_records"] == 2
    assert report["sessions"]["by_engine"] == {"kilo": 1, "opencode": 1, "codex": 1}
    assert report["sessions"]["by_status"] == {"completed": 1, "failed": 1}
    assert report["sessions"]["duration"]["count"] == 2
    assert report["sessions"]["duration"]["median_s"] == 450.0
    assert report["sessions"]["duration"]["max_s"] == 600.0


def test_failures_name_the_right_worker(tmp_path):
    root = _seed_repo(tmp_path)
    report = collect(root)

    failures = report["failures"]
    assert failures["total"] == 4
    assert failures["by_worker"] == {
        "sessions": 1,
        "codex": 1,
        "opencode": 1,
        "governance": 1,
    }
    assert failures["by_model"]["opencode/model-b"] == 1

    reasons = {(entry["worker"], entry["reason"]) for entry in failures["recent"]}
    assert ("opencode", "error: provider timeout") in reasons
    assert ("codex", "verification error: security scan failed") in reasons
    assert ("sessions", "status=failed branch=fix/x") in reasons
    assert ("governance", "governance approval_requested (severity=high)") in reasons


def test_cost_and_unmetered_rows(tmp_path):
    root = _seed_repo(tmp_path)
    report = collect(root)

    assert report["cost"]["by_model"] == {"opencode/model-b": 0.002}
    assert report["cost"]["total"] == 0.002
    assert report["cost"]["metered_rows"] == 1
    assert report["cost"]["unmetered_rows"] == 1


def test_malformed_line_is_skipped_not_fatal(tmp_path):
    root = _seed_repo(tmp_path)
    report = collect(root)

    assert any("skipped 1 malformed line" in note for note in report["notes"])
    assert report["failures"]["total"] == 4


def test_collect_writes_nothing(tmp_path):
    root = _seed_repo(tmp_path)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    db_before = (root / ".solocode" / "shared-state.db").read_bytes()

    collect(root)

    after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    assert after == before
    assert (root / ".solocode" / "shared-state.db").read_bytes() == db_before


def test_empty_directory_returns_empty_report(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    report = collect(empty)

    assert report["sessions"]["total"] == 0
    assert report["sessions"]["by_status"] == {}
    assert report["sessions"]["duration"] == {
        "count": 0,
        "median_s": None,
        "max_s": None,
    }
    assert report["failures"]["total"] == 0
    assert report["failures"]["recent"] == []
    assert report["cost"]["total"] == 0
    assert report["notes"]


def test_cli_empty_directory_exits_zero(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()

    assert main([str(empty)]) == 0
    out = capsys.readouterr().out
    assert "Failures: 0 total" in out
    assert "Notes" in out


def test_cli_json_matches_report(tmp_path, capsys):
    root = _seed_repo(tmp_path)

    assert main([str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["failures"]["total"] == 4
    assert payload["sessions"]["total"] == 3


def test_sweep_reads_lock_versions(tmp_path):
    current = tmp_path / "current"
    older = tmp_path / "older"
    for project in (current, older):
        project.mkdir()
    (current / ".harness.lock").write_text(
        '[harness]\nversion = "4.3.1"\n', encoding="utf-8"
    )
    (older / ".harness.lock").write_text(
        '[harness]\nversion = "4.0.0"\n', encoding="utf-8"
    )

    entries, skipped = sweep(tmp_path, "4.3.1")
    projects = {entry["project"]: entry for entry in entries}

    assert projects[str(current)]["version"] == "4.3.1"
    assert projects[str(current)]["matches"] is True
    assert projects[str(older)]["version"] == "4.0.0"
    assert projects[str(older)]["matches"] is False
    assert skipped == 0


def test_workers_section_lists_a_clean_uncosted_worker(tmp_path, capsys):
    """A worker with one clean row and no cost is still reported."""
    root = tmp_path / "repo"
    solocode = root / ".solocode"
    solocode.mkdir(parents=True)
    (solocode / "antigravity-usage.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-10-20T03:53:56+00:00",
                "model": "gemini-3.8-flash-medium",
                "usage": {"total_tokens": 10},
                "cost": None,
                "error": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = collect(root)
    assert report["workers"]["antigravity"] == {
        "total": 1,
        "errors": 0,
        "last": "2026-10-20T03:53:56+00:00",
    }
    assert report["failures"]["total"] == 0
    assert report["cost"]["metered_rows"] == 0

    assert main([str(root)]) == 0
    out = capsys.readouterr().out
    assert "Workers: 1 usage file(s)" in out
    assert "antigravity" in out


def test_sweep_skips_worktree_duplicates(tmp_path, capsys):
    """A .kilo/worktrees checkout carries a lock but is the same install."""
    project = tmp_path / "project"
    worktree = project / ".kilo" / "worktrees" / "project"
    worktree.mkdir(parents=True)
    (project / ".harness.lock").write_text(
        '[harness]\nversion = "4.3.1"\n', encoding="utf-8"
    )
    (worktree / ".harness.lock").write_text(
        '[harness]\nversion = "4.3.1"\n', encoding="utf-8"
    )

    projects, skipped = sweep(tmp_path, "4.3.1")

    assert [entry["project"] for entry in projects] == [str(project)]
    assert skipped == 1

    assert main(["--sweep", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "skipped 1 duplicate worktree install(s)" in out
