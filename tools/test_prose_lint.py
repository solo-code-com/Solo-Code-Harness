"""Tests for tools/prose_lint.py: the Markdown prose budget gate.

The gate counts long sentences, semicolons, and em-dashes in Markdown only.
`.claude`, `.copilot`, and `.gemini` are out of scope (they mirror `.kilo`),
and non-`.md` files are ignored. These tests pin that scope cut in place.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import prose_lint  # noqa: E402


def _long_sentence():
    return " ".join(f"word{i}" for i in range(31)) + "."


def test_long_sentence_counted():
    long_count, semi_count, dash_count = prose_lint.analyze(_long_sentence())
    assert long_count == 1
    assert semi_count == 0
    assert dash_count == 0


def test_fenced_block_not_counted():
    text = "```\n" + _long_sentence() + "\n```\n"
    long_count, _, _ = prose_lint.analyze(text)
    assert long_count == 0


def test_table_row_not_counted():
    text = "| " + _long_sentence() + "; " + prose_lint.EM_DASH + " |"
    long_count, semi_count, dash_count = prose_lint.analyze(text)
    assert (long_count, semi_count, dash_count) == (0, 0, 0)


def test_semicolon_counted():
    text = "one two three four five; six seven"
    long_count, semi_count, _ = prose_lint.analyze(text)
    assert semi_count == 1
    assert long_count == 0


def test_em_dash_counted():
    _, _, dash_count = prose_lint.analyze(prose_lint.EM_DASH)
    assert dash_count == 1


def test_py_file_ignored(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "notes.md").write_text("plain prose here\n", encoding="utf-8")
    (docs / "script.py").write_text(
        " ".join(f"word{i}" for i in range(31)) + ";\n", encoding="utf-8"
    )
    monkeypatch.setattr(prose_lint, "SCOPE_DIRS", (str(docs),))
    monkeypatch.setattr(prose_lint, "ROOT_FILES", ())
    totals, _ = prose_lint.compute()
    assert totals["long_sentence"] == 0
    assert totals["semicolon"] == 0


def test_update_then_check_exits_zero(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("one two three four five; six\n", encoding="utf-8")
    monkeypatch.setattr(prose_lint, "SCOPE_DIRS", (str(docs),))
    monkeypatch.setattr(prose_lint, "ROOT_FILES", ())
    budget_file = tmp_path / "budget.json"
    monkeypatch.setattr(prose_lint, "BUDGET_PATH", budget_file)

    assert prose_lint.main(["--update"]) == 0
    assert prose_lint.main([]) == 0


@pytest.mark.parametrize("rule", ["long_sentence", "semicolon", "em_dash"])
def test_budget_lowered_by_one_fails(rule, tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    lines = [
        " ".join(f"word{i}" for i in range(31)) + ".",
        "one two three four five; six",
        prose_lint.EM_DASH,
    ]
    content = "\n".join(lines) + "\n"
    (docs / "a.md").write_text(content, encoding="utf-8")
    monkeypatch.setattr(prose_lint, "SCOPE_DIRS", (str(docs),))
    monkeypatch.setattr(prose_lint, "ROOT_FILES", ())
    budget_file = tmp_path / "budget.json"
    monkeypatch.setattr(prose_lint, "BUDGET_PATH", budget_file)

    assert prose_lint.main(["--update"]) == 0
    budget = json.loads(budget_file.read_text(encoding="utf-8"))
    assert budget[rule] >= 1
    budget[rule] -= 1
    budget_file.write_text(json.dumps(budget), encoding="utf-8")

    assert prose_lint.main([]) == 1
