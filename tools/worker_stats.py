"""Read-only per-worker / per-model runtime performance report.

Reads the ``*-usage.jsonl`` files the wrapper scripts already write and groups
their rows by ``(worker, run_id)`` so each run can be classified:

* **FAIL** - any row in the run has a truthy ``error``, or an ``exit`` value
  that is not None and not 0.
* **OK** - the run has a ``phase == "finish"`` row and did not FAIL.
* **INCOMPLETE** - neither: a start that never finished (the crash case this
  report exists to surface).

The median latency is taken over OK runs only, using the last numeric ``elapsed_s`` in each run (the finish row carries the real duration; the start row records 0).

Rows without a ``run_id`` are legacy: they are counted per worker in the
``legacy`` column and excluded from run totals. A legacy row is attributed to
the worker and to the ``model`` on that row (``(unknown)`` when absent), so it
is counted exactly once even when the worker ran several models.

Token formula (cache and reasoning tokens are deliberately EXCLUDED from the
fallback, so the same work is not counted twice):

1. ``usage.total`` or ``usage.total_tokens`` when present;
2. else ``usage.input + usage.output``;
3. else ``prompt_tokens + completion_tokens``;
4. else 0.

This tool reports **runtime outcomes only**. It cannot produce a gate catch
rate: that number comes from ``tools/gate_probe.py``, a static probe, not from
here, so no reader should expect it in this report. The tool is read-only, uses
the standard library only, and exits 0 in every case so it can never block a
gate.

Usage:
    python tools/worker_stats.py [--json] [--worker NAME]
        [--since 7d|48h|2026-10-20] [--usage-dir DIR]

Example:
    python tools/worker_stats.py --since 7d --worker kilo
    # WORKER  MODEL  RUNS  OK  FAIL  INCOMPLETE  SUCCESS%  ...
    # TOTAL          <sums from the table above>  ...  <rate>  <median>  <cost>  <tokens>  <legacy>
    # legacy rows skipped: 5 | unreadable lines skipped: 0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

USAGE_SUFFIX = "-usage.jsonl"
UNKNOWN_MODEL = "(unknown)"

DOCUMENTED_KEYS = ("usage_dir", "workers", "totals", "bad_lines")
WORKER_KEYS = (
    "worker",
    "model",
    "runs",
    "ok",
    "fail",
    "incomplete",
    "success_rate",
    "median_elapsed_s",
    "cost_usd",
    "tokens",
    "legacy",
)

_TIME_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_RELATIVE_SPEC = re.compile(r"(\d+)([smhd])")


def _as_int(value: Any) -> int | None:
    """Return value as an int when it is an integral number, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _as_float(value: Any) -> float | None:
    """Return value as a float when it is a real number, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _row_tokens(row: dict[str, Any]) -> int:
    """Return the token count for one row, per the module docstring formula."""
    usage = row.get("usage")
    if isinstance(usage, dict):
        for key in ("total", "total_tokens"):
            value = _as_int(usage.get(key))
            if value is not None:
                return value
        input_tokens = _as_int(usage.get("input"))
        output_tokens = _as_int(usage.get("output"))
        if input_tokens is not None or output_tokens is not None:
            return (input_tokens or 0) + (output_tokens or 0)
    prompt_tokens = _as_int(row.get("prompt_tokens"))
    completion_tokens = _as_int(row.get("completion_tokens"))
    if prompt_tokens is not None or completion_tokens is not None:
        return (prompt_tokens or 0) + (completion_tokens or 0)
    return 0


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO timestamp to an aware datetime, or None when unparsable."""
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


def _parse_since(spec: str) -> datetime | None:
    """Parse ``Nd``/``Nh``/``Nm``/``Ns`` or an ISO date into a UTC datetime."""
    relative = _RELATIVE_SPEC.fullmatch(spec.strip())
    if relative:
        amount = int(relative.group(1))
        seconds = amount * _TIME_UNITS[relative.group(2)]
        return datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return _parse_ts(spec)


def _read_rows(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Read one JSONL file, returning the parsed rows and unreadable count."""
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


def _worker_name(path: Path) -> str:
    """Worker name is the filename minus the ``-usage.jsonl`` suffix."""
    name = path.name
    if name.endswith(USAGE_SUFFIX):
        return name[: -len(USAGE_SUFFIX)]
    return path.stem


def _model_of(row: dict[str, Any]) -> str:
    """Return the row's model, or ``(unknown)`` when it carries none."""
    candidate = row.get("model")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return UNKNOWN_MODEL


def _has_run_id(row: dict[str, Any]) -> bool:
    run_id = row.get("run_id")
    if run_id is None:
        return False
    return not (isinstance(run_id, str) and not run_id.strip())


def _classify(run_rows: list[dict[str, Any]]) -> str:
    """Classify one run as ok, fail or incomplete."""
    for row in run_rows:
        if row.get("error"):
            return "fail"
        exit_code = row.get("exit")
        if exit_code is not None and exit_code != 0:
            return "fail"
    if any(row.get("phase") == "finish" for row in run_rows):
        return "ok"
    return "incomplete"


def _run_model(run_rows: list[dict[str, Any]]) -> str:
    """Model of a run is its last non-empty model field."""
    model = ""
    for row in run_rows:
        candidate = row.get("model")
        if isinstance(candidate, str) and candidate.strip():
            model = candidate.strip()
    return model or UNKNOWN_MODEL


def _iter_rows(
    usage_dir: Path, since: datetime | None
) -> tuple[list[tuple[str, dict[str, Any]]], int, int]:
    """Read every usage file, returning (rows, bad_lines, dropped_ts)."""
    rows: list[tuple[str, dict[str, Any]]] = []
    bad_lines = 0
    dropped_ts = 0
    if not usage_dir.is_dir():
        return rows, bad_lines, dropped_ts
    for path in sorted(usage_dir.glob(f"*{USAGE_SUFFIX}")):
        worker = _worker_name(path)
        file_rows, file_bad = _read_rows(path)
        bad_lines += file_bad
        for row in file_rows:
            if since is not None:
                stamp = _parse_ts(row.get("ts"))
                if stamp is None:
                    dropped_ts += 1
                    continue
                if stamp < since:
                    continue
            rows.append((worker, row))
    return rows, bad_lines, dropped_ts


def _group_rows(
    rows: Iterable[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, list[list[dict[str, Any]]]], dict[str, int]]:
    """Group rows into (worker, model) buckets of runs plus legacy counts."""
    runs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    legacy: dict[str, int] = {}
    for worker, row in rows:
        if not _has_run_id(row):
            key = f"{worker}\x00{_model_of(row)}"
            legacy[key] = legacy.get(key, 0) + 1
            continue
        runs.setdefault((worker, str(row["run_id"])), []).append(row)

    buckets: dict[str, list[list[dict[str, Any]]]] = {}
    for (worker, _run_id), run_rows in runs.items():
        buckets.setdefault(f"{worker}\x00{_run_model(run_rows)}", []).append(run_rows)
    for key in legacy:
        buckets.setdefault(key, [])
    return buckets, legacy


def _bucket_stats(
    worker: str, model: str, runs: list[list[dict[str, Any]]], legacy: int
) -> dict[str, Any]:
    """Compute the metrics for one (worker, model) bucket."""
    ok = fail = incomplete = 0
    elapsed: list[float] = []
    cost_total = 0.0
    cost_seen = False
    tokens = 0
    for run_rows in runs:
        outcome = _classify(run_rows)
        if outcome == "ok":
            ok += 1
        elif outcome == "fail":
            fail += 1
        else:
            incomplete += 1
        for row in run_rows:
            tokens += _row_tokens(row)
            cost = _as_float(row.get("cost"))
            if cost is not None:
                cost_total += cost
                cost_seen = True
        # One sample per OK run: the last numeric elapsed_s wins (the finish
        # row is written last and carries the real duration, the start row 0).
        if outcome == "ok":
            run_elapsed: float | None = None
            for row in run_rows:
                seconds = _as_float(row.get("elapsed_s"))
                if seconds is not None:
                    run_elapsed = seconds
            if run_elapsed is not None:
                elapsed.append(run_elapsed)
    denominator = ok + fail
    return {
        "worker": worker,
        "model": model,
        "runs": ok + fail + incomplete,
        "ok": ok,
        "fail": fail,
        "incomplete": incomplete,
        "success_rate": (ok / denominator) if denominator else None,
        "median_elapsed_s": _median(elapsed),
        "cost_usd": cost_total if cost_seen else None,
        "tokens": tokens,
        "legacy": legacy,
    }


def _median(values: list[float]) -> float | None:
    """Median of a list, averaging the two middle values for an even count."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _totals(workers: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum a list of worker rows into the totals row."""
    totals: dict[str, Any] = {
        "runs": sum(item["runs"] for item in workers),
        "ok": sum(item["ok"] for item in workers),
        "fail": sum(item["fail"] for item in workers),
        "incomplete": sum(item["incomplete"] for item in workers),
        "tokens": sum(item["tokens"] for item in workers),
        "legacy": sum(item["legacy"] for item in workers),
    }
    costs = [item["cost_usd"] for item in workers if item["cost_usd"] is not None]
    totals["cost_usd"] = sum(costs) if costs else None
    denominator = totals["ok"] + totals["fail"]
    totals["success_rate"] = (totals["ok"] / denominator) if denominator else None
    return totals


def _aggregate(
    usage_dir: Path, since: datetime | None = None, worker: str | None = None
) -> tuple[dict[str, Any], int]:
    """Build the documented report plus the count of unparsable-ts rows."""
    rows, bad_lines, dropped_ts = _iter_rows(usage_dir, since)
    buckets, legacy = _group_rows(rows)

    workers: list[dict[str, Any]] = []
    for key, run_rows in buckets.items():
        name, model = key.split("\x00", 1)
        workers.append(_bucket_stats(name, model, run_rows, legacy.get(key, 0)))
    if worker is not None:
        workers = [item for item in workers if item["worker"] == worker]
    workers.sort(key=lambda item: (item["worker"], -item["runs"], item["model"]))

    report = {
        "usage_dir": str(usage_dir),
        "workers": workers,
        "totals": _totals(workers),
        "bad_lines": bad_lines,
    }
    return report, dropped_ts


def collect_stats(usage_dir: Path, since: datetime | None = None) -> dict[str, Any]:
    """Return the documented report dict for a usage directory."""
    return _aggregate(usage_dir, since)[0]


def build_report(
    usage_dir: Path, since: datetime | None = None, worker: str | None = None
) -> dict[str, Any]:
    """Return the documented report dict, optionally filtered to one worker."""
    return _aggregate(usage_dir, since, worker)[0]


def _format_tokens(value: int) -> str:
    """Format a token count with thousands separators or a k/M suffix."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:,}"


def _format_cost(value: float | None) -> str:
    """Format a USD cost, or '-'(dash) when no row carried one."""
    return "-" if value is None else f"${value:,.4f}"


def _format_rate(value: float | None) -> str:
    """Format a success rate as a percentage, or '-' when undefined."""
    return "-" if value is None else f"{value * 100:.1f}"


def _format_median(value: float | None) -> str:
    """Format a median seconds value, or '-' when undefined."""
    return "-" if value is None else f"{value:.1f}"


def _render_table(report: dict[str, Any]) -> list[str]:
    """Render the left-aligned table, sorted worker then runs descending."""
    headers = [
        "WORKER",
        "MODEL",
        "RUNS",
        "OK",
        "FAIL",
        "INCOMPLETE",
        "SUCCESS%",
        "MEDIAN_S",
        "COST",
        "TOKENS",
        "LEGACY",
    ]
    table: list[list[str]] = [headers]
    for item in report["workers"]:
        table.append(
            [
                item["worker"],
                item["model"],
                str(item["runs"]),
                str(item["ok"]),
                str(item["fail"]),
                str(item["incomplete"]),
                _format_rate(item["success_rate"]),
                _format_median(item["median_elapsed_s"]),
                _format_cost(item["cost_usd"]),
                _format_tokens(item["tokens"]),
                str(item["legacy"]),
            ]
        )
    totals = report["totals"]
    table.append(
        [
            "TOTAL",
            "",
            str(totals["runs"]),
            str(totals["ok"]),
            str(totals["fail"]),
            str(totals["incomplete"]),
            _format_rate(totals["success_rate"]),
            "-",
            _format_cost(totals["cost_usd"]),
            _format_tokens(totals["tokens"]),
            str(totals["legacy"]),
        ]
    )
    widths = [max(len(row[index]) for row in table) for index in range(len(headers))]
    return [
        "  ".join(
            cell.ljust(width) for cell, width in zip(row, widths, strict=True)
        ).rstrip()
        for row in table
    ]


def render_report(report: dict[str, Any], dropped_ts: int = 0) -> str:
    """Render the human table plus the skipped-row summary line."""
    lines = _render_table(report)
    summary = (
        f"legacy rows skipped: {report['totals']['legacy']} | "
        f"unreadable lines skipped: {report['bad_lines']}"
    )
    if dropped_ts:
        summary += f" | rows dropped for an unparsable ts: {dropped_ts}"
    lines.extend(["", summary])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point; always returns 0 unless argparse rejects an argument."""
    parser = argparse.ArgumentParser(
        description="Report per-worker runtime outcomes from usage logs."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit JSON instead of the table"
    )
    parser.add_argument("--worker", default=None, help="only report this worker")
    parser.add_argument(
        "--since",
        default=None,
        help="only rows newer than this point: Nd, Nh, Nm, Ns or an ISO date",
    )
    parser.add_argument(
        "--usage-dir", default=".solocode", help="directory holding *-usage.jsonl"
    )
    args = parser.parse_args(argv)

    since: datetime | None = None
    if args.since is not None:
        since = _parse_since(args.since)
        if since is None:
            parser.error(f"invalid --since value: {args.since!r}")

    report, dropped_ts = _aggregate(Path(args.usage_dir), since, args.worker)
    if args.json:
        payload = {key: report[key] for key in DOCUMENTED_KEYS}
        print(json.dumps(payload, indent=2))
    else:
        print(render_report(report, dropped_ts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
