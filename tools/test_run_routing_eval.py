#!/usr/bin/env python3
"""
Routing Eval Runner Tests
=========================
Offline unit tests for tools/run_routing_eval.py -- the runner that scores
tools/router.py's rule table against the frozen labels in
tools/eval/routing_eval_v1.json.

Every test builds its own dataset under tmp_path, so a broken fixture never
touches the shipped dataset, and one test reads the real dataset at its default
path to prove the shipped file still loads. No test opens a socket, calls a
model, or writes outside the temp directory.

Background: a disagreement between a label and the router is the report, not a
test failure, so the run must exit 0 on a mismatching row and only `--strict`
turns one into a non-zero exit. The refusal paths are the other half: a dataset
the runner cannot fully validate must exit 2 instead of scoring half of it.

Usage:
    python -m pytest tools/test_run_routing_eval.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import run_routing_eval  # noqa: E402


def _row(**overrides) -> dict:
    """One row the router answers correctly, with the caller's overrides applied."""
    row = {
        "id": "question-sample",
        "task": "explain how the cache is invalidated",
        "expected_task_type": "question",
        "expected_worker": "none",
        "expected_max_seconds": 30,
        "criterion": "question",
        "rationale": "Asks how something works.",
        "tags": [],
    }
    row.update(overrides)
    return row


def _disagreeing_row() -> dict:
    """A label the rule table must refuse: destructive is the first rule."""
    return _row(id="question-delete", task="how do I delete a file safely?")


def _write(path: Path, rows: list) -> Path:
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_invalid_json_exits_2_and_names_the_file(tmp_path, capsys):
    path = tmp_path / "broken.json"
    path.write_text("[{", encoding="utf-8")

    code = run_routing_eval.main(["--dataset", str(path)])

    assert code == 2
    assert str(path) in capsys.readouterr().err


def test_a_missing_key_exits_2_and_names_the_row(tmp_path, capsys):
    row = _row()
    del row["criterion"]
    path = _write(tmp_path / "dataset.json", [row])

    code = run_routing_eval.main(["--dataset", str(path)])

    assert code == 2
    assert "dataset: question-sample: missing criterion" in capsys.readouterr().err


def test_an_unknown_expected_task_type_exits_2(tmp_path, capsys):
    path = _write(tmp_path / "dataset.json", [_row(expected_task_type="bugfix")])

    code = run_routing_eval.main(["--dataset", str(path)])

    assert code == 2
    assert "bugfix" in capsys.readouterr().err


def test_duplicate_ids_exit_2(tmp_path, capsys):
    path = _write(tmp_path / "dataset.json", [_row(), _row(task="explain the build")])

    code = run_routing_eval.main(["--dataset", str(path)])

    assert code == 2
    error = capsys.readouterr().err
    assert "dataset: question-sample: duplicate id" in error


def test_a_disagreeing_row_is_reported_and_the_run_exits_0(tmp_path, capsys):
    path = _write(tmp_path / "dataset.json", [_row(), _disagreeing_row()])

    code = run_routing_eval.main(["--dataset", str(path)])

    out = capsys.readouterr().out
    assert code == 0
    assert (
        "question-delete | task_type | expected question | got destructive"
        " | severity wrong_stop" in out
    )
    assert "2 rows | 1 matched | 1 mismatched" in out


def test_strict_exits_1_on_the_same_dataset(tmp_path):
    path = _write(tmp_path / "dataset.json", [_row(), _disagreeing_row()])

    assert run_routing_eval.main(["--dataset", str(path), "--strict"]) == 1


def test_the_shipped_dataset_loads_and_runs(capsys):
    code = run_routing_eval.main([])

    out = capsys.readouterr().out
    assert code == 0
    assert "router evidence: rules:v2" in out
    assert "accuracy" in out.splitlines()[-1]
