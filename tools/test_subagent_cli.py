#!/usr/bin/env python3
"""Tests for tools/subagent_cli.py — the CLI Provider + dispatcher."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "subagent_cli.py"


def _load():
    spec = importlib.util.spec_from_file_location("subagent_cli_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── registry ─────────────────────────────────────────────────────────────────


def test_every_worker_wrapper_exists():
    mod = _load()
    assert {w.id for w in mod.WORKERS} == {"opencode", "kilo", "dsh", "codex", "gemini"}
    for worker in mod.WORKERS:
        assert (ROOT / worker.wrapper).is_file(), worker.wrapper


def test_router_cli_worker_ids_map_to_cli_providers():
    mod = _load()
    from tools import router

    provider_ids = {worker.id for worker in mod.WORKERS}
    cli_ids = {
        worker_id
        for worker_id in router.DECLARED_WORKERS
        if worker_id.startswith("cli:")
    }
    assert {
        mod.provider_id_for_router_worker(worker_id) for worker_id in cli_ids
    } <= provider_ids
    assert mod.provider_id_for_router_worker("cli:antigravity") == "gemini"
    assert mod.provider_id_for_router_worker("none") is None
    assert mod.provider_id_for_router_worker("subagent:explore") is None
    assert mod.provider_id_for_router_worker("in_process:solo-code-engineer") is None


def test_provider_is_a_runtime_and_advertises_read_bash_only():
    mod = _load()
    for worker in mod.WORKERS:
        provider = mod.CliProvider(worker)
        assert isinstance(provider, mod.SubagentRuntime)
        assert provider.supported_capabilities() == frozenset({"read", "bash"})


# ── argv mapping ─────────────────────────────────────────────────────────────


def test_build_argv_per_worker():
    mod = _load()
    q = mod.SubagentRequest
    assert mod.build_argv(
        "codex", q(prompt="p", model="gpt-6.1-sol", options={"allow_write": True})
    ) == [
        sys.executable,
        "tools/codex_delegate.py",
        "p",
        "--model",
        "gpt-6.1-sol",
        "--allow-write",
    ]
    assert (
        mod.build_argv("gemini", q(prompt="p", options={"allow_tools": True}))[-1]
        == "--allow-tools"
    )
    assert (
        mod.build_argv("opencode", q(prompt="p", options={"free": True}))[-1]
        == "--free"
    )
    assert mod.build_argv("dsh", q(prompt="p")) == [
        sys.executable,
        "tools/dsh_delegate.py",
        "p",
    ]


def test_build_argv_rejects_unknown_worker():
    mod = _load()
    with pytest.raises(ValueError):
        mod.build_argv("nope", mod.SubagentRequest(prompt="p"))


# ── run() evidence contract ──────────────────────────────────────────────────


def test_run_splits_evidence_from_summary():
    mod = _load()
    provider = mod.CliProvider(
        mod.WORKERS[0], runner=lambda _a, _t: (0, "the prose", "")
    )
    result = provider.run(mod.SubagentRequest(prompt="p"))
    assert result.ok is True
    assert result.summary == "the prose"  # untrusted channel
    assert result.evidence["exit_code"] == 0  # trusted channel
    assert result.evidence["argv"][1] == "tools/opencode_delegate.py"
    assert result.stop_reason == "completed"


def test_run_failure_puts_error_in_evidence():
    mod = _load()
    provider = mod.CliProvider(mod.WORKERS[0], runner=lambda _a, _t: (7, "", "boom"))
    result = provider.run(mod.SubagentRequest(prompt="p"))
    assert result.ok is False
    assert result.evidence["error"] == "boom"
    assert result.stop_reason == "error"


def test_run_timeout_maps_to_stop_reason():
    mod = _load()

    def boom(_argv, _t):
        raise TimeoutError("slow")

    result = mod.CliProvider(mod.WORKERS[0], runner=boom).run(
        mod.SubagentRequest(prompt="p", timeout_s=5)
    )
    assert result.ok is False
    assert result.stop_reason == "timeout"
    assert "5s" in str(result.evidence["error"])


def test_run_fails_loud_on_unadvertised_capability():
    mod = _load()
    request = mod.SubagentRequest(prompt="p", capabilities=frozenset({"write"}))
    with pytest.raises(ValueError):
        mod.CliProvider(mod.WORKERS[0], runner=lambda _a, _t: (0, "", "")).run(request)


# ── dispatcher ───────────────────────────────────────────────────────────────


def test_select_provider_defaults_to_cheapest_capable():
    mod = _load()
    assert mod.select_provider(mod.SubagentRequest(prompt="p")).id == "opencode"


def test_select_provider_honours_explicit_worker():
    mod = _load()
    req = mod.SubagentRequest(prompt="p", options={"worker": "codex"})
    assert mod.select_provider(req).id == "codex"


def test_select_provider_errors():
    mod = _load()
    with pytest.raises(ValueError):
        mod.select_provider(mod.SubagentRequest(prompt="p", options={"worker": "nope"}))
    with pytest.raises(ValueError):
        mod.select_provider(
            mod.SubagentRequest(prompt="p", capabilities=frozenset({"write"}))
        )


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_main_self_test():
    mod = _load()
    assert mod.main(["--self-test"]) == 0


def test_main_list(capsys):
    mod = _load()
    assert mod.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "opencode" in out and "ready=True" in out


def test_main_without_prompt_returns_one(capsys):
    mod = _load()
    assert mod.main([]) == 1


def test_main_runs_when_provider_is_mocked(monkeypatch, capsys):
    mod = _load()
    provider = mod.CliProvider(
        mod.WORKERS[0], runner=lambda _a, _t: (0, "ok output", "")
    )
    monkeypatch.setattr(mod, "select_provider", lambda _req: provider)
    assert mod.main(["do x", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["worker"] == "opencode"
    assert payload["ok"] is True
    assert payload["summary"] == "ok output"
