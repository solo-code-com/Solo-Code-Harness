"""Tests for tools/provider_audit.py - the banned-provider config audit.

Hermetic by design: every test points `main` at a `tmp_path` directory or file, so the real
`~/.claude` and `~/.codex` are never read. What is under test is the reporting contract --
which lines count as a violation, what the exit code says, and that no credential value can
reach the output.
"""

from __future__ import annotations

import json

from tools import provider_audit as audit


def test_freemodel_host_is_now_allowed(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text(
        "ANTHROPIC_BASE_URL=https://cc.freemodel.dev\n"
        "TAI_KHOAN_BAT_KY=supersecretvalue\n",
        encoding="utf-8",
    )

    assert audit.main([str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "banned-host" not in out
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


def test_freemodel_key_is_now_allowed(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("FREEMODEL_API_KEY=x\n", encoding="utf-8")

    assert audit.main([str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "banned-env-var" not in out
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
    config = tmp_path / "config.toml"
    config.write_text(
        '[sandbox]\nsandbox_mode = "danger-full-access"\n', encoding="utf-8"
    )

    assert audit.main([str(config), "--json"]) == 1

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


def _project_with_provider(root, env_lines):
    cfg = root / ".opencode"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "opencode.json").write_text(
        '{"providers": {"deepseek": {"env": ["DEEPSEEK_API_KEY"]}}}', encoding="utf-8"
    )
    (root / ".env").write_text(env_lines, encoding="utf-8")


def test_missing_provider_key_is_a_warning(tmp_path):
    _project_with_provider(tmp_path, "OTHER=1\n")
    warnings = audit.missing_provider_keys(tmp_path)
    assert [w["var"] for w in warnings] == ["DEEPSEEK_API_KEY"]


def test_present_provider_key_is_silent(tmp_path):
    _project_with_provider(tmp_path, "DEEPSEEK_API_KEY=sk-x\n")
    assert audit.missing_provider_keys(tmp_path) == []


def test_warnings_do_not_change_the_exit_code(tmp_path, capsys):
    _project_with_provider(tmp_path, "OTHER=1\n")
    assert audit.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "warning(s)" in out
