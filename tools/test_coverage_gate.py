"""Tests for tools/coverage_gate.py — per-file coverage ratchet."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import coverage_gate  # noqa: E402


def test_main_help_flag(monkeypatch, capsys):
    """--help should print docstring usage and exit 0 without running coverage."""
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "--help"])
    assert coverage_gate.main() == 0
    out = capsys.readouterr().out
    assert "Coverage Gate" in out
    assert "--update" in out


def test_main_short_help_flag(monkeypatch, capsys):
    """-h should print docstring usage and exit 0 without running coverage."""
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "-h"])
    assert coverage_gate.main() == 0
    out = capsys.readouterr().out
    assert "Coverage Gate" in out


def test_load_budget_missing_file(tmp_path):
    """Missing budget file returns an empty dict -- not None.

    The old bare `return` made this None despite the docstring promising an
    empty dict. The test pinned that: it asserted `is None`, so `--report`'s
    `set(budget.keys())` raised AttributeError on a first run and nothing
    caught it.
    """
    result = coverage_gate.load_budget(tmp_path / "nonexistent.json")
    assert result == {}
    assert list(result.keys()) == []


def test_load_budget_valid(tmp_path):
    """Valid budget JSON loads as one section per platform."""
    bfile = tmp_path / "budget.json"
    bfile.write_text(
        json.dumps({"platforms": {"win32": {"tools/deploy.py": 85.5}}}),
        encoding="utf-8",
    )
    loaded = coverage_gate.load_budget(bfile)
    assert loaded == {"win32": {"tools/deploy.py": 85.5}}


def test_load_budget_malformed(tmp_path):
    """Malformed budget JSON raises ValueError."""
    bfile = tmp_path / "budget.json"
    bfile.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed budget file"):
        coverage_gate.load_budget(bfile)


def test_check_ratchet_maintained():
    """Ratchet passes when all files maintain coverage."""
    budget = {sys.platform: {"a.py": 80.0, "b.py": 90.0}}
    current = {"a.py": 80.0, "b.py": 95.0}
    passed, violations = coverage_gate.check_ratchet(budget, current)
    assert passed is True
    assert violations == []


def test_check_ratchet_decreased():
    """Ratchet fails when coverage drops below budget."""
    budget = {sys.platform: {"a.py": 80.0, "b.py": 90.0}}
    current = {"a.py": 75.0, "b.py": 90.0}
    passed, violations = coverage_gate.check_ratchet(budget, current)
    assert passed is False
    assert len(violations) == 1
    assert "a.py: 80.0% -> 75.0%" in violations[0]


def test_runtime_copy_without_tests_is_skipped(tmp_path, monkeypatch, capsys):
    """A deployed target has no harness tests, so the baselines cannot apply: skip, do not fail.

    Measured on a real target: 40-odd files reported 0.0% because deploy.py excludes test_*.py by
    policy, and the gate failed while the project was fine.
    """
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "agent_scope.py").write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setattr(coverage_gate, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py"])

    assert coverage_gate.main() == 0
    out = capsys.readouterr().out
    assert "[SKIP]" in out
    assert "deployed runtime copy" in out


def test_harness_tests_present_detects_both_checkouts(tmp_path):
    """The guard itself must not fire in this repo, which does ship the tests."""
    assert coverage_gate.harness_tests_present(ROOT) is True
    (tmp_path / "tools").mkdir()
    assert coverage_gate.harness_tests_present(tmp_path) is False


def test_run_coverage_normalises_native_separators(tmp_path, monkeypatch):
    """coverage.py keys arrive with the OS separator; they must come back POSIX.

    coverage.py writes relative paths when run from ROOT, so the fallback in
    run_coverage() is what CI exercises. On Windows that produced
    `tools\\coverage_gate.py`, which matched none of the committed budget keys and
    read every file as 0.0% (CI run 39726010702). On POSIX the native form is
    already POSIX, so the assertion holds there too.
    """
    coverage_json = tmp_path / "coverage.json"
    native_key = str(Path("tools", "coverage_gate.py"))

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        if "--version" in cmd:
            return _Proc()
        coverage_json.write_text(
            json.dumps({"files": {native_key: {"summary": {"percent_covered": 87.5}}}}),
            encoding="utf-8",
        )
        return _Proc()

    monkeypatch.setattr(coverage_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(coverage_gate, "ROOT", tmp_path)
    monkeypatch.setattr(coverage_gate, "COVERAGE_FILE", tmp_path / ".coverage")

    result = coverage_gate.run_coverage()

    assert result == {"tools/coverage_gate.py": 87.5}
    assert all("\\" not in key for key in result)


def test_check_ratchet_reports_path_mismatch_not_mass_regressions():
    """Most of the budget missing at once is a separator mismatch, not a regression.

    The old code reported every missing key as 0.0%, so a POSIX run against the
    Windows-separated budget emitted 120 violations instead of one diagnostic.
    """
    budget = {
        sys.platform: {
            "tools/a.py": 90.0,
            "tools/b.py": 90.0,
            "tools/c.py": 90.0,
            "tools/d.py": 90.0,
        },
    }
    current = {"tools/a.py": 95.0}

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is False
    assert len(violations) == 1
    assert "path mismatch: 3 of 4 budget entries are missing" in violations[0]
    assert "Windows vs POSIX separators" in violations[0]
    assert "--update" in violations[0]


def test_check_ratchet_single_regression_still_reported():
    """The mismatch guard must not swallow a genuine one-file drop."""
    budget = {
        sys.platform: {
            "tools/a.py": 80.0,
            "tools/b.py": 90.0,
            "tools/c.py": 70.0,
            "tools/d.py": 60.0,
            "tools/e.py": 50.0,
            "tools/f.py": 40.0,
        },
    }
    current = dict(budget[sys.platform])
    current["tools/a.py"] = 75.0

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is False
    assert len(violations) == 1
    assert "tools/a.py: 80.0% -> 75.0%" in violations[0]


def test_check_ratchet_ignores_drop_in_exempt_live_api_file():
    """An exempt live-API file can drop without failing the ratchet.

    tools/test_e2e.py runs live API calls that skip on a provider timeout, so its
    coverage measures the network. Measured: 89.5% -> 87.2% with no test removed.
    """
    budget = {sys.platform: {"tools/test_e2e.py": 89.53}}
    current = {"tools/test_e2e.py": 87.2}

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is True
    assert violations == []


def test_check_ratchet_still_reports_drop_in_non_exempt_file():
    """The exemption is one file wide: a normal file still fails on a drop."""
    budget = {
        sys.platform: {"tools/test_e2e.py": 89.53, "tools/coverage_gate.py": 50.0}
    }
    current = {"tools/test_e2e.py": 80.0, "tools/coverage_gate.py": 45.0}

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is False
    assert len(violations) == 1
    assert "tools/coverage_gate.py: 50.0% -> 45.0%" in violations[0]
    assert "tools/test_e2e.py" not in violations[0]


def test_update_does_not_write_exempt_file_into_budget(monkeypatch, capsys):
    """--update must not turn a live-API file's oscillation into a baseline.

    An entry already in the budget is preserved rather than deleted, but the run's
    new number never replaces it.
    """
    saved: dict[str, float] = {}
    monkeypatch.setattr(coverage_gate, "save_budget", lambda data: saved.update(data))
    monkeypatch.setattr(coverage_gate, "load_budget", lambda: {})
    monkeypatch.setattr(
        coverage_gate,
        "run_coverage",
        lambda: {"tools/test_e2e.py": 87.2, "tools/coverage_gate.py": 50.0},
    )
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "--update"])

    assert coverage_gate.main() == 0
    assert saved == {"tools/coverage_gate.py": 50.0}
    out = capsys.readouterr().out
    assert "1 live-API file(s) not written to the budget" in out

    # An existing entry stays untouched: kept, not refreshed, not deleted.
    saved.clear()
    monkeypatch.setattr(
        coverage_gate,
        "load_budget",
        lambda: {sys.platform: {"tools/test_e2e.py": 89.53}},
    )

    assert coverage_gate.main() == 0
    assert saved == {"tools/coverage_gate.py": 50.0, "tools/test_e2e.py": 89.53}


def test_check_ratchet_flags_exempt_file_that_did_not_run():
    """An absent exempt file means deleted tests or a collection error, not a quiet pass."""
    budget = {
        sys.platform: {"tools/test_e2e.py": 89.53, "tools/coverage_gate.py": 50.0}
    }
    current = {"tools/coverage_gate.py": 50.0}

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is False
    assert len(violations) == 1
    assert (
        "tools/test_e2e.py is exempt from coverage, but it did not run at all"
        in violations[0]
    )
    assert "deleted tests, or a collection error?" in violations[0]


def test_update_drops_exempt_entry_when_file_did_not_run(monkeypatch):
    """--update must not carry an exempt baseline for a file missing from the run."""
    saved: dict[str, float] = {}
    monkeypatch.setattr(coverage_gate, "save_budget", lambda data: saved.update(data))
    monkeypatch.setattr(
        coverage_gate,
        "load_budget",
        lambda: {sys.platform: {"tools/test_e2e.py": 89.53}},
    )
    monkeypatch.setattr(
        coverage_gate, "run_coverage", lambda: {"tools/coverage_gate.py": 50.0}
    )
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "--update"])

    assert coverage_gate.main() == 0
    assert saved == {"tools/coverage_gate.py": 50.0}


def test_update_keeps_exempt_entry_when_file_ran(monkeypatch):
    """A pre-existing exempt entry survives only while its file is present."""
    saved: dict[str, float] = {}
    monkeypatch.setattr(coverage_gate, "save_budget", lambda data: saved.update(data))
    monkeypatch.setattr(
        coverage_gate,
        "load_budget",
        lambda: {sys.platform: {"tools/test_e2e.py": 89.53}},
    )
    monkeypatch.setattr(
        coverage_gate,
        "run_coverage",
        lambda: {"tools/test_e2e.py": 87.2, "tools/coverage_gate.py": 50.0},
    )
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "--update"])

    assert coverage_gate.main() == 0
    # The saved number is preserved, not refreshed from the live run.
    assert saved == {"tools/test_e2e.py": 89.53, "tools/coverage_gate.py": 50.0}


def test_check_ratchet_path_mismatch_still_reports_present_file_drop():
    """The diagnostic explains the missing majority; a present file's drop is still real."""
    budget = {
        sys.platform: {
            "tools/a.py": 90.0,
            "tools/b.py": 90.0,
            "tools/c.py": 90.0,
            "tools/d.py": 90.0,
        },
    }
    current = {"tools/a.py": 85.0}

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is False
    assert len(violations) == 2
    assert "path mismatch: 3 of 4 budget entries are missing" in violations[0]
    assert "tools/a.py: 90.0% -> 85.0%" in violations[1]


def test_load_budget_old_shape_is_read_as_win32(tmp_path):
    """A budget written before platform scoping holds Windows numbers.

    Those entries were measured on Windows, so reading them as win32 keeps an old
    checkout working instead of enforcing them on a platform that cannot match.
    """
    bfile = tmp_path / "budget.json"
    bfile.write_text(json.dumps({"files": {"tools/deploy.py": 85.5}}), encoding="utf-8")

    assert coverage_gate.load_budget(bfile) == {"win32": {"tools/deploy.py": 85.5}}


def test_check_ratchet_enforces_the_current_platform_section():
    """This platform's section is the one compared, and a drop in it fails."""
    budget = {sys.platform: {"tools/a.py": 80.0}}

    passed, violations = coverage_gate.check_ratchet(budget, {"tools/a.py": 75.0})

    assert passed is False
    assert len(violations) == 1
    assert "tools/a.py: 80.0% -> 75.0%" in violations[0]


def test_missing_platform_baseline_skips_with_documented_message(
    tmp_path, monkeypatch, capsys
):
    """No section for this platform is a documented skip naming the enforced ones.

    The gate must skip before paying for a coverage run, and it must say which
    baselines do exist so the log tells a reader where the numbers came from.
    """
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "test_gate.py").write_text("x = 1\n", encoding="utf-8")
    other = "linux" if sys.platform != "linux" else "win32"
    bfile = tmp_path / "budget.json"
    bfile.write_text(
        json.dumps({"platforms": {other: {"tools/a.py": 90.0}}}), encoding="utf-8"
    )
    real_load = coverage_gate.load_budget

    monkeypatch.setattr(coverage_gate, "ROOT", tmp_path)
    monkeypatch.setattr(coverage_gate, "load_budget", lambda *a, **k: real_load(bfile))
    monkeypatch.setattr(
        coverage_gate,
        "run_coverage",
        lambda: pytest.fail("a missing baseline must skip before running coverage"),
    )
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py"])

    assert coverage_gate.main() == 0
    out = capsys.readouterr().out
    assert f"[SKIP] no coverage baseline for {sys.platform}" in out
    assert f"baselines are: {other}." in out
    assert "Run --update on this platform to add one." in out


def test_update_writes_only_the_current_platform_section(tmp_path, monkeypatch):
    """--update replaces this platform's section and leaves every other one intact.

    Another platform's baselines were measured there; refreshing them from this
    run would replace numbers this platform cannot reproduce.
    """
    bfile = tmp_path / "budget.json"
    other = "linux" if sys.platform != "linux" else "win32"
    before = {"platforms": {other: {"tools/a.py": 71.0, "tools/b.py": 62.5}}}
    bfile.write_text(
        json.dumps(before, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    real_load = coverage_gate.load_budget
    real_save = coverage_gate.save_budget

    monkeypatch.setattr(coverage_gate, "load_budget", lambda *a, **k: real_load(bfile))
    monkeypatch.setattr(
        coverage_gate, "save_budget", lambda data, *a, **k: real_save(data, bfile)
    )
    monkeypatch.setattr(coverage_gate, "run_coverage", lambda: {"tools/a.py": 88.0})
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "--update"])

    assert coverage_gate.main() == 0

    expected = {
        "platforms": {
            other: before["platforms"][other],
            sys.platform: {"tools/a.py": 88.0},
        }
    }
    assert bfile.read_text(encoding="utf-8") == (
        json.dumps(expected, indent=2, sort_keys=True) + "\n"
    )


def test_capture_emits_one_baseline_line_when_platform_is_missing(
    tmp_path, monkeypatch, capsys
):
    """A runner with no baseline hands back exactly one line a human can paste."""
    bfile = tmp_path / "budget.json"
    other = "linux" if sys.platform != "linux" else "win32"
    bfile.write_text(
        json.dumps({"platforms": {other: {"tools/a.py": 90.0}}}), encoding="utf-8"
    )
    real_load = coverage_gate.load_budget

    monkeypatch.setattr(coverage_gate, "load_budget", lambda *a, **k: real_load(bfile))
    monkeypatch.setattr(
        coverage_gate,
        "run_coverage",
        lambda: {"tools/a.py": 61.0, "tools/test_e2e.py": 88.0},
    )
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "--capture"])

    assert coverage_gate.main() == 0

    assert capsys.readouterr().out.splitlines() == [
        f'COVERAGE_BASELINE {sys.platform} {{"tools/a.py":61.0}}'
    ]


def test_capture_prints_nothing_when_platform_has_a_baseline(
    tmp_path, monkeypatch, capsys
):
    """Nothing to hand back: the platform already has a section, so stay quiet."""
    bfile = tmp_path / "budget.json"
    bfile.write_text(
        json.dumps({"platforms": {sys.platform: {"tools/a.py": 90.0}}}),
        encoding="utf-8",
    )
    real_load = coverage_gate.load_budget

    monkeypatch.setattr(coverage_gate, "load_budget", lambda *a, **k: real_load(bfile))
    monkeypatch.setattr(
        coverage_gate,
        "run_coverage",
        lambda: pytest.fail("a platform with a baseline must not run coverage"),
    )
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "--capture"])

    assert coverage_gate.main() == 0
    assert capsys.readouterr().out == ""


def test_check_ratchet_ignores_drop_in_test_file():
    """A test file's coverage measures which tests ran here, not production code.

    Measured: tools/test_session_registry.py is 100% on a platform where all its
    tests run and 62.3% where a POSIX-only test skips, with nothing deleted.
    """
    budget = {sys.platform: {"tools/test_session_registry.py": 100.0}}
    current = {"tools/test_session_registry.py": 50.0}

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is True
    assert violations == []


def test_check_ratchet_flags_test_file_that_did_not_run():
    """Not comparing a test file numerically must not make a deleted test file silent."""
    budget = {sys.platform: {"tools/test_session_registry.py": 100.0}}
    current: dict[str, float] = {}

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is False
    assert len(violations) == 1
    assert (
        "tools/test_session_registry.py is a test file, so its coverage is not compared"
        in violations[0]
    )
    assert "did not run at all" in violations[0]


def test_check_ratchet_still_reports_drop_in_production_module():
    """The test-file exclusion is one file kind wide: a production module still fails."""
    budget = {
        sys.platform: {
            "tools/test_session_registry.py": 100.0,
            "tools/session_registry.py": 92.1,
        },
    }
    current = {
        "tools/test_session_registry.py": 50.0,
        "tools/session_registry.py": 80.0,
    }

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is False
    assert len(violations) == 1
    assert "tools/session_registry.py: 92.1% -> 80.0%" in violations[0]
    assert "tools/test_session_registry.py" not in violations[0]


def test_test_file_rule_uses_basename_not_directory():
    """A `test_` basename under `.github/scripts/` is still a test file for the ratchet.

    The rule is the basename, not the path: the gate must not treat a script under
    .github/scripts/ as production just because it lives outside tools/.
    """
    path = ".github/scripts/test_like_production.py"
    budget = {sys.platform: {path: 100.0}}

    passed, violations = coverage_gate.check_ratchet(budget, {path: 40.0})

    assert passed is True
    assert violations == []

    passed, violations = coverage_gate.check_ratchet(budget, {})

    assert passed is False
    assert len(violations) == 1
    assert f"{path} is a test file" in violations[0]
