#!/usr/bin/env python3
"""
FreeModel arm wrapper - unit tests
==================================
Network-free coverage for the parts of tools/free_delegate.py that decide what
runs and whether a run is trustworthy: binary resolution past a Windows .cmd
shim, argv construction for both CLIs, output parsing (zero-width watermark,
Codex log noise), failure classification, the model chain, the scope fence, and
the fail-closed refusal of a fence the snapshot cannot see.

Usage:
    python -m pytest tools/test_free_delegate.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import free_delegate as fd  # noqa: E402


def test_strip_zw_removes_watermark():
    assert fd.strip_zw("a\u200bb\u200cc\u200d\u2060d\ufeff") == "abcd"


def test_parse_claude_output_reads_result_key():
    raw = '{"type":"result","result":"HELLO\\u200b"}'
    assert fd.parse_claude_output(raw) == "HELLO"


def test_parse_claude_output_falls_back_to_last_json_line():
    raw = 'warning: chatter\n{"result":"FINAL"}\n'
    assert fd.parse_claude_output(raw) == "FINAL"


def test_parse_claude_output_returns_raw_when_not_json():
    assert fd.parse_claude_output("plain text") == "plain text"


def test_parse_codex_output_drops_cli_noise():
    raw = "OpenAI Codex v0.1\ntokens used\n11,652\nANSWER\nReconnecting... 1/5"
    assert "11,652" not in fd.parse_codex_output(raw)
    assert "ANSWER" in fd.parse_codex_output(raw)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("rate limit exceeded", "overloaded"),
        ("HTTP 503", "overloaded"),
        ("Reconnecting... 1/5", "overloaded"),
        ("User not found", "denied"),
        ("invalid api key", "denied"),
        ("something odd", "error"),
    ],
)
def test_classify_failure(text, expected):
    assert fd.classify_failure(text) == expected


def test_model_chain_dedupes_and_keeps_order():
    assert fd.model_chain("a", "b,a,c") == ["a", "b", "c"]


def test_model_chain_without_fallback():
    assert fd.model_chain("a", None) == ["a"]


def test_build_claude_argv_has_no_bare_and_uses_print():
    argv = fd.build_claude_argv(["claude"], "brief", "m", "high", write=False)
    assert "--bare" not in argv
    assert "-p" in argv
    assert argv[argv.index("--effort") + 1] == "high"
    assert "--permission-mode" not in argv


def test_build_claude_argv_write_grants_edits():
    argv = fd.build_claude_argv(["claude"], "brief", "m", None, write=True)
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def test_build_codex_argv_uses_config_approval_not_a_flag():
    argv = fd.build_codex_argv(
        ["codex"], "brief", "m", "medium", Path("."), write=False, last_message=None
    )
    assert "-a" not in argv
    assert 'approval_policy="never"' in argv
    assert argv[argv.index("-s") + 1] == "read-only"
    assert 'model_reasoning_effort="medium"' in argv


def test_build_codex_argv_write_is_workspace_write_and_takes_output_file():
    out = Path("last.txt")
    argv = fd.build_codex_argv(
        ["codex"], "brief", "m", None, Path("."), write=True, last_message=out
    )
    assert argv[argv.index("-s") + 1] == "workspace-write"
    assert argv[argv.index("-o") + 1] == str(out)


def test_resolve_binary_prefers_real_entry_over_cmd_shim(tmp_path, monkeypatch):
    package = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin"
    package.mkdir(parents=True)
    real = package / "claude.exe"
    real.write_text("", encoding="utf-8")
    shim = tmp_path / "claude.cmd"
    shim.write_text("", encoding="utf-8")
    monkeypatch.setattr(fd.shutil, "which", lambda name: str(shim))
    assert fd.resolve_binary("claude") == [str(real)]


def test_resolve_binary_runs_js_entry_through_node(tmp_path, monkeypatch):
    package = tmp_path / "node_modules" / "@openai" / "codex" / "bin"
    package.mkdir(parents=True)
    entry = package / "codex.js"
    entry.write_text("", encoding="utf-8")
    shim = tmp_path / "codex.cmd"
    shim.write_text("", encoding="utf-8")

    def fake_which(name):
        return str(shim) if name == "codex" else "C:/node/node.exe"

    monkeypatch.setattr(fd.shutil, "which", fake_which)
    assert fd.resolve_binary("chatgpt") == ["C:/node/node.exe", str(entry)]


def test_resolve_binary_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(fd.shutil, "which", lambda name: None)
    assert fd.resolve_binary("claude") is None


def test_scope_violations_flags_path_outside_fence(monkeypatch, tmp_path):
    monkeypatch.setattr(fd, "ROOT", tmp_path)
    (tmp_path / "fence").mkdir()
    before = {"fence/a.txt": "1"}
    after = {"fence/a.txt": "1", "escaped.txt": "2"}
    assert fd.scope_violations(before, after, tmp_path / "fence") == ["escaped.txt"]


def test_scope_violations_clean_when_only_inside_changed(monkeypatch, tmp_path):
    monkeypatch.setattr(fd, "ROOT", tmp_path)
    (tmp_path / "fence").mkdir()
    before = {"fence/a.txt": "1"}
    after = {"fence/a.txt": "2", "fence/new.txt": "3"}
    assert fd.scope_violations(before, after, tmp_path / "fence") == []


def test_scope_violations_none_when_snapshot_missing():
    assert fd.scope_violations(None, {}, Path(".")) is None


def test_enforce_scope_fails_closed_without_snapshot(monkeypatch, tmp_path, capsys):
    """An unavailable snapshot must stop the run, not approve it.

    A review found this: the code logged "fails closed" and then returned 0, so
    --enforce-scope reported success while verifying nothing.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FREEMODEL_API=fe_test\n", encoding="utf-8")
    monkeypatch.setattr(fd, "ROOT", tmp_path)
    monkeypatch.setattr(fd, "snapshot_root", lambda: None)
    rc = fd.main(
        [
            "brief",
            "--platform",
            "claude",
            "--model",
            "m",
            "--directory",
            ".",
            "--enforce-scope",
        ]
    )
    assert rc == 4
    assert "refusing to start" in capsys.readouterr().err


def test_lock_key_is_repo_relative(monkeypatch, tmp_path):
    monkeypatch.setattr(fd, "ROOT", tmp_path)
    monkeypatch.setattr(fd, "_lock_key", fd._lock_key)
    sub = tmp_path / "src"
    sub.mkdir()
    assert fd._lock_key(sub) == "src"
    assert fd._lock_key(tmp_path) == "."


def _isolated_state(monkeypatch, tmp_path):
    db = tmp_path / "state.db"
    monkeypatch.setattr(fd, "ROOT", tmp_path)
    import shared_state

    monkeypatch.setattr(shared_state, "DB_PATH", db, raising=False)
    return db


def test_acquire_lock_refuses_a_second_writer(tmp_path, monkeypatch):
    """Two write arms must not hold the same directory."""
    _isolated_state(monkeypatch, tmp_path)
    (tmp_path / "work").mkdir()

    assert fd.acquire_lock(tmp_path / "work", "claude", "m", "s1") is True
    assert fd.acquire_lock(tmp_path / "work", "chatgpt", "m", "s2") is False
    fd.release_lock(tmp_path / "work", "claude", "s1")
    assert fd.acquire_lock(tmp_path / "work", "chatgpt", "m", "s2") is True


def test_same_arm_second_session_is_refused(tmp_path, monkeypatch):
    """Without a distinct session_id, one Claude run overwrote another's lock.

    The conflict test is (owner != engine or owner_session != session_id), so two
    runs of the same arm with the empty default matched each other as the holder.
    """
    _isolated_state(monkeypatch, tmp_path)
    (tmp_path / "work").mkdir()

    assert fd.acquire_lock(tmp_path / "work", "claude", "m", "run-a") is True
    assert fd.acquire_lock(tmp_path / "work", "claude", "m", "run-b") is False


def test_lock_service_failure_denies_the_write(tmp_path, monkeypatch):
    """A broken lock service must refuse, not approve, a writing run.

    Reverting this to fail-open let a run write unserialised whenever SQLite was
    locked or the database was corrupt.
    """
    import sqlite3

    class Boom:
        def __init__(self, *_a, **_k):
            raise sqlite3.OperationalError("database is locked")

    fake = type("M", (), {"SharedState": Boom})
    monkeypatch.setattr(fd, "_load_sibling", lambda name: fake)
    assert fd.acquire_lock(tmp_path, "claude", "m", "s1") is False


def test_missing_lock_module_allows_the_run(tmp_path, monkeypatch):
    """No shared-state module means no lock service, which is not a denial."""

    def missing(_name):
        raise ImportError("no shared_state")

    monkeypatch.setattr(fd, "_load_sibling", missing)
    assert fd.acquire_lock(tmp_path, "claude", "m", "s1") is True


def test_release_uses_the_same_session(tmp_path, monkeypatch):
    """A release that forgets the session_id leaves the row until it expires."""
    _isolated_state(monkeypatch, tmp_path)
    (tmp_path / "work").mkdir()
    fd.acquire_lock(tmp_path / "work", "claude", "m", "run-a")
    fd.release_lock(tmp_path / "work", "claude", "wrong-session")
    assert fd.acquire_lock(tmp_path / "work", "chatgpt", "m", "b") is False
    fd.release_lock(tmp_path / "work", "claude", "run-a")
    assert fd.acquire_lock(tmp_path / "work", "chatgpt", "m", "b") is True


def test_ignored_fence_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fd, "ROOT", tmp_path)
    ignored = tmp_path / ".solocode" / "fence"
    ignored.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FREEMODEL_API=fe_test\n", encoding="utf-8")
    rc = fd.main(
        [
            "brief",
            "--platform",
            "claude",
            "--model",
            "m",
            "--directory",
            ".solocode/fence",
            "--write",
            "--enforce-scope",
        ]
    )
    assert rc == 2
    assert "snapshot-ignored" in capsys.readouterr().err
