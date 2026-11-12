#!/usr/bin/env python3
"""
Router Tests
============
Offline unit tests for tools/router.py -- the rule-table router that names a
brain, a worker and a budget for a task string.

Background: the orchestrator chose workers by hand and nothing recorded the
choice, so a delegation that silently fell back to the executor model looked
identical to a routed one. The router has to be reproducible, so every test here
runs without a network call and without consulting a brain.

Usage:
    python -m pytest tools/test_router.py -v
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import router  # noqa: E402

CONTRACT_KEYS = {
    "task_type",
    "brain",
    "worker",
    "model",
    "reason",
    "budget",
    "evidence",
}
LEDGER_KEYS = {
    "task_type",
    "brain",
    "worker",
    "model",
    "reason",
    "prompt_chars",
    "prompt_hash",
    "at",
    "spawned",
}

#: One representative prompt per task type.
CASES = {
    "question": "explain how the auth middleware works",
    "simple_edit": "rename the foo variable to bar in tools/sample.py",
    "complex": "build a routing module and refactor the session registry",
    "review": "review this diff for correctness",
    "survey": "where else does this pattern appear in the repo?",
    "destructive": "delete the users table",
}


def test_each_task_type_classifies_from_its_trigger():
    for expected, task in CASES.items():
        assert router.route(task)["task_type"] == expected, task


def test_a_destructive_task_stops_the_route():
    decision = router.route("drop the users table")

    assert decision["worker"] == "none"
    assert decision["budget"]["max_seconds"] == 0
    assert decision["model"] is None


def test_every_route_names_the_rule_table_as_its_brain():
    for task_type, task in CASES.items():
        assert router.route(task)["brain"] == "rules", task_type


def test_a_question_is_answered_in_place():
    decision = router.route(CASES["question"])

    assert decision["worker"] == "none"
    assert decision["budget"]["max_seconds"] > 0
    # The other "none" route is the blocked one: budget, not worker, tells them apart.
    assert router.route(CASES["destructive"])["budget"]["max_seconds"] == 0


def test_route_returns_the_full_contract():
    decision = router.route(CASES["review"])

    assert set(decision) == CONTRACT_KEYS
    assert set(decision["budget"]) == {"max_seconds", "max_prompt_chars"}
    assert decision["worker"] in router.DECLARED_WORKERS


def test_reason_is_written_from_the_task():
    first = router.route(CASES["review"])
    second = router.route("review the payments module for null handling")

    assert first["reason"].strip()
    assert first["reason"] != second["reason"]
    assert "review" in first["reason"]


def test_evidence_carries_a_model_roles_hash():
    evidence = router.route(CASES["question"])["evidence"]

    assert re.search(r"\b[0-9a-f]{8}\b", evidence)


def test_evidence_names_the_current_rules_version():
    assert router.route(CASES["complex"])["evidence"].startswith(
        f"rules:{router.RULES_VERSION}"
    )


def test_evidence_falls_back_when_the_roles_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "MODEL_ROLES_PATH", tmp_path / "absent.json")

    decision = router.route(CASES["complex"])

    assert decision["evidence"] == router.EVIDENCE_UNREADABLE
    assert decision["model"] is None


def test_an_invented_worker_is_an_error():
    with pytest.raises(ValueError, match="not a declared worker"):
        router.route(CASES["review"], force_worker="cli:invented")


def test_dry_run_appends_a_ledger_row(tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "route-decisions.jsonl"
    monkeypatch.setattr(router, "LEDGER_PATH", ledger)
    task = CASES["question"]

    assert router.main([task, "--dry-run"]) == 0

    rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == LEDGER_KEYS
    assert row["task_type"] == "question"
    assert row["spawned"] is False
    assert row["prompt_chars"] == len(task)
    assert row["prompt_hash"] == hashlib.sha256(task.encode("utf-8")).hexdigest()[:12]
    # The printed decision is the one that was logged.
    assert json.loads(capsys.readouterr().out)["reason"] == row["reason"]
    # The repo ledger was never touched: the temp path replaced it for this test.
    assert ledger == router.LEDGER_PATH


def test_dry_run_is_the_only_mode_that_writes_a_ledger(tmp_path, monkeypatch):
    ledger = tmp_path / "route-decisions.jsonl"
    monkeypatch.setattr(router, "LEDGER_PATH", ledger)

    assert router.main([CASES["question"]]) == 0
    assert not ledger.exists()


def test_probe_is_not_wired(capsys):
    with pytest.raises(NotImplementedError, match="not wired"):
        router.probe({"brain": "jev-router"})

    assert router.main([CASES["question"], "--probe"]) == 2
    assert "not wired" in capsys.readouterr().err


def test_route_never_touches_the_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("route() opened a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)

    assert router.route(CASES["complex"])["task_type"] == "complex"


def test_an_empty_task_is_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        router.route("   ")
