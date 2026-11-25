#!/usr/bin/env python3
"""Derived SQLite index over sibling projects' .harness.lock and usage logs."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from contextlib import closing, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / ".solocode" / "portfolio.db"
LOCK_NAME = ".harness.lock"
USAGE_FILES = {
    "opencode": "opencode-usage.jsonl",
    "kilo": "kilo-usage.jsonl",
    "antigravity": "antigravity-usage.jsonl",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    name TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    harness_version TEXT,
    fingerprint TEXT,
    lock_generated_at TEXT,
    scanned_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    project TEXT NOT NULL,
    arm TEXT NOT NULL,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    model TEXT,
    agent TEXT,
    prompt_chars INTEGER,
    elapsed_s REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cost REAL,
    error TEXT,
    finished INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project, arm, run_id)
);
"""

_HARNESS_SECTION_RE = re.compile(
    r"(?ms)^\[harness\][ \t]*(?:#[^\r\n]*)?(?:\r?\n(.*?))?(?=^\[|\Z)"
)
_LOCK_KV_RE = re.compile(
    r'^[ \t]*(version|generated_at|content_fingerprint)[ \t]*=[ \t]*(?:"([^"]*)"|\'([^\']*)\'|([^\s#]+))',
    re.MULTILINE,
)

_UPSERT_PROJECT_SQL = """
INSERT INTO projects (
    name, path, harness_version, fingerprint, lock_generated_at, scanned_at
) VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(name) DO UPDATE SET
    path = excluded.path,
    harness_version = excluded.harness_version,
    fingerprint = excluded.fingerprint,
    lock_generated_at = excluded.lock_generated_at,
    scanned_at = excluded.scanned_at
"""

_UPSERT_RUN_SQL = """
INSERT INTO runs (
    project, arm, run_id, ts, model, agent, prompt_chars, elapsed_s,
    input_tokens, output_tokens, cache_read_tokens, cost, error, finished
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(project, arm, run_id) DO UPDATE SET
    ts = COALESCE(NULLIF(excluded.ts, ''), runs.ts),
    model = COALESCE(excluded.model, runs.model),
    agent = COALESCE(excluded.agent, runs.agent),
    prompt_chars = COALESCE(excluded.prompt_chars, runs.prompt_chars),
    elapsed_s = COALESCE(excluded.elapsed_s, runs.elapsed_s),
    input_tokens = COALESCE(excluded.input_tokens, runs.input_tokens),
    output_tokens = COALESCE(excluded.output_tokens, runs.output_tokens),
    cache_read_tokens = COALESCE(excluded.cache_read_tokens, runs.cache_read_tokens),
    cost = COALESCE(excluded.cost, runs.cost),
    error = COALESCE(excluded.error, runs.error),
    finished = CASE WHEN excluded.finished = 1 THEN 1 ELSE runs.finished END
"""


def _make_streams_encoding_safe() -> None:
    """Avoid UnicodeEncodeError on Windows consoles when printing error text."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with suppress(OSError):
                reconfigure(errors="replace")


def read_lock(path: Path) -> dict[str, str]:
    """Parse version, generated_at and content_fingerprint from [harness]."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    match = _HARNESS_SECTION_RE.search(text)
    if not match or not match.group(1):
        return {}
    result: dict[str, str] = {}
    for kv in _LOCK_KV_RE.finditer(match.group(1)):
        key = kv.group(1)
        value = (
            kv.group(2)
            if kv.group(2) is not None
            else kv.group(3)
            if kv.group(3) is not None
            else kv.group(4)
        )
        if value is not None and key not in result:
            result[key] = value
    return result


def iter_projects(roots: list[Path], *, skip: Path | None = None) -> list[Path]:
    """Return sorted direct child directories of roots containing LOCK_NAME."""
    skip_resolved = skip.resolve() if skip is not None else None
    seen: dict[Path, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir() or not (child / LOCK_NAME).is_file():
                continue
            resolved = child.resolve()
            if skip_resolved is not None and resolved == skip_resolved:
                continue
            if resolved not in seen:
                seen[resolved] = child
    return sorted(seen.values(), key=lambda p: p.name)


def parse_usage(path: Path) -> list[dict[str, object]]:
    """Read one JSON object per non-empty line, skipping malformed lines."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    rows: list[dict[str, object]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _int_field(mapping: dict[str, object], *keys: str) -> int:
    for key in keys:
        val = mapping.get(key)
        if isinstance(val, int | float) and not isinstance(val, bool):
            return int(val)
    return 0


def _tokens(usage: object) -> tuple[int, int, int]:
    """Return (input, output, cache_read) tokens from an arm's usage payload."""
    if not isinstance(usage, dict):
        return (0, 0, 0)
    inp = _int_field(usage, "input", "input_tokens")
    out = _int_field(usage, "output", "output_tokens")
    cache_read = _int_field(usage, "cache_read_tokens", "cache.read")
    cache = usage.get("cache")
    if cache_read == 0 and isinstance(cache, dict):
        cache_read = _int_field(cache, "read")
    return (inp, out, cache_read)


def _opt_int(val: object) -> int | None:
    if isinstance(val, int | float) and not isinstance(val, bool):
        return int(val)
    return None


def _opt_float(val: object) -> float | None:
    if isinstance(val, int | float) and not isinstance(val, bool):
        return float(val)
    return None


def _opt_str(val: object) -> str | None:
    if val is None:
        return None
    return str(val)


def _open_db(db_path: Path, *, create: bool = True) -> sqlite3.Connection:
    if create:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    if create:
        conn.executescript(SCHEMA_SQL)
    return conn


def sync(
    roots: list[Path],
    db_path: Path = DB_PATH,
    *,
    rebuild: bool = False,
    skip: Path | None = None,
    prune: bool = True,
) -> dict[str, int]:
    """Scan direct child projects under roots and upsert projects and runs."""
    projects = iter_projects(roots, skip=skip)
    pruned = 0
    skipped = 0
    with closing(_open_db(db_path, create=True)) as conn:
        with conn:
            if rebuild:
                conn.execute("DELETE FROM runs")
                conn.execute("DELETE FROM projects")
            for proj_dir in projects:
                lock = read_lock(proj_dir / LOCK_NAME)
                scanned_at = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    _UPSERT_PROJECT_SQL,
                    (
                        proj_dir.name,
                        str(proj_dir),
                        lock.get("version"),
                        lock.get("content_fingerprint"),
                        lock.get("generated_at"),
                        scanned_at,
                    ),
                )
                for arm, filename in USAGE_FILES.items():
                    usage_path = proj_dir / ".solocode" / filename
                    if not usage_path.is_file():
                        continue
                    for entry in parse_usage(usage_path):
                        raw_run_id = entry.get("run_id")
                        if raw_run_id is None or raw_run_id == "":
                            raw_run_id = entry.get("session_id")
                        if raw_run_id is None or raw_run_id == "":
                            skipped += 1
                            continue
                        run_id = str(raw_run_id)
                        ts = str(entry.get("ts") or "")
                        usage = entry.get("usage")
                        if usage is None:
                            in_tok: int | None = None
                            out_tok: int | None = None
                            cache_tok: int | None = None
                        else:
                            in_tok, out_tok, cache_tok = _tokens(usage)
                        phase = entry.get("phase")
                        finished = 1 if phase == "finish" or phase is None else 0
                        conn.execute(
                            _UPSERT_RUN_SQL,
                            (
                                proj_dir.name,
                                arm,
                                run_id,
                                ts,
                                _opt_str(entry.get("model")),
                                _opt_str(entry.get("agent")),
                                _opt_int(entry.get("prompt_chars")),
                                _opt_float(entry.get("elapsed_s")),
                                in_tok,
                                out_tok,
                                cache_tok,
                                _opt_float(entry.get("cost")),
                                _opt_str(entry.get("error")),
                                finished,
                            ),
                        )

            if prune and projects:
                scanned_paths = {str(p) for p in projects}
                stale_names = [
                    row[0]
                    for row in conn.execute(
                        "SELECT name, path FROM projects"
                    ).fetchall()
                    if row[1] not in scanned_paths
                ]
                for name in stale_names:
                    conn.execute("DELETE FROM runs WHERE project = ?", (name,))
                    cur = conn.execute("DELETE FROM projects WHERE name = ?", (name,))
                    pruned += int(cur.rowcount)

        projects_count = int(
            conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        )
        runs_count = int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
        unfinished_count = int(
            conn.execute("SELECT COUNT(*) FROM runs WHERE finished = 0").fetchone()[0]
        )
        failures_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM runs "
                "WHERE finished = 1 AND error IS NOT NULL AND error != ''"
            ).fetchone()[0]
        )

    return {
        "scanned": len(projects),
        "projects": projects_count,
        "runs": runs_count,
        "unfinished": unfinished_count,
        "failures": failures_count,
        "pruned": pruned,
        "skipped": skipped,
    }


def _compute_drift(
    proj_version: str | None,
    proj_fp: str | None,
    ref_version: str | None,
    ref_fp: str | None,
) -> str:
    if not proj_fp or not ref_fp:
        return "missing"
    if (ref_version is None) != (proj_version is None):
        return "missing"
    if proj_fp == ref_fp and proj_version == ref_version:
        return "same"
    return "differs"


def status_rows(
    db_path: Path = DB_PATH, reference: Path | None = None
) -> list[dict[str, object]]:
    """Return per-project version, fingerprint drift, run and failure counts."""
    ref_path = ROOT / LOCK_NAME if reference is None else reference
    ref_lock = read_lock(ref_path)
    ref_version = ref_lock.get("version")
    ref_fp = ref_lock.get("content_fingerprint")

    with closing(_open_db(db_path, create=False)) as conn:
        raw_rows = conn.execute(
            """
            SELECT
                p.name,
                p.harness_version,
                p.fingerprint,
                p.lock_generated_at,
                COUNT(r.run_id) AS runs,
                COALESCE(
                    SUM(
                        CASE
                            WHEN r.finished = 1 AND r.error IS NOT NULL AND r.error != ''
                            THEN 1 ELSE 0
                        END
                    ),
                    0
                ) AS failures
            FROM projects p
            LEFT JOIN runs r ON r.project = p.name
            GROUP BY p.name
            ORDER BY p.name ASC
            """
        ).fetchall()

    results: list[dict[str, object]] = []
    for name, version, fingerprint, generated_at, runs, failures in raw_rows:
        results.append(
            {
                "name": name,
                "version": version,
                "fingerprint": fingerprint,
                "generated_at": generated_at,
                "drift": _compute_drift(version, fingerprint, ref_version, ref_fp),
                "runs": int(runs),
                "failures": int(failures),
            }
        )
    return results


def has_drift(rows: list[dict[str, object]]) -> bool:
    """Return True when any status row reports differs or missing."""
    return any(row.get("drift") in ("differs", "missing") for row in rows)


def failure_rows(db_path: Path = DB_PATH) -> list[dict[str, object]]:
    """Return one row per project and arm ordered by failures descending."""
    with closing(_open_db(db_path, create=False)) as conn:
        agg_rows = conn.execute(
            """
            SELECT
                project,
                arm,
                COUNT(*) AS runs,
                COALESCE(
                    SUM(
                        CASE
                            WHEN finished = 1 AND error IS NOT NULL AND error != ''
                            THEN 1 ELSE 0
                        END
                    ),
                    0
                ) AS failures,
                COALESCE(SUM(CASE WHEN finished = 0 THEN 1 ELSE 0 END), 0) AS unfinished
            FROM runs
            GROUP BY project, arm
            ORDER BY failures DESC, unfinished DESC, project ASC, arm ASC
            """
        ).fetchall()
        err_rows = conn.execute(
            """
            SELECT project, arm, error, COUNT(*) AS cnt
            FROM runs
            WHERE finished = 1 AND error IS NOT NULL AND error != ''
            GROUP BY project, arm, error
            ORDER BY project ASC, arm ASC, cnt DESC, error ASC
            """
        ).fetchall()

    top_errors: dict[tuple[str, str], tuple[str, int]] = {}
    for project, arm, error, cnt in err_rows:
        key = (project, arm)
        if key not in top_errors:
            top_errors[key] = (error, int(cnt))

    results: list[dict[str, object]] = []
    for project, arm, runs, failures, unfinished in agg_rows:
        top_err, top_cnt = top_errors.get((project, arm), (None, 0))
        results.append(
            {
                "project": project,
                "arm": arm,
                "runs": int(runs),
                "failures": int(failures),
                "unfinished": int(unfinished),
                "top_error": top_err,
                "top_error_count": top_cnt,
            }
        )
    return results


def usage_rows(db_path: Path = DB_PATH) -> list[dict[str, object]]:
    """Return summed run and token metrics per project and model."""
    with closing(_open_db(db_path, create=False)) as conn:
        raw_rows = conn.execute(
            """
            SELECT
                project,
                COALESCE(model, '') AS model,
                COUNT(*) AS runs,
                COALESCE(SUM(prompt_chars), 0) AS prompt_chars,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cost), 0.0) AS cost
            FROM runs
            GROUP BY project, COALESCE(model, '')
            ORDER BY project ASC, model ASC
            """
        ).fetchall()

    return [
        {
            "project": project,
            "model": model,
            "runs": int(runs),
            "prompt_chars": int(prompt_chars),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cost": float(cost),
        }
        for project, model, runs, prompt_chars, input_tokens, output_tokens, cost in raw_rows
    ]


def _print_table(
    headers: list[str],
    rows: list[list[str]],
    right_align: set[int] | None = None,
) -> None:
    right = right_align or set()
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def _fmt(values: list[str]) -> str:
        parts = []
        for idx, val in enumerate(values):
            w = widths[idx]
            parts.append(val.rjust(w) if idx in right else val.ljust(w))
        return "  ".join(parts).rstrip()

    print(_fmt(headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(_fmt(row))


def _require_db(db_path: Path) -> bool:
    if not db_path.is_file():
        print(f"Database not found at {db_path}. Run 'sync' first.")
        return False
    return True


def _cmd_sync(args: argparse.Namespace) -> int:
    roots = [ROOT.parent.parent, ROOT.parent] if args.root is None else args.root
    counts = sync(
        roots,
        args.db,
        rebuild=args.rebuild,
        skip=ROOT,
        prune=not args.keep_missing,
    )
    print(
        f"Scanned {counts['scanned']} project(s); "
        f"index holds {counts['projects']} project(s), {counts['runs']} run(s) "
        f"(unfinished={counts['unfinished']}, failures={counts['failures']}) -> {args.db}"
    )
    if counts["skipped"] > 0:
        print(f"skipped {counts['skipped']} usage line(s) with no run id")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    if not _require_db(args.db):
        return 1
    rows = status_rows(args.db)
    if not rows:
        print("No projects recorded.")
        return 0
    table = [
        [
            str(r["name"]),
            str(r["version"] or "-"),
            str(r["drift"]),
            str(r["runs"]),
            str(r["failures"]),
            str(r["generated_at"] or "-"),
            str(r["fingerprint"] or "-"),
        ]
        for r in rows
    ]
    _print_table(
        ["Project", "Version", "Drift", "Runs", "Failures", "Generated", "Fingerprint"],
        table,
        right_align={3, 4},
    )
    return 1 if args.strict and has_drift(rows) else 0


def _cmd_failures(args: argparse.Namespace) -> int:
    if not _require_db(args.db):
        return 1
    rows = failure_rows(args.db)
    if not rows:
        print("No runs recorded.")
        return 0
    table = [
        [
            str(r["project"]),
            str(r["arm"]),
            str(r["runs"]),
            str(r["failures"]),
            str(r["unfinished"]),
            str(r["top_error_count"]),
            str(r["top_error"] or "-"),
        ]
        for r in rows
    ]
    _print_table(
        ["Project", "Arm", "Runs", "Failures", "Unfinished", "TopCount", "TopError"],
        table,
        right_align={2, 3, 4, 5},
    )
    return 0


def _cmd_usage(args: argparse.Namespace) -> int:
    if not _require_db(args.db):
        return 1
    rows = usage_rows(args.db)
    if not rows:
        print("No usage recorded.")
        return 0
    table = [
        [
            str(r["project"]),
            str(r["model"] or "-"),
            str(r["runs"]),
            str(r["prompt_chars"]),
            str(r["input_tokens"]),
            str(r["output_tokens"]),
            f"{float(r['cost']):.6f}",
        ]
        for r in rows
    ]
    _print_table(
        [
            "Project",
            "Model",
            "Runs",
            "PromptChars",
            "InputTokens",
            "OutputTokens",
            "Cost",
        ],
        table,
        right_align={2, 3, 4, 5, 6},
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate .harness.lock and .solocode/*-usage.jsonl across projects."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="Scan projects and update the SQLite index.")
    p_sync.add_argument(
        "--root",
        action="append",
        type=Path,
        default=None,
        help=(
            "Portfolio scan root; may be given more than once "
            f"(default: {ROOT.parent.parent}, {ROOT.parent})"
        ),
    )
    p_sync.add_argument(
        "--rebuild",
        action="store_true",
        help="Clear existing tables before re-scanning.",
    )
    p_sync.add_argument(
        "--keep-missing",
        action="store_true",
        help="Keep projects missing from this scan; the index otherwise mirrors the scanned roots.",
    )
    p_sync.add_argument("--db", type=Path, default=DB_PATH, help="SQLite DB path.")
    p_sync.set_defaults(func=_cmd_sync)

    p_status = sub.add_parser("status", help="Show harness drift and run totals.")
    p_status.add_argument("--db", type=Path, default=DB_PATH, help="SQLite DB path.")
    p_status.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when any project differs from or misses the reference lock.",
    )
    p_status.set_defaults(func=_cmd_status)

    p_failures = sub.add_parser(
        "failures", help="Show failures, unfinished runs and top errors per arm."
    )
    p_failures.add_argument("--db", type=Path, default=DB_PATH, help="SQLite DB path.")
    p_failures.set_defaults(func=_cmd_failures)

    p_usage = sub.add_parser("usage", help="Show summed tokens and cost by model.")
    p_usage.add_argument("--db", type=Path, default=DB_PATH, help="SQLite DB path.")
    p_usage.set_defaults(func=_cmd_usage)

    return parser


def main(argv: list[str] | None = None) -> int:
    _make_streams_encoding_safe()
    parser = _build_parser()
    args = parser.parse_args(argv)
    func: Any = args.func
    return int(func(args))


if __name__ == "__main__":
    sys.exit(main())
