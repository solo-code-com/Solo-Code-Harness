#!/usr/bin/env python3
"""
Model API — unit tests
======================
Unit coverage for tools/model_api.py paths that need no network: usage-log
writes that fail, and a run that still completes when its start row cannot be
written.

Usage:
    python -m pytest tools/test_model_api.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from tools import model_api  # noqa: E402


def test_log_start_does_not_raise_when_the_usage_path_is_unwritable(
    monkeypatch, tmp_path
):
    """Opening a directory for append raises OSError; the writer swallows it."""
    blocked = tmp_path / "usage-log-is-a-directory"
    blocked.mkdir()
    monkeypatch.setattr(model_api, "USAGE_LOG", blocked)

    assert model_api.log_start("commandcode/x", 10, "run-1") is None
    assert blocked.is_dir()


def test_main_finishes_when_the_start_row_cannot_be_written(
    monkeypatch, tmp_path, capsys
):
    """An unwritable usage path must not change main's behaviour."""
    blocked = tmp_path / "usage-log-is-a-directory"
    blocked.mkdir()
    monkeypatch.setattr(model_api, "USAGE_LOG", blocked)
    monkeypatch.setattr(model_api.env_file, "load_project_env", lambda: None)

    calls = []

    def fake_call_model(*args, **kwargs):
        calls.append(args)
        return (
            {"content": "answer text"},
            {"prompt_tokens": 1, "completion_tokens": 2},
            "",
        )

    monkeypatch.setattr(model_api, "call_model", fake_call_model)

    assert model_api.main(["--no-guardrail", "hi"]) == 0
    assert "answer text" in capsys.readouterr().out
    assert len(calls) == 1
    assert blocked.is_dir()
