#!/usr/bin/env python3
"""
Failure Ledger — Aggregate what broke, where, and when.

Reads the harness's existing operational data without modifying it:
- .solocode/shared-state.db           session_log: engine, model, summary, verification
- .solocode/sessions.db               sessions: status, start_time, end_time
- .kilo/logs/governance-events.jsonl  one JSON object per line
- .solocode/*-usage.jsonl             per-worker runs: error, ok, exit, cost, usage

Every source is optional. A missing file, table, or column is skipped and
reported under Notes; malformed JSONL lines are counted, not fatal. SQLite
files are opened read-only (mode=ro): no database or JSONL content is written,
though SQLite may refresh its WAL shared-memory sidecar (-shm) while reading.

Usage:
    python tools/failure_ledger.py                 # Human-readable report
    python tools/failure_ledger.py --json          # Same data as JSON
    python tools/failure_ledger.py --sweep PATH    # Per-project .harness.lock sweep

Example:
    python tools/failure_ledger.py
    # Sessions: 1000 total (session_log), 1 row in sessions.db
    # Failures: 348 total
    #   by worker: kilo 326, opencode 13, codex-delegate 5, model-api 3
    # Cost: $0.005220 across 94 metered row(s)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.harness_fingerprint import (  # noqa: E402
    lock_shared_paths,
    lock_value,
    optional_fingerprint,
)

RECENT_FAILURE_LIMIT = 10

SESSION_LOG_COLUMNS = (
    "timestamp",
    "engine",
    "model",
    "session_id",
    "summary",
    "verification",
)
SESSIONS_COLUMNS = ("id", "start_time", "end_time", "status", "branch")

# Every table name this tool reads; anything else is refused before it
# can reach a query string.
KNOWN_TABLES = frozenset({"session_log", "sessions"})

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
_SKIP_PREFIXES = (".pytest_temp",)


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO timestamp to an aware datetime, or None when unparsable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds / 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def _median(values: list[float]) -> float | None:
    """Median of a list, averaging the two middle values for an even count."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _one_line(value: Any, limit: int = 80) -> str:
    """Collapse whitespace and truncate a value for a one-line reason."""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _format_counts(counts: dict[str, int]) -> str:
    """Render a count mapping as 'key value, key value'."""
    if not counts:
        return "none"
    return ", ".join(f"{key} {value}" for key, value in counts.items())


def _short_stamp(value: Any) -> str:
    """Render a timestamp as YYYY-MM-DD HH:MM, or 'unknown'."""
    parsed = _parse_iso(value)
    return parsed.strftime("%Y-%m-%d %H:%M") if parsed else "unknown"


def _connect_read_only(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite file without any write capability."""
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _read_table(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    *,
    label: str,
    notes: list[str],
) -> list[dict[str, Any]]:
    """Read the requested columns from one table, recording why it was skipped."""
    if table not in KNOWN_TABLES:
        notes.append(f"{label}: table {table} is not read by this tool")
        return []
    try:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error as exc:
        notes.append(f"{label}: table {table} unreadable ({exc})")
        return []
    if not info:
        notes.append(f"{label}: table {table} not found")
        return []
    present = {row[1] for row in info}
    missing = [column for column in columns if column not in present]
    if missing:
        notes.append(f"{label}: table {table} missing column(s) {', '.join(missing)}")
        return []
    try:
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM {table}"  # noqa: S608 — table validated against KNOWN_TABLES
        ).fetchall()
    except sqlite3.Error as exc:
        notes.append(f"{label}: table {table} unreadable ({exc})")
        return []
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _read_db(
    path: Path,
    table: str,
    columns: tuple[str, ...],
    *,
    label: str,
    notes: list[str],
) -> list[dict[str, Any]]:
    """Open a SQLite source read-only and read one table."""
    if not path.is_file():
        notes.append(f"{label}: source not found")
        return []
    try:
        conn = _connect_read_only(path)
    except sqlite3.Error as exc:
        notes.append(f"{label}: cannot open read-only ({exc})")
        return []
    try:
        return _read_table(conn, table, columns, label=label, notes=notes)
    finally:
        conn.close()


def _read_jsonl(path: Path, *, label: str, notes: list[str]) -> list[dict[str, Any]]:
    """Read a JSONL file, skipping (and counting) malformed lines."""
    if not path.is_file():
        notes.append(f"{label}: source not found")
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        notes.append(f"{label}: unreadable ({exc})")
        return []

    rows: list[dict[str, Any]] = []
    skipped = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            skipped += 1
    if skipped:
        notes.append(f"{label}: skipped {skipped} malformed line(s)")
    return rows


def _worker_name(path: Path) -> str:
    """Derive a worker name from its usage file stem (opencode-usage -> opencode)."""
    suffix = "-usage.jsonl"
    return path.name[: -len(suffix)] if path.name.endswith(suffix) else path.stem


def _usage_failure_reason(row: dict[str, Any]) -> str | None:
    """Return a one-line reason when a usage row reports a failed run."""
    error = row.get("error")
    if error not in (None, "", False):
        return f"error: {_one_line(error)}"
    if row.get("ok") is False:
        return "ok=false"
    exit_code = row.get("exit")
    if exit_code is not None and str(exit_code).strip() not in ("", "0"):
        return f"exit={exit_code}"
    return None


def _is_start_row(row: dict[str, Any]) -> bool:
    """True for a phase:start row, which records a launch, not an outcome."""
    return row.get("phase") == "start"


def _start_sort_key(row: dict[str, Any]) -> tuple[bool, datetime]:
    """Order start rows oldest first; unparsable timestamps sort first."""
    parsed = _parse_iso(row.get("ts"))
    return (parsed is not None, parsed or datetime.min.replace(tzinfo=timezone.utc))


def _unpaired_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize unpaired start/finish rows and legacy rows for one usage file.

    Pairing is by count: for one run_id, unpaired starts are max(0, starts -
    finishes) and unpaired finishes are max(0, finishes - starts). A finish proves
    a run completed, so the most recent excess starts are the ones left without a
    finish. Rows that cannot participate in pairing -- an unknown phase, or a
    run_id that is absent, empty, or not a string -- count as legacy, never
    unpaired.
    """
    starts: dict[str, list[dict[str, Any]]] = {}
    finishes: dict[str, list[dict[str, Any]]] = {}
    legacy = 0
    for row in rows:
        phase = row.get("phase")
        run_id = row.get("run_id")
        if (
            phase not in ("start", "finish")
            or not isinstance(run_id, str)
            or not run_id
        ):
            legacy += 1
            continue
        if phase == "start":
            starts.setdefault(run_id, []).append(row)
        else:
            finishes.setdefault(run_id, []).append(row)

    unpaired_start_rows: list[dict[str, Any]] = []
    for run_id, group in starts.items():
        excess = len(group) - len(finishes.get(run_id, []))
        if excess > 0:
            # Pair finishes with the earliest starts (FIFO); the most recent
            # excess starts are the ones left without a finish.
            ordered = sorted(group, key=_start_sort_key)
            unpaired_start_rows.extend(ordered[-excess:])

    unpaired_finishes = sum(
        max(0, len(group) - len(starts.get(run_id, [])))
        for run_id, group in finishes.items()
    )

    most_recent: dict[str, Any] | None = None
    most_recent_stamp: datetime | None = None
    for row in unpaired_start_rows:
        parsed = _parse_iso(row.get("ts"))
        if parsed is None:
            continue
        if most_recent_stamp is None or parsed > most_recent_stamp:
            most_recent_stamp = parsed
            most_recent = row

    return {
        "unpaired_starts": len(unpaired_start_rows),
        "unpaired_finishes": unpaired_finishes,
        "most_recent_start": (
            {
                "timestamp": most_recent.get("ts"),
                "model": most_recent.get("model"),
                "run_id": most_recent.get("run_id"),
            }
            if most_recent is not None
            else None
        ),
        "legacy": legacy,
    }


def _verification_error(value: Any) -> str | None:
    """Return the verification object's error, when it carries one."""
    if not value:
        return None
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if error not in (None, "", False):
            return _one_line(error)
    return None


def _failure_sort_key(entry: dict[str, Any]) -> tuple[bool, datetime]:
    """Sort failures newest first; unparsable timestamps land last."""
    parsed = _parse_iso(entry.get("timestamp"))
    return (parsed is not None, parsed or datetime.min.replace(tzinfo=timezone.utc))


def _read_sessions(
    root: Path, notes: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read session_log and sessions rows from their read-only databases."""
    log_rows = _read_db(
        root / ".solocode" / "shared-state.db",
        "session_log",
        SESSION_LOG_COLUMNS,
        label=".solocode/shared-state.db (session_log)",
        notes=notes,
    )
    session_rows = _read_db(
        root / ".solocode" / "sessions.db",
        "sessions",
        SESSIONS_COLUMNS,
        label=".solocode/sessions.db (sessions)",
        notes=notes,
    )
    return log_rows, session_rows


def _usage_files(root: Path) -> list[Path]:
    """Every .solocode/*-usage.jsonl file, sorted by name."""
    solocode = root / ".solocode"
    return sorted(solocode.glob("*-usage.jsonl")) if solocode.is_dir() else []


def _read_usage(root: Path, notes: list[str]) -> list[tuple[str, dict[str, Any]]]:
    """Read every .solocode/*-usage.jsonl file, tagged with its worker name."""
    files = _usage_files(root)
    if not files:
        notes.append(".solocode/*-usage.jsonl: no usage files found")
        return []

    rows: list[tuple[str, dict[str, Any]]] = []
    for path in files:
        label = f".solocode/{path.name}"
        for row in _read_jsonl(path, label=label, notes=notes):
            rows.append((_worker_name(path), row))
    return rows


def collect(root: Path) -> dict[str, Any]:
    """Aggregate sessions, failures, and cost for one repository root."""
    root = Path(root)
    notes: list[str] = []

    log_rows, session_rows = _read_sessions(root, notes)
    governance_rows = _read_jsonl(
        root / ".kilo" / "logs" / "governance-events.jsonl",
        label=".kilo/logs/governance-events.jsonl",
        notes=notes,
    )
    usage_rows = _read_usage(root, notes)

    by_engine: Counter[str] = Counter()
    for row in log_rows:
        by_engine[str(row.get("engine") or "unknown")] += 1

    by_status: Counter[str] = Counter()
    durations: list[float] = []
    for row in session_rows:
        by_status[str(row.get("status") or "unknown")] += 1
        start = _parse_iso(row.get("start_time"))
        end = _parse_iso(row.get("end_time"))
        if start and end:
            durations.append((end - start).total_seconds())

    failures: list[dict[str, Any]] = []
    for row in session_rows:
        status = str(row.get("status") or "").strip().lower()
        if status in ("", "completed", "active"):
            continue
        reason = f"status={status}"
        if row.get("branch"):
            reason += f" branch={row['branch']}"
        failures.append(
            {
                "timestamp": row.get("end_time") or row.get("start_time"),
                "worker": "sessions",
                "model": None,
                "source": ".solocode/sessions.db",
                "reason": reason,
            }
        )

    for row in log_rows:
        detail = _verification_error(row.get("verification"))
        if detail is None:
            continue
        failures.append(
            {
                "timestamp": row.get("timestamp"),
                "worker": str(row.get("engine") or "unknown"),
                "model": row.get("model"),
                "source": ".solocode/shared-state.db",
                "reason": f"verification error: {detail}",
            }
        )

    for row in governance_rows:
        payload = row.get("payload")
        severity = payload.get("severity") if isinstance(payload, dict) else None
        reason = f"governance {row.get('eventType') or 'event'}"
        if severity:
            reason += f" (severity={severity})"
        failures.append(
            {
                "timestamp": row.get("timestamp"),
                "worker": "governance",
                "model": row.get("model"),
                "source": ".kilo/logs/governance-events.jsonl",
                "reason": reason,
            }
        )

    # Start rows record a launch, not an outcome: only finish rows (and legacy
    # rows, which predate pairing) feed the failure, cost, worker and unmetered
    # totals below. Otherwise every invocation's start+finish pair doubles them.
    outcome_rows = [
        (worker, row) for worker, row in usage_rows if not _is_start_row(row)
    ]

    cost_by_model: dict[str, float] = {}
    metered_rows = 0
    unmetered_rows = 0
    for worker, row in outcome_rows:
        reason = _usage_failure_reason(row)
        if reason is not None:
            failures.append(
                {
                    "timestamp": row.get("ts"),
                    "worker": worker,
                    "model": row.get("model"),
                    "source": f".solocode/{worker}-usage.jsonl",
                    "reason": reason,
                }
            )
        cost = row.get("cost")
        if isinstance(cost, int | float) and not isinstance(cost, bool):
            model = str(row.get("model") or "unknown")
            cost_by_model[model] = cost_by_model.get(model, 0.0) + float(cost)
            metered_rows += 1
        if row.get("usage") is None:
            unmetered_rows += 1

    # The sections above name only workers that failed or reported cost, so a clean
    # worker stays invisible. One entry per usage file keeps coverage visible.
    workers: dict[str, dict[str, Any]] = {
        _worker_name(path): {"total": 0, "errors": 0, "last": None}
        for path in _usage_files(root)
    }
    for worker, row in outcome_rows:
        entry = workers.setdefault(worker, {"total": 0, "errors": 0, "last": None})
        entry["total"] += 1
        if _usage_failure_reason(row) is not None:
            entry["errors"] += 1
        stamp = _parse_iso(row.get("ts"))
        if stamp is not None and (entry["last"] is None or stamp > entry["last"]):
            entry["last"] = stamp

    # A start without a finish means the wrapper died mid-run or its write was
    # lost; a finish without a start means the start row itself was lost. Rows
    # with no phase key predate pairing and count as legacy, never unpaired.
    usage_by_worker: dict[str, list[dict[str, Any]]] = {}
    for worker, row in usage_rows:
        usage_by_worker.setdefault(worker, []).append(row)
    unpaired: dict[str, dict[str, Any]] = {}
    for path in _usage_files(root):
        name = _worker_name(path)
        unpaired[name] = _unpaired_summary(usage_by_worker.get(name, []))

    recent = sorted(failures, key=_failure_sort_key, reverse=True)[
        :RECENT_FAILURE_LIMIT
    ]

    return {
        "root": str(root),
        "sessions": {
            "total": len(log_rows),
            "session_records": len(session_rows),
            "by_engine": dict(by_engine.most_common()),
            "by_status": dict(by_status.most_common()),
            "duration": {
                "count": len(durations),
                "median_s": _median(durations),
                "max_s": max(durations) if durations else None,
            },
        },
        "failures": {
            "total": len(failures),
            "by_worker": dict(
                Counter(failure["worker"] for failure in failures).most_common()
            ),
            "by_model": dict(
                Counter(
                    str(failure["model"] or "unknown") for failure in failures
                ).most_common()
            ),
            "recent": recent,
        },
        "cost": {
            "by_model": {
                model: round(amount, 6)
                for model, amount in sorted(
                    cost_by_model.items(), key=lambda item: item[1], reverse=True
                )
            },
            "total": round(sum(cost_by_model.values()), 6),
            "metered_rows": metered_rows,
            "unmetered_rows": unmetered_rows,
        },
        "workers": {
            name: {
                "total": entry["total"],
                "errors": entry["errors"],
                "last": entry["last"].isoformat() if entry["last"] else None,
            }
            for name, entry in sorted(workers.items())
        },
        "unpaired": dict(sorted(unpaired.items())),
        "notes": notes,
    }


def print_report(report: dict[str, Any]) -> None:
    """Print the human-readable report."""
    sessions = report["sessions"]
    failures = report["failures"]
    cost = report["cost"]
    workers = report["workers"]
    unpaired = report["unpaired"]

    print(f"Failure Ledger — {report['root']}")
    print()
    print("Sessions")
    print(f"  total (session_log): {sessions['total']}")
    print(f"  rows in sessions.db: {sessions['session_records']}")
    print(f"  by engine: {_format_counts(sessions['by_engine'])}")
    print(f"  by status: {_format_counts(sessions['by_status'])}")

    duration = sessions["duration"]
    if duration["count"]:
        print(
            f"  duration: median {_format_duration(duration['median_s'])}, "
            f"max {_format_duration(duration['max_s'])} "
            f"(n={duration['count']})"
        )
    else:
        print("  duration: no session has both start_time and end_time")

    print()
    print(f"Failures: {failures['total']} total")
    print(f"  by worker: {_format_counts(failures['by_worker'])}")
    print(f"  by model: {_format_counts(failures['by_model'])}")
    if failures["recent"]:
        print("  recent:")
        for entry in failures["recent"]:
            model = entry["model"] or "-"
            print(
                f"    {_short_stamp(entry['timestamp'])}  "
                f"{entry['worker']:>18}  {str(model):<32}  {entry['reason']}"
            )
    else:
        print("  none recorded")

    print()
    print("Cost")
    if cost["by_model"]:
        for model, amount in cost["by_model"].items():
            print(f"  {model}: ${amount:.6f}")
    else:
        print("  no cost field present in any usage row")
    print(f"  total: ${cost['total']:.6f} across {cost['metered_rows']} metered row(s)")
    print(
        f"  rows with usage=null: {cost['unmetered_rows']} (cost coverage is partial)"
    )

    print()
    print(f"Workers: {len(workers)} usage file(s)")
    if workers:
        for name, entry in workers.items():
            print(
                f"  {name:<18}  rows {entry['total']:>5}  "
                f"errors {entry['errors']:>4}  last {_short_stamp(entry['last'])}"
            )
    else:
        print("  no usage file found")

    print()
    print(f"Unpaired: {len(unpaired)} usage file(s)")
    if unpaired:
        for name, entry in unpaired.items():
            recent = entry["most_recent_start"]
            if recent:
                suffix = (
                    f"  last start {_short_stamp(recent['timestamp'])} "
                    f"{str(recent['model'] or '-')}  run_id {recent['run_id']}"
                )
            else:
                suffix = ""
            print(
                f"  {name:<18}  starts {entry['unpaired_starts']:>4}  "
                f"finishes {entry['unpaired_finishes']:>4}  "
                f"legacy {entry['legacy']:>4}{suffix}"
            )
    else:
        print("  no usage file found")

    print()
    print("Notes")
    if report["notes"]:
        for note in report["notes"]:
            print(f"  - {note}")
    else:
        print("  - none")


def _lock_version(lock_path: Path) -> str | None:
    """Read the [harness] version value from a .harness.lock file."""
    try:
        text = lock_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    in_harness = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_harness = line[1:-1].strip() == "harness"
            continue
        if not in_harness or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "version":
            return value.strip().strip('"').strip("'")
    return None


def _find_locks(root: Path) -> list[Path]:
    """Walk a tree for .harness.lock files, pruning build and VCS directories."""
    found: list[Path] = []
    stack = [Path(root)]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name not in _SKIP_DIRS and not entry.name.startswith(
                        _SKIP_PREFIXES
                    ):
                        stack.append(entry)
                elif entry.name == ".harness.lock":
                    found.append(entry)
            except OSError:
                continue
    return sorted(found)


def _is_worktree_checkout(lock_path: Path) -> bool:
    """True when a lock sits inside a .kilo/worktrees checkout.

    A worktree carries its own copy of .harness.lock but is the same install, so
    counting it inflates the sweep (measured 2026-10-20: 18 found, 5 duplicates).
    """
    parts = lock_path.parts
    return any(
        parts[index] == ".kilo" and parts[index + 1] == "worktrees"
        for index in range(len(parts) - 1)
    )


def sweep(
    path: Path, reference_version: str | None
) -> tuple[list[dict[str, Any]], int]:
    """Report the .harness.lock version of every project under a path.

    Returns the entries plus the number of worktree duplicates skipped.
    """
    projects = []
    skipped = 0
    for lock in _find_locks(Path(path)):
        if _is_worktree_checkout(lock):
            skipped += 1
            continue
        version = _lock_version(lock)
        try:
            lock_text = lock.read_text(encoding="utf-8", errors="replace")
        except OSError:
            lock_text = ""
        recorded = lock_value(lock_text, "harness", "content_fingerprint")
        declared = lock_shared_paths(lock_text)
        fingerprint_matches: bool | None = None
        if recorded and declared is not None:
            fingerprint_matches = (
                optional_fingerprint(declared, lock.parent) == recorded
            )
        projects.append(
            {
                "project": str(lock.parent),
                "version": version,
                "reference_version": reference_version,
                "matches": (
                    version == reference_version
                    if version and reference_version
                    else None
                ),
                "content_fingerprint": recorded,
                "fingerprint_matches": fingerprint_matches,
            }
        )
    return projects, skipped


def print_sweep(
    projects: list[dict[str, Any]], root: Path, skipped_duplicates: int = 0
) -> None:
    """Print the sweep table."""
    reference = projects[0]["reference_version"] if projects else None
    print(f"Harness sweep — {root} (reference version {reference or 'unknown'})")
    print(f"  skipped {skipped_duplicates} duplicate worktree install(s)")
    if not projects:
        print("  no .harness.lock found")
        return
    for entry in projects:
        version = entry["version"] or "unknown"
        if entry["matches"] is True:
            verdict = "match"
        elif entry["matches"] is False:
            verdict = "DIFFERS"
        else:
            verdict = "unknown"
        if entry["fingerprint_matches"] is True:
            fingerprint = "fp match"
        elif entry["fingerprint_matches"] is False:
            fingerprint = "fp DIFFERS"
        else:
            fingerprint = "fp unknown"
        print(f"  {version:<10} {verdict:<8} {fingerprint:<10} {entry['project']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate harness failures, session health, and cost"
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=str(REPO_ROOT),
        help="Repository root to analyse (default: this repo)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--sweep",
        metavar="PATH",
        help="Walk PATH for .harness.lock files and report per project",
    )

    args = parser.parse_args(argv)

    if args.sweep:
        reference_version = _lock_version(REPO_ROOT / ".harness.lock")
        projects, skipped_duplicates = sweep(Path(args.sweep), reference_version)
        if args.json:
            print(
                json.dumps(
                    {
                        "sweep": str(Path(args.sweep)),
                        "reference_version": reference_version,
                        "projects": projects,
                        "skipped_duplicates": skipped_duplicates,
                    },
                    indent=2,
                )
            )
        else:
            print_sweep(projects, Path(args.sweep), skipped_duplicates)
        return 0

    report = collect(Path(args.root))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
