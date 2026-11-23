#!/usr/bin/env python3
"""Tests for tools/codex_usage.py — Codex token/cost reporting."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "codex_usage.py"

_META = {
    "type": "session_meta",
    "session_id": "s1",
    "timestamp": "2026-10-07T10:00:00Z",
    "model": "gpt-5.6-sol",
}
_USAGE = {
    "type": "token_usage_record",
    "payload": {
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 40,
            "output_tokens": 10,
            "reasoning_output_tokens": 5,
        }
    },
}
_PRICING = {"gpt-5.6-sol": (5.0, 30.0, 0.5)}


def _load():
    spec = importlib.util.spec_from_file_location("codex_usage_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_rollout(directory: Path, name: str, lines: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )
    return path


# ── Usage / SessionReport ────────────────────────────────────────────────────


def test_usage_accumulates_and_derives():
    mod = _load()
    usage = mod.Usage()
    usage.add(
        {
            "input_tokens": 100,
            "cached_input_tokens": 40,
            "output_tokens": 10,
            "reasoning_output_tokens": 5,
        }
    )
    assert usage.uncached_input_tokens == 60
    assert usage.cache_hit_rate == pytest.approx(0.4)
    assert usage.calls == 1
    usage.add({})  # missing keys count as zero, still one call
    assert usage.calls == 2


def test_cache_hit_rate_is_zero_without_input():
    assert _load().Usage().cache_hit_rate == 0.0


def test_cost_priced_and_unpriced(tmp_path):
    mod = _load()
    report = mod.SessionReport(path=tmp_path / "r.jsonl", model="gpt-5.6-sol")
    report.usage.add(
        {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10}
    )
    # (60*5 + 40*0.5 + 10*30) / 1e6
    assert report.cost(_PRICING) == pytest.approx(
        (60 * 5 + 40 * 0.5 + 10 * 30) / 1_000_000
    )
    assert (
        mod.SessionReport(path=tmp_path / "x", model="unknown").cost(_PRICING) is None
    )


def test_delegate_default_model_is_priced():
    """The model the Codex delegate runs by default must have a price, so a
    future default bump cannot silently bring back "unpriced" sessions."""
    spec = importlib.util.spec_from_file_location(
        "codex_delegate_under_test", Path(__file__).with_name("codex_delegate.py")
    )
    assert spec and spec.loader
    delegate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = delegate
    spec.loader.exec_module(delegate)
    assert delegate.DEFAULT_MODEL in _load().PRICING


# ── parsing ──────────────────────────────────────────────────────────────────


def test_parse_pricing_override():
    mod = _load()
    assert mod.parse_pricing_override("m=1/2/3") == ("m", (1.0, 2.0, 3.0))
    for bad in ("nope", "m=1/2"):
        with pytest.raises(ValueError):
            mod.parse_pricing_override(bad)


def test_iter_jsonl_skips_blank_and_bad_lines(tmp_path):
    mod = _load()
    path = tmp_path / "x.jsonl"
    path.write_text('{"a": 1}\n\nnot json\n{"b": 2}\n', encoding="utf-8")
    assert list(mod._iter_jsonl(path)) == [{"a": 1}, {"b": 2}]


def test_read_session_aggregates(tmp_path):
    mod = _load()
    path = _write_rollout(
        tmp_path,
        "rollout-1.jsonl",
        [
            _META,
            _USAGE,
            _USAGE,
            {"type": "turn_context", "payload": {"model": "ignored-because-model-set"}},
            {"type": "other", "payload": "not-a-dict"},
        ],
    )
    report = mod.read_session(path)
    assert report.session_id == "s1"
    assert report.model == "gpt-5.6-sol"
    assert report.usage.input_tokens == 200
    assert report.usage.calls == 2


def test_read_session_uses_turn_context_model_when_meta_lacks_it(tmp_path):
    mod = _load()
    path = _write_rollout(
        tmp_path,
        "rollout-2.jsonl",
        [
            {
                "type": "session_meta",
                "session_id": "s2",
                "timestamp": "2026-10-07T11:00:00Z",
            },
            {"type": "turn_context", "payload": {"model": "gpt-5.6-luna"}},
        ],
    )
    assert mod.read_session(path).model == "gpt-5.6-luna"


# ── collect ──────────────────────────────────────────────────────────────────


def test_collect_missing_dir_is_empty(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "SESSIONS_DIR", tmp_path / "absent")
    assert mod.collect(None) == []


def test_collect_limit_takes_newest(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "SESSIONS_DIR", tmp_path)
    _write_rollout(tmp_path, "rollout-a.jsonl", [_META])
    _write_rollout(tmp_path, "rollout-b.jsonl", [_META])
    assert len(mod.collect(None)) == 2
    assert len(mod.collect(1)) == 1


def test_fmt_cost():
    mod = _load()
    assert mod._fmt_cost(None) == "n/a"
    assert mod._fmt_cost(1.5) == "$1.5000"


# ── report printing ──────────────────────────────────────────────────────────


def _report(mod, tmp_path, model="gpt-5.6-sol", started="2026-10-07T10:00:00Z"):
    report = mod.SessionReport(path=tmp_path / "r.jsonl", model=model, started=started)
    report.usage.add(
        {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10}
    )
    return report


def test_print_report_empty(monkeypatch, capsys, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "SESSIONS_DIR", tmp_path)
    mod.print_report([], _PRICING)
    assert "No Codex sessions found" in capsys.readouterr().out


def test_print_report_table_and_totals(capsys, tmp_path):
    mod = _load()
    mod.print_report([_report(mod, tmp_path)], _PRICING)
    out = capsys.readouterr().out
    assert "TOTAL" in out and "sessions: 1" in out


def test_print_report_flags_unpriced(capsys, tmp_path):
    mod = _load()
    mod.print_report([_report(mod, tmp_path, model="unknown-model")], _PRICING)
    assert "no price entry" in capsys.readouterr().out


# ── plan projection ──────────────────────────────────────────────────────────


def test_parse_timestamp_variants():
    mod = _load()
    assert mod._parse_timestamp("") is None
    assert mod._parse_timestamp("not-a-date") is None
    assert (
        mod._parse_timestamp("2026-10-07T10:00:00").tzinfo is not None
    )  # naive -> UTC
    assert mod._parse_timestamp("2026-10-07T10:00:00Z") is not None


def test_print_plan_with_no_priced_sessions(capsys, tmp_path):
    mod = _load()
    mod.print_plan([], _PRICING)
    assert "nothing to project" in capsys.readouterr().out


def test_print_plan_projects_recent_sessions(capsys, tmp_path):
    mod = _load()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    mod.print_plan([_report(mod, tmp_path, started=now)], _PRICING)
    out = capsys.readouterr().out
    assert "FreeModel prepay plan" in out
    assert "Projected at this rate" in out


# ── main ─────────────────────────────────────────────────────────────────────


def test_main_json(monkeypatch, capsys, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "collect", lambda _limit: [_report(mod, tmp_path)])
    monkeypatch.setattr(sys, "argv", ["codex_usage.py", "--json"])
    assert mod.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["model"] == "gpt-5.6-sol"
    assert payload[0]["cost_usd"] is not None


def test_main_plan_mode(monkeypatch, capsys, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "collect", lambda _limit: [])
    monkeypatch.setattr(sys, "argv", ["codex_usage.py", "--plan"])
    assert mod.main() == 0
    assert "FreeModel prepay plan" in capsys.readouterr().out


def test_main_bad_pricing_exits_two(monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(sys, "argv", ["codex_usage.py", "--pricing", "broken"])
    assert mod.main() == 2
    assert "error:" in capsys.readouterr().err


def test_main_table_by_default(monkeypatch, capsys, tmp_path):
    mod = _load()
    monkeypatch.setattr(mod, "collect", lambda _limit: [_report(mod, tmp_path)])
    monkeypatch.setattr(sys, "argv", ["codex_usage.py"])
    assert mod.main() == 0
    assert "TOTAL" in capsys.readouterr().out
