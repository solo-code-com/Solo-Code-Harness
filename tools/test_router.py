#!/usr/bin/env python3
"""
Router Tests
============
Offline unit tests for tools/router.py -- the rule-table router that names a
brain, a worker and a budget for a task string.

Background: the orchestrator chose workers by hand and nothing recorded the
choice, so a delegation that silently fell back to the executor model looked
identical to a routed one. The router has to be reproducible, so every test here
runs without a network call and without consulting a brain.

Usage:
    python -m pytest tools/test_router.py -v
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import router  # noqa: E402

CONTRACT_KEYS = {
    "task_type",
    "brain",
    "worker",
    "model",
    "reason",
    "budget",
    "evidence",
}
LEDGER_KEYS = {
    "task_type",
    "brain",
    "worker",
    "model",
    "reason",
    "prompt_chars",
    "prompt_hash",
    "at",
    "spawned",
}
SHADOW_KEYS = {"version", "label", "confidence", "noul", "error"}

#: One representative prompt per task type.
CASES = {
    "question": "explain how the auth middleware works",
    "simple_edit": "rename the foo variable to bar in tools/sample.py",
    "complex": "build a routing module and refactor the session registry",
    "review": "review this diff for correctness",
    "survey": "where else does this pattern appear in the repo?",
    "destructive": "delete the users table",
}


def test_each_task_type_classifies_from_its_trigger():
    for expected, task in CASES.items():
        assert router.route(task)["task_type"] == expected, task


def test_a_destructive_task_stops_the_route():
    decision = router.route("drop the users table")

    assert decision["worker"] == "none"
    assert decision["budget"]["max_seconds"] == 0
    assert decision["model"] is None


def test_every_route_names_the_rule_table_as_its_brain():
    for task_type, task in CASES.items():
        assert router.route(task)["brain"] == "rules", task_type


def test_a_question_is_answered_in_place():
    decision = router.route(CASES["question"])

    assert decision["worker"] == "none"
    assert decision["budget"]["max_seconds"] > 0
    # The other "none" route is the blocked one: budget, not worker, tells them apart.
    assert router.route(CASES["destructive"])["budget"]["max_seconds"] == 0


def test_route_returns_the_full_contract():
    decision = router.route(CASES["review"])

    assert set(decision) == CONTRACT_KEYS
    assert set(decision["budget"]) == {"max_seconds", "max_prompt_chars"}
    assert decision["worker"] in router.DECLARED_WORKERS


def test_reason_is_written_from_the_task():
    first = router.route(CASES["review"])
    second = router.route("review the payments module for null handling")

    assert first["reason"].strip()
    assert first["reason"] != second["reason"]
    assert "review" in first["reason"]


def test_evidence_carries_a_model_roles_hash():
    evidence = router.route(CASES["question"])["evidence"]

    assert re.search(r"\b[0-9a-f]{8}\b", evidence)


def test_evidence_names_the_current_rules_version():
    assert router.route(CASES["complex"])["evidence"].startswith(
        f"rules:{router.RULES_VERSION}"
    )


def test_evidence_falls_back_when_the_roles_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "MODEL_ROLES_PATH", tmp_path / "absent.json")

    decision = router.route(CASES["complex"])

    assert decision["evidence"] == router.EVIDENCE_UNREADABLE
    assert decision["model"] is None


def test_an_invented_worker_is_an_error():
    with pytest.raises(ValueError, match="not a declared worker"):
        router.route(CASES["review"], force_worker="cli:invented")


def test_dry_run_appends_a_ledger_row(tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "route-decisions.jsonl"
    monkeypatch.setattr(router, "LEDGER_PATH", ledger)
    task = CASES["question"]

    assert router.main([task, "--dry-run"]) == 0

    rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == LEDGER_KEYS
    assert row["task_type"] == "question"
    assert row["spawned"] is False
    assert row["prompt_chars"] == len(task)
    assert row["prompt_hash"] == hashlib.sha256(task.encode("utf-8")).hexdigest()[:12]
    # The printed decision is the one that was logged.
    assert json.loads(capsys.readouterr().out)["reason"] == row["reason"]
    # The repo ledger was never touched: the temp path replaced it for this test.
    assert ledger == router.LEDGER_PATH


def test_a_plain_run_writes_no_ledger(tmp_path, monkeypatch):
    ledger = tmp_path / "route-decisions.jsonl"
    monkeypatch.setattr(router, "LEDGER_PATH", ledger)

    assert router.main([CASES["question"]]) == 0
    assert not ledger.exists()


def test_a_shadow_row_carries_the_nine_keys_plus_shadow(tmp_path, monkeypatch):
    ledger = tmp_path / "route-decisions.jsonl"
    monkeypatch.setattr(router, "LEDGER_PATH", ledger)
    monkeypatch.setattr(
        router,
        "shadow_decision",
        lambda task, decision: {
            "version": router.SHADOW_QUESTIONS_VERSION,
            "label": "question",
            "confidence": 0.62,
            "noul": 0.2,
            "error": None,
        },
    )

    assert router.main([CASES["question"], "--shadow", "--dry-run"]) == 0

    rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == LEDGER_KEYS | {"shadow"}
    assert set(row["shadow"]) == SHADOW_KEYS
    assert row["shadow"]["version"] == router.SHADOW_QUESTIONS_VERSION
    assert row["shadow"]["error"] is None


def test_shadow_never_changes_the_decision(tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "route-decisions.jsonl"
    monkeypatch.setattr(router, "LEDGER_PATH", ledger)
    monkeypatch.setattr(
        router,
        "shadow_decision",
        lambda task, decision: {
            "version": router.SHADOW_QUESTIONS_VERSION,
            "label": "survey",
            "confidence": 0.51,
            "noul": 0.4,
            "error": None,
        },
    )

    # --shadow without --dry-run still logs, and the label is one the rules never pick.
    assert router.main([CASES["question"], "--shadow"]) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["task_type"] == "question"
    assert printed["worker"] == "none"
    assert "shadow" not in printed
    rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["task_type"] == "question"
    assert rows[0]["shadow"]["label"] == "survey"


def test_shadow_import_is_lazy():
    program = (
        "import sys\n"
        f"sys.path.insert(0, {str(TOOLS)!r})\n"
        "import router\n"
        "assert 'jev_decide' not in sys.modules, 'router imported the brain eagerly'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0, completed.stderr
    # No network stack at module scope either: the brain import waits for --shadow.
    assert not any(name.split(".")[0] == "urllib" for name in vars(router))


def test_shadow_records_an_error_instead_of_raising(tmp_path, monkeypatch):
    ledger = tmp_path / "route-decisions.jsonl"
    monkeypatch.setattr(router, "LEDGER_PATH", ledger)
    offline = ModuleType("jev_decide")

    def refuse(*_args, **_kwargs):
        raise RuntimeError("brain is offline")

    offline.decide = refuse
    monkeypatch.setitem(sys.modules, "jev_decide", offline)

    assert router.main([CASES["question"], "--shadow"]) == 0

    row = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert row["task_type"] == "question"
    assert row["shadow"]["label"] is None
    assert row["shadow"]["confidence"] is None
    assert row["shadow"]["error"] == "RuntimeError: brain is offline"


def test_shadow_records_the_answer_the_brain_gives(tmp_path, monkeypatch):
    ledger = tmp_path / "route-decisions.jsonl"
    monkeypatch.setattr(router, "LEDGER_PATH", ledger)
    brain = ModuleType("jev_decide")

    def answer(state, questions, *, timeout):
        return {
            "answers": {
                "task_type": {"type": "choice", "choice": "survey", "confidence": 0.71},
                "needs_second_opinion": {"type": "noul", "noul": 0.33},
            }
        }

    brain.decide = answer
    monkeypatch.setitem(sys.modules, "jev_decide", brain)

    assert router.main([CASES["question"], "--shadow"]) == 0

    row = json.loads(ledger.read_text(encoding="utf-8").strip())
    # The rules still route it as a question; only the shadow label disagrees.
    assert row["task_type"] == "question"
    assert row["shadow"]["label"] == "survey"
    assert row["shadow"]["confidence"] == 0.71
    assert row["shadow"]["noul"] == 0.33
    assert row["shadow"]["error"] is None


def test_probe_is_not_wired(capsys):
    with pytest.raises(NotImplementedError, match="not wired"):
        router.probe({"brain": "jev-router"})

    assert router.main([CASES["question"], "--probe"]) == 2
    assert "not wired" in capsys.readouterr().err


def test_route_never_touches_the_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("route() opened a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)

    assert router.route(CASES["complex"])["task_type"] == "complex"


def test_an_empty_task_is_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        router.route("   ")


def _stub_jev(monkeypatch, fn, error_cls=None):
    mod = ModuleType("jev_decide")
    mod.JevDecideError = error_cls or type("JevDecideError", (Exception,), {})
    mod.decide = fn
    monkeypatch.setitem(sys.modules, "jev_decide", mod)
    return mod


def test_jev_route_destructive_never_calls_jev(monkeypatch):
    called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Jev should not be called for destructive tasks")

    _stub_jev(monkeypatch, fail_if_called)

    decision = router.jev_route("delete the users table")

    assert called is False
    assert decision["task_type"] == "destructive"
    assert decision["worker"] == "none"
    assert decision["jev"] is None
    assert "hard deny list" in decision["jev_note"]


def test_jev_route_confident_safe_answer_replaces_worker(monkeypatch):
    seen = {}

    def fake_decide(state, questions, *, model, timeout):
        seen["state"] = state
        seen["questions"] = questions
        seen["model"] = model
        seen["timeout"] = timeout
        return {
            "answers": {
                "worker": {
                    "type": "choice",
                    "choice": "cli:antigravity",
                    "confidence": 0.88,
                },
                "safe": {"type": "noul", "noul": 0.12},
            }
        }

    _stub_jev(monkeypatch, fake_decide)

    decision = router.jev_route(CASES["simple_edit"])

    assert set(seen["questions"]) == {"worker", "safe"}
    assert set(seen["questions"]["worker"]["criteria"]) == router.DECLARED_WORKERS
    assert (
        seen["questions"]["worker"]["criteria"]["none"]
        == "no delegation; the orchestrator answers or decides in place"
    )
    assert seen["model"] == "typesafe/jev-1.14.0"
    assert seen["timeout"] == 20
    assert decision["worker"] == "cli:antigravity"
    assert decision["brain"] == "jev"
    assert "0.88" in decision["reason"]
    assert "0.12" in decision["reason"]
    assert decision["jev_note"] == "accepted"
    assert decision["jev"]["worker"]["choice"] == "cli:antigravity"


def test_jev_route_low_confidence_keeps_rule_worker(monkeypatch):
    def fake_decide(*_args, **_kwargs):
        return {
            "answers": {
                "worker": {
                    "type": "choice",
                    "choice": "subagent:explore",
                    "confidence": 0.42,
                },
                "safe": {"type": "noul", "noul": 0.10},
            }
        }

    _stub_jev(monkeypatch, fake_decide)
    expected = router.route(CASES["simple_edit"])

    decision = router.jev_route(CASES["simple_edit"])

    assert decision["worker"] == expected["worker"]
    assert decision["brain"] == "rules"
    assert "confidence" in decision["jev_note"]
    assert decision["jev"]["worker"]["confidence"] == 0.42


def test_jev_route_unsafe_noul_keeps_rule_worker(monkeypatch):
    def fake_decide(*_args, **_kwargs):
        return {
            "answers": {
                "worker": {
                    "type": "choice",
                    "choice": "subagent:explore",
                    "confidence": 0.91,
                },
                "safe": {"type": "noul", "noul": 0.01},
            }
        }

    _stub_jev(monkeypatch, fake_decide)
    expected = router.route(CASES["simple_edit"])

    decision = router.jev_route(CASES["simple_edit"])

    assert decision["worker"] == expected["worker"]
    assert decision["brain"] == "rules"
    assert "0.03" in decision["jev_note"] and "noul" in decision["jev_note"]


def test_jev_route_undeclared_worker_keeps_rule_worker(monkeypatch):
    def fake_decide(*_args, **_kwargs):
        return {
            "answers": {
                "worker": {
                    "type": "choice",
                    "choice": "cli:invented",
                    "confidence": 0.95,
                },
                "safe": {"type": "noul", "noul": 0.05},
            }
        }

    _stub_jev(monkeypatch, fake_decide)
    expected = router.route(CASES["simple_edit"])

    decision = router.jev_route(CASES["simple_edit"])

    assert decision["worker"] == expected["worker"]
    assert decision["brain"] == "rules"
    assert "undeclared" in decision["jev_note"]


def test_jev_route_error_falls_back_without_raising(monkeypatch):
    from jev_decide import JevDecideError

    def fake_decide(*_args, **_kwargs):
        raise JevDecideError("HTTP 503: provider unavailable")

    _stub_jev(monkeypatch, fake_decide, error_cls=JevDecideError)
    expected = router.route(CASES["complex"])

    decision = router.jev_route(CASES["complex"])

    assert decision["worker"] == expected["worker"]
    assert decision["brain"] == "rules"
    assert decision["jev"] is None
    assert "HTTP 503: provider unavailable" in decision["jev_note"]


def test_jev_route_oserror_truncates_note_to_120_chars(monkeypatch):
    long_msg = "connection failed: " + ("x" * 200)

    def fake_decide(*_args, **_kwargs):
        raise OSError(long_msg)

    _stub_jev(monkeypatch, fake_decide)

    decision = router.jev_route(CASES["complex"])

    assert decision["brain"] == "rules"
    assert decision["jev"] is None
    assert len(decision["jev_note"]) == 120
    assert decision["jev_note"] == long_msg[:120]


def test_cli_jev_prints_jev_block(monkeypatch, capsys):
    def fake_decide(*_args, **_kwargs):
        return {
            "answers": {
                "worker": {
                    "type": "choice",
                    "choice": "subagent:explore",
                    "confidence": 0.85,
                },
                "safe": {"type": "noul", "noul": 0.15},
            }
        }

    _stub_jev(monkeypatch, fake_decide)

    assert router.main([CASES["question"], "--jev"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["brain"] == "jev"
    assert printed["worker"] == "subagent:explore"
    assert printed["jev_note"] == "accepted"
    assert "jev" not in printed
    assert "worker" in printed["advisor"]


def test_jev_route_split_within_group_is_accepted(monkeypatch):
    def fake_decide(*_args, **_kwargs):
        return {
            "answers": {
                "worker": {
                    "type": "choice",
                    "choice": "cli:antigravity",
                    "confidence": 0.55,
                    "probabilities": {
                        "cli:antigravity": 0.55,
                        "subagent:explore": 0.30,
                    },
                },
                "safe": {"type": "noul", "noul": 0.10},
            }
        }

    _stub_jev(monkeypatch, fake_decide)

    decision = router.jev_route(CASES["simple_edit"])

    assert decision["worker"] == "cli:antigravity"
    assert decision["brain"] == "jev"
    assert decision["jev_note"] == "accepted"
    assert "0.55" in decision["reason"]
    assert "0.85" in decision["reason"]


def test_jev_route_split_within_group_below_threshold_is_rejected(monkeypatch):
    def fake_decide(*_args, **_kwargs):
        return {
            "answers": {
                "worker": {
                    "type": "choice",
                    "choice": "cli:antigravity",
                    "confidence": 0.30,
                    "probabilities": {
                        "cli:antigravity": 0.30,
                        "subagent:explore": 0.20,
                    },
                },
                "safe": {"type": "noul", "noul": 0.10},
            }
        }

    _stub_jev(monkeypatch, fake_decide)
    expected = router.route(CASES["simple_edit"])

    decision = router.jev_route(CASES["simple_edit"])

    assert decision["worker"] == expected["worker"]
    assert decision["brain"] == "rules"
    assert "0.50" in decision["jev_note"]
    assert "0.30" in decision["jev_note"]
    assert "0.65" in decision["jev_note"]


def test_jev_route_single_worker_group_none_threshold(monkeypatch):
    def accept_none(*_args, **_kwargs):
        return {
            "answers": {
                "worker": {
                    "type": "choice",
                    "choice": "none",
                    "confidence": 0.70,
                    "probabilities": {"none": 0.70, "cli:opencode": 0.20},
                },
                "safe": {"type": "noul", "noul": 0.10},
            }
        }

    _stub_jev(monkeypatch, accept_none)
    accepted = router.jev_route(CASES["simple_edit"])
    assert accepted["worker"] == "none"
    assert accepted["brain"] == "jev"
    assert accepted["jev_note"] == "accepted"

    def reject_none(*_args, **_kwargs):
        return {
            "answers": {
                "worker": {
                    "type": "choice",
                    "choice": "none",
                    "confidence": 0.60,
                    "probabilities": {"none": 0.60, "cli:opencode": 0.25},
                },
                "safe": {"type": "noul", "noul": 0.10},
            }
        }

    _stub_jev(monkeypatch, reject_none)
    expected = router.route(CASES["simple_edit"])
    rejected = router.jev_route(CASES["simple_edit"])
    assert rejected["worker"] == expected["worker"]
    assert rejected["brain"] == "rules"
    assert "0.60" in rejected["jev_note"]


def test_group_confidence_ignores_undeclared_keys_and_non_numeric_values():
    assert (
        router._group_confidence(
            "cli:antigravity",
            {"cli:antigravity": 0.40, "cli:invented": 0.90},
        )
        == 0.40
    )
    assert router._group_confidence("cli:invented", {"cli:invented": 0.90}) == 0.0
    assert (
        router._group_confidence(
            "cli:antigravity",
            {"cli:antigravity": 0.40, "subagent:explore": "bad"},
        )
        == 0.40
    )
    assert (
        router._group_confidence(
            "cli:antigravity",
            {"cli:antigravity": 0.40, "subagent:explore": True},
        )
        == 0.40
    )


def _stub_gemini_http(monkeypatch, handler, *, api_key="test-secret-key"):
    """Stub env_file and urllib.request.urlopen so _gemini_decide never hits the network."""
    env_mod = ModuleType("env_file")
    env_mod.load_project_env = lambda *_args, **_kwargs: {}
    monkeypatch.setitem(sys.modules, "env_file", env_mod)
    if api_key is None:
        monkeypatch.delenv("GOOGLE_GEMINI_KEY", raising=False)
    else:
        monkeypatch.setenv("GOOGLE_GEMINI_KEY", api_key)

    class _FakeResponse:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

    def fake_urlopen(request, timeout=20):
        result = handler(request, timeout)
        if isinstance(result, bytes):
            return _FakeResponse(result)
        return _FakeResponse(json.dumps(result).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_decide_route_confident_gemini_replaces_worker(monkeypatch):
    seen = {}

    def handle(request, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["headers"] = {k.lower(): v for k, v in request.header_items()}
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        return {
            "answers": {
                "worker": {
                    "type": "choice",
                    "choice": "cli:antigravity",
                    "confidence": 0.92,
                    "probabilities": {"cli:antigravity": 0.92, "none": 0.08},
                },
                "safe": {"type": "noul", "noul": 0.14},
            }
        }

    _stub_gemini_http(monkeypatch, handle)

    decision = router.decide_route(CASES["simple_edit"])

    assert seen["url"] == router._GEMINI_URL
    assert seen["method"] == "POST"
    assert seen["headers"]["x-goog-api-key"] == "test-secret-key"
    assert seen["body"]["model"] == "gemini-decision-1"
    assert seen["body"]["input"] == [{"type": "text", "text": CASES["simple_edit"]}]
    assert set(seen["body"]["questions"]) == {"worker", "safe"}
    assert seen["timeout"] == 20
    assert decision["worker"] == "cli:antigravity"
    assert decision["brain"] == "gemini"
    assert "cli:antigravity" in decision["reason"]
    assert "0.92" in decision["reason"]
    assert "0.14" in decision["reason"]
    assert decision["advisor"]["worker"]["choice"] == "cli:antigravity"
    assert "jev" not in decision
    assert decision["jev_note"] == "accepted"


def test_decide_route_gemini_failure_falls_through_to_jev(monkeypatch):
    def fail_gemini(*_args, **_kwargs):
        raise router.DecisionError("HTTP 503: service unavailable")

    def fake_jev(*_args, **_kwargs):
        return {
            "answers": {
                "worker": {
                    "type": "choice",
                    "choice": "subagent:explore",
                    "confidence": 0.88,
                },
                "safe": {"type": "noul", "noul": 0.10},
            }
        }

    monkeypatch.setattr(router, "_gemini_decide", fail_gemini)
    _stub_jev(monkeypatch, fake_jev)

    decision = router.decide_route(CASES["simple_edit"])

    assert decision["brain"] == "jev"
    assert decision["worker"] == "subagent:explore"
    assert decision["advisor"]["worker"]["choice"] == "subagent:explore"
    assert "HTTP 503: service unavailable" in decision["jev_note"]


def test_decide_route_both_backends_fail_returns_none_and_both_errors(monkeypatch):
    from jev_decide import JevDecideError

    def fail_gemini(*_args, **_kwargs):
        raise router.DecisionError("gemini timeout")

    def fail_jev(*_args, **_kwargs):
        raise JevDecideError("jev gateway 502")

    monkeypatch.setattr(router, "_gemini_decide", fail_gemini)
    _stub_jev(monkeypatch, fail_jev, error_cls=JevDecideError)

    decision = router.decide_route(CASES["complex"])

    assert decision["worker"] == "none"
    assert decision["brain"] == "none"
    assert decision["advisor"] is None
    assert "jev" not in decision
    assert "gemini timeout" in decision["jev_note"]
    assert "jev gateway 502" in decision["jev_note"]


def test_decide_route_low_confidence_stops_with_none(monkeypatch):
    monkeypatch.setattr(
        router,
        "_gemini_decide",
        lambda *_args, **_kwargs: {
            "worker": {
                "type": "choice",
                "choice": "cli:opencode",
                "confidence": 0.35,
            },
            "safe": {"type": "noul", "noul": 0.10},
        },
    )

    decision = router.decide_route(CASES["simple_edit"])

    assert decision["worker"] == "none"
    assert decision["brain"] == "none"
    assert "jev" not in decision
    assert "confidence" in decision["jev_note"]


def test_decide_route_blocks_low_safety_noul_and_accepts_high_noul(monkeypatch):
    monkeypatch.setattr(
        router,
        "_gemini_decide",
        lambda *_args, **_kwargs: {
            "worker": {
                "type": "choice",
                "choice": "cli:antigravity",
                "confidence": 0.90,
            },
            "safe": {"type": "noul", "noul": 0.01},
        },
    )
    blocked = router.decide_route(CASES["simple_edit"])
    assert blocked["worker"] == "none"
    assert blocked["brain"] == "none"
    assert "jev" not in blocked
    assert "unsafe" in blocked["jev_note"]
    assert "0.01" in blocked["jev_note"]
    assert "0.03" in blocked["jev_note"]

    monkeypatch.setattr(
        router,
        "_gemini_decide",
        lambda *_args, **_kwargs: {
            "worker": {
                "type": "choice",
                "choice": "cli:antigravity",
                "confidence": 0.90,
            },
            "safe": {"type": "noul", "noul": 0.95},
        },
    )
    accepted = router.decide_route(CASES["simple_edit"])
    assert accepted["worker"] == "cli:antigravity"
    assert accepted["brain"] == "gemini"
    assert "jev" not in accepted
    assert accepted["jev_note"] == "accepted"


def test_high_safety_noul_does_not_block(monkeypatch):
    # This shipped inverted once (blocking high noul instead of low); the test
    # exists to stop it shipping again.
    monkeypatch.setattr(
        router,
        "_gemini_decide",
        lambda *_args, **_kwargs: {
            "worker": {
                "type": "choice",
                "choice": "cli:antigravity",
                "confidence": 0.95,
            },
            "safe": {"type": "noul", "noul": 0.95},
        },
    )
    decision = router.decide_route(CASES["survey"])
    assert decision["worker"] == "cli:antigravity"
    assert decision["brain"] == "gemini"
    assert decision["jev_note"] == "accepted"

    def fake_jev(*_args, **_kwargs):
        return {
            "answers": {
                "worker": {
                    "type": "choice",
                    "choice": "cli:antigravity",
                    "confidence": 0.95,
                },
                "safe": {"type": "noul", "noul": 0.95},
            }
        }

    _stub_jev(monkeypatch, fake_jev)
    jev_decision = router.jev_route(CASES["survey"])
    assert jev_decision["worker"] == "cli:antigravity"
    assert jev_decision["brain"] == "jev"
    assert jev_decision["jev_note"] == "accepted"


def test_decide_route_destructive_calls_neither_backend(monkeypatch):
    called = []

    def fail_gemini(*_args, **_kwargs):
        called.append("gemini")
        raise AssertionError("gemini should not be called")

    def fail_jev(*_args, **_kwargs):
        called.append("jev")
        raise AssertionError("jev should not be called")

    monkeypatch.setattr(router, "_gemini_decide", fail_gemini)
    _stub_jev(monkeypatch, fail_jev)

    decision = router.decide_route("delete the users table")

    assert called == []
    assert decision["task_type"] == "destructive"
    assert decision["brain"] == "rules"
    assert decision["worker"] == "none"
    assert decision["advisor"] is None
    assert "jev" not in decision
    assert "hard deny list" in decision["jev_note"]


def test_decide_route_split_within_group_is_accepted(monkeypatch):
    monkeypatch.setattr(
        router,
        "_gemini_decide",
        lambda *_args, **_kwargs: {
            "worker": {
                "type": "choice",
                "choice": "cli:antigravity",
                "confidence": 0.40,
                "probabilities": {
                    "cli:antigravity": 0.40,
                    "subagent:explore": 0.25,
                },
            },
            "safe": {"type": "noul", "noul": 0.10},
        },
    )

    decision = router.decide_route(CASES["simple_edit"])

    assert decision["worker"] == "cli:antigravity"
    assert decision["brain"] == "gemini"
    assert decision["jev_note"] == "accepted"
    assert "jev" not in decision
    assert "0.65" in decision["reason"]


def test_decide_route_undeclared_worker_stops_with_none(monkeypatch):
    monkeypatch.setattr(
        router,
        "_gemini_decide",
        lambda *_args, **_kwargs: {
            "worker": {
                "type": "choice",
                "choice": "cli:invented",
                "confidence": 0.95,
            },
            "safe": {"type": "noul", "noul": 0.05},
        },
    )

    decision = router.decide_route(CASES["simple_edit"])

    assert decision["worker"] == "none"
    assert decision["brain"] == "none"
    assert "jev" not in decision
    assert "undeclared" in decision["jev_note"]


def test_gemini_decide_translates_failures_and_scrubs_key(monkeypatch):
    import io
    import urllib.error

    leak_canary = "placeholder-key-should-not-leak"

    _stub_gemini_http(monkeypatch, lambda *_args: {}, api_key=None)
    with pytest.raises(router.DecisionError, match="GOOGLE_GEMINI_KEY"):
        router._gemini_decide(CASES["question"])

    def raise_http(*_args):
        raise urllib.error.HTTPError(
            router._GEMINI_URL,
            403,
            "Forbidden",
            {},
            io.BytesIO(f"bad key {leak_canary}".encode()),
        )

    _stub_gemini_http(monkeypatch, raise_http, api_key=leak_canary)
    with pytest.raises(router.DecisionError, match="HTTP 403") as http_exc:
        router._gemini_decide(CASES["question"])
    assert leak_canary not in str(http_exc.value)

    def raise_oserror(*_args):
        raise OSError(f"proxy rejected key {leak_canary}")

    _stub_gemini_http(monkeypatch, raise_oserror, api_key=leak_canary)
    with pytest.raises(router.DecisionError) as exc_info:
        router._gemini_decide(CASES["question"])
    assert leak_canary not in str(exc_info.value)

    _stub_gemini_http(monkeypatch, lambda *_args: b"{not json", api_key=leak_canary)
    with pytest.raises(router.DecisionError, match="invalid JSON"):
        router._gemini_decide(CASES["question"])


def test_cli_decide_and_backend_flags(monkeypatch, capsys):
    monkeypatch.setattr(
        router,
        "_gemini_decide",
        lambda *_args, **_kwargs: {
            "worker": {
                "type": "choice",
                "choice": "subagent:explore",
                "confidence": 0.90,
            },
            "safe": {"type": "noul", "noul": 0.10},
        },
    )
    assert router.main([CASES["question"], "--decide", "--backend", "gemini"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["brain"] == "gemini"
    assert printed["worker"] == "subagent:explore"
    assert "jev" not in printed
    assert printed["advisor"]["worker"]["choice"] == "subagent:explore"

    assert router.main([CASES["question"], "--decide", "--probe"]) == 2
    assert "not wired" in capsys.readouterr().err


def test_worker_descriptions_pin_explore_and_antigravity_wording():
    criteria = router._routing_questions()["worker"]["criteria"]

    assert set(criteria) == router.DECLARED_WORKERS
    assert "locates" in criteria["subagent:explore"]
    assert "summarize" not in criteria["subagent:explore"]
    assert "summarizes" in criteria["cli:antigravity"]


def test_group_confidence_rejects_non_string_choice_and_non_dict_probabilities():
    """A backend that answers with the wrong shapes scores zero, not a crash."""
    assert router._group_confidence(None, {"none": 1.0}) == 0.0
    assert router._group_confidence("none", ["none"]) == 0.0


def test_as_finite_float_returns_the_default_for_bools_and_junk():
    """bool is an int subclass, so True would otherwise score as 1.0."""
    assert router._as_finite_float(True, 7.5) == 7.5
    assert router._as_finite_float("not a number", 7.5) == 7.5
    assert router._as_finite_float(None, 7.5) == 7.5


def test_parse_routing_answers_survives_non_dict_blocks():
    """A malformed worker or safety block must not raise; defaults stand in."""
    choice, confidence, group_confidence, noul = router._parse_routing_answers(
        {"worker": "cli:opencode", "safe": 5}
    )

    assert choice is None
    assert confidence == 0.0
    assert group_confidence == 0.0
    assert noul == 1.0


def test_gemini_decide_adds_its_own_directory_to_sys_path(monkeypatch):
    """The module imports env_file as a sibling, so its directory must be importable."""
    tools_dir = str(ROOT / "tools")
    _stub_gemini_http(
        monkeypatch,
        lambda *_args: {"answers": {"worker": {"choice": "none"}}},
        api_key="test-secret-key",
    )
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != tools_dir])
    assert tools_dir not in sys.path

    router._gemini_decide(CASES["question"])

    assert tools_dir in sys.path


def test_gemini_decide_rejects_a_body_without_answers(monkeypatch):
    """A 200 carrying the wrong shape is a DecisionError, not a KeyError later."""
    _stub_gemini_http(monkeypatch, lambda *_args: {"model": "x"}, api_key="k")

    with pytest.raises(router.DecisionError, match="missing answers"):
        router._gemini_decide(CASES["question"])


def test_jev_decide_reraises_a_programming_error(monkeypatch):
    """Only transport failures become DecisionError; a bug in the backend must surface."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    _stub_jev(monkeypatch, boom)

    with pytest.raises(RuntimeError, match="boom"):
        router._jev_decide(CASES["question"])


def test_jev_decide_returns_empty_when_the_response_has_no_answers(monkeypatch):
    _stub_jev(monkeypatch, lambda *_args, **_kwargs: {"model": "jev"})

    assert router._jev_decide(CASES["question"]) == {}


def test_decide_route_rejects_an_unknown_backend():
    with pytest.raises(ValueError, match="unknown backend"):
        router.decide_route(CASES["question"], backend="invented")


def test_append_decision_survives_an_unwritable_ledger(tmp_path):
    """A full disk must not lose the decision that was already printed."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    router.append_decision(
        CASES["question"], router.route(CASES["question"]), blocker / "ledger.jsonl"
    )

    assert blocker.is_file()


def test_cli_reports_a_value_error_and_exits_2(capsys):
    """A bad task becomes a message and exit 2, not a traceback."""
    assert router.main(["   "]) == 2
    assert "non-empty" in capsys.readouterr().err


def test_module_entry_point_exits_through_main(monkeypatch):
    """`python tools/router.py` must exit with main()'s code."""
    import runpy

    monkeypatch.setattr(sys, "argv", ["router.py", CASES["question"]])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(ROOT / "tools" / "router.py"), run_name="__main__")

    assert exc_info.value.code == 0
