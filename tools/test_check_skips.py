#!/usr/bin/env python3
"""Tests for .github/scripts/check_skips.py — the no-skips test policy."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "check_skips.py"

# Built by concatenation on purpose: check_skips.py scans this very file, and a
# literal bare skip marker here would (correctly) be reported as its own
# violation. The marker under test must be generated, not spelled out.
_BARE_SKIP = "@pytest.mark." + "skip"


def _load():
    spec = importlib.util.spec_from_file_location("check_skips_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


def test_unconditional_skip_is_a_violation(tmp_path):
    mod = _load()
    f = tmp_path / "test_x.py"
    f.write_text(
        f"import pytest\n\n\n{_BARE_SKIP}\ndef test_a():\n    pass\n",
        encoding="utf-8",
    )
    violations = mod.check_file(f)
    assert len(violations) == 1
    assert "Unconditional skip" in violations[0]


def test_skip_with_reason_is_allowed(tmp_path):
    mod = _load()
    f = tmp_path / "test_x.py"
    f.write_text(
        '@pytest.mark.skip(reason="flaky upstream")\ndef test_a():\n    pass\n',
        encoding="utf-8",
    )
    assert mod.check_file(f) == []


def test_skipif_with_reason_is_allowed(tmp_path):
    mod = _load()
    f = tmp_path / "test_x.py"
    f.write_text(
        '@pytest.mark.skipif(sys.platform == "win32", reason="posix only")\n'
        "def test_a():\n    pass\n",
        encoding="utf-8",
    )
    assert mod.check_file(f) == []


def test_skip_platform_constant_is_allowed(tmp_path):
    mod = _load()
    f = tmp_path / "test_x.py"
    f.write_text(
        "@pytest.mark.skipif(SKIP_PLATFORM, reason='x')\ndef test_a():\n    pass\n",
        encoding="utf-8",
    )
    assert mod.check_file(f) == []


def test_known_gap_marker_is_allowed(tmp_path):
    mod = _load()
    f = tmp_path / "test_x.py"
    f.write_text(
        "@pytest.mark.skipif(KNOWN_GAP)\ndef test_a():\n    pass\n", encoding="utf-8"
    )
    assert mod.check_file(f) == []


def test_ts_ignore_without_justification_is_flagged(tmp_path):
    mod = _load()
    f = tmp_path / "thing.mjs"
    f.write_text("const x = 1\n// @ts-ignore\nx()\n", encoding="utf-8")
    violations = mod.check_file(f)
    assert any("@ts-ignore" in item for item in violations)


def test_clean_file_has_no_violations(tmp_path):
    mod = _load()
    f = tmp_path / "test_x.py"
    f.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    assert mod.check_file(f) == []


def test_main_ok_on_clean_dir(tmp_path):
    (tmp_path / "test_clean.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    result = _run(str(tmp_path))
    assert result.returncode == 0
    assert "OK" in result.stdout


def test_main_fails_on_violation(tmp_path):
    (tmp_path / "test_bad.py").write_text(
        f"{_BARE_SKIP}\ndef test_a():\n    pass\n", encoding="utf-8"
    )
    result = _run(str(tmp_path))
    assert result.returncode == 1
    assert "violation" in result.stdout


def test_main_skips_missing_dir():
    result = _run("does/not/exist")
    assert result.returncode == 0
    assert "SKIP" in result.stdout


def test_multiline_skipif_with_nested_call_is_allowed(tmp_path):
    mod = _load()
    f = tmp_path / "test_x.py"
    f.write_text(
        f"import os\nimport pytest\n\n\n@{_BARE_SKIP}if(\n"
        '    not os.environ.get("KEY") or not flag(),\n'
        '    reason="needs the key and a flag",\n'
        ")\n"
        "def test_a():\n    pass\n",
        encoding="utf-8",
    )
    assert mod.check_file(f) == []


def test_multiline_skipif_without_reason_is_a_violation(tmp_path):
    mod = _load()
    f = tmp_path / "test_x.py"
    f.write_text(
        f"import pytest\n\n\n@{_BARE_SKIP}if(\n    SOME_FLAG,\n)\ndef test_a():\n    pass\n",
        encoding="utf-8",
    )
    violations = mod.check_file(f)
    assert len(violations) == 1
    assert "Unconditional skip" in violations[0]
