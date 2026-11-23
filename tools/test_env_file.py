"""Tests for tools/env_file.py, including the drift guard that would have caught today's bug.

The bug it pins: `env_file` normalised `DEEPSEEK_BASE_URL` by stripping a trailing `/v1`, but the
generated provider config uses that variable verbatim (`"baseURL": "${DEEPSEEK_BASE_URL}"`) while
it appends `/v1` only for `OPENAI_BASE_URL`. The export therefore handed OpenCode
`https://api.deepseek.com` instead of `.../v1`, and every deepseek call through the OpenCode
delegate had the wrong endpoint. The rule is derived from the generated config rather than from
memory, so it cannot drift again.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import env_file  # noqa: E402

CONFIG = ROOT / ".opencode" / "opencode.json"
LAUNCHER = ROOT / "opencode-env.ps1"


def _templates() -> list[tuple[str, str]]:
    """Every `${VAR...}` baseURL in the generated config, as (template, var name)."""
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    for provider in (data.get("providers") or {}).values():
        base = ((provider or {}).get("settings") or {}).get("baseURL") or ""
        if "${" in base:
            found.append((base, base.split("${", 1)[1].split("}", 1)[0]))
    return found


def test_env_file_reads_and_strips_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        'PLAIN=value\nQUOTED="two words"\n# comment\nEMPTY=\n', encoding="utf-8"
    )
    values = env_file.parse_env_file(env)
    assert values["PLAIN"] == "value"
    assert values["QUOTED"] == "two words"
    assert "EMPTY" in values


def test_load_project_env_no_longer_derives_the_freemodel_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The freemodel provider is gone, so nothing may invent FREEMODEL_API_KEY any more."""
    (tmp_path / ".env").write_text(
        'OPENAI_API_KEY=sk-test\nOPENAI_BASE_URL="https://x/v1"\n', encoding="utf-8"
    )
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "FREEMODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    env_file.load_project_env(tmp_path)
    assert "FREEMODEL_API_KEY" not in os.environ, "alias must not be derived"


def test_model_override_reads_the_env_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLOCODE_MODEL_KILO", "deepseek/deepseek-v4.1-flash")
    assert env_file.model_override("kilo") == "deepseek/deepseek-v4.1-flash"
    monkeypatch.delenv("SOLOCODE_MODEL_KILO")
    assert env_file.model_override("kilo") is None
    assert env_file.model_override("no-such-role") is None


def test_normalisation_matches_how_the_config_uses_each_variable() -> None:
    """The drift guard: strip `/v1` exactly where the config appends it, nowhere else."""
    templates = _templates()
    assert templates, "expected at least one templated baseURL in the generated config"
    appends = {
        var
        for template, var in templates
        if "/v1" in template.removeprefix("${" + var + "}")
    }
    verbatim = {var for template, var in templates if var not in appends}
    assert set(env_file.NORMALIZE_BASE_URLS) == appends, (
        "NORMALIZE_BASE_URLS must list exactly the variables whose baseURL template appends /v1: "
        f"strip={sorted(env_file.NORMALIZE_BASE_URLS)} appends={sorted(appends)}"
    )
    assert not (set(env_file.NORMALIZE_BASE_URLS) & verbatim), (
        "a variable used verbatim must never be normalised (this was today's bug)"
    )


def test_launcher_covers_the_aliases_env_file_derives() -> None:
    """The manual launcher must be a superset of what the delegates do, or manual use differs."""
    text = LAUNCHER.read_text(encoding="utf-8")
    assignments = dict(re.findall(r'\$env:(\w+)\s*=\s*\$config\["(\w+)"\]', text))
    for alias, source in env_file.ALIASES.items():
        assert assignments.get(alias) == source, (
            f"opencode-env.ps1 does not derive {alias} from {source}; manual launches would differ "
            "from delegate launches"
        )


def test_provider_endpoint_prefers_the_raw_env_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A URL built for a request must not be normalised: the API path needs its /v1."""
    raw = "https://api.deepseek.com/v1"
    monkeypatch.setattr(
        env_file,
        "parse_env_file",
        lambda _path: {
            "DEEPSEEK_BASE_URL": raw,
            "DEEPSEEK_API_KEY": "test-key",
        },
    )
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    url, key = env_file.provider_endpoint("deepseek", "chat")
    assert "/v1" in url, f"{url} should keep the raw base {raw}"
    assert url == raw + "/chat/completions"
    assert key == "test-key"


def test_provider_endpoint_keeps_the_models_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The models kind is built from the same raw base, so it keeps its /v1 too."""
    raw = "https://api.deepseek.com/v1"
    monkeypatch.setattr(
        env_file,
        "parse_env_file",
        lambda _path: {
            "DEEPSEEK_BASE_URL": raw,
            "DEEPSEEK_API_KEY": "test-key",
        },
    )
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    url, key = env_file.provider_endpoint("deepseek", "models")
    assert url == raw + "/models"
    assert key == "test-key"


def _point_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> None:
    """Give `provider_endpoint` a temp `.env`, parsed by the real reader."""
    env = tmp_path / ".env"
    env.write_text(text, encoding="utf-8")
    real_parse = env_file.parse_env_file
    monkeypatch.setattr(env_file, "parse_env_file", lambda _path: real_parse(env))
    monkeypatch.setattr(env_file, "_WARNED_KEY_CONFLICTS", set())


def test_provider_endpoint_warns_when_the_shell_shadows_the_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stale export wins silently today; the warning is the only pointer to the 401's cause."""
    _point_at(monkeypatch, tmp_path, "COMMANDCODE_API_KEY=file-key\n")
    monkeypatch.setenv("COMMANDCODE_API_KEY", "shell-key")
    _url, key = env_file.provider_endpoint("commandcode", "chat")
    err = capsys.readouterr().err
    assert key == "shell-key", "the shell still wins"
    assert err.count("COMMANDCODE_API_KEY differs between the shell and .env") == 1
    assert "shell-key" not in err and "file-key" not in err, (
        "the warning must not leak the key"
    )


def test_provider_endpoint_warns_once_per_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A delegate calls this per request; the hint must not flood stderr."""
    _point_at(monkeypatch, tmp_path, "COMMANDCODE_API_KEY=file-key\n")
    monkeypatch.setenv("COMMANDCODE_API_KEY", "shell-key")
    env_file.provider_endpoint("commandcode", "chat")
    env_file.provider_endpoint("commandcode", "chat")
    assert capsys.readouterr().err.count("differs between the shell and .env") == 1


def test_provider_endpoint_is_silent_when_the_values_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _point_at(monkeypatch, tmp_path, "COMMANDCODE_API_KEY=same-key\n")
    monkeypatch.setenv("COMMANDCODE_API_KEY", "same-key")
    _url, key = env_file.provider_endpoint("commandcode", "chat")
    assert key == "same-key"
    assert "differs between the shell and .env" not in capsys.readouterr().err


def test_provider_endpoint_is_silent_when_only_the_env_file_has_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One source means nothing is shadowed, so the file value is used without comment."""
    _point_at(monkeypatch, tmp_path, "COMMANDCODE_API_KEY=file-key\n")
    monkeypatch.delenv("COMMANDCODE_API_KEY", raising=False)
    _url, key = env_file.provider_endpoint("commandcode", "chat")
    assert key == "file-key"
    assert "differs between the shell and .env" not in capsys.readouterr().err
