#!/usr/bin/env python3
"""Unit tests for tools/protected_state.py."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from tools import protected_state  # noqa: E402


def test_absent_protected_path_appears_with_marker_so_creation_is_change(
    tmp_path: Path,
):
    """1. An absent protected path appears with the marker, so its later creation is a change."""
    before = protected_state.fingerprint(tmp_path)
    for path in protected_state.PROTECTED_PATHS:
        assert before[path] == "<absent>"

    target = tmp_path / ".solocode" / "executor-mode"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"on\n")

    after = protected_state.fingerprint(tmp_path)
    assert after[".solocode/executor-mode"] != "<absent>"

    violations = protected_state.new_violations(before, after)
    assert violations == [".solocode/executor-mode"]


def test_writing_file_inside_reviews_changes_fingerprint_and_new_violations_names_it(
    tmp_path: Path,
):
    """2. Writing a file inside .solocode/reviews changes the fingerprint, and new_violations names it."""
    reviews_dir = tmp_path / ".solocode" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    before = protected_state.fingerprint(tmp_path)
    assert before[".solocode/reviews"] == "<empty>"

    review_file = reviews_dir / "2026-11-24-rev.json"
    review_file.write_bytes(b"status: PASS\n")

    after = protected_state.fingerprint(tmp_path)
    new_key = ".solocode/reviews/2026-11-24-rev.json"
    assert new_key in after

    violations = protected_state.new_violations(before, after)
    assert new_key in violations


def test_rewriting_existing_receipt_with_different_content_changes_hash(tmp_path: Path):
    """3. Rewriting an existing receipt with different content changes its hash even though path and size stay plausible."""
    receipts_dir = tmp_path / ".solocode" / "task-receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    receipt = receipts_dir / "task-42.json"

    receipt.write_bytes(b"status: FAIL, task: A\n")
    before = protected_state.fingerprint(tmp_path)
    key = ".solocode/task-receipts/task-42.json"
    orig_hash = before[key]

    receipt.write_bytes(b"status: PASS, task: A\n")
    assert receipt.stat().st_size == len(b"status: PASS, task: A\n")

    after = protected_state.fingerprint(tmp_path)
    new_hash = after[key]
    assert orig_hash != new_hash

    violations = protected_state.new_violations(before, after)
    assert violations == [key]


def test_file_written_during_normal_run_not_in_fingerprint(tmp_path: Path):
    """4. A file the wrappers write during a normal run, such as .solocode/opencode-usage.jsonl, is NOT in the fingerprint."""
    solocode = tmp_path / ".solocode"
    solocode.mkdir(parents=True, exist_ok=True)

    normal_files = [
        solocode / "opencode-usage.jsonl",
        solocode / "antigravity-usage.jsonl",
        solocode / "shared-state.db",
        solocode / "context-checkpoint.json",
    ]
    for nf in normal_files:
        nf.write_bytes(b"logged: true\n")

    fp = protected_state.fingerprint(tmp_path)
    for nf in normal_files:
        rel = nf.relative_to(tmp_path).as_posix()
        assert rel not in fp

    before = protected_state.fingerprint(tmp_path)
    (solocode / "opencode-usage.jsonl").write_bytes(b"logged: true, extra: 1\n")
    after = protected_state.fingerprint(tmp_path)
    assert protected_state.new_violations(before, after) == []


def test_fingerprints_of_two_unchanged_trees_equal_and_new_violations_empty(
    tmp_path: Path,
):
    """5. Fingerprints of two unchanged trees are equal and new_violations is empty."""
    solocode = tmp_path / ".solocode"
    solocode.mkdir(parents=True, exist_ok=True)
    (solocode / "executor-mode").write_bytes(b"off\n")
    reviews = solocode / "reviews"
    reviews.mkdir()
    (reviews / "rev.json").write_bytes(b"review: 1\n")

    fp1 = protected_state.fingerprint(tmp_path)
    fp2 = protected_state.fingerprint(tmp_path)

    assert fp1 == fp2
    assert protected_state.new_violations(fp1, fp2) == []


def test_nested_file_under_task_receipts_tracked_individually_rename_shows_removal_and_addition(
    tmp_path: Path,
):
    """6. A nested file under .solocode/task-receipts is tracked individually, so a rename shows as one removal and one addition."""
    nested = tmp_path / ".solocode" / "task-receipts" / "worker_a"
    nested.mkdir(parents=True, exist_ok=True)
    original = nested / "receipt-alpha.json"
    original.write_bytes(b"id: alpha\n")

    before = protected_state.fingerprint(tmp_path)
    old_key = ".solocode/task-receipts/worker_a/receipt-alpha.json"
    assert old_key in before

    renamed = nested / "receipt-beta.json"
    original.rename(renamed)

    after = protected_state.fingerprint(tmp_path)
    new_key = ".solocode/task-receipts/worker_a/receipt-beta.json"
    assert new_key in after
    assert old_key not in after

    violations = protected_state.new_violations(before, after)
    assert violations == sorted([old_key, new_key])

    removals = [k for k in violations if k in before and k not in after]
    additions = [k for k in violations if k in after and k not in before]
    assert removals == [old_key]
    assert additions == [new_key]
