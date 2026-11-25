#!/usr/bin/env python3
"""Tests for tools/portfolio.py."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from tools import portfolio


def _write_lock(
    project_dir: Path,
    *,
    version: str | None = "4.3.1",
    generated_at: str | None = "2026-11-25T00:00:00+00:00",
    fingerprint: str | None = "sha256:abc",
) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    lines = ["[harness]"]
    if version is not None:
        lines.append(f'version = "{version}"')
    if generated_at is not None:
        lines.append(f'generated_at = "{generated_at}"')
    if fingerprint is not None:
        lines.append(f'content_fingerprint = "{fingerprint}"')
    lines.append("[boundaries]")
    lines.append('version = "ignore-me"')
    lock_path = project_dir / portfolio.LOCK_NAME
    lock_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lock_path


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_sync_reads_a_fake_project(tmp_path: Path) -> None:
    scan_root = tmp_path / "portfolio"
    alpha = scan_root / "alpha"
    ref_lock = _write_lock(
        tmp_path / "ref",
        version="4.3.1",
        generated_at="2026-11-25T00:00:00+00:00",
        fingerprint="sha256:aaa",
    )
    _write_lock(
        alpha,
        version="4.3.1",
        generated_at="2026-11-25T00:00:00+00:00",
        fingerprint="sha256:aaa",
    )
    _write_jsonl(
        alpha / ".solocode" / "opencode-usage.jsonl",
        [
            {
                "ts": "2026-11-25T01:00:00+00:00",
                "model": "gpt-5",
                "agent": "jev",
                "prompt_chars": 250,
                "elapsed_s": 0,
                "phase": "start",
                "run_id": "run-1",
            },
            {
                "ts": "2026-11-25T01:00:10+00:00",
                "model": "gpt-5",
                "agent": "jev",
                "prompt_chars": 250,
                "elapsed_s": 10.2,
                "usage": {"input": 120, "output": 45, "cache": {"read": 30}},
                "cost": 0.012,
                "error": None,
                "phase": "finish",
                "run_id": "run-1",
            },
        ],
    )
    db_path = tmp_path / "portfolio.db"

    counts = portfolio.sync([scan_root], db_path)
    assert counts["projects"] == 1
    assert counts["runs"] == 1
    assert counts["unfinished"] == 0

    statuses = portfolio.status_rows(db_path, reference=ref_lock)
    assert len(statuses) == 1
    assert statuses[0]["version"] == "4.3.1"

    usages = portfolio.usage_rows(db_path)
    assert len(usages) == 1
    assert usages[0]["prompt_chars"] == 250
    assert usages[0]["input_tokens"] == 120
    assert usages[0]["output_tokens"] == 45


def test_failures_and_unfinished_runs(tmp_path: Path) -> None:
    scan_root = tmp_path / "portfolio"
    beta = scan_root / "beta"
    _write_lock(beta)
    _write_jsonl(
        beta / ".solocode" / "antigravity-usage.jsonl",
        [
            {
                "ts": "2026-11-25T02:00:00+00:00",
                "model": "gemini",
                "prompt_chars": 100,
                "elapsed_s": 120.0,
                "error": "Timeout after 120s",
                "phase": "finish",
                "run_id": "r-fail",
            },
            {
                "ts": "2026-11-25T02:05:00+00:00",
                "model": "gemini",
                "prompt_chars": 80,
                "elapsed_s": 5.0,
                "usage": {"input_tokens": 50, "output_tokens": 10},
                "error": None,
                "phase": "finish",
                "run_id": "r-ok",
            },
            {
                "ts": "2026-11-25T02:10:00+00:00",
                "model": "gemini",
                "prompt_chars": 90,
                "elapsed_s": 0,
                "phase": "start",
                "run_id": "r-lost",
            },
        ],
    )
    db_path = tmp_path / "portfolio.db"

    portfolio.sync([scan_root], db_path)
    failures = portfolio.failure_rows(db_path)
    assert len(failures) == 1
    assert failures[0]["failures"] == 1
    assert failures[0]["unfinished"] == 1
    assert failures[0]["error_text"] == "Timeout after 120s"
    assert failures[0]["same_error_count"] == 1


def test_sync_is_idempotent_and_rebuild_clears(tmp_path: Path) -> None:
    scan_root = tmp_path / "portfolio"
    gamma = scan_root / "gamma"
    _write_lock(gamma)
    _write_jsonl(
        gamma / ".solocode" / "kilo-usage.jsonl",
        [
            {
                "ts": "2026-11-25T03:00:00+00:00",
                "model": "kilo-model",
                "prompt_chars": 50,
                "elapsed_s": 1.0,
                "usage": {"input": 10, "output": 5},
                "phase": "finish",
                "run_id": "k-1",
            }
        ],
    )
    db_path = tmp_path / "portfolio.db"

    portfolio.sync([scan_root], db_path)
    second = portfolio.sync([scan_root], db_path)
    assert second["runs"] == 1

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        conn.execute("UPDATE projects SET scanned_at = 'stale' WHERE name = 'gamma'")
        conn.execute(
            "INSERT INTO projects (name, path, scanned_at) VALUES ('ghost', '/ghost', 'stale')"
        )
        conn.commit()

    rebuilt = portfolio.sync([scan_root], db_path, rebuild=True)
    assert rebuilt["projects"] == 1
    assert rebuilt["runs"] == 1
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute("SELECT name, scanned_at FROM projects").fetchall()
        assert len(row) == 1
        assert row[0][0] == "gamma"
        assert row[0][1] != "stale"
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_status_flags_a_fingerprint_mismatch(tmp_path: Path) -> None:
    scan_root = tmp_path / "portfolio"
    delta = scan_root / "delta"
    ref_lock = _write_lock(
        tmp_path / "ref",
        version="4.3.1",
        fingerprint="sha256:A",
    )
    _write_lock(delta, version="4.3.1", fingerprint="sha256:B")
    db_path = tmp_path / "portfolio.db"

    portfolio.sync([scan_root], db_path)
    rows = portfolio.status_rows(db_path, reference=ref_lock)
    assert rows[0]["drift"] == "differs"
    assert portfolio.has_drift(rows) is True

    _write_lock(delta, version="4.3.1", fingerprint="sha256:A")
    portfolio.sync([scan_root], db_path)
    rows_equal = portfolio.status_rows(db_path, reference=ref_lock)
    assert rows_equal[0]["drift"] == "same"
    assert portfolio.has_drift(rows_equal) is False


def test_roots_are_unioned_and_deduplicated(tmp_path: Path) -> None:
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    _write_lock(root_a / "zulu")
    _write_lock(root_b / "alpha")

    found = portfolio.iter_projects([root_a, root_b])
    assert [p.name for p in found] == ["alpha", "zulu"]

    deduped = portfolio.iter_projects([root_a, root_b, root_a])
    assert [p.name for p in deduped] == ["alpha", "zulu"]

    skipped = tmp_path / "skipped"
    _write_lock(skipped)
    assert [p.name for p in portfolio.iter_projects([root_a, root_b, tmp_path])] == [
        "alpha",
        "skipped",
        "zulu",
    ]
    after_skip = portfolio.iter_projects([root_a, root_b, tmp_path], skip=skipped)
    assert [p.name for p in after_skip] == ["alpha", "zulu"]

    db_path = tmp_path / "portfolio.db"
    counts = portfolio.sync([root_a, root_b], db_path)
    assert counts["scanned"] == 2
    assert counts["projects"] == 2


def test_sync_prunes_projects_that_are_no_longer_scanned(tmp_path: Path) -> None:
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    alpha = root_a / "alpha"
    beta = root_b / "beta"
    _write_lock(alpha)
    _write_lock(beta)
    _write_jsonl(
        alpha / ".solocode" / "opencode-usage.jsonl",
        [
            {
                "ts": "2026-11-25T04:00:00+00:00",
                "model": "gpt-5",
                "prompt_chars": 40,
                "elapsed_s": 1.0,
                "usage": {"input": 10, "output": 5},
                "phase": "finish",
                "run_id": "a-1",
            }
        ],
    )
    _write_jsonl(
        beta / ".solocode" / "opencode-usage.jsonl",
        [
            {
                "ts": "2026-11-25T04:05:00+00:00",
                "model": "gpt-5",
                "prompt_chars": 60,
                "elapsed_s": 2.0,
                "usage": {"input": 20, "output": 8},
                "phase": "finish",
                "run_id": "b-1",
            }
        ],
    )

    db_prune = tmp_path / "prune.db"
    portfolio.sync([root_a], db_prune)
    first_rows = portfolio.status_rows(db_prune, reference=alpha / portfolio.LOCK_NAME)
    assert [r["name"] for r in first_rows] == ["alpha"]

    second_counts = portfolio.sync([root_b], db_prune)
    assert second_counts["pruned"] == 1
    second_rows = portfolio.status_rows(db_prune, reference=beta / portfolio.LOCK_NAME)
    assert [r["name"] for r in second_rows] == ["beta"]
    assert second_rows[0]["runs"] == 1
    with closing(sqlite3.connect(db_prune)) as conn:
        run_projects = [
            r[0] for r in conn.execute("SELECT project FROM runs").fetchall()
        ]
        assert run_projects == ["beta"]

    empty_counts = portfolio.sync([tmp_path / "missing_root"], db_prune)
    assert empty_counts["scanned"] == 0
    assert empty_counts["pruned"] == 0
    assert empty_counts["projects"] == 1

    db_keep = tmp_path / "keep.db"
    portfolio.sync([root_a], db_keep, prune=False)
    keep_first = portfolio.status_rows(db_keep, reference=alpha / portfolio.LOCK_NAME)
    assert [r["name"] for r in keep_first] == ["alpha"]

    keep_counts = portfolio.sync([root_b], db_keep, prune=False)
    assert keep_counts["pruned"] == 0
    keep_both = portfolio.status_rows(db_keep, reference=beta / portfolio.LOCK_NAME)
    assert [r["name"] for r in keep_both] == ["alpha", "beta"]


def test_idless_lines_are_skipped(tmp_path: Path) -> None:
    scan_root = tmp_path / "portfolio"
    alpha = scan_root / "alpha"
    _write_lock(alpha)
    _write_jsonl(
        alpha / ".solocode" / "opencode-usage.jsonl",
        [
            {
                "ts": "2026-11-25T05:00:00+00:00",
                "model": "gpt-5",
                "prompt_chars": 100,
                "elapsed_s": 0,
                "phase": "start",
            },
            {
                "ts": "2026-11-25T05:01:00+00:00",
                "model": "gpt-5",
                "prompt_chars": 100,
                "elapsed_s": 5.0,
                "error": "boom",
                "phase": "finish",
            },
        ],
    )
    db_path = tmp_path / "portfolio.db"

    counts = portfolio.sync([scan_root], db_path)
    assert counts["runs"] == 0
    assert counts["skipped"] == 2
    assert portfolio.failure_rows(db_path) == []


def test_a_run_keeps_its_key_when_an_earlier_line_is_dropped(tmp_path: Path) -> None:
    scan_root = tmp_path / "portfolio"
    alpha = scan_root / "alpha"
    _write_lock(alpha)
    usage_path = alpha / ".solocode" / "opencode-usage.jsonl"
    idless_row: dict[str, object] = {
        "ts": "2026-11-25T06:00:00+00:00",
        "model": "gpt-5",
        "prompt_chars": 50,
        "elapsed_s": 1.0,
        "phase": "finish",
    }
    start_row: dict[str, object] = {
        "ts": "2026-11-25T06:01:00+00:00",
        "model": "gpt-5",
        "prompt_chars": 120,
        "elapsed_s": 0,
        "phase": "start",
        "run_id": "run-keep",
    }
    finish_row: dict[str, object] = {
        "ts": "2026-11-25T06:01:10+00:00",
        "model": "gpt-5",
        "prompt_chars": 120,
        "elapsed_s": 10.0,
        "usage": {"input": 30, "output": 15},
        "error": None,
        "phase": "finish",
        "run_id": "run-keep",
    }
    _write_jsonl(usage_path, [idless_row, start_row, finish_row])
    db_path = tmp_path / "portfolio.db"

    first = portfolio.sync([scan_root], db_path)
    assert first["runs"] == 1
    assert first["unfinished"] == 0
    assert first["skipped"] == 1

    _write_jsonl(usage_path, [start_row, finish_row])
    second = portfolio.sync([scan_root], db_path)
    assert second["runs"] == first["runs"]
    assert second["unfinished"] == first["unfinished"]
    assert second["failures"] == first["failures"]
    assert second["projects"] == first["projects"]
    assert second["skipped"] == 0


def test_failure_rows_separates_total_failures_from_same_error_count(
    tmp_path: Path,
) -> None:
    scan_root = tmp_path / "portfolio"
    alpha = scan_root / "alpha"
    _write_lock(alpha)
    _write_jsonl(
        alpha / ".solocode" / "opencode-usage.jsonl",
        [
            {
                "ts": f"2026-11-25T07:0{i}:00+00:00",
                "model": "gpt-5",
                "prompt_chars": 50,
                "elapsed_s": 1.0,
                "error": err,
                "phase": "finish",
                "run_id": f"r-{i}",
            }
            for i, err in enumerate(
                [
                    "rate limit exceeded",
                    "rate limit exceeded",
                    "rate limit exceeded",
                    "timeout A",
                    "timeout B",
                ]
            )
        ],
    )
    db_path = tmp_path / "portfolio.db"

    portfolio.sync([scan_root], db_path)
    rows = portfolio.failure_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["failures"] == 5
    assert rows[0]["same_error_count"] == 3
    assert rows[0]["error_text"] == "rate limit exceeded"
