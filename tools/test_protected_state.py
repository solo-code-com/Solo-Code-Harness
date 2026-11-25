#!/usr/bin/env python3
"""Unit tests for tools/protected_state.py."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_protected_file_replaced_by_symlink_to_identical_content_reported_as_change(
    tmp_path: Path, monkeypatch
):
    """1. A protected file replaced by a symlink to an identical-content file is reported as a change."""
    solocode = tmp_path / ".solocode"
    solocode.mkdir(parents=True, exist_ok=True)
    target = solocode / "executor-mode"
    target.write_bytes(b"on\n")

    before = protected_state.fingerprint(tmp_path)
    assert before[".solocode/executor-mode"].startswith("file:")

    other_file = tmp_path / "other-file"
    other_file.write_bytes(b"on\n")

    target.unlink()
    try:
        target.symlink_to(other_file)
    except OSError:
        target.write_bytes(b"on\n")
        real_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path, "is_symlink", lambda self: self == target or real_is_symlink(self)
        )
        real_readlink = os.readlink
        monkeypatch.setattr(
            os,
            "readlink",
            lambda p: str(other_file) if Path(p) == target else real_readlink(p),
        )

    after = protected_state.fingerprint(tmp_path)
    assert after[".solocode/executor-mode"].startswith("link:")
    violations = protected_state.new_violations(before, after)
    assert violations == [".solocode/executor-mode"]


def _make_junction(link: Path, target: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows directory junctions require win32")
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"mklink /J failed: {proc.stderr or proc.stdout}")


def test_junction_under_protected_path_recorded_as_link_marker_not_hashed(
    tmp_path: Path,
):
    reviews = tmp_path / ".solocode" / "reviews"
    reviews.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "secret.json"
    outside_file.write_bytes(b"secret-v1\n")

    _make_junction(reviews / "ext", outside)

    before = protected_state.fingerprint(tmp_path)
    assert ".solocode/reviews/ext" in before
    assert before[".solocode/reviews/ext"].startswith("link:")
    assert not any(key.startswith(".solocode/reviews/ext/") for key in before)

    outside_file.write_bytes(b"secret-v2\n")
    after = protected_state.fingerprint(tmp_path)
    assert after == before


def test_permission_change_on_protected_file_reported_as_change(tmp_path: Path):
    """2. A permission change on a protected file is reported as a change."""
    solocode = tmp_path / ".solocode"
    solocode.mkdir(parents=True, exist_ok=True)
    target = solocode / "executor-mode"
    target.write_bytes(b"on\n")

    before = protected_state.fingerprint(tmp_path)
    orig_entry = before[".solocode/executor-mode"]
    assert orig_entry.startswith("file:")

    try:
        os.chmod(target, stat.S_IREAD)
        after = protected_state.fingerprint(tmp_path)
        new_entry = after[".solocode/executor-mode"]
        assert orig_entry != new_entry
        violations = protected_state.new_violations(before, after)
        assert violations == [".solocode/executor-mode"]
    finally:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)


def test_gate_fingerprint_changes_when_guard_rules_changes(tmp_path: Path):
    """3. gate_fingerprint changes when tools/guard_rules.py content changes, and gate_changes names it."""
    guard_rules = tmp_path / "tools" / "guard_rules.py"
    guard_rules.parent.mkdir(parents=True, exist_ok=True)
    guard_rules.write_text("# initial\n", encoding="utf-8")

    before = protected_state.gate_fingerprint(tmp_path)
    assert "tools/guard_rules.py" in before
    assert before["tools/guard_rules.py"].startswith("file:")

    guard_rules.write_text("# modified\n", encoding="utf-8")
    after = protected_state.gate_fingerprint(tmp_path)

    assert before["tools/guard_rules.py"] != after["tools/guard_rules.py"]
    changes = protected_state.gate_changes(before, after)
    assert changes == ["tools/guard_rules.py"]


def test_recent_ignored_writes_names_file_after_since_and_not_before(
    tmp_path: Path,
):
    """4. recent_ignored_writes names a file created under node_modules after since, and does not name a file whose mtime predates it."""
    nm = tmp_path / "node_modules"
    nm.mkdir(parents=True, exist_ok=True)

    old_file = nm / "old_pkg.js"
    old_file.write_text("console.log('old');\n", encoding="utf-8")

    new_file = nm / "new_pkg.js"
    new_file.write_text("console.log('new');\n", encoding="utf-8")

    since = 1_000_000.0
    os.utime(old_file, (since - 100.0, since - 100.0))
    os.utime(new_file, (since + 100.0, since + 100.0))

    hits = protected_state.recent_ignored_writes(tmp_path, since=since)
    assert "node_modules/new_pkg.js" in hits
    assert "node_modules/old_pkg.js" not in hits
    assert hits == ["node_modules/new_pkg.js"]


def test_recent_ignored_writes_ignores_pycache_and_pytest_cache(tmp_path: Path):
    """5. recent_ignored_writes ignores __pycache__ and .pytest_cache, which are written by ordinary Python runs."""
    venv = tmp_path / ".venv"
    venv.mkdir(parents=True, exist_ok=True)

    pycache_file = venv / "__pycache__" / "module.cpython-313.pyc"
    pycache_file.parent.mkdir(parents=True, exist_ok=True)
    pycache_file.write_bytes(b"compiled")

    pytest_cache_file = venv / ".pytest_cache" / "v" / "cache" / "lastfailed"
    pytest_cache_file.parent.mkdir(parents=True, exist_ok=True)
    pytest_cache_file.write_text("{}", encoding="utf-8")

    root_pycache = tmp_path / "__pycache__" / "root.pyc"
    root_pycache.parent.mkdir(parents=True, exist_ok=True)
    root_pycache.write_bytes(b"root-pyc")

    root_pytest = tmp_path / ".pytest_cache" / "node.json"
    root_pytest.parent.mkdir(parents=True, exist_ok=True)
    root_pytest.write_text("{}", encoding="utf-8")

    since = 1_000_000.0
    for f in (pycache_file, pytest_cache_file, root_pycache, root_pytest):
        os.utime(f, (since + 50.0, since + 50.0))

    hits = protected_state.recent_ignored_writes(tmp_path, since=since)
    assert hits == []

    tampered = venv / "tampered.py"
    tampered.write_text("injected = True\n", encoding="utf-8")
    os.utime(tampered, (since + 50.0, since + 50.0))

    hits = protected_state.recent_ignored_writes(tmp_path, since=since)
    assert hits == [".venv/tampered.py"]


def test_recent_ignored_writes_stops_when_tree_exceeds_limit(tmp_path: Path):
    """6. recent_ignored_writes stops and says so when a tree exceeds the limit."""
    target_dir = tmp_path / "node_modules"
    target_dir.mkdir(parents=True, exist_ok=True)

    since = 1_000_000.0
    files = [
        target_dir / "f1.js",
        target_dir / "f2.js",
        target_dir / "f3.js",
        target_dir / "f4.js",
    ]
    for f in files:
        f.write_text("x", encoding="utf-8")
        os.utime(f, (since + 10.0, since + 10.0))

    hits = protected_state.recent_ignored_writes(tmp_path, since=since, limit=3)
    assert hits[-1] == "<walk truncated after 3 entries>"


def test_home_fingerprint_detects_a_changed_key_file(tmp_path: Path):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    key = ssh_dir / "id_ed25519"
    key.write_bytes(b"key-v1\n")

    before = protected_state.home_fingerprint(tmp_path)
    key.write_bytes(b"key-v2\n")
    after = protected_state.home_fingerprint(tmp_path)

    assert protected_state.home_changes(before, after) == [".ssh/id_ed25519"]


def test_home_fingerprint_detects_a_new_credential_file(tmp_path: Path):
    before = protected_state.home_fingerprint(tmp_path)
    assert before[".netrc"] == "<absent>"

    (tmp_path / ".netrc").write_bytes(b"machine example.com\n")
    after = protected_state.home_fingerprint(tmp_path)

    assert protected_state.home_changes(before, after) == [".netrc"]


def test_home_fingerprint_detects_a_symlink_swap(tmp_path: Path, monkeypatch):
    target = tmp_path / ".gitconfig"
    target.write_bytes(b"[user]\n\tname = a\n")

    before = protected_state.home_fingerprint(tmp_path)
    assert not before[".gitconfig"].startswith("link:")

    other_file = tmp_path / "other-gitconfig"
    other_file.write_bytes(b"[user]\n\tname = a\n")

    target.unlink()
    try:
        target.symlink_to(other_file)
    except OSError:
        target.write_bytes(b"[user]\n\tname = a\n")
        real_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path, "is_symlink", lambda self: self == target or real_is_symlink(self)
        )
        real_readlink = os.readlink
        monkeypatch.setattr(
            os,
            "readlink",
            lambda p: str(other_file) if Path(p) == target else real_readlink(p),
        )

    after = protected_state.home_fingerprint(tmp_path)
    assert after[".gitconfig"].startswith("link:")
    assert protected_state.home_changes(before, after) == [".gitconfig"]


def test_home_fingerprint_records_junction_as_link_marker_without_descending(
    tmp_path: Path,
):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_key = outside / "id_rsa"
    outside_key.write_bytes(b"outside-key-v1\n")

    _make_junction(ssh_dir / "ext", outside)

    before = protected_state.home_fingerprint(tmp_path)
    assert ".ssh/ext" in before
    assert before[".ssh/ext"].startswith("link:")
    assert not any(key.startswith(".ssh/ext/") for key in before)

    outside_key.write_bytes(b"outside-key-v2\n")
    after = protected_state.home_fingerprint(tmp_path)
    assert after == before


def test_home_fingerprint_leaves_unrelated_files_out(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / ".bashrc").write_text("export X=1\n", encoding="utf-8")
    other_cfg = tmp_path / ".config" / "other"
    other_cfg.mkdir(parents=True)
    (other_cfg / "cfg.json").write_text("{}", encoding="utf-8")

    fp = protected_state.home_fingerprint(tmp_path)
    assert "notes.txt" not in fp
    assert ".bashrc" not in fp
    assert ".config/other/cfg.json" not in fp
    assert set(fp) == set(protected_state.HOME_PROTECTED_PATHS)


def test_home_changes_empty_when_nothing_moved(tmp_path: Path):
    (tmp_path / ".gitconfig").write_text("[user]\n", encoding="utf-8")
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "known_hosts").write_text("host\n", encoding="utf-8")

    fp1 = protected_state.home_fingerprint(tmp_path)
    fp2 = protected_state.home_fingerprint(tmp_path)

    assert fp1 == fp2
    assert protected_state.home_changes(fp1, fp2) == []
