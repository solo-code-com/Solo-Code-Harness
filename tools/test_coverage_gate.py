"""Tests for tools/coverage_gate.py — per-file coverage ratchet."""

from __future__ import annotations

import json
import re
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
    monkeypatch.setattr(
        coverage_gate, "save_budget", lambda data, *a, **k: saved.update(data)
    )
    monkeypatch.setattr(
        coverage_gate, "collect_test_counts", lambda: {"tools/test_a.py": 2}
    )
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
    monkeypatch.setattr(
        coverage_gate, "save_budget", lambda data, *a, **k: saved.update(data)
    )
    monkeypatch.setattr(
        coverage_gate, "collect_test_counts", lambda: {"tools/test_a.py": 2}
    )
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
    monkeypatch.setattr(
        coverage_gate, "save_budget", lambda data, *a, **k: saved.update(data)
    )
    monkeypatch.setattr(
        coverage_gate, "collect_test_counts", lambda: {"tools/test_a.py": 2}
    )
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
        coverage_gate,
        "save_budget",
        lambda data, *a, **k: real_save(data, bfile, **k),
    )
    monkeypatch.setattr(coverage_gate, "run_coverage", lambda: {"tools/a.py": 88.0})
    monkeypatch.setattr(
        coverage_gate, "collect_test_counts", lambda: {"tools/test_a.py": 3}
    )
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "--update"])

    assert coverage_gate.main() == 0

    expected = {
        "platforms": {
            other: before["platforms"][other],
            sys.platform: {"tools/a.py": 88.0},
        },
        "test_counts": {sys.platform: {"tools/test_a.py": 3}},
    }
    assert bfile.read_text(encoding="utf-8") == (
        json.dumps(expected, indent=2, sort_keys=True) + "\n"
    )


def test_capture_emits_one_baseline_chunk_when_platform_is_missing(
    tmp_path, monkeypatch, capsys
):
    """A small section still carries the index/count header, so one parser reads both.

    The header is always `1/1`, never omitted for the single-chunk case: a consumer
    that splits on the index would otherwise have to special-case it.
    """
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
    # No counts on the fake platform: keep the emitted lines coverage-only.
    monkeypatch.setattr(coverage_gate, "collect_test_counts", lambda: {})
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "--capture"])

    assert coverage_gate.main() == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [f'COVERAGE_BASELINE {sys.platform} 1/1 {{"tools/a.py":61.0}}']
    assert json.loads(lines[0].split(" ", 3)[3]) == {"tools/a.py": 61.0}


def _run_capture(tmp_path, monkeypatch, capsys, measured):
    """Run --capture against a budget that only knows another platform."""
    bfile = tmp_path / "budget.json"
    other = "linux" if sys.platform != "linux" else "win32"
    bfile.write_text(
        json.dumps({"platforms": {other: {"tools/a.py": 90.0}}}), encoding="utf-8"
    )
    real_load = coverage_gate.load_budget

    monkeypatch.setattr(coverage_gate, "load_budget", lambda *a, **k: real_load(bfile))
    monkeypatch.setattr(coverage_gate, "run_coverage", lambda: dict(measured))
    monkeypatch.setattr(coverage_gate, "collect_test_counts", lambda: {})
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "--capture"])

    assert coverage_gate.main() == 0
    return capsys.readouterr().out.splitlines()


def test_capture_splits_a_large_section_into_contiguous_chunks(
    tmp_path, monkeypatch, capsys
):
    """The Linux baseline is 121 entries and did not fit one 4096-char annotation.

    Every emitted line must carry an index that runs 1..n with no gap, and joining
    the chunks in that order must parse back to the measured dict exactly.
    """
    measured = {f"tools/module_{index:03d}.py": 90.0 for index in range(200)}

    lines = _run_capture(tmp_path, monkeypatch, capsys, measured)

    pattern = re.compile(
        rf"^COVERAGE_BASELINE {re.escape(sys.platform)} (\d+)/(\d+) (.*)$"
    )
    assert len(lines) > 1
    parsed = [pattern.match(line) for line in lines]
    assert all(parsed), lines
    indexes = [int(match.group(1)) for match in parsed]
    counts = {int(match.group(2)) for match in parsed}
    assert indexes == list(range(1, len(lines) + 1))
    assert counts == {len(lines)}
    assert json.loads("".join(match.group(3) for match in parsed)) == measured


def test_capture_chunks_never_exceed_the_annotation_budget():
    """No chunk may reach the 4096-character annotation limit, prefix included."""
    measured = {f"tools/module_{index:03d}.py": 100.0 for index in range(200)}

    chunks = coverage_gate.chunk_baseline(measured)

    assert len(chunks) > 1
    assert max(len(chunk) for chunk in chunks) <= 3000
    assert json.loads("".join(chunks)) == measured


def test_capture_prints_nothing_when_platform_has_a_baseline(
    tmp_path, monkeypatch, capsys
):
    """Nothing to hand back: the platform already has a section, so stay quiet."""
    bfile = tmp_path / "budget.json"
    bfile.write_text(
        json.dumps(
            {
                "platforms": {sys.platform: {"tools/a.py": 90.0}},
                "test_counts": {sys.platform: {"tools/test_a.py": 4}},
            }
        ),
        encoding="utf-8",
    )
    real_load = coverage_gate.load_budget
    real_load_counts = coverage_gate.load_test_counts

    monkeypatch.setattr(coverage_gate, "load_budget", lambda *a, **k: real_load(bfile))
    monkeypatch.setattr(
        coverage_gate, "load_test_counts", lambda *a, **k: real_load_counts(bfile)
    )
    monkeypatch.setattr(
        coverage_gate,
        "run_coverage",
        lambda: pytest.fail("a platform with a baseline must not run coverage"),
    )
    monkeypatch.setattr(
        coverage_gate,
        "collect_test_counts",
        lambda: pytest.fail("a platform with counts must not collect tests"),
    )
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "--capture"])

    assert coverage_gate.main() == 0
    assert capsys.readouterr().out == ""


def test_force_capture_emits_baseline_even_when_platform_has_one(
    tmp_path, monkeypatch, capsys
):
    """A stale committed baseline must be re-measurable without deleting it.

    The linux baseline was captured before the code changed, so the ratchet failed
    and the plain --capture stayed silent because a baseline was present. Both
    spellings of the force flag re-emit the measured section.
    """
    bfile = tmp_path / "budget.json"
    bfile.write_text(
        json.dumps(
            {
                "platforms": {sys.platform: {"tools/a.py": 90.0}},
                "test_counts": {sys.platform: {"tools/test_a.py": 4}},
            }
        ),
        encoding="utf-8",
    )
    real_load = coverage_gate.load_budget
    real_load_counts = coverage_gate.load_test_counts

    monkeypatch.setattr(coverage_gate, "load_budget", lambda *a, **k: real_load(bfile))
    monkeypatch.setattr(
        coverage_gate, "load_test_counts", lambda *a, **k: real_load_counts(bfile)
    )
    monkeypatch.setattr(coverage_gate, "run_coverage", lambda: {"tools/a.py": 55.0})
    # Counts are already recorded, so a force re-capture must not collect again.
    monkeypatch.setattr(
        coverage_gate,
        "collect_test_counts",
        lambda: pytest.fail("a recorded test-count section must not be collected"),
    )

    expected = [f'COVERAGE_BASELINE {sys.platform} 1/1 {{"tools/a.py":55.0}}']
    for argv in (
        ["coverage_gate.py", "--force-capture"],
        ["coverage_gate.py", "--capture", "--force"],
    ):
        monkeypatch.setattr(sys, "argv", argv)

        assert coverage_gate.main() == 0
        assert capsys.readouterr().out.splitlines() == expected


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


def test_check_ratchet_tolerates_a_sub_tolerance_drop_with_a_note():
    """A 0.9 point drop is measured noise, not a regression - but it stays visible.

    Measured on CI: .github/scripts/boundary_audit.py was 65.1% on the recorded
    linux baseline and 64.2% on the next enforcing run of the same commit.
    """
    budget = {sys.platform: {".github/scripts/boundary_audit.py": 65.1}}
    current = {".github/scripts/boundary_audit.py": 64.2}

    notes: list[str] = []
    passed, violations = coverage_gate.check_ratchet(budget, current, notes)

    assert passed is True
    assert violations == []
    assert len(notes) == 1
    assert (
        ".github/scripts/boundary_audit.py: 65.1% -> 64.2% (-0.9%, within tolerance)"
        in notes[0]
    )
    assert "[NOTE]" in notes[0]


def test_check_ratchet_fails_above_the_tolerance():
    """A 1.1 point drop is still a violation: the tolerance is not a free pass."""
    budget = {sys.platform: {"tools/garden.py": 85.1}}
    current = {"tools/garden.py": 84.0}

    notes: list[str] = []
    passed, violations = coverage_gate.check_ratchet(budget, current, notes)

    assert passed is False
    assert len(violations) == 1
    assert "tools/garden.py: 85.1% -> 84.0% (-1.1%)" in violations[0]
    assert notes == []


def test_save_budget_refuses_to_overwrite_a_malformed_file(tmp_path):
    """A malformed budget may still hold the other platforms: do not rewrite it.

    The old code swallowed the parse error and wrote this platform alone, silently
    destroying every sibling section.
    """
    bfile = tmp_path / "coverage-budget.json"
    original = '{"platforms": {"linux": {"tools/a.py": 90.0}'
    bfile.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        coverage_gate.save_budget({"tools/a.py": 88.0}, bfile)

    assert bfile.read_text(encoding="utf-8") == original


def test_update_fails_on_a_malformed_budget_without_writing(
    tmp_path, monkeypatch, capsys
):
    """--update must name the file and the parse error, and leave it alone."""
    bfile = tmp_path / "coverage-budget.json"
    original = '{"platforms": {"linux": {"tools/a.py": 90.0}'
    bfile.write_text(original, encoding="utf-8")
    real_load = coverage_gate.load_budget
    real_save = coverage_gate.save_budget

    monkeypatch.setattr(coverage_gate, "BUDGET_FILE", bfile)
    monkeypatch.setattr(coverage_gate, "load_budget", lambda *a, **k: real_load(bfile))
    monkeypatch.setattr(
        coverage_gate, "save_budget", lambda data, *a, **k: real_save(data, bfile)
    )
    monkeypatch.setattr(
        coverage_gate,
        "run_coverage",
        lambda: pytest.fail("a malformed budget must fail before the coverage run"),
    )
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py", "--update"])

    assert coverage_gate.main() == 1

    out = capsys.readouterr().out
    assert str(bfile) in out
    assert "Malformed budget file" in out
    assert bfile.read_text(encoding="utf-8") == original


def test_empty_platform_section_fails_instead_of_enforcing_nothing(
    tmp_path, monkeypatch, capsys
):
    """An empty section measures nothing, so [OK] would be a false guarantee."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "test_gate.py").write_text("x = 1\n", encoding="utf-8")
    bfile = tmp_path / "budget.json"
    bfile.write_text(json.dumps({"platforms": {sys.platform: {}}}), encoding="utf-8")
    real_load = coverage_gate.load_budget

    monkeypatch.setattr(coverage_gate, "ROOT", tmp_path)
    monkeypatch.setattr(coverage_gate, "load_budget", lambda *a, **k: real_load(bfile))
    monkeypatch.setattr(
        coverage_gate,
        "run_coverage",
        lambda: pytest.fail("an empty baseline must fail before running coverage"),
    )
    monkeypatch.setattr(sys, "argv", ["coverage_gate.py"])

    assert coverage_gate.main() == 1

    out = capsys.readouterr().out
    assert f"[FAIL] the baseline for {sys.platform} is empty" in out
    assert "run --update on this platform" in out


def test_collect_test_counts_parses_pytest_collect_only_output(monkeypatch):
    """One `path::test_name` line per collected test, keyed by the part before `::`.

    The trailing "N tests collected" summary has no `::`, so it must not appear as a
    file key - that was the only subtlety in parsing the -q output.
    """
    sample = (
        "tools/test_alpha.py::test_one\n"
        "tools/test_alpha.py::test_two\n"
        "tools/test_beta.py::test_three\n"
        "\n"
        "3 tests collected in 0.04s\n"
    )

    class _Proc:
        returncode = 0
        stdout = sample
        stderr = ""

    monkeypatch.setattr(coverage_gate.subprocess, "run", lambda *a, **k: _Proc())

    assert coverage_gate.collect_test_counts() == {
        "tools/test_alpha.py": 2,
        "tools/test_beta.py": 1,
    }


def test_check_test_counts_flags_deleted_tests_naming_both_numbers():
    """12 recorded, 9 collected: the count ratchet fails and names both numbers."""
    passed, violations = coverage_gate.check_test_counts(
        {"tools/test_x.py": 12}, {"tools/test_x.py": 9}
    )

    assert passed is False
    assert len(violations) == 1
    assert (
        "tools/test_x.py: 12 collected tests -> 9 (-3) - tests were deleted"
        in violations[0]
    )


def test_check_test_counts_accepts_the_same_count():
    """12 with 12 is the normal, unchanged case."""
    passed, violations = coverage_gate.check_test_counts(
        {"tools/test_x.py": 12}, {"tools/test_x.py": 12}
    )

    assert passed is True
    assert violations == []


def test_check_test_counts_accepts_more_tests():
    """12 with 15 is adding tests, which is never a regression."""
    passed, violations = coverage_gate.check_test_counts(
        {"tools/test_x.py": 12}, {"tools/test_x.py": 15}
    )

    assert passed is True
    assert violations == []


def test_check_test_counts_flags_a_recorded_file_that_collects_nothing():
    """A budgeted test file that now collects zero tests had its bodies deleted."""
    passed, violations = coverage_gate.check_test_counts({"tools/test_x.py": 12}, {})

    assert passed is False
    assert len(violations) == 1
    assert (
        "tools/test_x.py: 12 collected tests -> 0 (-12) - tests were deleted"
        in violations[0]
    )


def test_check_test_counts_notes_an_unconfigured_platform_without_failing():
    """No test_counts section is a documented note; coverage stays enforced."""
    notes: list[str] = []

    passed, violations = coverage_gate.check_test_counts(
        None, {"tools/test_x.py": 12}, notes
    )

    assert passed is True
    assert violations == []
    assert len(notes) == 1
    assert "[NOTE] test-count enforcement is not configured" in notes[0]
    assert "--update" in notes[0]


def test_check_ratchet_flags_test_file_collapsed_to_zero():
    """A test file whose bodies were deleted still runs but collapses to 0%.

    The count check catches a deleted test function; this catches a file whose
    bodies were emptied, which still collects and still appears in the run.
    """
    budget = {sys.platform: {"tools/test_session_registry.py": 100.0}}
    current = {"tools/test_session_registry.py": 0.0}

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is False
    assert len(violations) == 1
    assert "tools/test_session_registry.py: 100.0% -> 0.0%" in violations[0]
    assert "below the 20% test-file floor" in violations[0]


def test_check_ratchet_flags_test_file_below_the_floor():
    """15% is under the floor even though it is not zero: the bodies are gone."""
    budget = {sys.platform: {"tools/test_session_registry.py": 91.19}}
    current = {"tools/test_session_registry.py": 15.0}

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is False
    assert len(violations) == 1
    assert "tools/test_session_registry.py: 91.2% -> 15.0%" in violations[0]
    assert "test bodies were deleted, not skipped" in violations[0]


def test_check_ratchet_accepts_test_file_above_the_floor():
    """50% is the platform-skip swing the test-file exclusion still forgives."""
    budget = {sys.platform: {"tools/test_session_registry.py": 100.0}}
    current = {"tools/test_session_registry.py": 50.0}

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is True
    assert violations == []


def test_check_ratchet_still_flags_production_collapse_to_zero():
    """The floor is test-file only: a production module keeps the 1.0 rule."""
    budget = {sys.platform: {"tools/session_registry.py": 90.0}}
    current = {"tools/session_registry.py": 0.0}

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is False
    assert len(violations) == 1
    assert "tools/session_registry.py: 90.0% -> 0.0%" in violations[0]
    assert "test-file floor" not in violations[0]


def test_test_file_floor_does_not_touch_exempt_files():
    """RATCHET_EXEMPT is wider than the floor: an exempt file is never floored."""
    budget = {sys.platform: {"tools/test_e2e.py": 89.53}}
    current = {"tools/test_e2e.py": 0.0}

    passed, violations = coverage_gate.check_ratchet(budget, current)

    assert passed is True
    assert violations == []
