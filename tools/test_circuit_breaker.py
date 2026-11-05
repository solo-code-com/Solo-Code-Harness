#!/usr/bin/env python3
"""test_circuit_breaker.py — unit tests for loop budget & circuit breaker tool."""

from __future__ import annotations

from pathlib import Path

from tools import circuit_breaker


def test_circuit_breaker_trips_after_max_attempts(tmp_path: Path):
    state_file = tmp_path / "cb-state.json"
    task_id = "test-task-1"
    error = "SyntaxError: invalid syntax at line 42"

    # Attempt 1
    can_continue, count, msg = circuit_breaker.check_and_record_failure(
        task_id, error, max_attempts=3, state_file=state_file
    )
    assert can_continue is True
    assert count == 1
    assert "attempt 1 of 3" in msg

    # Attempt 2
    can_continue, count, msg = circuit_breaker.check_and_record_failure(
        task_id, error, max_attempts=3, state_file=state_file
    )
    assert can_continue is True
    assert count == 2
    assert "attempt 2 of 3" in msg

    # Attempt 3 — should trip!
    report_target = tmp_path / "TASK_FAILURE_REPORT.md"
    can_continue, count, msg = circuit_breaker.check_and_record_failure(
        task_id, error, max_attempts=3, state_file=state_file, report_path=report_target
    )
    assert can_continue is False
    assert count == 3
    assert "CIRCUIT BREAKER TRIPPED" in msg
    assert circuit_breaker.is_tripped(task_id, state_file=state_file) is True
    assert report_target.is_file()


def test_circuit_breaker_resets_on_success(tmp_path: Path):
    state_file = tmp_path / "cb-state.json"
    task_id = "test-task-2"
    error = "Connection timeout"

    circuit_breaker.check_and_record_failure(
        task_id, error, max_attempts=3, state_file=state_file
    )
    circuit_breaker.record_success(task_id, state_file=state_file)

    assert circuit_breaker.is_tripped(task_id, state_file=state_file) is False


def test_circuit_breaker_cli(tmp_path: Path, monkeypatch, capsys):
    state_file = tmp_path / "cb-state.json"
    monkeypatch.setattr(circuit_breaker, "DEFAULT_STATE_FILE", state_file)

    task_id = "cli-task"
    error = "TypeError: NoneType has no attribute"

    # Check 1
    code = circuit_breaker.main(
        ["check", "--task", task_id, "--error", error, "--max-attempts", "2"]
    )
    assert code == 0
    assert "[OK]" in capsys.readouterr().out

    # Check 2 (trips with max-attempts=2)
    report_file = tmp_path / "CLI_REPORT.md"
    code = circuit_breaker.main(
        [
            "check",
            "--task",
            task_id,
            "--error",
            error,
            "--max-attempts",
            "2",
            "--report-path",
            str(report_file),
        ]
    )
    assert code == circuit_breaker.CIRCUIT_BREAKER_EXIT_CODE
    assert "[STOP]" in capsys.readouterr().out
    assert report_file.is_file()

    # Reset
    code = circuit_breaker.main(["reset", "--task", task_id])
    assert code == 0
    assert "reset" in capsys.readouterr().out.lower()

    # Status after reset
    code = circuit_breaker.main(["status", "--task", task_id])
    assert code == 0
    assert "CLOSED" in capsys.readouterr().out
