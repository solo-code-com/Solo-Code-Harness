#!/usr/bin/env python3
"""Tests for tools/codex_session.py — the Codex lifecycle adapter."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "codex_session.py"


def _load():
    spec = importlib.util.spec_from_file_location("codex_session_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeState:
    """Stand-in for SharedState so tests never touch the real SQLite DB."""

    def __init__(self, *_a, **_k):
        self.entries: list[dict] = []
        self.recent = [{"engine": "codex", "summary": "previous session"}]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get_recent_sessions(self, limit: int = 3):
        return self.recent[:limit]

    def add_session_entry(self, **kwargs):
        self.entries.append(kwargs)


def test_model_defaults_and_reads_env(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "CODEX_CONFIG", tmp_path / "config.toml")
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    assert mod._model() == mod.FALLBACK_MODEL
    monkeypatch.setenv("CODEX_MODEL", "custom-model")
    assert mod._model() == "custom-model"


def test_model_prefers_the_cli_argument(monkeypatch):
    mod = _load()
    monkeypatch.setenv("CODEX_MODEL", "custom-model")
    assert mod._model("cli-model") == "cli-model"


def test_model_falls_back_to_the_codex_config(monkeypatch, tmp_path):
    mod = _load()
    if mod._toml is None:
        pytest.skip("tomllib requires Python 3.11+")
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-6.1-sol"\n', encoding="utf-8")
    monkeypatch.setattr(mod, "CODEX_CONFIG", config)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    assert mod._model() == "gpt-6.1-sol"


def test_model_falls_back_to_unknown(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "CODEX_CONFIG", tmp_path / "missing.toml")
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    assert mod._model() == "unknown"


def test_start_reports_the_session_and_recent(monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(mod, "SharedState", _FakeState)
    assert mod.start("sid-1") == 0
    out = capsys.readouterr().out
    assert "[codex] session sid-1 started" in out
    assert "previous session" in out


def test_end_records_changed_files(monkeypatch, capsys):
    mod = _load()
    captured: dict = {}

    class Recorder(_FakeState):
        def add_session_entry(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(mod, "SharedState", Recorder)
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(stdout="a.py\nb.py\n"),
    )
    assert mod.end("sid-2", "did things") == 0
    assert captured["engine"] == "codex"
    assert captured["files_changed"] == ["a.py", "b.py"]
    assert captured["summary"] == "did things"
    assert "2 changed files" in capsys.readouterr().out


def test_end_survives_a_git_timeout(monkeypatch):
    mod = _load()
    captured: dict = {}

    class Recorder(_FakeState):
        def add_session_entry(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(mod, "SharedState", Recorder)

    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert mod.end("sid-3", "s") == 0
    assert captured["files_changed"] == []


def test_main_dispatches_start_and_end(monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(mod, "SharedState", _FakeState)
    monkeypatch.setattr(
        mod.subprocess, "run", lambda *_a, **_k: SimpleNamespace(stdout="")
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["codex_session.py", "start", "--session-id", "zzz", "--model", "cli-model"],
    )
    assert mod.main() == 0
    assert "model=cli-model" in capsys.readouterr().out
    monkeypatch.setattr(
        sys,
        "argv",
        ["codex_session.py", "end", "--session-id", "zzz", "--summary", "done"],
    )
    assert mod.main() == 0
