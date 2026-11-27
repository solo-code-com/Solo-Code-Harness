"""Tests for tools/tier_gate.py."""

from __future__ import annotations

import os

from tools import tier_gate


def _write_marker(path, value):
    (path / ".harness-tier").write_text(value, encoding="utf-8")


def _write_opencode(path, text):
    target = path / ".opencode"
    target.mkdir(parents=True, exist_ok=True)
    (target / "opencode.json").write_text(text, encoding="utf-8")


def test_missing_marker_is_client(tmp_path):
    assert tier_gate.resolve_tier(tmp_path) == "client"


def test_invalid_marker_is_client(tmp_path):
    _write_marker(tmp_path, "whatever")
    assert tier_gate.resolve_tier(tmp_path) == "client"


def test_empty_marker_is_client(tmp_path):
    _write_marker(tmp_path, "   \n")
    assert tier_gate.resolve_tier(tmp_path) == "client"


def test_marker_values_round_trip(tmp_path):
    for value in ("client", "personal", "public", " Public \n"):
        _write_marker(tmp_path, value)
        assert tier_gate.resolve_tier(tmp_path) == value.strip().lower()


def test_marker_inherited_from_parent(tmp_path):
    _write_marker(tmp_path, "personal")
    child = tmp_path / "a" / "b"
    child.mkdir(parents=True)
    assert tier_gate.resolve_tier(child) == "personal"


def test_nearest_marker_wins(tmp_path):
    _write_marker(tmp_path, "public")
    child = tmp_path / "sub"
    child.mkdir()
    _write_marker(child, "client")
    assert tier_gate.resolve_tier(child) == "client"


def test_non_client_tier_never_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.freemodel.dev/v1")
    _write_opencode(
        tmp_path,
        '{"providers": {"freemodel": {"settings": {"baseURL": "https://cc.freemodel.dev"}}}}',
    )
    for tier in ("personal", "public"):
        _write_marker(tmp_path, tier)
        assert tier_gate.refusal_for_project(tmp_path, ("OPENAI_BASE_URL",)) is None


def test_client_tier_allows_freemodel_env_var(tmp_path, monkeypatch):
    _write_marker(tmp_path, "client")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.freemodel.dev/v1")
    assert tier_gate.refusal_for_project(tmp_path, ("OPENAI_BASE_URL",)) is None


def test_client_tier_allows_freemodel_project_provider(tmp_path):
    _write_marker(tmp_path, "client")
    _write_opencode(
        tmp_path,
        '{"providers": {"freemodel": {"settings": {"baseURL": "https://cc.freemodel.dev"}}}}',
    )
    assert tier_gate.refusal_for_project(tmp_path) is None


def test_client_tier_allows_named_providers(tmp_path, monkeypatch):
    _write_marker(tmp_path, "client")
    _write_opencode(
        tmp_path,
        '{"providers": {"commandcode": {"settings": {"baseURL": "https://api.commandcode.ai/provider/v1"}}}}',
    )
    for name in tier_gate.PROVIDER_URL_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.commandcode.ai/provider/v1")
    assert tier_gate.refusal_for_project(tmp_path, ("OPENAI_BASE_URL",)) is None


def test_client_tier_ignores_unlisted_env_vars(tmp_path, monkeypatch):
    """A stale variable outside var_names must not block an unrelated run."""
    _write_marker(tmp_path, "client")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://cc.freemodel.dev")
    assert tier_gate.refusal_for_project(tmp_path, ("OPENAI_BASE_URL",)) is None


def test_banned_host_is_empty_when_nothing_is_banned():
    assert tier_gate.banned_host("https://api-cc.freemodel.dev") is None
    assert tier_gate.banned_host("https://api.deepseek.com") is None
    assert tier_gate.banned_host(None) is None


def test_repo_root_has_no_tier_marker():
    """The harness repo must not pin a tier for every project it deploys into."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.exists(os.path.join(root, ".harness-tier"))
