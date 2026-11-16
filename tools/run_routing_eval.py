#!/usr/bin/env python3
"""Score the rule table against frozen expected routing labels.

`tools/router.py` decides a task type, a worker and a budget from a trigger
word, and nothing checked those decisions against a label a human wrote down,
so a rule could regress with every test still green. This runner holds the
frozen labels and prints where the rule table disagrees with them:

    dataset: <path> | N rows | router evidence: rules:v2 model-roles:...

A row matches only when all three compared fields agree: `task_type`, `worker`
and `budget.max_seconds`. The other keys `route()` returns are read for the
header and never compared -- `brain` names a decision source, `reason` is prose,
and `max_prompt_chars` moves with the budget table rather than with the task.

A disagreement is the product, not a failure. Rows 4-7 and 26 are labelled the
way a human reads the request, and the rule table is known to answer differently
today (destructive is the first rule and there is no negation handling; question
is matched before complex), so the run exits 0 and the mismatch lines are the
report. `--strict` exits 1 when any row disagrees, for a caller that wants to
gate on it.

Per expected type the table gives:

    TYPE  ROWS  MATCHED  PRECISION  RECALL  WILSON%

`matched` counts the rows of that type where all three fields agree, precision
is matched over the rows the router *predicted* as that type, recall is matched
over the rows labelled that type, and WILSON% is the Wilson 95% lower bound of
that per-type accuracy -- six right out of six is not evidence of anything.

The runner only reads: it writes no file, and no flag rewrites an expected
label.

Usage:
    python tools/run_routing_eval.py
    python tools/run_routing_eval.py --strict
    python tools/run_routing_eval.py --dataset tools/eval/routing_eval_v1.json --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from router import DECLARED_WORKERS, SHADOW_QUESTIONS, route  # noqa: E402
from router_eval import wilson_lower_bound  # noqa: E402

DEFAULT_DATASET = ROOT / "tools" / "eval" / "routing_eval_v1.json"

#: The six task types, in the order the frozen question set declares them.
TASK_TYPES = tuple(SHADOW_QUESTIONS["task_type"]["criteria"])

#: Keys a dataset row must carry before it can be scored.
REQUIRED_KEYS = (
    "id",
    "task",
    "expected_task_type",
    "expected_worker",
    "expected_max_seconds",
    "criterion",
    "rationale",
    "tags",
)

#: The three compared fields, in report order.
FIELDS = ("task_type", "worker", "budget.max_seconds")

SEVERITY_WRONG_STOP = "wrong_stop"
SEVERITY_WRONG_WORKER = "wrong_worker"
SEVERITY_WRONG_BUDGET = "wrong_budget"

#: Types where the router is asked to answer, not to act on the repo.
_STOP_TYPES = frozenset({"question", "review"})
_DESTRUCTIVE = "destructive"


class DatasetError(Exception):
    """A dataset that cannot be scored; the message is the one line to print."""


def _validate(raw: Any, path: Path) -> list[dict[str, Any]]:
    """Return the rows, or raise on the first violation with the row's own id."""
    if not isinstance(raw, list) or not raw:
        raise DatasetError(f"dataset: {path}: expected a non-empty JSON array")
    seen: set[str] = set()
    for index, row in enumerate(raw, 1):
        if not isinstance(row, dict):
            raise DatasetError(f"dataset: row {index}: not a JSON object")
        row_id = row.get("id")
        label = row_id if isinstance(row_id, str) and row_id.strip() else f"row {index}"
        missing = [key for key in REQUIRED_KEYS if key not in row]
        if missing:
            raise DatasetError(f"dataset: {label}: missing {', '.join(missing)}")
        if not isinstance(row_id, str) or not row_id.strip():
            raise DatasetError(f"dataset: {label}: id must be a non-empty string")
        if row_id in seen:
            raise DatasetError(f"dataset: {row_id}: duplicate id")
        seen.add(row_id)
        if row["expected_task_type"] not in TASK_TYPES:
            raise DatasetError(
                f"dataset: {row_id}: expected_task_type "
                f"{row['expected_task_type']!r} is not one of {', '.join(TASK_TYPES)}"
            )
        if row["expected_worker"] not in DECLARED_WORKERS:
            raise DatasetError(
                f"dataset: {row_id}: expected_worker {row['expected_worker']!r} "
                "is not a declared worker"
            )
        seconds = row["expected_max_seconds"]
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            raise DatasetError(
                f"dataset: {row_id}: expected_max_seconds must be an int, "
                f"got {type(seconds).__name__}"
            )
    return raw


def load_dataset(path: Path) -> list[dict[str, Any]]:
    """Read and validate the dataset; a refusal is one line, never a partial set."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetError(
            f"dataset: {path}: cannot read ({exc.strerror or exc})"
        ) from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetError(
            f"dataset: {path}: invalid JSON ({exc.msg}, line {exc.lineno})"
        ) from exc
    return _validate(raw, path)


def _expected(row: dict[str, Any]) -> dict[str, Any]:
    """The three fields a human labelled, keyed the way the report names them."""
    return {
        "task_type": row["expected_task_type"],
        "worker": row["expected_worker"],
        "budget.max_seconds": row["expected_max_seconds"],
    }


def _got(decision: dict[str, Any]) -> dict[str, Any]:
    """The same three fields as the router answered them."""
    return {
        "task_type": decision["task_type"],
        "worker": decision["worker"],
        "budget.max_seconds": decision["budget"]["max_seconds"],
    }


def _severity(expected: dict[str, Any], got: dict[str, Any]) -> str:
    """Which kind of disagreement this is: a stopped task, a worker, or a budget."""
    if expected["task_type"] in _STOP_TYPES and got["task_type"] == _DESTRUCTIVE:
        return SEVERITY_WRONG_STOP
    if expected["worker"] != got["worker"]:
        return SEVERITY_WRONG_WORKER
    return SEVERITY_WRONG_BUDGET


def evaluate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score every row, keeping the expected label and the router's answer apart."""
    results: list[dict[str, Any]] = []
    for row in rows:
        decision = route(row["task"])
        expected = _expected(row)
        got = _got(decision)
        differing = [field for field in FIELDS if expected[field] != got[field]]
        results.append(
            {
                "id": row["id"],
                "expected_task_type": row["expected_task_type"],
                "predicted_task_type": decision["task_type"],
                "expected": expected,
                "got": got,
                "differing": differing,
                "severity": _severity(expected, got) if differing else None,
                "rationale": row["rationale"],
                "evidence": decision["evidence"],
            }
        )
    return results


def build_table(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per expected type: rows, matched, precision, recall and the Wilson bound."""
    table: list[dict[str, Any]] = []
    for task_type in TASK_TYPES:
        rows = [item for item in results if item["expected_task_type"] == task_type]
        matched = sum(1 for item in rows if not item["differing"])
        predicted = sum(
            1 for item in results if item["predicted_task_type"] == task_type
        )
        table.append(
            {
                "task_type": task_type,
                "rows": len(rows),
                "matched": matched,
                "predicted": predicted,
                "precision": (matched / predicted) if predicted else None,
                "recall": (matched / len(rows)) if rows else None,
                "wilson_lower": wilson_lower_bound(matched, len(rows)),
            }
        )
    return table


def mismatches(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per differing field, in dataset order."""
    return [
        {
            "id": item["id"],
            "field": field,
            "expected": item["expected"][field],
            "got": item["got"][field],
            "severity": item["severity"],
            "rationale": item["rationale"],
        }
        for item in results
        for field in item["differing"]
    ]


def collect(path: Path) -> dict[str, Any]:
    """Build the whole report: header facts, table, mismatch list and summary."""
    results = evaluate(load_dataset(path))
    differing = [item for item in results if item["differing"]]
    matched = len(results) - len(differing)
    return {
        "dataset": str(path),
        "rows": len(results),
        "evidence": results[0]["evidence"],
        "table": build_table(results),
        "mismatches": mismatches(results),
        "summary": {
            "rows": len(results),
            "matched": matched,
            "mismatched": len(differing),
            "accuracy": matched / len(results),
            "wilson_lower": wilson_lower_bound(matched, len(results)),
        },
    }


def _percent(value: float | None) -> str:
    """Format a 0..1 rate as one decimal percentage, or '-' when undefined."""
    return "-" if value is None else f"{value * 100:.1f}%"


def _rate(matched: int, total: int) -> str:
    """Format a count over its denominator with the percentage beside it."""
    return f"{matched}/{total} ({_percent(matched / total if total else None)})"


def _render_table(table: list[dict[str, Any]]) -> list[str]:
    """Render the left-aligned per-type table."""
    headers = ["TYPE", "ROWS", "MATCHED", "PRECISION", "RECALL", "WILSON%"]
    cells: list[list[str]] = [headers]
    for item in table:
        cells.append(
            [
                item["task_type"],
                str(item["rows"]),
                str(item["matched"]),
                _rate(item["matched"], item["predicted"]),
                _rate(item["matched"], item["rows"]),
                _percent(item["wilson_lower"]),
            ]
        )
    widths = [max(len(row[index]) for row in cells) for index in range(len(headers))]
    return [
        "  ".join(
            cell.ljust(width) for cell, width in zip(row, widths, strict=True)
        ).rstrip()
        for row in cells
    ]


def render_report(report: dict[str, Any]) -> str:
    """Render the header, the per-type table, every mismatch and the summary."""
    lines = [
        f"dataset: {report['dataset']} | {report['rows']} rows"
        f" | router evidence: {report['evidence']}",
        "",
        *_render_table(report["table"]),
        "",
    ]
    if report["mismatches"]:
        lines.extend(
            f"{item['id']} | {item['field']} | expected {item['expected']}"
            f" | got {item['got']} | severity {item['severity']}"
            f" | {item['rationale']}"
            for item in report["mismatches"]
        )
    else:
        lines.append("no mismatches")
    summary = report["summary"]
    lines.extend(
        [
            "",
            f"{summary['rows']} rows | {summary['matched']} matched"
            f" | {summary['mismatched']} mismatched"
            f" | accuracy {_percent(summary['accuracy'])}"
            f" (wilson lower {_percent(summary['wilson_lower'])})",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point; 2 for an unusable dataset, else 0, or 1 under --strict."""
    parser = argparse.ArgumentParser(
        description="Score the rule table against frozen routing labels"
    )
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET),
        help="path to the labelled dataset (JSON array)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when any row disagrees with the router",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit one JSON object instead of the report"
    )
    args = parser.parse_args(argv)

    try:
        report = collect(Path(args.dataset))
    except DatasetError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_report(report))
    if args.strict and report["summary"]["mismatched"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
