#!/usr/bin/env python3
"""Tests for tools/kilo_server_manager.py — Kilo server lifecycle, no real server."""

from __future__ import annotations

import http.server
import importlib.util
import json
import shutil
import socket
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "kilo_server_manager.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "kilo_server_manager_under_test", SCRIPT
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    """Isolate every on-disk path the manager touches into tmp_path."""
    m = _load()
    monkeypatch.setattr(m, "SOLOCODE", tmp_path / ".solocode")
    monkeypatch.setattr(m, "PID_FILE", tmp_path / ".solocode" / "kilo-server.pid")
    monkeypatch.setattr(m, "LOG_FILE", tmp_path / ".solocode" / "kilo-server.log")
    return m


class _FakePopen:
    def __init__(self, pid=4242, returncode=None):
        self.pid = pid
        self.returncode = returncode

    def poll(self):
        return self.returncode


class _Kernel32:
    def __init__(self, handle=1, terminate_ok=1):
        self._handle = handle
        self._terminate_ok = terminate_ok
        self.calls: list[tuple] = []

    def OpenProcess(self, *_a):  # noqa: N802 — mirrors the Windows API name
        self.calls.append(("OpenProcess",))
        return self._handle

    def CloseHandle(self, *_a):  # noqa: N802 — mirrors the Windows API name
        self.calls.append(("CloseHandle",))

    def TerminateProcess(self, *_a):  # noqa: N802 — mirrors the Windows API name
        self.calls.append(("TerminateProcess",))
        return self._terminate_ok


def _install_fake_ctypes(monkeypatch, kernel32):
    fake = types.ModuleType("ctypes")
    fake.windll = SimpleNamespace(kernel32=kernel32)
    monkeypatch.setitem(sys.modules, "ctypes", fake)
    monkeypatch.setitem(
        sys.modules, "ctypes.wintypes", types.ModuleType("ctypes.wintypes")
    )
    return fake


# ── helpers ──────────────────────────────────────────────────────────────────


def test_expand_glob(tmp_path):
    mod = _load()
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    (tmp_path / "b.txt").write_text("", encoding="utf-8")
    assert mod._expand_glob(str(tmp_path / "*.txt")).name == "a.txt"
    assert mod._expand_glob(str(tmp_path / "*.nope")) is None


def test_find_kilo_binary_prefers_path(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(
        shutil, "which", lambda name: "/opt/kilo" if name == "kilo" else None
    )
    assert mod.find_kilo_binary() == Path("/opt/kilo")


def test_find_kilo_binary_uses_extension_dir(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(mod.Path, "home", staticmethod(lambda: tmp_path))
    ext = tmp_path / ".antigravity-ide" / "extensions" / "kilocode.kilo-code-2" / "bin"
    ext.mkdir(parents=True)
    (ext / "kilo.exe").write_text("", encoding="utf-8")
    assert mod.find_kilo_binary().name == "kilo.exe"


def test_find_kilo_binary_returns_none(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(mod.Path, "home", staticmethod(lambda: tmp_path))
    assert mod.find_kilo_binary() is None


# ── PID file ─────────────────────────────────────────────────────────────────


def test_pid_roundtrip(mod):
    assert mod._read_pid() is None
    mod._write_pid(1234)
    assert mod._read_pid() == 1234
    mod._remove_pid()
    assert mod._read_pid() is None


def test_read_pid_garbage_is_none(mod):
    mod.PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    mod.PID_FILE.write_text("not-a-number", encoding="utf-8")
    assert mod._read_pid() is None


def test_is_running(mod, monkeypatch):
    assert mod.is_running() is False
    mod._write_pid(999)
    monkeypatch.setattr(mod, "_pid_is_alive", lambda _pid: True)
    assert mod.is_running() is True


# ── health check ─────────────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal stand-in for the object returned by ``urlopen``."""

    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _serve_status(status: int) -> http.server.ThreadingHTTPServer:
    """Start a loopback HTTP server answering *status*; caller must shut down."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — mirrors the stdlib handler API
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass  # keep pytest output clean

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_health_check_ok(mod, monkeypatch):
    monkeypatch.setattr(
        mod.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(200)
    )
    assert mod.health_check(port=1, timeout=5) is True


def test_health_check_recovers_after_refusal(mod, monkeypatch):
    calls = {"n": 0}

    def flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise mod.urllib.error.URLError("refused")
        return _FakeResponse(200)

    monkeypatch.setattr(mod.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    assert mod.health_check(port=1, timeout=5) is True


def test_health_check_times_out(mod, monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    assert mod.health_check(port=1, timeout=0) is False


def test_health_check_unreachable_port_returns_false(mod):
    # Bind then immediately release a loopback port, so nothing listens there.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert mod.health_check(port=port, timeout=0.2) is False


def test_health_check_server_error_returns_false(mod):
    server = _serve_status(500)
    try:
        assert mod.health_check(port=server.server_address[1], timeout=0.3) is False
    finally:
        server.shutdown()
        server.server_close()


def test_health_check_success_returns_true(mod):
    server = _serve_status(200)
    try:
        assert mod.health_check(port=server.server_address[1], timeout=2) is True
    finally:
        server.shutdown()
        server.server_close()


# ── start / stop / status / ensure ───────────────────────────────────────────


def test_start_server_without_binary(mod, monkeypatch):
    monkeypatch.setattr(mod, "find_kilo_binary", lambda: None)
    assert mod.start_server() is False


def test_start_server_process_dies_immediately(mod, monkeypatch):
    monkeypatch.setattr(mod, "find_kilo_binary", lambda: Path("kilo"))
    monkeypatch.setattr(
        mod.subprocess, "Popen", lambda *_a, **_k: _FakePopen(returncode=3)
    )
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    assert mod.start_server(1234) is False
    assert mod._read_pid() is None  # stale pid cleaned up


def test_start_server_health_failure(mod, monkeypatch):
    monkeypatch.setattr(mod, "find_kilo_binary", lambda: Path("kilo"))
    monkeypatch.setattr(
        mod.subprocess, "Popen", lambda *_a, **_k: _FakePopen(returncode=None)
    )
    monkeypatch.setattr(mod, "health_check", lambda *_a, **_k: False)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    assert mod.start_server(1234) is False


def test_start_server_success(mod, monkeypatch):
    monkeypatch.setattr(mod, "find_kilo_binary", lambda: Path("kilo"))
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *_a, **_k: _FakePopen(pid=4321))
    monkeypatch.setattr(mod, "health_check", lambda *_a, **_k: True)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    assert mod.start_server(1234) is True
    assert mod._read_pid() == 4321


def test_stop_server_no_pid(mod):
    assert mod.stop_server() is True


def test_stop_server_stale_pid(mod, monkeypatch):
    mod._write_pid(111)
    monkeypatch.setattr(mod, "_pid_is_alive", lambda _pid: False)
    assert mod.stop_server() is True
    assert mod._read_pid() is None


def test_stop_server_terminates(mod, monkeypatch):
    mod._write_pid(222)
    monkeypatch.setattr(mod, "_pid_is_alive", lambda _pid: True)
    _install_fake_ctypes(monkeypatch, _Kernel32(handle=1, terminate_ok=1))
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    assert mod.stop_server() is True
    assert mod._read_pid() is None


def test_stop_server_cannot_open(mod, monkeypatch):
    mod._write_pid(333)
    monkeypatch.setattr(mod, "_pid_is_alive", lambda _pid: True)
    _install_fake_ctypes(monkeypatch, _Kernel32(handle=0))
    assert mod.stop_server() is True  # nothing to kill -> clean up and succeed


def test_stop_server_terminate_fails(mod, monkeypatch):
    mod._write_pid(444)
    monkeypatch.setattr(mod, "_pid_is_alive", lambda _pid: True)
    _install_fake_ctypes(monkeypatch, _Kernel32(handle=1, terminate_ok=0))
    assert mod.stop_server() is False


def test_get_status_stopped(mod):
    assert mod.get_status() == {
        "running": False,
        "pid": None,
        "port": None,
        "uptime": None,
    }


def test_get_status_running(mod, monkeypatch):
    mod._write_pid(555)
    monkeypatch.setattr(mod, "_pid_is_alive", lambda _pid: True)
    status = mod.get_status()
    assert status["running"] is True
    assert status["pid"] == 555
    assert status["uptime"] is not None


def test_ensure_running_already_up(mod, monkeypatch):
    monkeypatch.setattr(mod, "is_running", lambda: True)
    monkeypatch.setattr(
        mod, "start_server", lambda *_a: pytest.fail("should not start")
    )
    assert mod.ensure_running() is True


def test_ensure_running_starts_when_down(mod, monkeypatch):
    monkeypatch.setattr(mod, "is_running", lambda: False)
    monkeypatch.setattr(mod, "start_server", lambda *_a: True)
    assert mod.ensure_running() is True


# ── log helpers ──────────────────────────────────────────────────────────────


def test_log_helpers(mod, capsys):
    assert mod._log_line_count() == 0
    mod._print_log_tail()  # absent file -> silent
    mod.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    mod.LOG_FILE.write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert mod._log_line_count() == 3
    mod._print_log_tail(2)
    err = capsys.readouterr().err
    assert "[log] two" in err and "[log] one" not in err


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_build_parser_subcommands(mod):
    parser = mod._build_parser()
    for cmd in ("start", "stop", "status", "restart", "ensure"):
        assert parser.parse_args([cmd]).command == cmd


def test_main_start_ok(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "start_server", lambda _port: True)
    assert mod.main(["start", "--port", "1234"]) == 0
    assert "started on port 1234" in capsys.readouterr().out


def test_main_start_failure_returns_one(mod, monkeypatch):
    monkeypatch.setattr(mod, "start_server", lambda _port: False)
    assert mod.main(["start"]) == 1


def test_main_stop(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "stop_server", lambda: True)
    assert mod.main(["stop"]) == 0
    assert "stopped" in capsys.readouterr().out


def test_main_status_json(mod, monkeypatch, capsys):
    monkeypatch.setattr(
        mod,
        "get_status",
        lambda: {"running": True, "pid": 1, "port": 2, "uptime": "3s"},
    )
    assert mod.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["running"] is True


def test_main_status_human(mod, monkeypatch, capsys):
    monkeypatch.setattr(
        mod,
        "get_status",
        lambda: {"running": False, "pid": None, "port": None, "uptime": None},
    )
    assert mod.main(["status"]) == 0
    assert "STOPPED" in capsys.readouterr().out


def test_main_restart(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "stop_server", lambda: True)
    monkeypatch.setattr(mod, "start_server", lambda _port: True)
    assert mod.main(["restart"]) == 0
    assert "restarted" in capsys.readouterr().out


def test_main_ensure_reports_running(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "ensure_running", lambda _port: True)
    monkeypatch.setattr(
        mod,
        "get_status",
        lambda: {"running": True, "pid": 1, "port": 2, "uptime": "3s"},
    )
    assert mod.main(["ensure"]) == 0
    assert "RUNNING" in capsys.readouterr().out


def test_main_ensure_failure(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "ensure_running", lambda _port: False)
    assert mod.main(["ensure"]) == 1
