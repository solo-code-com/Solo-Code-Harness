"""Tests for tools/provider_audit.py - the banned-provider config audit.

Hermetic by design: every test points `main` at a `tmp_path` directory or file, so the real
`~/.claude` and `~/.codex` are never read. What is under test is the reporting contract --
which lines count as a violation, what the exit code says, and that no credential value can
reach the output.
"""

from __future__ import annotations

import json

from tools import provider_audit as audit


def test_banned_host_in_env_is_reported_without_any_value(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text(
        "ANTHROPIC_BASE_URL=https://cc.freemodel.dev\n"
        "TAI_KHOAN_BAT_KY=supersecretvalue\n",
        encoding="utf-8",
    )

    assert audit.main([str(tmp_path)]) == 1

    out = capsys.readouterr().out
    assert "banned-host" in out
    assert "cc.freemodel.dev" in out
    assert "TAI_KHOAN" not in out
    assert "supersecretvalue" not in out


def test_env_without_a_banned_host_is_clean(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text(
        "ANTHROPIC_BASE_URL=https://api.anthropic.com\n",
        encoding="utf-8",
    )

    assert audit.main([str(tmp_path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["violations"] == []


def test_banned_env_var_name_is_reported(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("FREEMODEL_API_KEY=x\n", encoding="utf-8")

    assert audit.main([str(tmp_path)]) == 1

    out = capsys.readouterr().out
    assert "banned-env-var" in out
    assert "FREEMODEL_API_KEY" in out
    assert "FREEMODEL_API_KEY=x" not in out


def test_codex_unsafe_settings_are_two_violations(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text(
        '[sandbox]\nsandbox_mode = "danger-full-access"\napproval_policy = "never"\n',
        encoding="utf-8",
    )

    assert audit.main([str(config), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    kinds = sorted(violation["kind"] for violation in payload["violations"])
    assert kinds == ["codex-approval-never", "codex-danger-full-access"]
    assert payload["violations"][0]["path"] == str(config)


def test_json_reports_the_documented_schema(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("FREEMODEL_API_KEY=x\n", encoding="utf-8")

    assert audit.main([str(tmp_path), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"ok", "checked", "violations"}
    assert payload["ok"] is False
    assert payload["checked"] == 1
    assert len(payload["violations"]) == 1
    for violation in payload["violations"]:
        assert set(violation) == {"path", "line", "kind", "detail"}
        assert isinstance(violation["line"], int)


def test_directory_without_known_files_is_clean(tmp_path, capsys):
    (tmp_path / "unrelated.txt").write_text("api.freemodel.dev\n", encoding="utf-8")

    assert audit.main([str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "0 violation(s)" in out


def test_json_checked_count_survives_an_empty_directory(tmp_path, capsys):
    assert audit.main([str(tmp_path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "checked": 0, "violations": []}


def test_comment_lines_are_not_wiring(tmp_path):
    """A commented example must not fail the gate; a live line must."""
    env = tmp_path / ".env"
    env.write_text(
        "# ANTHROPIC_BASE_URL=https://cc.freemodel.dev\n"
        "# [removed freemodel] OPENAI_BASE_URL=https://api.freemodel.dev\n"
        "DEEPSEEK_API_KEY=sk-x\n",
        encoding="utf-8",
    )
    assert audit.audit_file(env) == []
