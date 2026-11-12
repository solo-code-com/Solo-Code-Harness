"""Tests for tools/check_lint_budget.py — the S/BLE ratchet.

The gate exists because `ruff check .` reports "All checks passed" while
security findings sit in the tree: `.ruff.toml`'s `select` omits the `S` and
`BLE` families, and `.ruff.toml` is protected config. So these tests care most
about the two ways such a gate fails silently -- counting wrong, and passing
when it should fail.
"""

from __future__ import annotations

import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import check_lint_budget as budget  # noqa: E402

# --- load_budget -------------------------------------------------------------


def test_load_budget_reads_max_findings(tmp_path):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"maxFindings": 12}), encoding="utf-8")
    assert budget.load_budget(f) == 12


@pytest.mark.parametrize(
    "payload",
    [
        {},  # key absent
        {"maxFindings": -1},  # negative
        {"maxFindings": "12"},  # string, not int
        {"maxFindings": 1.5},  # float
        {"maxFindings": True},  # bool is an int subclass -- must still fail
    ],
)
def test_load_budget_rejects_malformed(tmp_path, payload):
    """A budget that silently defaults to a large number is a gate that passes
    for the wrong reason, so every malformed shape must raise."""
    f = tmp_path / "b.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        budget.load_budget(f)


def test_load_budget_raises_on_missing_file(tmp_path):
    with pytest.raises(OSError):
        budget.load_budget(tmp_path / "absent.json")


# --- count_findings ----------------------------------------------------------


def test_count_findings_counts_one_line_per_finding(tmp_path):
    """The default `full` output format adds source-context lines, which
    counted 381 for the same 38 findings. One line per finding is the whole
    contract of this function."""
    src = tmp_path / "m.py"
    src.write_text(
        "import subprocess\n"
        "def f():\n"
        "    try:\n"
        "        subprocess.run(['ls'])\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8",
    )

    count, raw = budget.count_findings(tmp_path)

    non_empty = [ln for ln in raw.splitlines() if ln.strip()]
    assert count == len(non_empty)
    assert count > 0, raw
    # Every line must be a finding, i.e. carry a rule code -- not context.
    assert all(":" in ln for ln in non_empty), raw


def test_count_findings_ignores_test_asserts(tmp_path):
    """S101 flags every `assert`, of which this repo has ~324 in pytest files.
    Counting them would add noise that can never reach zero."""
    src = tmp_path / "t.py"
    src.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    count, raw = budget.count_findings(tmp_path)

    assert count == 0, raw


def test_count_findings_sees_rules_absent_from_ruff_toml(tmp_path):
    """The reason this gate exists: these findings are invisible to
    `ruff check .` because `select` omits their families."""
    src = tmp_path / "m.py"
    src.write_text(
        "def f():\n    try:\n        pass\n    except Exception:\n        return 1\n",
        encoding="utf-8",
    )

    _, raw = budget.count_findings(tmp_path)

    assert "BLE001" in raw, raw


def test_count_findings_zero_on_clean_tree(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    count, raw = budget.count_findings(tmp_path)
    assert count == 0, raw


def test_count_findings_uses_the_root_ruff_configuration(tmp_path):
    """Fixture config files must not change the repository-wide lint budget."""
    (tmp_path / ".ruff.toml").write_text("{}", encoding="utf-8")
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")

    count, raw = budget.count_findings(tmp_path)

    assert (count, raw) == (0, "")


def test_count_findings_raises_when_ruff_itself_fails(monkeypatch):
    """ruff exits 1 on findings, which is the expected case. Anything above that
    is a broken invocation (bad config, unusable CLI), and counting it as zero
    findings would turn the gate into a pass for the wrong reason."""

    def failing_run(*_a, **_k):
        return subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="error: bad config"
        )

    monkeypatch.setattr(budget.subprocess, "run", failing_run)

    with pytest.raises(RuntimeError, match="bad config"):
        budget.count_findings(ROOT)


# --- main (the ratchet itself) -----------------------------------------------


def _patch(monkeypatch, actual, budget_value):
    monkeypatch.setattr(budget, "count_findings", lambda root=None: (actual, ""))
    monkeypatch.setattr(budget, "load_budget", lambda *a, **k: budget_value)
    monkeypatch.setattr(sys, "argv", ["check_lint_budget.py"])


def test_main_fails_when_over_budget(monkeypatch, capsys):
    """The one behaviour that makes this a gate rather than a report."""
    _patch(monkeypatch, actual=40, budget_value=39)
    assert budget.main() == 1
    assert "over budget" in capsys.readouterr().out


def test_main_passes_at_budget(monkeypatch, capsys):
    _patch(monkeypatch, actual=39, budget_value=39)
    assert budget.main() == 0
    assert "At budget" in capsys.readouterr().out


def test_main_passes_under_budget_and_asks_for_a_ratchet(monkeypatch, capsys):
    """Under budget must not just pass quietly: an un-lowered budget lets the
    gain be silently lost the next time someone adds a finding."""
    _patch(monkeypatch, actual=30, budget_value=39)
    assert budget.main() == 0
    out = capsys.readouterr().out
    assert "Lower maxFindings to 30" in out


def test_main_fails_on_malformed_budget(monkeypatch, capsys):
    def boom(*a, **k):
        raise ValueError("bad budget")

    monkeypatch.setattr(budget, "load_budget", boom)
    monkeypatch.setattr(sys, "argv", ["check_lint_budget.py"])
    assert budget.main() == 1
    assert "bad budget" in capsys.readouterr().out


def test_main_help_flag(monkeypatch, capsys):
    """--help prints usage docstring, exits 0, and does NOT call count_findings()."""

    def boom(*a, **k):
        raise AssertionError(
            "expensive linter path count_findings() must not be called on --help"
        )

    monkeypatch.setattr(budget, "count_findings", boom)
    monkeypatch.setattr(sys, "argv", ["check_lint_budget.py", "--help"])
    assert budget.main() == 0
    out = capsys.readouterr().out
    assert "Lint Budget" in out
    assert "--list" in out


def test_main_short_help_flag(monkeypatch, capsys):
    """-h prints usage docstring, exits 0, and does NOT call count_findings()."""

    def boom(*a, **k):
        raise AssertionError(
            "expensive linter path count_findings() must not be called on -h"
        )

    monkeypatch.setattr(budget, "count_findings", boom)
    monkeypatch.setattr(sys, "argv", ["check_lint_budget.py", "-h"])
    assert budget.main() == 0
    out = capsys.readouterr().out
    assert "Lint Budget" in out


def test_main_list_prints_the_findings(monkeypatch, capsys):
    """--list is the remediation the failure message points at, so it must show
    the findings ruff actually produced."""
    monkeypatch.setattr(
        budget, "count_findings", lambda root=None: (1, "m.py:1:1: S603 subprocess")
    )
    monkeypatch.setattr(sys, "argv", ["check_lint_budget.py", "--list"])

    assert budget.main() == 0
    assert "S603" in capsys.readouterr().out


def test_main_list_says_so_when_there_is_nothing_to_show(monkeypatch, capsys):
    monkeypatch.setattr(budget, "count_findings", lambda root=None: (0, ""))
    monkeypatch.setattr(sys, "argv", ["check_lint_budget.py", "--list"])

    assert budget.main() == 0
    assert "(no findings)" in capsys.readouterr().out


def test_main_skips_when_ruff_is_not_installed(monkeypatch, capsys):
    """No ruff means the families were never checked; the gate must say so
    instead of silently reporting a clean tree."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(sys, "argv", ["check_lint_budget.py"])

    assert budget.main() == 0
    assert "[SKIP] lint budget: ruff not installed on PATH" in capsys.readouterr().out


def test_main_fails_when_counting_raises(monkeypatch, capsys):
    """A counter that raises (ruff broken, unreadable config) is a failed gate,
    not an empty finding list."""

    def boom(*a, **k):
        raise RuntimeError("ruff failed: bad config")

    monkeypatch.setattr(budget, "load_budget", lambda *a, **k: 39)
    monkeypatch.setattr(budget, "count_findings", boom)
    monkeypatch.setattr(shutil, "which", lambda name: "/fake/ruff")
    monkeypatch.setattr(sys, "argv", ["check_lint_budget.py"])

    assert budget.main() == 1
    assert "ruff failed: bad config" in capsys.readouterr().out


def test_module_entry_point_exits_with_the_main_status(monkeypatch, capsys):
    """`if __name__ == "__main__"` must exit with main()'s status rather than
    falling off the end and exiting 0 whatever the budget said."""
    monkeypatch.setattr(sys, "argv", ["check_lint_budget.py", "--help"])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(
            str(ROOT / "tools" / "check_lint_budget.py"), run_name="__main__"
        )

    assert excinfo.value.code == 0
    assert "Lint Budget" in capsys.readouterr().out


# --- the shipped budget ------------------------------------------------------


def test_shipped_budget_matches_reality():
    """Guards against the budget and the tree drifting apart -- a budget set
    well above the real count is a gate that can never fail."""
    actual, raw = budget.count_findings(ROOT)
    shipped = budget.load_budget()
    assert actual <= shipped, f"{actual} findings vs budget {shipped}:\n{raw}"
    assert actual == shipped, (
        f"budget is {shipped} but only {actual} findings remain -- "
        f"lower maxFindings to {actual}"
    )
