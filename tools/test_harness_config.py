#!/usr/bin/env python3
"""Tests for tools/harness_config.py — layered config resolution."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "harness_config.py"


def _load():
    spec = importlib.util.spec_from_file_location("harness_config_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── _deep_merge ──────────────────────────────────────────────────────────────


def test_deep_merge_is_recursive_and_override_wins():
    mod = _load()
    merged = mod._deep_merge({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 9, "z": 3}})
    assert merged == {"a": {"x": 1, "y": 9, "z": 3}, "b": 1}


def test_deep_merge_replaces_a_dict_with_a_scalar():
    mod = _load()
    assert mod._deep_merge({"a": {"x": 1}}, {"a": 5}) == {"a": 5}


# ── HarnessConfig ────────────────────────────────────────────────────────────


def test_get_uses_dotted_path_and_default():
    mod = _load()
    cfg = mod.HarnessConfig(
        data={"gates": {"security": False}},
        project_root=Path("/p"),
        global_dir=Path("/g"),
        sources=[("built-in defaults", {})],
    )
    assert cfg.get("gates.security") is False
    assert cfg.get("gates.missing") is None
    assert cfg.get("gates.missing", "dflt") == "dflt"
    assert cfg.get("not.a.path", 42) == 42


def test_gate_skill_and_profile_helpers():
    mod = _load()
    cfg = mod.HarnessConfig(
        data={
            "gates": {"security": True},
            "skills": {"disabled": ["plan"]},
            "hooks": {"profile": "strict"},
        },
        project_root=Path("/p"),
        global_dir=Path("/g"),
        sources=[("built-in defaults", {})],
    )
    assert cfg.is_gate_enabled("security") is True
    assert cfg.is_gate_enabled("unknown") is True  # absent gate defaults to enabled
    assert cfg.is_skill_disabled("plan") is True
    assert cfg.is_skill_disabled("other") is False
    assert cfg.get_hooks_profile() == "strict"


def test_debug_print_sources_lists_layers(capsys):
    mod = _load()
    cfg = mod.HarnessConfig(
        data={},
        project_root=Path("/p"),
        global_dir=Path("/g"),
        sources=[("built-in defaults", {}), ("project (x)", {})],
    )
    cfg.debug_print_sources()
    out = capsys.readouterr().out
    assert "Config layers" in out
    assert "project (x)" in out
    assert "Project root:" in out


# ── _strip_jsonc / _load_file ────────────────────────────────────────────────


def test_strip_jsonc_removes_line_comments():
    mod = _load()
    src = '{\n  // lead\n  "a": 1, // trail\n  "b": 2\n}\n'
    assert json.loads(mod._strip_jsonc(src)) == {"a": 1, "b": 2}


def test_load_file_missing_returns_none(tmp_path):
    mod = _load()
    assert mod._load_file(tmp_path / "nope.toml") is None


def test_load_file_unknown_extension_returns_none(tmp_path):
    mod = _load()
    p = tmp_path / "x.yaml"
    p.write_text("a: 1\n", encoding="utf-8")
    assert mod._load_file(p) is None


def test_load_file_reads_toml_and_json(tmp_path):
    mod = _load()
    t = tmp_path / "settings.toml"
    t.write_text("[gates]\nsecurity = false\n", encoding="utf-8")
    if sys.version_info >= (3, 11):
        assert mod._load_file(t) == {"gates": {"security": False}}
    else:
        # harness_config documents that TOML needs 3.11+; on 3.10 it raises rather than guessing.
        with pytest.raises(RuntimeError, match="tomllib"):
            mod._load_file(t)
    j = tmp_path / "settings.json"
    j.write_text('{"gates": {"lint": false}}', encoding="utf-8")
    assert mod._load_file(j) == {"gates": {"lint": False}}


def test_load_file_reads_jsonc(tmp_path):
    mod = _load()
    p = tmp_path / "settings.jsonc"
    p.write_text(
        '{\n  // comment\n  "gates": {"security": false}\n}\n', encoding="utf-8"
    )
    assert mod._load_file(p) == {"gates": {"security": False}}


# ── path discovery ───────────────────────────────────────────────────────────


def test_find_project_root_prefers_agents_md_plus_kilo(tmp_path):
    mod = _load()
    (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
    (tmp_path / ".kilo").mkdir()
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert mod._find_project_root(deep) == tmp_path


def test_find_project_root_falls_back_to_git(tmp_path):
    mod = _load()
    (tmp_path / ".git").mkdir()
    deep = tmp_path / "x" / "y"
    deep.mkdir(parents=True)
    assert mod._find_project_root(deep) == tmp_path


def test_get_global_config_dir_honours_env(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setenv("SOLOCODE_HARNESS_DIR", str(tmp_path))
    assert mod._get_global_config_dir() == tmp_path.resolve()


def test_get_global_config_dir_defaults_to_home(monkeypatch):
    mod = _load()
    monkeypatch.delenv("SOLOCODE_HARNESS_DIR", raising=False)
    d = mod._get_global_config_dir()
    assert d.name == ".solocode"
    assert d.parent == Path.home()


def test_find_config_file_prefers_toml(tmp_path):
    mod = _load()
    assert mod._find_config_file(tmp_path) is None
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    assert mod._find_config_file(tmp_path).name == "settings.json"
    (tmp_path / "settings.toml").write_text("", encoding="utf-8")
    assert mod._find_config_file(tmp_path).name == "settings.toml"


# ── environment overrides ────────────────────────────────────────────────────


def test_env_overrides_parse_bools_and_lists(monkeypatch):
    mod = _load()
    monkeypatch.setattr(
        mod.os,
        "environ",
        {
            "SOLOCODE_GATES_SECURITY": "false",
            "SOLOCODE_SKILLS_DISABLED": "a, b ,,c",
            "SOLOCODE_HOOKS_PROFILE": "minimal",
        },
    )
    overrides = mod._load_env_overrides()
    assert overrides["gates"]["security"] is False
    assert overrides["skills"]["disabled"] == ["a", "b", "c"]
    assert overrides["hooks"]["profile"] == "minimal"


def test_env_overrides_empty_when_nothing_is_set(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod.os, "environ", {})
    assert mod._load_env_overrides() == {}


# ── load_harness_config layering ─────────────────────────────────────────────


def test_load_defaults_only(tmp_path):
    mod = _load()
    cfg = mod.load_harness_config(
        project_root=tmp_path / "p", global_dir=tmp_path / "g"
    )
    assert cfg.get("gates.security") is True
    assert cfg.get_hooks_profile() == "standard"
    assert cfg.project_root == tmp_path / "p"


def test_project_config_overrides_defaults_and_keeps_siblings(tmp_path):
    mod = _load()
    project = tmp_path / "p"
    (project / ".solocode").mkdir(parents=True)
    (project / ".solocode" / "settings.json").write_text(
        json.dumps({"gates": {"security": False}}), encoding="utf-8"
    )
    cfg = mod.load_harness_config(project_root=project, global_dir=tmp_path / "g")
    assert cfg.get("gates.security") is False
    assert cfg.get("gates.lint") is True  # deep merge kept the sibling


def test_project_config_beats_global(tmp_path):
    mod = _load()
    project = tmp_path / "p"
    glob = tmp_path / "g"
    (project / ".solocode").mkdir(parents=True)
    glob.mkdir(parents=True)
    (glob / "settings.json").write_text(
        json.dumps({"gates": {"security": False}}), encoding="utf-8"
    )
    (project / ".solocode" / "settings.json").write_text(
        json.dumps({"gates": {"security": True}}), encoding="utf-8"
    )
    cfg = mod.load_harness_config(project_root=project, global_dir=glob)
    assert cfg.get("gates.security") is True


def test_env_overrides_beat_config_files(tmp_path, monkeypatch):
    mod = _load()
    project = tmp_path / "p"
    (project / ".solocode").mkdir(parents=True)
    (project / ".solocode" / "settings.json").write_text(
        json.dumps({"hooks": {"profile": "minimal"}}), encoding="utf-8"
    )
    monkeypatch.setenv("SOLOCODE_HOOKS_PROFILE", "strict")
    cfg = mod.load_harness_config(project_root=project, global_dir=tmp_path / "g")
    assert cfg.get_hooks_profile() == "strict"


def test_toml_project_config_is_read(tmp_path):
    mod = _load()
    if mod._toml is None:
        pytest.skip("tomllib requires Python 3.11+")
    project = tmp_path / "p"
    (project / ".solocode").mkdir(parents=True)
    (project / ".solocode" / "settings.toml").write_text(
        '[models]\ndefault = "x"\n', encoding="utf-8"
    )
    cfg = mod.load_harness_config(project_root=project, global_dir=tmp_path / "g")
    assert cfg.get("models.default") == "x"


# ── create_default_config ────────────────────────────────────────────────────


def test_create_default_toml(tmp_path):
    mod = _load()
    p = tmp_path / "settings.toml"
    assert mod.create_default_config(p, format="toml") == p
    assert 'profile = "standard"' in p.read_text(encoding="utf-8")


def test_create_default_json(tmp_path):
    mod = _load()
    p = tmp_path / "settings.json"
    mod.create_default_config(p, format="json")
    assert json.loads(p.read_text(encoding="utf-8"))["gates"]["security"] is True


def test_create_default_config_never_overwrites(tmp_path):
    mod = _load()
    p = tmp_path / "settings.toml"
    p.write_text("keep = true\n", encoding="utf-8")
    assert mod.create_default_config(p, format="toml") == p
    assert p.read_text(encoding="utf-8") == "keep = true\n"


def test_generated_default_toml_parses():
    mod = _load()
    if mod._toml is None:
        pytest.skip("tomllib requires Python 3.11+")
    parsed = mod._toml.loads(mod._generate_default_toml())
    assert parsed["hooks"]["profile"] == "standard"
    assert parsed["models"]["default"] == "deepseek-v4-pro[1m]"


# ── CLI main() ───────────────────────────────────────────────────────────────


def test_main_init_writes_file(tmp_path, monkeypatch, capsys):
    mod = _load()
    monkeypatch.setenv("SOLOCODE_HARNESS_DIR", str(tmp_path / "g"))
    target = tmp_path / "cfg.toml"
    monkeypatch.setattr(
        sys,
        "argv",
        ["harness_config.py", "init", "--path", str(target), "--format", "toml"],
    )
    assert mod.main() == 0
    assert target.is_file()
    assert "Created:" in capsys.readouterr().out


def test_main_show_prints_the_merged_config(tmp_path, monkeypatch, capsys):
    mod = _load()
    monkeypatch.setenv("SOLOCODE_HARNESS_DIR", str(tmp_path / "g"))
    monkeypatch.setattr(
        sys, "argv", ["harness_config.py", "show", "--project", str(tmp_path / "p")]
    )
    assert mod.main() == 0
    assert json.loads(capsys.readouterr().out)["gates"]["security"] is True


def test_main_show_key_prints_one_value(tmp_path, monkeypatch, capsys):
    mod = _load()
    monkeypatch.setenv("SOLOCODE_HARNESS_DIR", str(tmp_path / "g"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "harness_config.py",
            "show",
            "--project",
            str(tmp_path / "p"),
            "--key",
            "gates.security",
        ],
    )
    assert mod.main() == 0
    assert capsys.readouterr().out.strip() == "true"


def test_main_debug_prints_layers(tmp_path, monkeypatch, capsys):
    mod = _load()
    monkeypatch.setenv("SOLOCODE_HARNESS_DIR", str(tmp_path / "g"))
    monkeypatch.setattr(
        sys, "argv", ["harness_config.py", "debug", "--project", str(tmp_path / "p")]
    )
    assert mod.main() == 0
    assert "Config layers" in capsys.readouterr().out


def test_main_without_command_returns_one(monkeypatch):
    mod = _load()
    monkeypatch.setattr(sys, "argv", ["harness_config.py"])
    assert mod.main() == 1
