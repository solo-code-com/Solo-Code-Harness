#!/usr/bin/env python3
"""
Router Eval Tests
=================
Offline unit tests for tools/router_eval.py -- the read-only report that decides
whether the shadow ledger holds enough evidence to promote the decision brain.

Every test builds its own ledger and usage file under tmp_path, so no test reads
the repo's real .solocode logs, opens a socket or writes outside the temp
directory. The fixtures are synthetic on purpose: a passing test proves the
counting, never that the recorded data is good.

Usage:
    python -m pytest tools/test_router_eval.py -v
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import router_eval  # noqa: E402

#: Inside the default 14d window, and stable for the whole module.
NOW = int(time.time())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shadow(label: str | None = None, error: str | None = None) -> dict:
    """One shadow record as tools/router.py --shadow writes it."""
    return {
        "version": "v1",
        "label": label,
        "confidence": None,
        "noul": None,
        "error": error,
    }


def _agreeing_rows(count: int, task_type: str = "question") -> list[dict]:
    return [
        {"task_type": task_type, "at": NOW, "shadow": _shadow(task_type)}
        for _ in range(count)
    ]


def _clean_usage(count: int) -> list[dict]:
    """`count` finished runs, each one a start row and a zero-exit finish row."""
    rows: list[dict] = []
    for index in range(count):
        run_id = f"clean-{index}"
        rows.append({"ts": _now_iso(), "run_id": run_id, "phase": "start"})
        rows.append(
            {
                "ts": _now_iso(),
                "run_id": run_id,
                "phase": "finish",
                "exit": 0,
                "elapsed_s": 4.0,
            }
        )
    return rows


def _write_jsonl(path: Path, rows: list) -> Path:
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _collect(
    directory: Path, ledger_rows: list, usage_rows: list, min_samples: int = 3
) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    ledger = _write_jsonl(directory / "route-decisions.jsonl", ledger_rows)
    usage = _write_jsonl(directory / "opencode-usage.jsonl", usage_rows)
    since = router_eval.parse_since("14d")
    assert since is not None
    return router_eval.collect(ledger, usage, since, min_samples)


def _main(directory: Path, ledger_rows: list, usage_rows: list, argv: list) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    ledger = _write_jsonl(directory / "route-decisions.jsonl", ledger_rows)
    usage = _write_jsonl(directory / "opencode-usage.jsonl", usage_rows)
    return router_eval.main(["--ledger", str(ledger), "--usage", str(usage), *argv])


def test_parse_since_reads_relative_and_absolute_windows():
    now = datetime.now(timezone.utc)

    week = router_eval.parse_since("7d")
    hours = router_eval.parse_since("48h")
    day = router_eval.parse_since("2026-11-01")

    assert week is not None
    assert timedelta(days=6, hours=23) < now - week < timedelta(days=7, minutes=1)
    assert hours is not None
    assert timedelta(hours=47) < now - hours < timedelta(hours=49)
    assert day == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_an_unreadable_since_exits_2(tmp_path, capsys):
    ledger = tmp_path / "route-decisions.jsonl"
    usage = tmp_path / "opencode-usage.jsonl"

    code = router_eval.main(
        ["--ledger", str(ledger), "--usage", str(usage), "--since", "soon"]
    )

    assert code == 2
    assert "invalid --since" in capsys.readouterr().err
    # A refused window never opens either file.
    assert not ledger.exists() and not usage.exists()


def test_agreement_counts_two_of_three(tmp_path, capsys):
    rows = [
        {"task_type": "question", "at": NOW, "shadow": _shadow("question")},
        {"task_type": "question", "at": NOW, "shadow": _shadow("question")},
        {"task_type": "question", "at": NOW, "shadow": _shadow("complex")},
    ]

    stats = router_eval.shadow_stats(rows)[0]

    assert (stats["agree"], stats["disagree"]) == (2, 1)
    assert f"{stats['agreement_rate'] * 100:.1f}" == "66.7"
    assert _main(tmp_path, rows, [], ["--min-samples", "3"]) == 0
    assert "66.7" in capsys.readouterr().out


def test_a_shadow_error_is_counted_and_excluded_from_agreement():
    rows = [
        {"task_type": "question", "at": NOW, "shadow": _shadow("question")},
        {"task_type": "question", "at": NOW, "shadow": _shadow("question")},
        # A label next to an error must not be scored: the call did not answer.
        {
            "task_type": "question",
            "at": NOW,
            "shadow": _shadow("complex", error="JevDecideError: offline"),
        },
    ]

    stats = router_eval.shadow_stats(rows)[0]

    assert stats["decisions"] == 3
    assert stats["with_shadow"] == 3
    assert stats["shadow_errors"] == 1
    assert (stats["agree"], stats["disagree"]) == (2, 0)


def test_usage_escalations_cover_error_exit_and_lost_runs():
    stamp = _now_iso()
    rows = [
        {"ts": stamp, "run_id": "r1", "phase": "finish", "error": "provider timeout"},
        {"ts": stamp, "run_id": "r2", "phase": "finish", "exit": 3},
        {"ts": stamp, "run_id": "r3", "phase": "start"},
        {"ts": stamp, "run_id": "r4", "phase": "finish", "exit": 0},
        {"ts": stamp, "run_id": "r5", "phase": "start"},
        {"ts": stamp, "run_id": "r5", "phase": "finish", "exit": 0},
    ]

    summary = router_eval.summarize_usage(rows)

    # r1 fails on error, r2 on a non-zero exit, r3 is a start with no finish.
    assert summary["runs"] == 5
    assert summary["escalations"] == 3
    assert f"{summary['escalation_rate'] * 100:.1f}" == "60.0"


def test_anonymous_rows_do_not_inflate_the_escalation_rate(tmp_path):
    # A legacy row without a run_id is not a run that lost its finish: pairing it
    # with nothing would read as an escalation and inflate the rate.
    usage = [*_clean_usage(1), {"ts": _now_iso(), "phase": "start"}]

    report = _collect(tmp_path, _agreeing_rows(3), usage, min_samples=3)

    assert report["usage"]["anonymous"] == 1
    assert report["usage"]["runs"] == 1
    assert report["usage"]["escalations"] == 0


def test_zero_runs_is_not_a_zero_escalation_rate(tmp_path):
    report = _collect(tmp_path, _agreeing_rows(3), [], min_samples=3)

    assert report["usage"]["runs"] == 0
    assert report["usage"]["escalation_rate"] is None
    assert report["verdict"] == "NEED_MORE_DATA"

    text = router_eval.render_report(report)
    assert "escalation% -" in text
    assert "below the 5% floor" not in text


def test_untimed_rows_are_not_called_unreadable(tmp_path):
    # A row with valid JSON but no ts was read fine; only the invalid line was not.
    usage = [*_clean_usage(1), {"run_id": "r-late", "phase": "finish"}, "{not json"]

    report = _collect(tmp_path, _agreeing_rows(3), usage, min_samples=3)

    assert report["usage"]["untimed"] == 1
    assert report["usage"]["unparsable"] == 1
    assert report["usage"]["runs"] == 1

    header = router_eval.render_report(report).splitlines()[0]
    assert "1 untimed" in header
    assert "1 unparsable" in header


def test_anonymous_count_is_in_the_json(tmp_path, capsys):
    usage = [*_clean_usage(1), {"ts": _now_iso(), "phase": "finish", "exit": 0}]

    code = _main(tmp_path, _agreeing_rows(3), usage, ["--min-samples", "3", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["usage"]["anonymous"] == 1


def test_wilson_lower_bound_shrinks_a_small_sample():
    assert router_eval.wilson_lower_bound(16, 20) > 0.5
    assert router_eval.wilson_lower_bound(14, 20) < 0.5
    assert router_eval.wilson_lower_bound(0, 0) == 0.0


def test_verdict_need_more_data_below_min_samples(tmp_path):
    report = _collect(tmp_path, _agreeing_rows(2), _clean_usage(4), min_samples=5)

    assert report["usable_shadow_rows"] == 2
    assert report["missing_samples"] == 3
    assert report["verdict"] == "NEED_MORE_DATA"
    text = router_eval.render_report(report)
    assert "3 more usable shadow rows needed" in text


def test_verdict_headroom_too_small_below_the_floor(tmp_path):
    report = _collect(tmp_path, _agreeing_rows(20), _clean_usage(4), min_samples=20)

    assert report["usage"]["escalation_rate"] == 0.0
    assert report["verdict"] == "HEADROOM_TOO_SMALL"
    assert "HEADROOM_TOO_SMALL" in router_eval.render_report(report)


def test_verdict_linkage_required_at_or_above_the_floor(tmp_path):
    # 1 escalation in 20 runs is exactly 5%, which must not read as headroom.
    usage = _clean_usage(19)
    usage.append(
        {"ts": _now_iso(), "run_id": "r-fail", "phase": "finish", "error": "boom"}
    )
    report = _collect(tmp_path, _agreeing_rows(20), usage, min_samples=20)

    assert report["usage"]["runs"] == 20
    assert report["verdict"] == "LINKAGE_REQUIRED"
    assert "run_id" in router_eval.render_report(report)


def test_json_output_carries_the_table_numbers(tmp_path, capsys):
    ledger_rows = _agreeing_rows(2) + [
        {"task_type": "question", "at": NOW, "shadow": _shadow("complex")}
    ]
    usage_rows = [
        {"ts": _now_iso(), "run_id": "r1", "phase": "finish", "exit": 0},
        {"ts": _now_iso(), "run_id": "r2", "phase": "finish", "error": "boom"},
    ]

    assert (
        _main(tmp_path, ledger_rows, usage_rows, ["--min-samples", "3", "--json"]) == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert set(payload) == set(router_eval.DOCUMENTED_KEYS)
    task = payload["task_types"][0]
    assert set(task) == set(router_eval.TASK_KEYS)
    assert (task["agree"], task["disagree"]) == (2, 1)
    assert payload["usage"]["runs"] == 2
    assert payload["usage"]["escalations"] == 1
    assert payload["verdict"] == "LINKAGE_REQUIRED"

    assert _main(tmp_path, ledger_rows, usage_rows, ["--min-samples", "3"]) == 0
    table = capsys.readouterr().out
    assert f"{task['agreement_rate'] * 100:.1f}" in table
    assert f"runs {payload['usage']['runs']}" in table
    assert f"escalations {payload['usage']['escalations']}" in table
    assert payload["verdict"] in table


def test_the_tool_never_claims_a_winner():
    source = Path(router_eval.__file__).read_text(encoding="utf-8")

    # With no per-task linkage, a winner claim would be fabricated.
    assert "BRAIN_WINS" not in source
    assert "RULES_WIN" not in source
