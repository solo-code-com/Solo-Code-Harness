"""Tests for tools/worker_stats.py using synthetic usage logs in tmp_path."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.worker_stats import (
    DOCUMENTED_KEYS,
    WORKER_KEYS,
    build_report,
    collect_stats,
    main,
    render_report,
)


def _write_usage(directory: Path, worker: str, rows: list[Any]) -> Path:
    """Write synthetic rows (dicts or raw strings) as a usage JSONL file."""
    path = directory / f"{worker}-usage.jsonl"
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _now_iso(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat()


def _mixed_rows() -> list[dict[str, Any]]:
    return [
        {"ts": _now_iso(), "run_id": "r1", "model": "kilo-1", "phase": "start"},
        {
            "ts": _now_iso(),
            "run_id": "r1",
            "model": "kilo-1",
            "phase": "finish",
            "elapsed_s": 10,
        },
        {
            "ts": _now_iso(),
            "run_id": "r2",
            "model": "kilo-1",
            "phase": "finish",
            "elapsed_s": 20,
        },
        {
            "ts": _now_iso(),
            "run_id": "r3",
            "model": "kilo-1",
            "phase": "finish",
            "error": "boom",
        },
        {"ts": _now_iso(), "run_id": "r4", "model": "kilo-1", "phase": "start"},
    ]


def _only(report: dict[str, Any], worker: str) -> dict[str, Any]:
    matches = [item for item in report["workers"] if item["worker"] == worker]
    assert len(matches) == 1
    return matches[0]


def test_outcomes_for_one_model(tmp_path: Path) -> None:
    _write_usage(tmp_path, "kilo", _mixed_rows())

    stats = _only(collect_stats(tmp_path), "kilo")

    assert stats["runs"] == 4
    assert stats["ok"] == 2
    assert stats["fail"] == 1
    assert stats["incomplete"] == 1
    assert f"{stats['success_rate'] * 100:.1f}" == "66.7"
    assert stats["model"] == "kilo-1"
    assert stats["median_elapsed_s"] == 15.0


def test_exit_only_worker(tmp_path: Path) -> None:
    rows = [
        {"run_id": "a", "phase": "finish", "exit": 0},
        {"run_id": "b", "phase": "finish", "exit": 3},
        {"run_id": "c", "exit": 1},
        {"run_id": "d", "phase": "start"},
    ]
    _write_usage(tmp_path, "codex-delegate", rows)

    stats = _only(collect_stats(tmp_path), "codex-delegate")

    assert stats["ok"] == 1
    assert stats["fail"] == 2
    assert stats["incomplete"] == 1
    assert stats["model"] == "(unknown)"


def test_exit_zero_without_finish_is_incomplete(tmp_path: Path) -> None:
    rows = [{"run_id": "a", "phase": "start", "exit": 0}]
    _write_usage(tmp_path, "codex-delegate", rows)

    stats = _only(collect_stats(tmp_path), "codex-delegate")

    assert stats["incomplete"] == 1
    assert stats["ok"] == 0


def test_token_fallback_prefers_total(tmp_path: Path) -> None:
    rows = [
        {
            "run_id": "a",
            "phase": "finish",
            "usage": {"total": 500, "input": 10, "output": 20, "cache": {"read": 999}},
        },
        {"run_id": "b", "phase": "finish", "usage": {"total_tokens": 70}},
        {"run_id": "c", "phase": "finish", "prompt_tokens": 4, "completion_tokens": 6},
        {"run_id": "d", "phase": "finish"},
        {
            "run_id": "e",
            "phase": "finish",
            "usage": {
                "input": 100,
                "output": 200,
                "reasoning": 50,
                "cache": {"read": 7, "write": 11},
            },
        },
    ]
    _write_usage(tmp_path, "kilo", rows)

    stats = _only(collect_stats(tmp_path), "kilo")

    assert stats["tokens"] == 500 + 70 + 10 + 0 + 300


def test_malformed_line_does_not_raise(tmp_path: Path) -> None:
    _write_usage(
        tmp_path,
        "kilo",
        [{"run_id": "a", "phase": "finish"}, "{not json", "", "[1, 2]"],
    )

    report = collect_stats(tmp_path)

    assert report["bad_lines"] == 2
    assert _only(report, "kilo")["ok"] == 1


def test_legacy_row_is_counted_separately(tmp_path: Path) -> None:
    rows = [
        {"run_id": "a", "phase": "finish"},
        {"phase": "finish"},
        {"phase": "finish"},
    ]
    _write_usage(tmp_path, "jev-decide", rows)

    report = collect_stats(tmp_path)
    stats = _only(report, "jev-decide")

    assert stats["runs"] == 1
    assert stats["legacy"] == 2
    assert report["totals"]["legacy"] == 2


def test_legacy_row_counted_once_across_models(tmp_path: Path) -> None:
    rows = [
        {"run_id": "a", "model": "m-one", "phase": "finish"},
        {"run_id": "b", "model": "m-two", "phase": "finish"},
        {"model": "m-one", "phase": "finish"},
    ]
    _write_usage(tmp_path, "kilo", rows)

    report = collect_stats(tmp_path)
    per_model = {item["model"]: item["legacy"] for item in report["workers"]}

    assert per_model == {"m-one": 1, "m-two": 0}
    assert report["totals"]["legacy"] == 1


def test_json_shape_matches_table(tmp_path: Path) -> None:
    _write_usage(tmp_path, "kilo", _mixed_rows())

    report = build_report(tmp_path)
    stats = _only(report, "kilo")

    assert tuple(report) == DOCUMENTED_KEYS
    assert tuple(stats) == WORKER_KEYS
    assert tuple(report["totals"]) == (
        "runs",
        "ok",
        "fail",
        "incomplete",
        "tokens",
        "legacy",
        "cost_usd",
        "success_rate",
    )
    assert report["totals"]["runs"] == stats["runs"]
    rendered = render_report(report)
    assert "SUCCESS%" in rendered
    assert "66.7" in rendered
    assert "TOTAL" in rendered


def test_since_drops_old_rows(tmp_path: Path) -> None:
    rows = [
        {"ts": _now_iso(timedelta(days=30)), "run_id": "old", "phase": "finish"},
        {"ts": _now_iso(), "run_id": "new", "phase": "finish"},
    ]
    _write_usage(tmp_path, "kilo", rows)
    since = datetime.now(timezone.utc) - timedelta(days=7)

    fresh = _only(collect_stats(tmp_path, since=since), "kilo")

    assert fresh["runs"] == 1
    assert fresh["ok"] == 1


def test_since_reports_unparsable_ts(tmp_path: Path, capsys: Any) -> None:
    rows = [
        {"ts": "not-a-date", "run_id": "bad", "phase": "finish"},
        {"ts": _now_iso(), "run_id": "new", "phase": "finish"},
    ]
    _write_usage(tmp_path, "kilo", rows)

    code = main(["--usage-dir", str(tmp_path), "--since", "7d"])
    out = capsys.readouterr().out

    assert code == 0
    assert "unparsable ts: 1" in out


def test_missing_dir_is_empty(tmp_path: Path) -> None:
    report = collect_stats(tmp_path / "absent")

    assert report["workers"] == []
    assert report["totals"]["runs"] == 0
    assert report["bad_lines"] == 0


def test_worker_filter(tmp_path: Path) -> None:
    _write_usage(tmp_path, "kilo", [{"run_id": "a", "phase": "finish"}])
    _write_usage(tmp_path, "opencode", [{"run_id": "b", "phase": "finish"}])

    report = build_report(tmp_path, worker="kilo")

    assert [item["worker"] for item in report["workers"]] == ["kilo"]
    assert report["totals"]["runs"] == 1


def test_main_exits_zero_for_missing_dir(tmp_path: Path, capsys: Any) -> None:
    code = main(["--usage-dir", str(tmp_path / "absent")])
    out = capsys.readouterr().out

    assert code == 0
    assert "TOTAL" in out


def test_main_json_output_carries_documented_keys(tmp_path: Path, capsys: Any) -> None:
    _write_usage(tmp_path, "kilo", _mixed_rows())

    code = main(["--usage-dir", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert tuple(payload) == DOCUMENTED_KEYS
    assert payload["workers"][0]["runs"] == 4
    assert payload["totals"]["ok"] == 2


def test_median_uses_one_sample_per_run(tmp_path: Path) -> None:
    rows = [
        {"run_id": "r1", "phase": "start", "elapsed_s": 0},
        {"run_id": "r1", "phase": "finish", "elapsed_s": 10},
        {"run_id": "r2", "phase": "start", "elapsed_s": 0},
        {"run_id": "r2", "phase": "finish", "elapsed_s": 20},
        {"run_id": "r3", "phase": "start", "elapsed_s": 0},
        {"run_id": "r3", "phase": "finish", "elapsed_s": 30},
        {"run_id": "r4", "phase": "start"},
        {"run_id": "r4", "phase": "finish"},
    ]
    _write_usage(tmp_path, "kilo", rows)

    stats = _only(collect_stats(tmp_path), "kilo")

    assert stats["runs"] == 4
    assert stats["median_elapsed_s"] == 20.0
