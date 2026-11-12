#!/usr/bin/env python3
"""Report whether the shadow ledger can justify promoting the decision brain.

`tools/router.py --shadow` records what the decision model would have answered
next to the rule table's answer, so the two can be compared. No ledger row
carries the `run_id` of the work it routed, so a shadow label cannot be joined to
the outcome of the run it would have chosen -- this report states that limit and
never claims a winner. The tool only reads: nothing here writes a file.

The table has one row per `task_type`:

    TASK_TYPE  DECISIONS  WITH_SHADOW  SHADOW_ERRORS  AGREE  DISAGREE  AGREEMENT%

`with_shadow` counts the rows that carry a shadow record, `shadow_errors` the
ones whose brain call failed, and only a row with a non-null `shadow.label` and
no `shadow.error` counts toward agree or disagree. Each agreement rate is
followed by its Wilson 95% lower bound, because a 100% rate over four rows is not
evidence of anything.

The usage summary covers the same window:

    runs N | anonymous N | escalations N | escalation% N | p95_s N

A run escalates when any of its rows carries a truthy `error`, an `exit` that is
not 0, or a `phase == "start"` with no `finish` for that `run_id` (the wrapper
died mid-run). p95 is the nearest-rank 95th percentile of the last numeric
`elapsed_s` in each run.

Only a row with a usable `run_id` belongs to a run. Legacy rows without one are
counted as `anonymous` and never scored as a run, so a lone unkeyed `start` row
cannot pass for a lost run and inflate the escalation rate. With no keyed run at
all there is no rate to report: `escalation%` prints `-` and the JSON carries
null. The header line also names `unparsable` (no valid JSON) and `untimed`
(valid JSON, no usable `ts`) separately, because a row missing a timestamp was
read fine and only dropped from the window.

One verdict line follows, and it is one of three:

    NEED_MORE_DATA      fewer usable shadow rows than --min-samples, or no
                        keyed usage run to compute an escalation rate from
    HEADROOM_TOO_SMALL  the escalation rate is below 5%, so the rules already
                        answer often enough that a brain cannot pay for itself
    LINKAGE_REQUIRED    enough data, but no `run_id` in the ledger to join a
                        shadow label to that run's outcome

Usage:
    python tools/router_eval.py
    python tools/router_eval.py --since 7d --min-samples 50
    python tools/router_eval.py --json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
#: Written by `tools/router.py --dry-run` and `--shadow`.
LEDGER_PATH = ROOT / ".solocode" / "route-decisions.jsonl"
#: Written by `tools/opencode_delegate.py`.
USAGE_PATH = ROOT / ".solocode" / "opencode-usage.jsonl"

DEFAULT_SINCE = "14d"
DEFAULT_MIN_SAMPLES = 30
#: Below this escalation rate the rule table is already right often enough.
ESCALATION_FLOOR = 0.05
P95_QUANTILE = 0.95

VERDICT_NEED_MORE_DATA = "NEED_MORE_DATA"
VERDICT_HEADROOM_TOO_SMALL = "HEADROOM_TOO_SMALL"
VERDICT_LINKAGE_REQUIRED = "LINKAGE_REQUIRED"

UNKNOWN_TASK_TYPE = "(unknown)"

_RELATIVE_SPEC = re.compile(r"(\d+)([dh])")
_DATE_SPEC = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME_UNITS = {"d": 86400, "h": 3600}

DOCUMENTED_KEYS = (
    "window",
    "ledger",
    "usage",
    "task_types",
    "usable_shadow_rows",
    "missing_samples",
    "min_samples",
    "verdict",
)
TASK_KEYS = (
    "task_type",
    "decisions",
    "with_shadow",
    "shadow_errors",
    "agree",
    "disagree",
    "agreement_rate",
    "wilson_lower",
)


def _as_float(value: Any) -> float | None:
    """Return value as a float when it is a real number, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp to an aware datetime, or None when unparsable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_since(spec: str) -> datetime | None:
    """Parse `7d`, `48h` or `YYYY-MM-DD` into a UTC datetime, or None."""
    text = spec.strip()
    relative = _RELATIVE_SPEC.fullmatch(text)
    if relative:
        amount = int(relative.group(1))
        seconds = amount * _TIME_UNITS[relative.group(2)]
        return datetime.now(timezone.utc) - timedelta(seconds=seconds)
    if _DATE_SPEC.fullmatch(text):
        try:
            day = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        return day.replace(tzinfo=timezone.utc)
    return None


def wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    """The Wilson score interval's lower bound; 0.0 when there is no sample."""
    if total <= 0:
        return 0.0
    phat = successes / total
    denominator = 1 + (z * z) / total
    centre = phat + (z * z) / (2 * total)
    spread = z * math.sqrt(phat * (1 - phat) / total + (z * z) / (4 * total * total))
    return max((centre - spread) / denominator, 0.0)


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Read one JSONL file, returning the parsed rows and the unreadable count."""
    rows: list[dict[str, Any]] = []
    bad = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows, bad
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
        else:
            bad += 1
    return rows, bad


def _window_ledger(rows: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    """Rows whose numeric `at` epoch is inside the window; an untimed row is dropped."""
    cutoff = since.timestamp()
    kept: list[dict[str, Any]] = []
    for row in rows:
        at = row.get("at")
        if isinstance(at, bool) or not isinstance(at, int | float):
            continue
        if float(at) < cutoff:
            continue
        kept.append(row)
    return kept


def _window_usage(
    rows: list[dict[str, Any]], since: datetime
) -> tuple[list[dict[str, Any]], int]:
    """Rows whose `ts` is inside the window, plus the count with no usable ts."""
    kept: list[dict[str, Any]] = []
    untimed = 0
    for row in rows:
        stamp = _parse_ts(row.get("ts"))
        if stamp is None:
            untimed += 1
            continue
        if stamp < since:
            continue
        kept.append(row)
    return kept, untimed


def _group_runs(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Group rows into runs by `run_id`, counting every row that carries none."""
    runs: dict[str, list[dict[str, Any]]] = {}
    anonymous = 0
    for row in rows:
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            anonymous += 1
            continue
        runs.setdefault(run_id.strip(), []).append(row)
    return runs, anonymous


def _exit_failed(row: dict[str, Any]) -> bool:
    """True when the row carries an `exit` that is present and not 0."""
    if "exit" not in row:
        return False
    value = row.get("exit")
    if value is None:
        return False
    number = _as_float(value)
    return number != 0 if number is not None else value != 0


def _run_escalates(run_rows: list[dict[str, Any]]) -> bool:
    """True on a truthy error, a non-zero exit, or a start with no finish row."""
    if any(row.get("error") for row in run_rows):
        return True
    if any(_exit_failed(row) for row in run_rows):
        return True
    phases = {row.get("phase") for row in run_rows}
    return "start" in phases and "finish" not in phases


def _percentile(values: list[float], quantile: float) -> float | None:
    """Nearest-rank percentile, or None over an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(quantile * len(ordered))
    return ordered[max(rank - 1, 0)]


def summarize_usage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return runs, anonymous rows, escalations, the rate and p95 seconds."""
    runs, anonymous = _group_runs(rows)
    escalations = sum(1 for run_rows in runs.values() if _run_escalates(run_rows))
    elapsed: list[float] = []
    for run_rows in runs.values():
        last: float | None = None
        for row in run_rows:
            seconds = _as_float(row.get("elapsed_s"))
            if seconds is not None:
                last = seconds
        if last is not None:
            elapsed.append(last)
    total = len(runs)
    return {
        "runs": total,
        "anonymous": anonymous,
        "escalations": escalations,
        "escalation_rate": (escalations / total) if total else None,
        "p95_s": _percentile(elapsed, P95_QUANTILE),
    }


def shadow_stats(ledger_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-task-type decision, shadow and agreement counts, newest-heavy order."""
    buckets: dict[str, dict[str, Any]] = {}
    for row in ledger_rows:
        task_type = row.get("task_type")
        key = (
            task_type
            if isinstance(task_type, str) and task_type.strip()
            else (UNKNOWN_TASK_TYPE)
        )
        bucket = buckets.setdefault(
            key,
            {
                "task_type": key,
                "decisions": 0,
                "with_shadow": 0,
                "shadow_errors": 0,
                "agree": 0,
                "disagree": 0,
            },
        )
        bucket["decisions"] += 1
        shadow = row.get("shadow")
        if not isinstance(shadow, dict):
            continue
        bucket["with_shadow"] += 1
        if shadow.get("error"):
            bucket["shadow_errors"] += 1
            continue
        label = shadow.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        if label == key:
            bucket["agree"] += 1
        else:
            bucket["disagree"] += 1

    ordered = sorted(
        buckets.values(), key=lambda item: (-item["decisions"], item["task_type"])
    )
    for bucket in ordered:
        total = bucket["agree"] + bucket["disagree"]
        bucket["agreement_rate"] = (bucket["agree"] / total) if total else None
        bucket["wilson_lower"] = wilson_lower_bound(bucket["agree"], total)
    return ordered


def verdict_for(usable: int, min_samples: int, escalation_rate: float | None) -> str:
    """The one verdict the data supports; a winner claim is never among them."""
    if escalation_rate is None:
        return VERDICT_NEED_MORE_DATA
    if usable < min_samples:
        return VERDICT_NEED_MORE_DATA
    if escalation_rate < ESCALATION_FLOOR:
        return VERDICT_HEADROOM_TOO_SMALL
    return VERDICT_LINKAGE_REQUIRED


def collect(
    ledger_path: Path, usage_path: Path, since: datetime, min_samples: int
) -> dict[str, Any]:
    """Build the documented report dict from both files, inside the window."""
    ledger_all, ledger_bad = _read_jsonl(ledger_path)
    usage_all, usage_bad = _read_jsonl(usage_path)
    ledger_rows = _window_ledger(ledger_all, since)
    usage_rows, untimed = _window_usage(usage_all, since)

    task_types = shadow_stats(ledger_rows)
    usable = sum(item["agree"] + item["disagree"] for item in task_types)
    usage = summarize_usage(usage_rows)
    return {
        "window": {"cutoff": since.isoformat()},
        "ledger": {
            "path": str(ledger_path),
            "decisions": len(ledger_rows),
            "bad_lines": ledger_bad,
        },
        "usage": {
            "path": str(usage_path),
            "unparsable": usage_bad,
            "untimed": untimed,
            "runs": usage["runs"],
            "anonymous": usage["anonymous"],
            "escalations": usage["escalations"],
            "escalation_rate": usage["escalation_rate"],
            "p95_s": usage["p95_s"],
        },
        "task_types": task_types,
        "usable_shadow_rows": usable,
        "missing_samples": max(min_samples - usable, 0),
        "min_samples": min_samples,
        "verdict": verdict_for(usable, min_samples, usage["escalation_rate"]),
    }


def _format_rate(value: float | None) -> str:
    """Format a 0..1 rate as one decimal percentage, or '-' when undefined."""
    return "-" if value is None else f"{value * 100:.1f}"


def _format_seconds(value: float | None) -> str:
    """Format a seconds value, or '-' when undefined."""
    return "-" if value is None else f"{value:.1f}"


def _render_table(task_types: list[dict[str, Any]]) -> list[str]:
    """Render the left-aligned per-task-type table."""
    headers = [
        "TASK_TYPE",
        "DECISIONS",
        "WITH_SHADOW",
        "SHADOW_ERRORS",
        "AGREE",
        "DISAGREE",
        "AGREEMENT%",
    ]
    table: list[list[str]] = [headers]
    for item in task_types:
        table.append(
            [
                item["task_type"],
                str(item["decisions"]),
                str(item["with_shadow"]),
                str(item["shadow_errors"]),
                str(item["agree"]),
                str(item["disagree"]),
                _format_rate(item["agreement_rate"]),
            ]
        )
    widths = [max(len(row[index]) for row in table) for index in range(len(headers))]
    return [
        "  ".join(
            cell.ljust(width) for cell, width in zip(row, widths, strict=True)
        ).rstrip()
        for row in table
    ]


def _verdict_reason(report: dict[str, Any]) -> str:
    """One line of reasoning behind the verdict, in the data's own terms."""
    usage = report["usage"]
    if report["verdict"] == VERDICT_NEED_MORE_DATA:
        if usage["escalation_rate"] is None:
            return (
                "no usage row carries a usable run_id inside the window, so no "
                "escalation rate can be measured and no headroom claim is made"
            )
        return (
            f"{report['missing_samples']} more usable shadow rows needed "
            f"({report['usable_shadow_rows']} of {report['min_samples']})"
        )
    if report["verdict"] == VERDICT_HEADROOM_TOO_SMALL:
        return (
            f"escalation rate {_format_rate(usage['escalation_rate'])}% is below the "
            f"{ESCALATION_FLOOR:.0%} floor, so the rules already answer often enough "
            "that a brain cannot pay for itself"
        )
    return (
        "no ledger row carries a run_id, so a shadow label cannot be joined to the "
        "outcome of the run it would have chosen"
    )


def render_report(report: dict[str, Any]) -> str:
    """Render the table, the usage summary and exactly one verdict line."""
    usage = report["usage"]
    lines = [
        f"window: at or after {report['window']['cutoff']}"
        f" | ledger {report['ledger']['decisions']} decisions"
        f" ({report['ledger']['bad_lines']} unreadable)"
        f" | usage {usage['runs']} runs"
        f" ({usage['anonymous']} anonymous, {usage['unparsable']} unparsable,"
        f" {usage['untimed']} untimed)",
        "",
        *_render_table(report["task_types"]),
    ]
    bounded = [
        item for item in report["task_types"] if item["agreement_rate"] is not None
    ]
    if bounded:
        lines.extend(
            [
                "",
                "agreement% 95% Wilson lower bound (a small sample cannot look "
                "conclusive):",
            ]
        )
        for item in bounded:
            sample = item["agree"] + item["disagree"]
            lines.append(
                f"  {item['task_type']}: {item['wilson_lower'] * 100:.1f} (n={sample})"
            )
    lines.extend(
        [
            "",
            f"runs {usage['runs']} | anonymous {usage['anonymous']}"
            f" | escalations {usage['escalations']}"
            f" | escalation% {_format_rate(usage['escalation_rate'])}"
            f" | p95_s {_format_seconds(usage['p95_s'])}",
            "",
            f"{report['verdict']}: {_verdict_reason(report)}",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns 2 only for an unreadable `--since`, else 0."""
    parser = argparse.ArgumentParser(
        description="Report whether shadow ledger data can justify promoting the brain"
    )
    parser.add_argument(
        "--ledger",
        default=str(LEDGER_PATH),
        help="path to the route-decision ledger (JSONL)",
    )
    parser.add_argument(
        "--usage",
        default=str(USAGE_PATH),
        help="path to the opencode usage log (JSONL)",
    )
    parser.add_argument(
        "--since",
        default=DEFAULT_SINCE,
        help="window start: 7d, 48h or YYYY-MM-DD",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=DEFAULT_MIN_SAMPLES,
        help="usable shadow rows needed before any verdict but NEED_MORE_DATA",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit one JSON object instead of the table"
    )
    args = parser.parse_args(argv)

    since = parse_since(args.since)
    if since is None:
        print(f"[router_eval] invalid --since value: {args.since!r}", file=sys.stderr)
        return 2

    report = collect(Path(args.ledger), Path(args.usage), since, args.min_samples)
    if args.json:
        print(json.dumps({key: report[key] for key in DOCUMENTED_KEYS}, indent=2))
    else:
        print(render_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
