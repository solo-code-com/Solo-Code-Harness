#!/usr/bin/env python3
"""Review receipt tests.

Pin `tools/review_receipt.py`: the same tree hashes identically, content moves
the hash, both thresholds must be met before a receipt is required, a receipt
for a stale tree does not satisfy the check, and a broken git environment, a
corrupt receipt or a policy escaping the repository is an operational failure
(exit 2) rather than a silent pass.

Each test builds its own throwaway git repository, so the gate is measured
against real `git diff --cached` output instead of a stubbed change set.

Usage:
    python -m pytest tools/test_review_receipt.py -q
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "review_receipt.py"

# A binary name that cannot exist; used to prove a missing git is exit 2.
MISSING_GIT = "definitely-not-a-real-git-binary-xyz-12345"

POLICY = {
    "min_changed_files": 2,
    "min_changed_lines": 5,
    "receipt_dir": ".solocode/reviews",
}

# A 30-line threshold pins the line side of the AND: 6 changed lines is far
# below it, so a tree can clear the file count and still be exempt.
AND_POLICY = {
    "min_changed_files": 2,
    "min_changed_lines": 30,
    "receipt_dir": ".solocode/reviews",
}

SHIPPED_POLICY = {
    "min_changed_files": 5,
    "min_changed_lines": 120,
    "receipt_dir": ".solocode/reviews",
}


RECEIPT_FIELDS = {
    "schema",
    "tree_hash",
    "reviewer",
    "model",
    "recorded_at",
    "changed_files",
    "changed_lines",
    "tool_version",
}


def _load_tool():
    """Load review_receipt.py by path: tests pass an explicit root, not the repo."""
    spec = importlib.util.spec_from_file_location("review_receipt_under_test", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )


def _repo(tmp_path: Path, policy: dict | None = None) -> Path:
    """A git repository with two committed files and a policy file."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@example.com")
    _git(root, "config", "user.name", "Review Receipt Tests")

    (root / "base.txt").write_text("one\n", encoding="utf-8")
    (root / "extra.txt").write_text("alpha\n", encoding="utf-8")
    _git(root, "add", "base.txt", "extra.txt")
    _git(root, "commit", "-q", "-m", "baseline")

    config_dir = root / "tools" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "review-policy.json").write_text(
        json.dumps(policy or POLICY), encoding="utf-8"
    )
    return root


def _write(root: Path, name: str, text: str) -> None:
    """Write `name` and stage it: the gate hashes the index, so edits must be added."""
    (root / name).write_text(text, encoding="utf-8")
    _git(root, "add", name)


def _over_threshold(root: Path) -> None:
    """Stage both files with 3 added lines each: 2 files, 6 changed lines."""
    _write(root, "base.txt", "one\ntwo\nthree\nfour\n")
    _write(root, "extra.txt", "alpha\nbeta\ngamma\ndelta\n")


def test_same_tree_hashes_identically(tmp_path: Path) -> None:
    """Measurement is deterministic: two reads of one tree agree."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _write(root, "base.txt", "one\ntwo\n")

    first = tool.compute_change_set(root)
    second = tool.compute_change_set(root)

    assert first["tree_hash"] == second["tree_hash"]
    assert first["changed_files"] == 1
    assert first["changed_lines"] == 1


def test_content_change_moves_the_hash(tmp_path: Path) -> None:
    """Any diff content change must make the old receipt unusable."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _write(root, "base.txt", "one\ntwo\n")
    before = tool.compute_change_set(root)["tree_hash"]

    _write(root, "base.txt", "one\nthree\n")
    after = tool.compute_change_set(root)["tree_hash"]

    assert before != after


def test_under_threshold_passes_without_receipt(tmp_path: Path, capsys) -> None:
    """BOTH thresholds must be met: one file changed is exempt."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _write(root, "base.txt", "one\ntwo\n")

    assert tool.main(["verify"], root=root) == 0
    assert "review not required" in capsys.readouterr().out


def test_over_threshold_without_receipt_exits_1(tmp_path: Path, capsys) -> None:
    """Over both thresholds and no receipt: the gate refuses."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _over_threshold(root)

    assert tool.main(["verify"], root=root) == 1
    assert "no receipt" in capsys.readouterr().out


def test_file_threshold_alone_does_not_require_receipt(tmp_path: Path, capsys) -> None:
    """AND semantics, file side: 40 changed lines in one file stays exempt."""
    tool = _load_tool()
    root = _repo(tmp_path, AND_POLICY)
    _write(root, "base.txt", "one\n" + "added line\n" * 40)

    change = tool.verify(root)
    assert change["changed_files"] == 1
    assert change["changed_lines"] == 40
    assert not change["required"]
    assert tool.main(["verify"], root=root) == 0
    assert "review not required" in capsys.readouterr().out


def test_line_threshold_alone_does_not_require_receipt(tmp_path: Path, capsys) -> None:
    """AND semantics, line side: two files with 4 changed lines stays exempt."""
    tool = _load_tool()
    root = _repo(tmp_path, AND_POLICY)
    _write(root, "base.txt", "one\ntwo\nthree\n")
    _write(root, "extra.txt", "alpha\nbeta\ngamma\n")

    change = tool.verify(root)
    assert change["changed_files"] == 2
    assert change["changed_lines"] == 4
    assert not change["required"]
    assert tool.main(["verify"], root=root) == 0
    assert "review not required" in capsys.readouterr().out


def test_both_thresholds_met_without_receipt_exits_1(tmp_path: Path, capsys) -> None:
    """AND semantics, both sides: 2 files and 40 changed lines refuses."""
    tool = _load_tool()
    root = _repo(tmp_path, AND_POLICY)
    _write(root, "base.txt", "one\n" + "added line\n" * 20)
    _write(root, "extra.txt", "alpha\n" + "added line\n" * 20)

    change = tool.verify(root)
    assert change["changed_files"] == 2
    assert change["changed_lines"] == 40
    assert change["required"]
    assert tool.main(["verify"], root=root) == 1
    assert "no receipt" in capsys.readouterr().out


def test_record_then_verify_exits_0(tmp_path: Path) -> None:
    """A receipt for this tree satisfies the gate."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _over_threshold(root)

    path, receipt = tool.record_receipt("codex", "gpt-6.1-sol", root)

    assert path.is_file()
    assert path.name == f"{receipt['tree_hash']}.json"
    assert tool.main(["verify"], root=root) == 0


def test_stale_receipt_does_not_satisfy(tmp_path: Path, capsys) -> None:
    """A receipt written before a later edit must not pass the gate."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _over_threshold(root)
    stale, _ = tool.record_receipt("codex", "gpt-6.1-sol", root)

    _write(root, "base.txt", "one\nedited after review\nthree\nfour\n")

    assert stale.is_file()
    assert tool.main(["verify"], root=root) == 1
    assert "no receipt" in capsys.readouterr().out


def test_corrupt_receipt_exits_2(tmp_path: Path, capsys) -> None:
    """Invalid JSON at this tree's receipt path is an operational failure."""
    tool = _load_tool()
    root = _repo(tmp_path, AND_POLICY)
    _write(root, "base.txt", "one\n" + "added line\n" * 20)
    _write(root, "extra.txt", "alpha\n" + "added line\n" * 20)
    tree_hash = str(tool.compute_change_set(root)["tree_hash"])

    receipt_dir = root / str(AND_POLICY["receipt_dir"])
    receipt_dir.mkdir(parents=True)
    (receipt_dir / f"{tree_hash}.json").write_text("{not json", encoding="utf-8")

    assert tool.main(["verify"], root=root) == 2
    assert "receipt unreadable" in capsys.readouterr().err


def test_receipt_schema_boolean_exits_2(tmp_path: Path, capsys) -> None:
    """`True == 1` in Python, so the schema check must reject the type."""
    tool = _load_tool()
    root = _repo(tmp_path, AND_POLICY)
    _write(root, "base.txt", "one\n" + "added line\n" * 20)
    _write(root, "extra.txt", "alpha\n" + "added line\n" * 20)
    path, _ = tool.record_receipt("codex", "gpt-6.1-sol", root)

    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["schema"] = True
    path.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")

    assert tool.main(["verify"], root=root) == 2
    assert "receipt is corrupt" in capsys.readouterr().err


def test_git_unavailable_exits_2(tmp_path: Path, capsys) -> None:
    """A missing git binary is an operational failure, never a pass."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _over_threshold(root)

    assert tool.main(["verify"], root=root, git=MISSING_GIT) == 2
    assert "git is not available" in capsys.readouterr().err


def test_not_a_git_repo_exits_2(capsys) -> None:
    """A directory that is not a repository reports exit 2, not a clean pass.

    Built outside the repo on purpose: a directory nested inside this checkout
    still resolves to this repository when git walks up for a `.git` directory.
    """
    tool = _load_tool()
    with tempfile.TemporaryDirectory() as raw:
        plain = Path(raw) / "plain"
        config_dir = plain / "tools" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "review-policy.json").write_text(
            json.dumps(POLICY), encoding="utf-8"
        )

        assert tool.main(["verify"], root=plain) == 2
    assert "git diff failed" in capsys.readouterr().err


def test_receipt_dir_outside_repository_exits_2(tmp_path: Path, capsys) -> None:
    """A policy pointing outside the repository is rejected, never followed."""
    tool = _load_tool()
    root = _repo(tmp_path, dict(POLICY, receipt_dir="../outside-reviews"))

    assert tool.main(["verify"], root=root) == 2
    assert "must stay inside the repository" in capsys.readouterr().err


def test_receipt_json_has_exactly_the_documented_fields(tmp_path: Path) -> None:
    """The receipt shape is a contract: extra or missing keys are drift."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _over_threshold(root)

    path, _ = tool.record_receipt("codex", "gpt-6.1-sol", root)
    stored = json.loads(path.read_text(encoding="utf-8"))

    assert set(stored) == RECEIPT_FIELDS
    assert stored["schema"] == 1
    assert isinstance(stored["schema"], int) and not isinstance(stored["schema"], bool)
    assert len(stored["tree_hash"]) == 64
    assert stored["reviewer"] == "codex"
    assert stored["model"] == "gpt-6.1-sol"
    assert stored["changed_files"] == 2
    assert stored["changed_lines"] == 6
    assert stored["tool_version"] == tool.TOOL_VERSION
    assert stored["recorded_at"].endswith("Z")
    assert datetime.fromisoformat(stored["recorded_at"].replace("Z", "+00:00"))


def _advisory_rows(tool, root: Path) -> list[dict]:
    """Read the advisory log written under `root` into a list of row dicts."""
    path = root / tool.ADVISORY_LOG
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def test_advisory_required_without_receipt_exits_0(tmp_path: Path, capsys) -> None:
    """Advisory never blocks: required + missing still exits 0 and logs one row."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _over_threshold(root)

    assert tool.main(["verify", "--advisory"], root=root) == 0

    rows = _advisory_rows(tool, root)
    assert len(rows) == 1
    assert rows[0]["required"] is True
    assert rows[0]["had_receipt"] is False
    assert rows[0]["error"] is None
    assert "review required" in capsys.readouterr().err


def test_advisory_under_threshold_exits_0(tmp_path: Path, capsys) -> None:
    """Under threshold: exits 0, logs required false, and stays silent."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _write(root, "base.txt", "one\ntwo\n")

    assert tool.main(["verify", "--advisory"], root=root) == 0

    rows = _advisory_rows(tool, root)
    assert len(rows) == 1
    assert rows[0]["required"] is False
    assert capsys.readouterr().err == ""


def test_advisory_git_unavailable_exits_0(tmp_path: Path, capsys) -> None:
    """A broken git environment is an error row, not a non-zero exit."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _over_threshold(root)

    assert tool.main(["verify", "--advisory"], root=root, git=MISSING_GIT) == 0

    rows = _advisory_rows(tool, root)
    assert len(rows) == 1
    assert rows[0]["error"] is not None
    assert "git is not available" in rows[0]["error"]
    assert capsys.readouterr().err == ""


def test_advisory_second_run_appends(tmp_path: Path) -> None:
    """Each run appends a row; the log is never replaced."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _over_threshold(root)

    assert tool.main(["verify", "--advisory"], root=root) == 0
    assert tool.main(["verify", "--advisory"], root=root) == 0

    assert len(_advisory_rows(tool, root)) == 2


def test_advisory_stats_counts_rows_and_ratio(tmp_path: Path, capsys) -> None:
    """stats reads the log: two rows, one required, ratio one half."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _over_threshold(root)
    assert tool.main(["verify", "--advisory"], root=root) == 0

    _write(root, "base.txt", "one\n")
    assert tool.main(["verify", "--advisory"], root=root) == 0

    assert tool.main(["stats"], root=root) == 0
    out = capsys.readouterr().out
    assert "total runs: 2" in out
    assert "required: 1" in out
    assert "required without receipt: 1" in out
    assert "ratio: 0.5" in out


def test_advisory_stats_missing_log_prints_zeros(tmp_path: Path, capsys) -> None:
    """No log yet: stats prints zeros and exits 0."""
    tool = _load_tool()
    root = _repo(tmp_path)

    assert tool.main(["stats"], root=root) == 0
    out = capsys.readouterr().out
    assert "total runs: 0" in out
    assert "ratio: 0.0" in out
    assert "most recent required-without-receipt: never" in out


def test_unstaged_edit_does_not_move_hash_or_counts(tmp_path: Path) -> None:
    """Only the index is hashed: an unstaged edit leaves hash and counts alone."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _write(root, "base.txt", "one\ntwo\n")  # staged: 1 file, 1 line
    staged = tool.compute_change_set(root)

    # Unstaged edit on top of the staged tree: invisible to git diff --cached.
    (root / "base.txt").write_text("one\ntwo\nthree\nunstaged\n", encoding="utf-8")
    after = tool.compute_change_set(root)

    assert after["tree_hash"] == staged["tree_hash"]
    assert after["changed_files"] == staged["changed_files"] == 1
    assert after["changed_lines"] == staged["changed_lines"] == 1


def test_staging_same_content_twice_yields_same_hash(tmp_path: Path) -> None:
    """Staging is idempotent: re-adding identical bytes keeps the hash."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _write(root, "base.txt", "one\ntwo\n")
    first = tool.compute_change_set(root)

    _git(root, "add", "base.txt")
    second = tool.compute_change_set(root)

    assert first["tree_hash"] == second["tree_hash"]


def test_staging_moves_hash_but_unstaged_edit_does_not(tmp_path: Path) -> None:
    """Index semantics in both directions: staging moves the hash, an unstaged
    edit on top of the staged tree does not."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _write(root, "base.txt", "one\ntwo\n")  # staged: 1 file, 1 line
    staged = tool.compute_change_set(root)["tree_hash"]

    # Unstaged edit: invisible to `git diff --cached`, so the hash stays put.
    (root / "base.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    unstaged = tool.compute_change_set(root)["tree_hash"]
    assert unstaged == staged

    # Stage that edit: the index changes, so the hash moves.
    _git(root, "add", "base.txt")
    restaged = tool.compute_change_set(root)["tree_hash"]
    assert restaged != staged


def test_stage_after_receipt_moves_hash_and_verify_exits_1(
    tmp_path: Path, capsys
) -> None:
    """A receipt survives an unstaged edit but not the staged edit that follows.

    The intermediate assertion - hash unchanged while the edit is still
    unstaged - is what pins index semantics: hashing `git diff HEAD` would move
    the hash on that unstaged edit alone, so the later exit-1 would pass for the
    wrong reason.
    """
    tool = _load_tool()
    root = _repo(tmp_path)
    _over_threshold(root)
    _, receipt = tool.record_receipt("codex", "gpt-6.1-sol", root)
    staged_hash = receipt["tree_hash"]

    # Edit again, still unstaged: the index is untouched, so the receipt fits.
    (root / "base.txt").write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
    assert tool.compute_change_set(root)["tree_hash"] == staged_hash

    # Stage that edit: the hash moves and the old receipt no longer matches.
    _git(root, "add", "base.txt")
    assert tool.compute_change_set(root)["tree_hash"] != staged_hash
    assert tool.main(["verify"], root=root) == 1
    assert "no receipt" in capsys.readouterr().out


def test_five_files_ten_lines_not_required(tmp_path: Path, capsys) -> None:
    """AND at the shipped numbers, line side: 5 files but 10 lines stays exempt."""
    tool = _load_tool()
    root = _repo(tmp_path, SHIPPED_POLICY)
    for i in range(1, 6):
        _write(root, f"f{i}.txt", "line 1\nline 2\n")

    change = tool.verify(root)
    assert change["changed_files"] == 5
    assert change["changed_lines"] == 10
    assert not change["required"]
    assert tool.main(["verify"], root=root) == 0
    assert "review not required" in capsys.readouterr().out


def test_three_files_two_hundred_lines_not_required(tmp_path: Path, capsys) -> None:
    """AND at the shipped numbers, file side: 3 files with 200 lines stays exempt."""
    tool = _load_tool()
    root = _repo(tmp_path, SHIPPED_POLICY)
    _write(root, "a.txt", "".join(f"line {i}\n" for i in range(1, 101)))
    _write(root, "b.txt", "".join(f"line {i}\n" for i in range(1, 61)))
    _write(root, "c.txt", "".join(f"line {i}\n" for i in range(1, 41)))

    change = tool.verify(root)
    assert change["changed_files"] == 3
    assert change["changed_lines"] == 200
    assert not change["required"]
    assert tool.main(["verify"], root=root) == 0
    assert "review not required" in capsys.readouterr().out


def test_five_files_one_hundred_twenty_lines_required(tmp_path: Path, capsys) -> None:
    """AND at the shipped numbers, both sides: 5 files and 120 lines refuses."""
    tool = _load_tool()
    root = _repo(tmp_path, SHIPPED_POLICY)
    for i in range(1, 6):
        _write(root, f"f{i}.txt", "".join(f"line {j}\n" for j in range(1, 25)))

    change = tool.verify(root)
    assert change["changed_files"] == 5
    assert change["changed_lines"] == 120
    assert change["required"]
    assert tool.main(["verify"], root=root) == 1
    assert "no receipt" in capsys.readouterr().out


def test_record_then_add_file_past_threshold_exits_1(tmp_path: Path, capsys) -> None:
    """A receipt for a staged tree survives until one more staged file lands."""
    tool = _load_tool()
    root = _repo(tmp_path, SHIPPED_POLICY)
    for i in range(1, 6):
        _write(root, f"f{i}.txt", "".join(f"line {j}\n" for j in range(1, 25)))

    path, receipt = tool.record_receipt("codex", "gpt-6.1-sol", root)
    assert path.is_file()
    assert tool.main(["verify"], root=root) == 0

    _write(root, "f6.txt", "line 1\n")
    assert tool.main(["verify"], root=root) == 1
    assert "no receipt" in capsys.readouterr().out


def test_tree_hash_differs_for_two_invalid_utf8_patches(tmp_path: Path) -> None:
    """The hash covers raw bytes, not lossy-decoded text.

    Both patches decode to the same U+FFFD under errors="replace"; hashing the
    decoded text would make two distinct trees hash identically.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    tool = _load_tool()
    root = _repo(tmp_path)
    path = root / "base.txt"

    path.write_bytes(b"line\xffend\n")
    _git(root, "add", "base.txt")
    first = tool.compute_change_set(root)["tree_hash"]

    path.write_bytes(b"line\xfeend\n")
    _git(root, "add", "base.txt")
    second = tool.compute_change_set(root)["tree_hash"]

    assert first != second


def test_policy_with_a_bom_loads(tmp_path: Path) -> None:
    """A BOM is platform noise, so it must not turn a valid policy into exit 2.

    harness_fingerprint.canonical_bytes strips the BOM, so the policy loader
    reading utf-8-sig keeps the fingerprint's "unchanged" claim honest.
    """
    tool = _load_tool()
    root = _repo(tmp_path)
    policy_path = root / tool.POLICY_PATH
    policy_path.write_bytes(b"\xef\xbb\xbf" + json.dumps(POLICY).encode("utf-8"))

    assert tool.load_policy(root) == POLICY


if __name__ == "__main__":
    sys.exit("Run with: python -m pytest tools/test_review_receipt.py -q")
