#!/usr/bin/env python3
"""Checklist gate tests.

Pin the mandatory-vs-optional tool policy in `.github/scripts/checklist.py`:
a missing REQUIRED tool must fail the gate (the gate cannot verify what it
claims), while a missing OPTIONAL tool may skip cleanly. Before this policy,
`FileNotFoundError` returned `passed=True, skipped=True` for every tool,
so a machine without `ruff` reported a green gate.

Usage:
    python -m pytest tools/test_checklist_gate.py -v
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKLIST = ROOT / ".github" / "scripts" / "checklist.py"

# A binary name that cannot exist; used to trigger FileNotFoundError.
_MISSING = "definitely-not-a-real-binary-xyz-12345"


def _load_checklist():
    """Load checklist.py by path (it lives outside the tools/ package)."""
    spec = importlib.util.spec_from_file_location("checklist_under_test", CHECKLIST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _budget_project(tmp_path: Path) -> Path:
    """A project that carries both the ratchet script and a budget file."""
    (tmp_path / "tools" / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools" / "coverage_gate.py").write_text("", encoding="utf-8")
    (tmp_path / "tools" / "config" / "coverage-budget.json").write_text(
        "{}", encoding="utf-8"
    )
    return tmp_path


def test_build_parser_defaults_to_the_full_gate() -> None:
    """`--fast` is opt-in: a forgotten flag still runs the ratchet, and asking for
    the switch is what turns it off. Tested through the parser rather than by
    running main(), which would execute a five-minute checklist."""
    module = _load_checklist()
    parser = module.build_parser()
    assert parser.parse_args(["."]).fast is False
    assert parser.parse_args([".", "--fast"]).fast is True


def test_coverage_ratchet_skips_when_no_budget(tmp_path, capsys) -> None:
    """A project that never opted into a budget must not be failed by it."""
    module = _load_checklist()
    assert module.coverage_ratchet_check(tmp_path) is None
    assert "skipping coverage ratchet" in capsys.readouterr().out


def test_coverage_ratchet_skips_when_gate_script_absent(tmp_path) -> None:
    """Budget present but the script missing still skips rather than crashing."""
    module = _load_checklist()
    (tmp_path / "tools" / "config").mkdir(parents=True)
    (tmp_path / "tools" / "config" / "coverage-budget.json").write_text(
        "{}", encoding="utf-8"
    )
    assert module.coverage_ratchet_check(tmp_path) is None


def test_coverage_ratchet_runs_when_budget_present(tmp_path, monkeypatch) -> None:
    """With both files present the gate is invoked, with a raised timeout."""
    module = _load_checklist()
    captured: dict = {}

    def fake_run_check(name, command, timeout=120, required=True):
        captured.update(name=name, command=command, timeout=timeout)
        return {"name": name, "passed": True, "skipped": False}

    monkeypatch.setattr(module, "run_check", fake_run_check)
    result = module.coverage_ratchet_check(_budget_project(tmp_path))

    assert result is not None and result["passed"] is True
    assert captured["name"] == "Coverage Ratchet"
    assert captured["command"][0] == sys.executable
    assert captured["command"][1].endswith("coverage_gate.py")
    # The ratchet runs the whole suite under coverage; the default 120s is too tight.
    assert captured["timeout"] == 900


def test_boundary_audit_runs_in_strict_mode(tmp_path, monkeypatch) -> None:
    """Warn-only mode never fails, so the gate must pass --strict.

    checklist.py called boundary_audit.py without --strict, and that script
    returns 0 unless --strict, so the "Boundary Audit" gate could not fail.
    """
    module = _load_checklist()
    scripts = tmp_path / ".github" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "boundary_audit.py").write_text("", encoding="utf-8")
    captured: dict = {}

    def fake_run_check(name, command, timeout=120, required=True):
        captured.update(name=name, command=command)
        return {"name": name, "passed": True, "skipped": False}

    monkeypatch.setattr(module, "run_check", fake_run_check)
    result = module.boundary_audit_check(tmp_path)

    assert result is not None and result["passed"] is True
    assert captured["name"] == "Boundary Audit"
    assert captured["command"][1].endswith("boundary_audit.py")
    assert "--strict" in captured["command"]


def test_boundary_audit_skips_when_script_absent(tmp_path, capsys) -> None:
    """A project that ships no auditor is not failed by it."""
    module = _load_checklist()
    assert module.boundary_audit_check(tmp_path) is None
    assert "skipping Boundary Audit" in capsys.readouterr().out


def test_missing_required_tool_fails() -> None:
    """A required tool missing from PATH must FAIL the check."""
    module = _load_checklist()
    result = module.run_check("Missing required", [_MISSING], required=True)
    assert result["passed"] is False
    assert result["skipped"] is False


def test_missing_optional_tool_is_skipped() -> None:
    """An optional tool missing from PATH may skip cleanly."""
    module = _load_checklist()
    result = module.run_check("Missing optional", [_MISSING], required=False)
    assert result["passed"] is True
    assert result["skipped"] is True


def test_failing_command_is_failure_not_skip() -> None:
    """A command that runs but exits non-zero must FAIL, not skip."""
    module = _load_checklist()
    result = module.run_check("Failing", [sys.executable, "-c", "raise SystemExit(1)"])
    assert result["passed"] is False
    assert result["skipped"] is False


def test_passing_command_passes() -> None:
    """A command that exits zero must PASS and not be marked skipped."""
    module = _load_checklist()
    result = module.run_check("Passing", [sys.executable, "-c", "raise SystemExit(0)"])
    assert result["passed"] is True
    assert result["skipped"] is False
