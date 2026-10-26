"""Tests for tools/worker_preflight.py - the worker readiness table.

Hermetic by design: every finder, the probe and the provider lookup are monkeypatched, so
no test spawns a real binary, contacts a server or reads the provider env. What is under
test is the reporting contract -- a missing optional worker is reported but not failed,
`--strict` is the one failure mode, and no credential can reach the output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import worker_preflight as preflight


def _which(name: str) -> str | None:
    """Fake PATH: codex, pnpm and wsl present, nothing else."""
    return {"codex": "/fake/codex", "pnpm": "/fake/pnpm", "wsl": "/fake/wsl"}.get(name)


@pytest.fixture
def all_ready(monkeypatch):
    """Seven ready workers with every external dependency replaced."""
    monkeypatch.setattr(
        preflight.opencode_delegate,
        "find_opencode_binary",
        lambda: "/fake/opencode",
    )
    monkeypatch.setattr(
        preflight.kilo_cli_delegate, "find_kilo_binary", lambda: "/fake/kilo"
    )
    monkeypatch.setattr(
        preflight.kilo_server_manager, "health_check", lambda *a, **k: True
    )
    monkeypatch.setattr(
        preflight.antigravity_delegate, "find_agy_binary", lambda: "/fake/agy"
    )
    monkeypatch.setattr(
        preflight.dsh_delegate, "find_dsh_dir", lambda explicit=None: Path("/fake/dsh")
    )
    monkeypatch.setattr(
        preflight.env_file,
        "provider_endpoint",
        lambda provider, kind="chat": ("https://example.invalid/v1", "fake-key"),
    )
    monkeypatch.setattr(preflight.shutil, "which", _which)
    monkeypatch.setattr(preflight, "probe_version", lambda binary: (True, "1.2.3"))

    class _WslProbe:
        returncode = 0
        stdout = "/home/fake"
        stderr = ""

    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: _WslProbe())


def _row_line(output: str, worker: str) -> str:
    return next(line for line in output.splitlines() if line.startswith(worker))


def test_all_ready_exits_zero(all_ready, capsys):
    assert preflight.main([]) == 0
    out = capsys.readouterr().out
    assert "7 ready, 0 unusable" in out
    assert "unusable" not in out.replace("0 unusable", "")


def test_missing_binary_is_reported_not_failed(all_ready, monkeypatch, capsys):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    assert preflight.main([]) == 0

    out = capsys.readouterr().out
    row = _row_line(out, "codex")
    assert " no " in row
    assert row.rstrip().endswith("unusable")
    assert preflight.check_codex()["state"] == "not on PATH"


def test_strict_exits_one_when_a_worker_is_missing(all_ready, monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    assert preflight.main(["--strict"]) == 1


def test_strict_exits_zero_when_all_ready(all_ready):
    assert preflight.main(["--strict"]) == 0


def test_model_api_without_env_reports_missing(all_ready, monkeypatch, capsys):
    monkeypatch.setattr(
        preflight.env_file,
        "provider_endpoint",
        lambda provider, kind="chat": ("", ""),
    )

    assert preflight.main([]) == 0

    out = capsys.readouterr().out
    row = _row_line(out, "model_api")
    assert "missing" in row
    assert row.rstrip().endswith("unusable")


def test_model_api_never_prints_the_key(all_ready, monkeypatch, capsys):
    key_value = "sk-live-SECRET-SHOULD-NOT-APPEAR"
    monkeypatch.setattr(
        preflight.env_file,
        "provider_endpoint",
        lambda provider, kind="chat": ("https://example.invalid/v1", key_value),
    )

    assert preflight.main([]) == 0
    assert preflight.main(["--json"]) == 0

    captured = capsys.readouterr()
    assert key_value not in captured.out
    assert "configured" in captured.out


def test_json_shape(all_ready, capsys):
    assert preflight.main(["--json"]) == 0
    out = capsys.readouterr().out

    payload = json.loads(out)

    assert payload["ready"] == 7
    assert payload["unusable"] == 0
    assert [row["worker"] for row in payload["workers"]] == [
        "opencode",
        "kilo",
        "codex",
        "antigravity",
        "model_api",
        "dsh",
        "wsl-sandbox",
    ]
    for row in payload["workers"]:
        assert set(row) == {"worker", "found", "state", "verdict"}
        assert row["verdict"] in {"ready", "unusable"}
        assert isinstance(row["found"], bool)


class FakeResult:
    """The bits of `subprocess.CompletedProcess` the wsl probe reads."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _sequence(*results: FakeResult):
    """Fake `subprocess.run` that answers each call with the next result."""
    remaining = list(results)

    def run(*args, **kwargs):
        return remaining.pop(0)

    return run


def test_wsl_sandbox_reports_absent_when_wsl_is_missing(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    row = preflight.check_wsl_sandbox()

    assert row["verdict"] == "unusable"
    assert row["state"] == "WSL not available"
    assert row["found"] is False


def test_wsl_sandbox_reports_a_failed_probe(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/fake/wsl")

    def boom(*args, **kwargs):
        raise OSError("wsl exploded")

    monkeypatch.setattr(preflight.subprocess, "run", boom)

    row = preflight.check_wsl_sandbox()

    assert row["verdict"] == "unusable"
    assert row["state"] == "WSL probe failed"


def test_wsl_sandbox_reports_missing_unshare(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/fake/wsl")
    monkeypatch.setattr(
        preflight.subprocess, "run", _sequence(FakeResult(returncode=1))
    )

    row = preflight.check_wsl_sandbox()

    assert row["verdict"] == "unusable"
    assert row["state"] == "WSL up; unshare missing"


def test_wsl_sandbox_reports_unreadable_home(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/fake/wsl")
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        _sequence(FakeResult(returncode=0), FakeResult(returncode=1, stdout="/home/u")),
    )

    row = preflight.check_wsl_sandbox()

    assert row["verdict"] == "unusable"
    assert row["state"] == "unshare ok; home unreadable"


def test_wsl_sandbox_reports_missing_cli(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/fake/wsl")
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        _sequence(
            FakeResult(returncode=0),
            FakeResult(returncode=0, stdout="/home/u"),
            FakeResult(returncode=1),
        ),
    )

    row = preflight.check_wsl_sandbox()

    assert row["verdict"] == "unusable"
    assert row["state"] == "unshare ok; opencode not installed"


def test_wsl_sandbox_reports_ready_with_a_version(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/fake/wsl")
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        _sequence(
            FakeResult(returncode=0),
            FakeResult(returncode=0, stdout="/home/u"),
            FakeResult(returncode=0),
            FakeResult(returncode=0, stdout="opencode 2.1.4"),
        ),
    )

    row = preflight.check_wsl_sandbox()

    assert row["verdict"] == "ready"
    assert row["state"] == "unshare ok; opencode 2.1.4"


def test_wsl_sandbox_falls_back_to_stderr_for_the_version(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/fake/wsl")
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        _sequence(
            FakeResult(returncode=0),
            FakeResult(returncode=0, stdout="/home/u"),
            FakeResult(returncode=0),
            FakeResult(returncode=0, stdout="", stderr="opencode 2.1.4\n"),
        ),
    )

    row = preflight.check_wsl_sandbox()

    assert row["verdict"] == "ready"
    assert row["state"] == "unshare ok; opencode 2.1.4"
