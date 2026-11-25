#!/usr/bin/env python3
"""Review check tests.

Pin `tools/review_check.py`: high and medium findings require a `ran` proof,
low findings may carry `read` or `inferred`, `No findings.` passes, unparseable
or empty reports are rejected, pipes in text and colons after numbers parse,
mixed reports name only unverified findings, and `--json` emits the documented
schema.

Usage:
    python -m pytest tools/test_review_check.py -q
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "review_check.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("review_check_under_test", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_report(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_high_finding_with_ran_proof_accepted(tmp_path: Path, capsys) -> None:
    """1. A high finding with a `ran` proof is accepted."""
    tool = _load_tool()
    report = _write_report(
        tmp_path,
        "report.txt",
        (
            "1. high | code | tools/portfolio.py:393 | two counts share a name\n"
            "  proof: ran python -m pytest tools/test_portfolio.py -q\n"
        ),
    )

    assert tool.main([str(report)]) == 0
    out = capsys.readouterr().out
    assert "accepted" in out
    assert "verified=1" in out


def test_high_finding_with_read_proof_rejected(tmp_path: Path, capsys) -> None:
    """2. A high finding with a `read` proof is rejected and names its number."""
    tool = _load_tool()
    report = _write_report(
        tmp_path,
        "report.txt",
        (
            "4. high | code | tools/portfolio.py:10 | stale branch check\n"
            "  proof: read tools/portfolio.py:10-20\n"
        ),
    )

    assert tool.main([str(report)]) == 1
    out = capsys.readouterr().out
    assert "4" in out
    assert "high" in out
    assert "rejected" in out


def test_medium_finding_with_inferred_proof_rejected(tmp_path: Path, capsys) -> None:
    """3. A medium finding with an `inferred` proof is rejected."""
    tool = _load_tool()
    report = _write_report(
        tmp_path,
        "report.txt",
        ("2. medium | feature | missing guard on empty input\n  proof: inferred\n"),
    )

    assert tool.main([str(report)]) == 1
    out = capsys.readouterr().out
    assert "2" in out
    assert "medium" in out
    assert "inferred=1" in out
    assert "rejected" in out


def test_low_finding_with_inferred_accepted(tmp_path: Path, capsys) -> None:
    """4. A low finding with `inferred` is accepted."""
    tool = _load_tool()
    report = _write_report(
        tmp_path,
        "report.txt",
        ("1. low | code | tools/foo.py:5 | minor naming drift\n  proof: inferred\n"),
    )

    assert tool.main([str(report)]) == 0
    out = capsys.readouterr().out
    assert "accepted" in out
    assert "inferred=1" in out


def test_no_findings_accepted(tmp_path: Path, capsys) -> None:
    """5. A report saying `No findings.` (with or without period) is accepted."""
    tool = _load_tool()
    report_dot = _write_report(tmp_path, "none_dot.txt", "No findings.\n")
    report_plain = _write_report(tmp_path, "none_plain.txt", "No findings\n")

    assert tool.main([str(report_dot)]) == 0
    assert "accepted" in capsys.readouterr().out

    assert tool.main([str(report_plain)]) == 0
    assert "accepted" in capsys.readouterr().out


def test_empty_or_prose_report_rejected_as_unparseable(tmp_path: Path, capsys) -> None:
    """6. Empty reports or prose with no headers are rejected as unparseable."""
    tool = _load_tool()
    empty = _write_report(tmp_path, "empty.txt", "")
    prose = _write_report(
        tmp_path, "prose.txt", "Everything looks clean and well tested.\n"
    )

    assert tool.main([str(empty)]) == 1
    out_empty = capsys.readouterr().out
    assert "unparseable" in out_empty
    assert "rejected" in out_empty

    assert tool.main([str(prose)]) == 1
    out_prose = capsys.readouterr().out
    assert "unparseable" in out_prose
    assert "rejected" in out_prose


def test_pipes_in_text_and_colon_after_number_parse(tmp_path: Path) -> None:
    """7. Text containing pipes and a colon after the number still parse."""
    tool = _load_tool()
    report = _write_report(
        tmp_path,
        "report.txt",
        (
            "7: high | code | tools/a.py:12 | expected A | B but got C\n"
            "  proof: ran python -m pytest tools/test_a.py -q\n"
        ),
    )

    result = tool.check_file(report)
    assert result["ok"] is True
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["number"] == 7
    assert finding["severity"] == "high"
    assert finding["kind"] == "code"
    assert finding["text"] == "tools/a.py:12 | expected A | B but got C"
    assert finding["proof_kind"] == "ran"
    assert finding["proof"] == "python -m pytest tools/test_a.py -q"


def test_mixed_findings_rejected_names_only_unverified(tmp_path: Path, capsys) -> None:
    """8. Mixed findings: rejected and only the unverified finding is named."""
    tool = _load_tool()
    report = _write_report(
        tmp_path,
        "mixed.txt",
        (
            "11. high | code | tools/ok.py:1 | verified bug\n"
            "  proof: ran python -m pytest -q\n"
            "22. medium | feature | missing check\n"
            "  proof: read tools/bad.py:42\n"
        ),
    )

    assert tool.main([str(report)]) == 1
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 2
    assert "22" in lines[0]
    assert "medium" in lines[0]
    assert "11" not in lines[0]
    assert "rejected" in lines[1]
    assert "verified=1" in lines[1]
    assert "read=1" in lines[1]


def test_json_output_returns_documented_shape(tmp_path: Path, capsys) -> None:
    """9. `--json` returns the documented top-level and finding keys."""
    tool = _load_tool()
    report = _write_report(
        tmp_path,
        "report.txt",
        (
            "1. high | code | tools/x.py:9 | broken guard\n"
            "  proof: ran pytest -q\n"
            "2. low | feature | typo in help\n"
            "  proof: read tools/x.py:20\n"
        ),
    )

    assert tool.main([str(report), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"ok", "findings", "reasons"}
    assert payload["ok"] is True
    assert payload["reasons"] == []
    assert len(payload["findings"]) == 2
    for item in payload["findings"]:
        assert set(item) == {
            "number",
            "severity",
            "kind",
            "text",
            "proof_kind",
            "proof",
        }


def test_missing_report_file_exits_2(tmp_path: Path, capsys) -> None:
    """A missing report file returns exit code 2."""
    tool = _load_tool()
    missing = tmp_path / "does-not-exist.txt"

    assert tool.main([str(missing)]) == 2
    assert "cannot read report" in capsys.readouterr().err


def test_missing_proof_line_rejected(tmp_path: Path, capsys) -> None:
    """A finding without a `proof:` line is rejected as missing proof."""
    tool = _load_tool()
    report = _write_report(
        tmp_path,
        "missing_proof.txt",
        "1. high | code | tools/x.py:1 | no evidence here\n",
    )

    assert tool.main([str(report)]) == 1
    out = capsys.readouterr().out
    assert "1" in out
    assert "missing proof" in out


if __name__ == "__main__":
    sys.exit("Run with: python -m pytest tools/test_review_check.py -q")
