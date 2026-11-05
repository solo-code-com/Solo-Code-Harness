#!/usr/bin/env python3
"""test_task_receipt.py — unit tests for task receipt recorder and formatter."""

from __future__ import annotations

import json
from pathlib import Path

from tools import task_receipt


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
