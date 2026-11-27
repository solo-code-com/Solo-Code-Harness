#!/usr/bin/env python3
"""test_task_receipt.py — unit tests for task receipt recorder and formatter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tools import task_receipt

ROOT = Path(__file__).resolve().parent.parent


def test_record_task_receipt_creates_json_and_md(tmp_path: Path):
    receipt_dir = tmp_path / "receipts"
    path = task_receipt.record_task_receipt(
        task="Add feature X",
        worker="opencode",
        model="deepseek-v4.1",
        allowed_directory="tools",
        status="VERIFIED",
        run_id="test1234",
        verifications=[{"command": "pytest", "exit_code": 0, "note": "30 passed"}],
        receipt_dir=receipt_dir,
    )

    assert path.is_file()
    assert path.suffix == ".json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["task"] == "Add feature X"
    assert data["worker"] == "opencode"
    assert data["status"] == "VERIFIED"
    assert data["run_id"] == "test1234"
    assert len(data["verifications"]) == 1

    # Check companion markdown
    md_files = list(receipt_dir.glob("*.md"))
    assert len(md_files) == 1
    md_text = md_files[0].read_text(encoding="utf-8")
    assert "# Task Receipt: test1234 [PASS]" in md_text
    assert "Add feature X" in md_text
    assert "`pytest` -> **PASS**" in md_text


def test_list_receipts_reads_all(tmp_path: Path):
    receipt_dir = tmp_path / "receipts"
    task_receipt.record_task_receipt(
        "task 1", "opencode", "m1", receipt_dir=receipt_dir
    )
    task_receipt.record_task_receipt("task 2", "codex", "m2", receipt_dir=receipt_dir)

    receipts = task_receipt.list_receipts(receipt_dir)
    assert len(receipts) == 2
    tasks = {r["task"] for r in receipts}
    assert tasks == {"task 1", "task 2"}


def test_cli_record_and_show(tmp_path: Path, monkeypatch, capsys):
    receipt_dir = tmp_path / "receipts"
    monkeypatch.setattr(task_receipt, "DEFAULT_RECEIPT_DIR", receipt_dir)

    # CLI record
    exit_code = task_receipt.main(
        [
            "record",
            "--task",
            "CLI Task",
            "--worker",
            "codex",
            "--model",
            "gpt-6.1",
            "--status",
            "VERIFIED",
            "--run-id",
            "cli01",
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "Receipt recorded:" in captured

    # CLI list
    exit_code = task_receipt.main(["list"])
    assert exit_code == 0
    list_out = capsys.readouterr().out
    assert "cli01" in list_out
    assert "worker=codex" in list_out

    # CLI show
    exit_code = task_receipt.main(["show", "cli01"])
    assert exit_code == 0
    show_out = capsys.readouterr().out
    assert "Task Receipt: cli01" in show_out
    assert "CLI Task" in show_out


def test_git_helpers_return_empty_when_git_cannot_run(monkeypatch):
    """A missing git binary is an empty receipt section, not a crash."""

    def boom(*_args, **_kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(task_receipt.subprocess, "run", boom)

    assert task_receipt._git_diff_stat() == ""
    assert task_receipt._git_status_porcelain() == []


def test_markdown_says_when_nothing_changed():
    text = task_receipt.format_receipt_markdown(
        {"run_id": "r1", "status": "PENDING", "changed_files": []}
    )

    assert "*(No workspace files modified)*" in text


def test_list_receipts_ignores_a_missing_dir_and_bad_json(tmp_path):
    assert task_receipt.list_receipts(tmp_path / "absent") == []

    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "broken.json").write_text("{not json", encoding="utf-8")

    assert task_receipt.list_receipts(receipt_dir) == []


def test_cli_list_with_no_receipts(monkeypatch, capsys):
    """`list` prints a sentence, not an empty screen, when nothing was recorded."""
    monkeypatch.setattr(task_receipt, "list_receipts", lambda: [])

    assert task_receipt.main(["list"]) == 0
    assert "No task receipts found." in capsys.readouterr().out


def test_cli_show_with_an_unknown_run_id(monkeypatch, capsys):
    """`show` names the miss on stderr and exits 1, so a script can branch on it."""
    monkeypatch.setattr(task_receipt, "list_receipts", lambda: [])

    assert task_receipt.main(["show", "missing-id"]) == 1
    assert "not found" in capsys.readouterr().err


def test_cli_without_a_command_prints_help(capsys):
    assert task_receipt.main([]) == 0
    assert "usage:" in capsys.readouterr().out.lower()


def test_module_entry_point_exits_through_main(monkeypatch):
    import runpy

    monkeypatch.setattr(sys, "argv", ["task_receipt.py"])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(ROOT / "tools" / "task_receipt.py"), run_name="__main__")

    assert exc_info.value.code == 0
