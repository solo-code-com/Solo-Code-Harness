#!/usr/bin/env python3
"""
Session Analytics — Query and analyze session persistence data.

Provides aggregation and reporting capabilities over .solocode/sessions.db:
- Most active branches
- Session duration statistics
- Files changed distribution
- Session status breakdown

Usage:
    python tools/session_analytics.py --stats           # Overall statistics
    python tools/session_analytics.py --by-branch       # Group by branch
    python tools/session_analytics.py --by-status       # Group by status
    python tools/session_analytics.py --recent N        # Last N sessions with details
    python tools/session_analytics.py --self-test       # Framework validation

Example:
    python tools/session_analytics.py --stats
    # Sessions: 42 total, 40 completed, 2 active
    # Duration: avg 18m, median 12m, max 3h 24m
    # Files changed: avg 5.2, median 3, max 47
    # Top branches: main (35), feature/auth (4), fix/timeout (3)
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import session_persistence as sp
except ImportError:
    # Allow running from project root
    sys.path.insert(0, str(Path(__file__).parent))
    import session_persistence as sp


def _parse_iso(s: str | None) -> datetime | None:
    """Parse ISO timestamp string to datetime."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _duration_seconds(start: str, end: str | None) -> float | None:
    """Calculate session duration in seconds."""
    if not end:
        return None
    dt_start = _parse_iso(start)
    dt_end = _parse_iso(end)
    if not dt_start or not dt_end:
        return None
    return (dt_end - dt_start).total_seconds()


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


def overall_stats(*, path: Path | None = None) -> dict[str, Any]:
    """Compute overall session statistics.

    Args:
        path: SQLite database path. Defaults to the production ``DB_PATH``
            when omitted; pass an explicit temp path in tests to avoid
            touching production data.
    """
    sessions = sp.list_sessions(limit=1000, path=path)  # Large limit for all sessions

    if not sessions:
        return {
            "total": 0,
            "by_status": {},
            "duration": {},
            "files_changed": {},
            "top_branches": [],
        }

    # Status breakdown
    by_status: dict[str, int] = {}
    for s in sessions:
        status = s["status"]
        by_status[status] = by_status.get(status, 0) + 1

    # Duration stats (only completed sessions)
    durations = [
        _duration_seconds(s["start_time"], s["end_time"])
        for s in sessions
        if s["end_time"]
    ]
    durations = [d for d in durations if d is not None]

    duration_stats = {}
    if durations:
        durations_sorted = sorted(durations)
        duration_stats = {
            "avg": sum(durations) / len(durations),
            "median": durations_sorted[len(durations_sorted) // 2],
            "min": min(durations),
            "max": max(durations),
        }

    # Files changed stats
    files_changed = [s["files_changed"] for s in sessions]
    files_sorted = sorted(files_changed)
    files_stats = {
        "avg": sum(files_changed) / len(files_changed) if files_changed else 0,
        "median": files_sorted[len(files_sorted) // 2] if files_sorted else 0,
        "min": min(files_changed) if files_changed else 0,
        "max": max(files_changed) if files_changed else 0,
    }

    # Top branches
    branch_count: dict[str, int] = {}
    for s in sessions:
        branch = s.get("branch") or "unknown"
        branch_count[branch] = branch_count.get(branch, 0) + 1
    top_branches = sorted(branch_count.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "total": len(sessions),
        "by_status": by_status,
        "duration": duration_stats,
        "files_changed": files_stats,
        "top_branches": top_branches,
    }


def by_branch_stats(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Group sessions by branch with statistics."""
    sessions = sp.list_sessions(limit=1000, path=path)  # Large limit for all sessions

    branch_data: dict[str, list[dict]] = {}
    for s in sessions:
        branch = s.get("branch") or "unknown"
        if branch not in branch_data:
            branch_data[branch] = []
        branch_data[branch].append(s)

    result = {}
    for branch, branch_sessions in branch_data.items():
        durations = [
            _duration_seconds(s["start_time"], s["end_time"])
            for s in branch_sessions
            if s["end_time"]
        ]
        durations = [d for d in durations if d is not None]

        result[branch] = {
            "count": len(branch_sessions),
            "completed": sum(1 for s in branch_sessions if s["status"] == "completed"),
            "active": sum(1 for s in branch_sessions if s["status"] == "active"),
            "avg_files_changed": (
                sum(s["files_changed"] for s in branch_sessions) / len(branch_sessions)
                if branch_sessions
                else 0
            ),
            "avg_duration": sum(durations) / len(durations) if durations else None,
        }

    return result


def by_status_stats(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Group sessions by status with statistics."""
    sessions = sp.list_sessions(limit=1000, path=path)  # Large limit for all sessions

    status_data: dict[str, list[dict]] = {}
    for s in sessions:
        status = s["status"]
        if status not in status_data:
            status_data[status] = []
        status_data[status].append(s)

    result = {}
    for status, status_sessions in status_data.items():
        result[status] = {
            "count": len(status_sessions),
            "total_files_changed": sum(s["files_changed"] for s in status_sessions),
            "branches": len({s.get("branch") or "unknown" for s in status_sessions}),
        }

    return result


def recent_sessions_detail(
    limit: int = 10, *, path: Path | None = None
) -> list[dict[str, Any]]:
    """Get recent sessions with computed duration."""
    sessions = sp.list_sessions(limit, path=path)

    result = []
    for s in sessions:
        duration = _duration_seconds(s["start_time"], s["end_time"])
        result.append(
            {
                "id": s["id"],
                "start": s["start_time"],
                "end": s["end_time"],
                "duration": _format_duration(duration) if duration else "active",
                "branch": s.get("branch") or "unknown",
                "commit": (s.get("commit_hash") or "unknown")[:7],
                "files_changed": s["files_changed"],
                "status": s["status"],
            }
        )

    return result


def print_stats(stats: dict[str, Any]) -> None:
    """Pretty-print overall statistics."""
    print(f"Sessions: {stats['total']} total", end="")
    if stats["by_status"]:
        status_parts = [f"{v} {k}" for k, v in stats["by_status"].items()]
        print(f", {', '.join(status_parts)}")
    else:
        print()

    if stats["duration"]:
        d = stats["duration"]
        print(
            f"Duration: avg {_format_duration(d['avg'])}, "
            f"median {_format_duration(d['median'])}, "
            f"max {_format_duration(d['max'])}"
        )

    f = stats["files_changed"]
    print(f"Files changed: avg {f['avg']:.1f}, median {f['median']}, max {f['max']}")

    if stats["top_branches"]:
        branch_str = ", ".join(f"{b} ({c})" for b, c in stats["top_branches"])
        print(f"Top branches: {branch_str}")


def print_by_branch(branch_stats: dict[str, dict[str, Any]]) -> None:
    """Pretty-print branch statistics."""
    for branch, data in sorted(
        branch_stats.items(), key=lambda x: x[1]["count"], reverse=True
    ):
        print(f"\n{branch}:")
        print(
            f"  Sessions: {data['count']} ({data['completed']} completed, {data['active']} active)"
        )
        print(f"  Avg files changed: {data['avg_files_changed']:.1f}")
        if data["avg_duration"]:
            print(f"  Avg duration: {_format_duration(data['avg_duration'])}")


def print_by_status(status_stats: dict[str, dict[str, Any]]) -> None:
    """Pretty-print status statistics."""
    for status, data in status_stats.items():
        print(f"\n{status}:")
        print(f"  Count: {data['count']}")
        print(f"  Total files changed: {data['total_files_changed']}")
        print(f"  Unique branches: {data['branches']}")


def print_recent(sessions: list[dict[str, Any]]) -> None:
    """Pretty-print recent sessions."""
    for s in sessions:
        print(
            f"{s['start'][:16]} | {s['duration']:>8} | "
            f"{s['branch']:>15}@{s['commit']} | "
            f"{s['files_changed']:>3} files | {s['status']}"
        )


def run_self_test() -> bool:
    """Self-test: validate analytics framework against a throwaway DB.

    Never touches the production ``.solocode/sessions.db`` — seeds a temp DB
    with two known sessions and asserts concrete numbers, not just structure.
    """
    print("Running session analytics self-test...")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "sessions.db"

            # Seed a known dataset: one completed session on main, one active
            # session on feature/x.
            sp.record_session_start("ana-test-1", "main", "abc1234", path=db)
            sp.record_session_start("ana-test-2", "feature/x", "def5678", path=db)
            sp.record_session_end("ana-test-1", 3, "completed", path=db)

            # Test 1: overall_stats against the seeded DB
            print("\n1. Testing overall_stats()...")
            stats = overall_stats(path=db)
            assert stats["total"] == 2, stats
            assert stats["by_status"] == {"completed": 1, "active": 1}, stats
            assert stats["files_changed"]["avg"] == 1.5, stats
            assert len(stats["top_branches"]) == 2, stats
            assert all(count == 1 for _, count in stats["top_branches"]), stats
            print("[OK] overall_stats() computes concrete values")

            # Test 2: by_branch_stats against the seeded DB
            print("\n2. Testing by_branch_stats()...")
            branch_stats = by_branch_stats(path=db)
            assert branch_stats["main"]["count"] == 1, branch_stats
            assert branch_stats["main"]["completed"] == 1, branch_stats
            assert branch_stats["feature/x"]["active"] == 1, branch_stats
            print("[OK] by_branch_stats() computes concrete values")

            # Test 3: recent_sessions_detail against the seeded DB
            print("\n3. Testing recent_sessions_detail()...")
            recent = recent_sessions_detail(5, path=db)
            assert len(recent) == 2, recent
            assert recent[0]["status"] == "active", recent
            assert recent[1]["status"] == "completed", recent
            print("[OK] recent_sessions_detail() returns concrete sessions")

    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {e}", file=sys.stderr)
        return False

    # Test 4: duration formatting
    print("\n4. Testing duration formatting...")
    assert _format_duration(45) == "45s"
    assert _format_duration(120) == "2m"
    assert _format_duration(3665) == "1h 1m"
    print("[OK] Duration formatting works")

    print("\n[OK] All self-tests passed!")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Session analytics and reporting")
    parser.add_argument("--stats", action="store_true", help="Show overall statistics")
    parser.add_argument("--by-branch", action="store_true", help="Group by branch")
    parser.add_argument("--by-status", action="store_true", help="Group by status")
    parser.add_argument("--recent", type=int, metavar="N", help="Show last N sessions")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--self-test", action="store_true", help="Run self-test")

    args = parser.parse_args()

    if args.self_test:
        return 0 if run_self_test() else 1

    if not any([args.stats, args.by_branch, args.by_status, args.recent]):
        parser.print_help()
        return 1

    try:
        if args.stats:
            stats = overall_stats()
            if args.json:
                print(json.dumps(stats, indent=2))
            else:
                print_stats(stats)

        if args.by_branch:
            branch_stats = by_branch_stats()
            if args.json:
                print(json.dumps(branch_stats, indent=2))
            else:
                print_by_branch(branch_stats)

        if args.by_status:
            status_stats = by_status_stats()
            if args.json:
                print(json.dumps(status_stats, indent=2))
            else:
                print_by_status(status_stats)

        if args.recent:
            sessions = recent_sessions_detail(args.recent)
            if args.json:
                print(json.dumps(sessions, indent=2))
            else:
                print_recent(sessions)

        return 0
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
