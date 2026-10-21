#!/usr/bin/env python3
"""
Kilo CLI Delegate — unit tests
==============================
Unit coverage for tools/kilo_cli_delegate.py helpers that need no network.

Background: on Windows an npm install puts `kilo.cmd` on PATH. Its batch `%*`
expansion destroys multi-line arguments (the guardrail reaches the model
truncated or empty). `find_kilo_binary` must prefer a real executable.

Usage:
    python -m pytest tools/test_kilo_cli_delegate.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    sys.path.insert(0, _entry)

import session_registry  # noqa: E402

from tools import kilo_cli_delegate  # noqa: E402


def _registry():
    """The registry module the delegate imports, so both share one object."""
    return session_registry


@pytest.fixture(autouse=True)
def _registry_into_tmp(tmp_path, monkeypatch):
    """No unit test may write session records into the repo's .solocode/."""
    monkeypatch.setattr(session_registry, "SESSION_DIR", tmp_path)


def test_is_batch_shim_detects_cmd_and_bat_on_windows():
    assert kilo_cli_delegate._is_batch_shim("C:/x/kilo.cmd", platform="win32")
    assert kilo_cli_delegate._is_batch_shim("C:/x/kilo.bat", platform="win32")


def test_is_batch_shim_ignores_exe_and_posix():
    assert not kilo_cli_delegate._is_batch_shim("C:/x/kilo.exe", platform="win32")
    assert not kilo_cli_delegate._is_batch_shim("/usr/bin/kilo.cmd", platform="linux")


def test_wrapped_executable_finds_node_modules_exe(tmp_path):
    shim = tmp_path / "kilo.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    real = tmp_path / "node_modules" / "@kilocode" / "cli" / "bin" / "kilo.exe"
    real.parent.mkdir(parents=True)
    real.write_text("", encoding="utf-8")

    assert kilo_cli_delegate._wrapped_executable(str(shim)) == str(real)


def test_wrapped_executable_is_none_when_absent(tmp_path):
    shim = tmp_path / "kilo.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")

    assert kilo_cli_delegate._wrapped_executable(str(shim)) is None


def test_find_kilo_binary_prefers_real_candidate_over_shim(tmp_path, monkeypatch):
    shim = tmp_path / "kilo.cmd"
    shim.write_text("", encoding="utf-8")
    real = tmp_path / "kilo.exe"
    real.write_text("", encoding="utf-8")

    monkeypatch.setattr(kilo_cli_delegate.shutil, "which", lambda name: str(shim))
    monkeypatch.setattr(kilo_cli_delegate, "_real_kilo_candidates", lambda: [real])

    assert kilo_cli_delegate.find_kilo_binary(platform="win32") == str(real)


def test_find_kilo_binary_uses_wrapped_exe_when_no_candidate(tmp_path, monkeypatch):
    shim = tmp_path / "kilo.cmd"
    shim.write_text("", encoding="utf-8")
    real = tmp_path / "node_modules" / "kilo" / "bin" / "kilo.exe"
    real.parent.mkdir(parents=True)
    real.write_text("", encoding="utf-8")

    monkeypatch.setattr(kilo_cli_delegate.shutil, "which", lambda name: str(shim))
    monkeypatch.setattr(kilo_cli_delegate, "_real_kilo_candidates", list)

    assert kilo_cli_delegate.find_kilo_binary(platform="win32") == str(real)


def test_find_kilo_binary_returns_real_path_directly(monkeypatch):
    monkeypatch.setattr(kilo_cli_delegate.shutil, "which", lambda name: "/usr/bin/kilo")

    assert kilo_cli_delegate.find_kilo_binary(platform="linux") == "/usr/bin/kilo"


def test_parse_json_events_extracts_text_and_session():
    stream = "\n".join(
        [
            '{"type":"step_start","sessionID":"ses_kilo"}',
            '{"type":"text","part":{"text":"PONG"}}',
            '{"type":"step_finish","part":{"tokens":{"total":5}}}',
        ]
    )
    result = kilo_cli_delegate.parse_json_events(stream)

    assert result["text"] == "PONG"
    assert result["session_id"] == "ses_kilo"
    assert result["tokens"] == {"total": 5}


def test_make_streams_encoding_safe_relaxes_errors(monkeypatch):
    calls = {}

    class FakeStream:
        def reconfigure(self, **kwargs):
            calls.update(kwargs)

    monkeypatch.setattr(kilo_cli_delegate.sys, "stdout", FakeStream())
    monkeypatch.setattr(kilo_cli_delegate.sys, "stderr", FakeStream())

    kilo_cli_delegate._make_streams_encoding_safe()

    assert calls.get("encoding") == "utf-8"
    assert calls.get("errors") == "replace"


def test_make_streams_encoding_safe_tolerates_missing_reconfigure(monkeypatch):
    class Bare:
        pass

    monkeypatch.setattr(kilo_cli_delegate.sys, "stdout", Bare())
    monkeypatch.setattr(kilo_cli_delegate.sys, "stderr", Bare())

    kilo_cli_delegate._make_streams_encoding_safe()


# ── run_kilo_cli + registry wiring (gap G7) ──────────────────────────────────


class _FakePopen:
    """A Popen stand-in: pid for the registry, communicate() for the streams."""

    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        pid: int = 7170,
        timeout: bool = False,
    ) -> None:
        self.pid = pid
        self.returncode = None
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode
        self._timeout = timeout

    def communicate(self, timeout: int | None = None):
        if self._timeout:
            # One-shot: the timeout path kills the tree, so the next communicate()
            # returns instead of raising again.
            self._timeout = False
            raise subprocess.TimeoutExpired(cmd="kilo", timeout=timeout or 0)
        self.returncode = self._returncode
        return self._stdout, self._stderr


def _run(monkeypatch, mod, proc):
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *_a, **_k: proc)
    return mod.run_kilo_cli(
        prompt="x",
        model="m",
        server_url="http://127.0.0.1:1",
        directory=".",
        kilo_binary="kilo",
        timeout_s=5,
    )


def test_run_kilo_cli_records_the_session_and_settles_it(monkeypatch, tmp_path):
    registry = _registry()
    stream = '{"type":"text","part":{"text":"PONG"}}'
    result = _run(monkeypatch, kilo_cli_delegate, _FakePopen(stdout=stream))
    assert result["text"] == "PONG"

    sessions = registry.list_sessions(session_dir=tmp_path)
    assert len(sessions) == 1
    assert sessions[0]["worker"] == "kilo"
    assert sessions[0]["pid"] == 7170
    assert sessions[0]["state"] == "exited"


def test_run_kilo_cli_timeout_kills_the_tree_and_records_it(monkeypatch, tmp_path):
    registry = _registry()
    killed: list[int] = []
    monkeypatch.setattr(
        kilo_cli_delegate,
        "_load_sibling",
        lambda name: (
            SimpleNamespace(kill_tree=lambda pid: killed.append(pid) or [pid])
            if name == "session_control"
            else session_registry
        ),
    )
    result = _run(monkeypatch, kilo_cli_delegate, _FakePopen(timeout=True))

    assert result["error"] == "Timeout after 5s"
    assert killed == [7170]
    assert registry.list_sessions(session_dir=tmp_path)[0]["state"] == "timeout"


def test_run_kilo_cli_launch_failure_records_no_session(monkeypatch, tmp_path):
    registry = _registry()

    def boom(*_a, **_k):
        raise OSError("no kilo")

    monkeypatch.setattr(kilo_cli_delegate.subprocess, "Popen", boom)
    result = kilo_cli_delegate.run_kilo_cli(
        prompt="x",
        model="m",
        server_url="http://127.0.0.1:1",
        directory=".",
        kilo_binary="kilo",
        timeout_s=5,
    )
    assert result["error"] == "no kilo"
    assert registry.list_sessions(session_dir=tmp_path) == []


def test_run_kilo_cli_reports_a_nonzero_exit_without_output(monkeypatch, tmp_path):
    result = _run(
        monkeypatch,
        kilo_cli_delegate,
        _FakePopen(returncode=3, stderr="boom"),
    )
    assert result["error"] == "kilo CLI exit code 3"


# ── server precondition ───────────────────────────────────────────────


def _reach_the_runner(monkeypatch):
    """Let main() run without .env, without a real kilo binary, without a log file."""
    monkeypatch.setattr(kilo_cli_delegate, "_load_project_env", lambda: None)
    monkeypatch.setattr(kilo_cli_delegate, "find_kilo_binary", lambda: "kilo")
    monkeypatch.setattr(kilo_cli_delegate, "log_usage", lambda *_a, **_k: None)
    monkeypatch.setattr(kilo_cli_delegate, "log_start", lambda *_a, **_k: None)


def test_main_reports_a_dead_server_instead_of_an_exit_code(monkeypatch, capsys):
    _reach_the_runner(monkeypatch)
    monkeypatch.setattr(kilo_cli_delegate, "_server_healthy", lambda _url: False)

    code = kilo_cli_delegate.main(
        ["hello", "--no-guardrail", "--server", "http://127.0.0.1:9"]
    )

    assert code == 7
    err = capsys.readouterr().err
    assert "http://127.0.0.1:9" in err
    assert "python tools/kilo_server_manager.py ensure" in err


def test_main_ensure_server_starts_it_then_reaches_the_runner(monkeypatch, capsys):
    _reach_the_runner(monkeypatch)
    probes = iter([False, True])
    monkeypatch.setattr(kilo_cli_delegate, "_server_healthy", lambda _url: next(probes))

    commands: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(kilo_cli_delegate.subprocess, "run", fake_run)

    ran: list[dict] = []

    def fake_runner(**kwargs):
        ran.append(kwargs)
        return {
            "text": "PONG",
            "events": [],
            "session_id": "ses_kilo",
            "tokens": None,
            "cost": None,
            "error": None,
        }

    monkeypatch.setattr(kilo_cli_delegate, "run_kilo_cli", fake_runner)

    code = kilo_cli_delegate.main(["hello", "--no-guardrail", "--ensure-server"])

    assert code == 0
    assert len(commands) == 1
    assert commands[0][0] == sys.executable
    assert commands[0][1].endswith("kilo_server_manager.py")
    assert commands[0][2] == "ensure"
    assert "--port" not in commands[0]
    assert len(ran) == 1
    assert "PONG" in capsys.readouterr().out


def test_server_healthy_is_false_for_a_closed_port_without_raising():
    start = time.monotonic()

    assert kilo_cli_delegate._server_healthy("http://127.0.0.1:9") is False
    assert time.monotonic() - start < 10


def test_ensure_server_passes_a_custom_port_to_the_manager(monkeypatch, capsys):
    _reach_the_runner(monkeypatch)
    probes = iter([False, True])
    monkeypatch.setattr(kilo_cli_delegate, "_server_healthy", lambda _url: next(probes))

    commands: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(kilo_cli_delegate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        kilo_cli_delegate,
        "run_kilo_cli",
        lambda **_kwargs: {
            "text": "PONG",
            "events": [],
            "session_id": None,
            "tokens": None,
            "cost": None,
            "error": None,
        },
    )

    code = kilo_cli_delegate.main(
        [
            "hello",
            "--no-guardrail",
            "--ensure-server",
            "--server",
            "http://127.0.0.1:14137",
        ]
    )

    assert code == 0
    assert len(commands) == 1
    assert commands[0][-2:] == ["--port", "14137"]


def _usage_rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_one_invocation_writes_a_start_then_finish_row(monkeypatch, tmp_path):
    log = tmp_path / "kilo-usage.jsonl"
    monkeypatch.setattr(kilo_cli_delegate, "USAGE_LOG", log)
    monkeypatch.setattr(kilo_cli_delegate, "_server_healthy", lambda _url: True)
    monkeypatch.setattr(kilo_cli_delegate, "_load_project_env", lambda: None)
    monkeypatch.setattr(kilo_cli_delegate, "find_kilo_binary", lambda: "kilo")
    monkeypatch.setattr(
        kilo_cli_delegate,
        "run_kilo_cli",
        lambda **kw: {
            "text": "PONG",
            "events": [],
            "session_id": "ses_kilo",
            "tokens": None,
            "cost": None,
            "error": None,
        },
    )

    assert kilo_cli_delegate.main(["x", "--no-guardrail"]) == 0

    rows = _usage_rows(log)
    assert len(rows) == 2
    start, finish = rows
    assert start["phase"] == "start"
    assert finish["phase"] == "finish"
    assert start["run_id"] == finish["run_id"]
    assert start["elapsed_s"] == 0


def test_start_row_exists_before_the_worker_is_spawned(monkeypatch, tmp_path):
    """The start row is on disk by the time the worker subprocess is spawned."""
    log = tmp_path / "kilo-usage.jsonl"
    monkeypatch.setattr(kilo_cli_delegate, "USAGE_LOG", log)
    monkeypatch.setattr(kilo_cli_delegate, "_server_healthy", lambda _url: True)
    monkeypatch.setattr(kilo_cli_delegate, "_load_project_env", lambda: None)
    monkeypatch.setattr(kilo_cli_delegate, "find_kilo_binary", lambda: "kilo")

    seen_at_spawn = {}

    def fake_popen(cmd, **kwargs):
        seen_at_spawn["rows"] = _usage_rows(log)
        return _FakePopen(stdout='{"type":"text","part":{"text":"PONG"}}')

    monkeypatch.setattr(kilo_cli_delegate.subprocess, "Popen", fake_popen)

    assert kilo_cli_delegate.main(["x", "--no-guardrail"]) == 0

    assert seen_at_spawn["rows"]
    start = seen_at_spawn["rows"][-1]
    assert start["phase"] == "start"
    finish = _usage_rows(log)[-1]
    assert finish["phase"] == "finish"
    assert finish["run_id"] == start["run_id"]


def test_unwritable_usage_log_does_not_break_the_run(monkeypatch, tmp_path):
    """A start write that raises OSError must not crash the wrapper."""
    blocked = tmp_path / "usage-log-is-a-directory"
    blocked.mkdir()
    monkeypatch.setattr(kilo_cli_delegate, "USAGE_LOG", blocked)
    monkeypatch.setattr(kilo_cli_delegate, "_server_healthy", lambda _url: True)
    monkeypatch.setattr(kilo_cli_delegate, "_load_project_env", lambda: None)
    monkeypatch.setattr(kilo_cli_delegate, "find_kilo_binary", lambda: "kilo")
    monkeypatch.setattr(
        kilo_cli_delegate,
        "run_kilo_cli",
        lambda **kw: {
            "text": "PONG",
            "events": [],
            "session_id": "ses_kilo",
            "tokens": None,
            "cost": None,
            "error": None,
        },
    )

    assert kilo_cli_delegate.main(["x", "--no-guardrail"]) == 0
