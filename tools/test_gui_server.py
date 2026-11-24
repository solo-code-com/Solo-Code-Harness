#!/usr/bin/env python3
"""Tests for tools/gui_server.py — the local JEV worker console."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "gui_server.py"


def _load():
    spec = importlib.util.spec_from_file_location("gui_server_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── discovery ────────────────────────────────────────────────────────────────


def test_env_key_names_returns_names_not_values(tmp_path):
    mod = _load()
    p = tmp_path / ".env"
    p.write_text("# comment\nA_KEY=super-secret\n\nB=2\nA_KEY=dup\n", encoding="utf-8")
    names = mod.env_key_names(p)
    assert names == ["A_KEY", "B"]
    assert all("secret" not in n for n in names)


def test_env_key_names_missing_file(tmp_path):
    assert _load().env_key_names(tmp_path / "nope") == []


def test_providers_reads_catalog(tmp_path):
    mod = _load()
    p = tmp_path / "opencode.json"
    p.write_text(
        json.dumps(
            {"providers": {"x": {"models": {"m1": {}, "m2": {}}}, "y": {"models": {}}}}
        ),
        encoding="utf-8",
    )
    assert mod.providers(p) == {"x": ["m1", "m2"], "y": []}


def test_providers_bad_json_is_empty(tmp_path):
    mod = _load()
    p = tmp_path / "opencode.json"
    p.write_text("{ not json", encoding="utf-8")
    assert mod.providers(p) == {}


def test_known_models_builds_flat_refs():
    mod = _load()
    assert mod.known_models({"b": ["k2"], "a": ["k1"]}) == [
        {"provider": "a", "key": "k1", "ref": "a/k1"},
        {"provider": "b", "key": "k2", "ref": "b/k2"},
    ]


def test_workers_ready_requires_cli_and_wrapper(tmp_path, monkeypatch):
    mod = _load()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "antigravity_delegate.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        mod.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "agy" else None
    )
    by_id = {w["id"]: w for w in mod.workers(tmp_path)}
    assert by_id["gemini"]["ready"] is True
    assert by_id["opencode"]["ready"] is False  # CLI present? no -> not ready
    assert all(w["note"] is None for w in by_id.values())  # no worker is 'pending'


# ── argv builder / validation (the security-relevant part) ───────────────────


def test_build_argv_gemini_with_tools():
    mod = _load()
    argv = mod.build_argv(
        "gemini", "do x", "gemini-3.8-flash-medium", {"allow_tools": True}
    )
    assert argv == [
        sys.executable,
        "tools/antigravity_delegate.py",
        "do x",
        "--model",
        "gemini-3.8-flash-medium",
        "--allow-tools",
    ]


def test_build_argv_opencode_free():
    mod = _load()
    assert mod.build_argv("opencode", "b", None, {"free": True}) == [
        sys.executable,
        "tools/opencode_delegate.py",
        "b",
        "--free",
    ]


def test_build_argv_kilo_and_dsh():
    mod = _load()
    assert mod.build_argv("kilo", "b", "m", {}) == [
        sys.executable,
        "tools/kilo_cli_delegate.py",
        "b",
        "--model",
        "m",
    ]
    assert mod.build_argv("dsh", "b", None, {}) == [
        sys.executable,
        "tools/dsh_delegate.py",
        "b",
    ]


def test_build_argv_rejects_unknown_worker():
    mod = _load()
    with pytest.raises(ValueError):
        mod.build_argv("bogus", "b", None, {})


def test_validate_run_accepts_and_rejects():
    mod = _load()
    for worker in ("gemini", "opencode", "kilo", "dsh"):
        mod.validate_run(worker, "x", None, set())
    mod.validate_run("gemini", "x", "prov/key", {"prov/key"})
    for args in (
        ("bogus", "x", None, set()),
        ("opencode", "   ", None, set()),
        ("opencode", "x" * 25_000, None, set()),
        ("gemini", "x", "prov/nope", {"prov/key"}),
    ):
        with pytest.raises(ValueError):
            mod.validate_run(*args)


# ── HTTP surface ─────────────────────────────────────────────────────────────


@pytest.fixture()
def server():
    mod = _load()
    httpd = mod.build_server("127.0.0.1", 0, "tok123", 30)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield mod, httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _call(port, path, *, token=None, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"X-Token": token} if token else {}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as res:  # noqa: S310 — fixed 127.0.0.1 URL
            return res.status, res.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_index_is_public_and_carries_the_token(server):
    _, port = server
    status, body = _call(port, "/")
    assert status == 200
    assert "JEV Worker Console" in body
    assert "tok123" in body  # injected so the page can call the API


def test_state_requires_token(server):
    _, port = server
    assert _call(port, "/api/state")[0] == 403
    status, body = _call(port, "/api/state", token="tok123")  # noqa: S106 — test fixture
    assert status == 200
    assert "workers" in json.loads(body)


def test_run_rejects_non_runnable_worker(server):
    _, port = server
    status, body = _call(
        port,
        "/api/run",
        token="tok123",  # noqa: S106 — test fixture
        method="POST",  # noqa: S106 — test fixture
        payload={"worker": "bogus", "brief": "x"},
    )
    assert status == 400
    assert "not runnable" in body


def test_run_ok_with_mocked_runner(server, monkeypatch):
    mod, port = server
    monkeypatch.setattr(
        mod,
        "run_task",
        lambda w, b, m, f, t: {
            "argv": ["x"],
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
        },
    )
    status, body = _call(
        port,
        "/api/run",
        token="tok123",  # noqa: S106 — test fixture
        method="POST",  # noqa: S106 — test fixture
        payload={"worker": "opencode", "brief": "do x"},
    )
    assert status == 200
    assert json.loads(body)["stdout"] == "ok"


# ── streaming (SSE) ──────────────────────────────────────────────────────────


def test_sse_framing():
    mod = _load()
    assert (
        mod._sse("chunk", {"line": "hi"}) == b'event: chunk\ndata: {"line": "hi"}\n\n'
    )


def test_stream_endpoint_requires_token(server):
    _, port = server
    status, _ = _call(
        port,
        "/api/run/stream",
        method="POST",
        payload={"worker": "opencode", "brief": "x"},
    )
    assert status == 403


def test_stream_endpoint_streams_output(server, monkeypatch):
    mod, port = server
    monkeypatch.setattr(
        mod,
        "_stream_argv",
        lambda _w, _b, _m, _f: [sys.executable, "-c", "print('hello-stream')"],
    )
    status, body = _call(
        port,
        "/api/run/stream",
        token="tok123",  # noqa: S106 — test fixture
        method="POST",  # noqa: S106 — test fixture
        payload={"worker": "opencode", "brief": "x"},
    )
    assert status == 200
    assert "event: start" in body
    assert "hello-stream" in body  # the child's stdout reached the stream
    assert "event: done" in body
    assert '"stop": "completed"' in body


def test_stream_start_event_carries_run_id(server, monkeypatch):
    mod, port = server
    monkeypatch.setattr(
        mod, "_stream_argv", lambda _w, _b, _m, _f: [sys.executable, "-c", "print('x')"]
    )
    status, body = _call(
        port,
        "/api/run/stream",
        token="tok123",  # noqa: S106 — test fixture
        method="POST",
        payload={"worker": "opencode", "brief": "x"},
    )
    assert status == 200
    assert '"run_id": "' in body


# ── executor mode / sandbox status ───────────────────────────────────────────


def test_executor_mode_roundtrip(tmp_path):
    mod = _load()
    assert mod.executor_mode_enabled(tmp_path) is True  # absent -> ON (fail closed)
    mod.executor_mode_set(False, tmp_path)
    assert mod.executor_mode_enabled(tmp_path) is False
    mod.executor_mode_set(True, tmp_path)
    assert mod.executor_mode_enabled(tmp_path) is True


def test_executor_mode_accepts_off_with_comment(tmp_path):
    mod = _load()
    (tmp_path / ".solocode").mkdir()
    (tmp_path / ".solocode" / "executor-mode").write_text(
        "off  # later\n", encoding="utf-8"
    )
    assert mod.executor_mode_enabled(tmp_path) is False


def test_state_reports_executor_mode_and_sandboxes(server, monkeypatch):
    mod, port = server
    monkeypatch.setattr(mod, "executor_mode_enabled", lambda root=None: False)
    status, body = _call(port, "/api/state", token="tok123")  # noqa: S106 — test fixture
    assert status == 200
    payload = json.loads(body)
    assert payload["executor_mode"] is False
    assert "opencode" in payload["sandboxes"]


def test_executor_mode_endpoint_toggles(server, monkeypatch):
    mod, port = server
    seen: dict = {}
    monkeypatch.setattr(
        mod, "executor_mode_set", lambda on, root=None: seen.update(on=on)
    )
    monkeypatch.setattr(
        mod, "executor_mode_enabled", lambda root=None: bool(seen.get("on"))
    )
    status, body = _call(
        port,
        "/api/executor-mode",
        token="tok123",  # noqa: S106 — test fixture
        method="POST",
        payload={"on": True},
    )
    assert status == 200
    assert seen["on"] is True
    assert json.loads(body)["executor_mode"] is True


# ── stop ─────────────────────────────────────────────────────────────────────


def test_stop_run_and_stop_all():
    mod = _load()
    killed: list[int] = []

    class FakeProc:
        def kill(self):
            killed.append(1)

    mod._RUNNING["a"] = FakeProc()
    mod._RUNNING["b"] = FakeProc()
    assert mod.stop_run("a") is True
    assert mod.stop_run("a") is False  # already gone
    assert mod.stop_all() == 1
    assert len(killed) == 2


def test_stop_endpoint_unknown_run(server):
    _, port = server
    status, body = _call(
        port,
        "/api/stop",
        token="tok123",  # noqa: S106 — test fixture
        method="POST",
        payload={"run_id": "nope"},
    )
    assert status == 200
    assert json.loads(body)["stopped"] == 0


# ── JEV agent worker ─────────────────────────────────────────────────────────


def test_build_argv_jev_adds_agent_and_session():
    mod = _load()
    assert mod.build_argv("jev", "b", None, {}) == [
        sys.executable,
        "tools/opencode_delegate.py",
        "b",
        "--agent",
        "jev",
    ]
    assert mod.build_argv("jev", "b", "m", {"session": "ses_1"}) == [
        sys.executable,
        "tools/opencode_delegate.py",
        "b",
        "--agent",
        "jev",
        "--model",
        "m",
        "--session",
        "ses_1",
    ]


def test_workers_includes_the_jev_agent(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    by_id = {w["id"]: w for w in mod.workers()}
    assert by_id["jev"]["ready"] is True
    assert by_id["jev"]["cli"] == "opencode"
