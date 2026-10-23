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
    """Fake PATH: codex and pnpm present, nothing else."""
    return {"codex": "/fake/codex", "pnpm": "/fake/pnpm"}.get(name)


@pytest.fixture
def all_ready(monkeypatch):
    """Six ready workers with every external dependency replaced."""
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


def _row_line(output: str, worker: str) -> str:
    return next(line for line in output.splitlines() if line.startswith(worker))


def test_all_ready_exits_zero(all_ready, capsys):
    assert preflight.main([]) == 0
    out = capsys.readouterr().out
    assert "6 ready, 0 unusable" in out
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

    assert payload["ready"] == 6
    assert payload["unusable"] == 0
    assert [row["worker"] for row in payload["workers"]] == [
        "opencode",
        "kilo",
        "codex",
        "antigravity",
        "model_api",
        "dsh",
    ]
    for row in payload["workers"]:
        assert set(row) == {"worker", "found", "state", "verdict"}
        assert row["verdict"] in {"ready", "unusable"}
        assert isinstance(row["found"], bool)
