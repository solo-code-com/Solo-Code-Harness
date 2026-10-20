#!/usr/bin/env python3
"""Gate probe tests.

The probe exists to make gate coverage visible: does gate X catch defect Y?
These tests pin the report's shape, not a model's opinion -- every assertion
runs the real adapters (subprocess to the repo's scripts, a real import of
check_hook), never a stub.

Four properties are pinned, plus the per-entry expected outcome that the
corpus section of tools/gate_probe.py documents:
  * the matrix is identical across two consecutive runs,
  * the summary counts match the corpus composition (a gate of None counts as no-gate),
  * at least one caught and at least one missed outcome exist,
  * adapters run for real: no gate row degrades to an error string.

The no-gate branch is covered by a one-entry ad-hoc corpus, because the real
corpus has no gate-less rows.

Usage:
    python -m pytest tools/test_gate_probe.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import gate_probe  # noqa: E402

# The expected outcome for each corpus id, as documented in the CORPUS section.
# Kept in the test, not in the corpus, so the probe must measure it rather than
# read it back.
EXPECTED = {
    "hook-claims-missing-exit": "caught",
    "hook-claims-always-zero": "caught",
    "comment-exempts-file": "caught",
    "non-harness-extension": "caught",
    "gui-asset-wrong-path": "caught",
    "root-level-project-file": "missed",
    "pip-dependency-in-tools": "caught",
    "fake-bypass-variable": "caught",
    "generator-newline-churn": "caught",
}


def test_matrix_is_identical_across_two_consecutive_runs():
    first = gate_probe.run_probe()
    second = gate_probe.run_probe()
    assert first == second


def test_summary_counts_match_the_corpus_composition():
    rows = gate_probe.run_probe()
    counts = gate_probe.summary_counts(rows)
    no_gate = sum(1 for entry in gate_probe.CORPUS if entry["gate"] is None)
    gated = len(gate_probe.CORPUS) - no_gate
    assert counts["no-gate"] == no_gate
    assert counts["caught"] + counts["missed"] == gated
    assert sum(counts.values()) == len(gate_probe.CORPUS)
    assert counts["caught"] >= 1
    assert counts["missed"] >= 1


def test_no_gate_outcome_is_covered_by_an_adhoc_corpus(monkeypatch):
    entry = {
        "id": "adhoc-no-gate",
        "defect": "A defect that has no gate yet.",
        "gate": None,
        "fixture": {"docs/notes.md": "No gate covers this defect yet.\n"},
    }
    monkeypatch.setattr(gate_probe, "CORPUS", [entry])
    assert gate_probe.run_probe() == [
        {"id": "adhoc-no-gate", "gate": None, "outcome": "no-gate"}
    ]


def test_every_entry_matches_its_expected_outcome():
    by_id = {row["id"]: row["outcome"] for row in gate_probe.run_probe()}
    assert set(by_id) == set(EXPECTED)
    for entry_id, expected in EXPECTED.items():
        assert by_id[entry_id] == expected, f"{entry_id}: expected {expected}"


def test_adapters_run_for_real_never_a_stub_or_an_error():
    for row in gate_probe.run_probe():
        if row["gate"] is not None:
            assert row["outcome"] in {"caught", "missed"}, row


def test_cli_exits_zero_and_writes_one_jsonl_record_per_entry(tmp_path):
    code = gate_probe.main([], root=tmp_path)
    assert code == 0
    log = tmp_path / ".solocode" / gate_probe.JSONL_NAME
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(gate_probe.CORPUS)
    records = [json.loads(line) for line in lines]
    assert {r["id"] for r in records} == {e["id"] for e in gate_probe.CORPUS}
    for record in records:
        assert set(record) == {"ts", "id", "gate", "outcome"}


def test_json_mode_prints_rows_and_summary(tmp_path, capsys):
    code = gate_probe.main(["--json"], root=tmp_path)
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"caught": 8, "missed": 1, "no-gate": 0}
    assert len(payload["rows"]) == len(gate_probe.CORPUS)
