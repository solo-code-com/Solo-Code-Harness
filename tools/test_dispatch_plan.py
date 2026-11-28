#!/usr/bin/env python3
"""
Dispatch plan - fallback tests
==============================
An arm can be unavailable for four different reasons: the name is unknown, the
project turned it off, its delegate file is missing, or its model failed the
probe. Each one must end in "no usable arm", never in a command for the arm that
was just rejected.

Usage:
    python -m pytest tools/test_dispatch_plan.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import dispatch  # noqa: E402


@pytest.fixture(scope="module")
def fleet() -> dict:
    return dispatch.load_json(dispatch.FLEET)


def test_an_unknown_arms_token_survives_parsing(fleet):
    """A typo must reach plan() as a name, or it cannot be reported at all."""
    assert dispatch.enabled_arms("typo", {}, {}, fleet) == ["typo"]


def test_unknown_arm_is_reported_not_raised(fleet):
    """--arms typo used to raise StopIteration out of plan()."""
    decision = dispatch.plan(
        "implement a retry", ["nosucharm"], fleet, {}, {}, "auto", None
    )
    assert decision["family"] is None
    assert "nosucharm:unknown" in decision["skipped"]
    assert decision["command"] is None


def test_off_arm_is_not_reselected(fleet):
    """The old fallback picked the first enabled arm even when it was marked off."""
    decision = dispatch.plan(
        "implement a retry", ["claude"], fleet, {"off": ["claude"]}, {}, "auto", None
    )
    assert decision["family"] is None
    assert "claude:off" in decision["skipped"]


def test_missing_tool_is_not_reselected(fleet, monkeypatch):
    monkeypatch.setitem(dispatch.TOOL_FOR, "deepseek", "tools/does_not_exist.py")
    decision = dispatch.plan(
        "implement a retry", ["deepseek"], fleet, {}, {}, "auto", None
    )
    assert decision["family"] is None
    assert "deepseek:tool-missing" in decision["skipped"]


def test_failed_probe_is_not_reselected(fleet):
    model = fleet["families"]["claude"]["models"]["code"]
    decision = dispatch.plan(
        "implement a retry", ["claude"], fleet, {}, {model: "overloaded"}, "auto", None
    )
    assert decision["family"] is None
    assert any(
        s.startswith("claude:") and "overloaded" in s for s in decision["skipped"]
    )


def test_disabled_arm_is_not_reselected(fleet):
    fleet = {
        **fleet,
        "families": {
            **fleet["families"],
            "claude": {**fleet["families"]["claude"], "enabled": False},
        },
    }
    decision = dispatch.plan(
        "implement a retry", ["claude"], fleet, {}, {}, "auto", None
    )
    assert decision["family"] is None
    assert "claude:off" in decision["skipped"]


def test_only_an_enabled_arm_is_a_candidate(fleet):
    """The enabled set is honoured: with only claude enabled, deepseek is not a fallback."""
    decision = dispatch.plan(
        "implement a retry", ["claude"], fleet, {}, {}, "auto", None
    )
    assert decision["family"] == "claude"


def test_a_family_missing_from_preference_is_still_a_candidate():
    """Preference orders the fleet; it does not define membership."""
    fleet = {
        "preference": {"code": ["deepseek"]},
        "families": {"deepseek": {"command": "x"}, "custom": {"command": "y"}},
    }
    decision = dispatch.plan(
        "implement a retry", ["custom"], fleet, {}, {}, "auto", None
    )
    assert decision["family"] == "custom"


def test_a_skip_reason_is_reported_once():
    decision = dispatch.plan(
        "implement a retry", ["typo"], {"families": {}}, {}, {}, "auto", None
    )
    assert decision["skipped"] == ["typo:unknown"]


def test_main_exits_2_when_no_arm_is_usable(monkeypatch, capsys):
    monkeypatch.setattr(dispatch, "enabled_arms", lambda *a, **k: ["nosucharm"])
    rc = dispatch.main(["implement a retry"])
    assert rc == 2
    assert "no usable arm" in capsys.readouterr().err


def test_record_does_not_write_a_family_when_the_plan_failed(monkeypatch, tmp_path):
    target = tmp_path / "last-write.json"
    monkeypatch.setattr(dispatch, "LAST_WRITE", target)
    monkeypatch.setattr(dispatch, "enabled_arms", lambda *a, **k: ["nosucharm"])
    rc = dispatch.main(["implement a retry", "--record"])
    assert rc == 2
    assert not target.exists()


def test_record_writes_the_chosen_family(monkeypatch, tmp_path):
    target = tmp_path / "last-write.json"
    monkeypatch.setattr(dispatch, "LAST_WRITE", target)
    monkeypatch.setattr(dispatch, "enabled_arms", lambda *a, **k: ["deepseek"])
    assert dispatch.main(["implement a retry", "--record"]) == 0
    assert target.read_text(encoding="utf-8").find("deepseek") != -1
