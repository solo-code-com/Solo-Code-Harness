#!/usr/bin/env python3
"""Retention management for .solocode state.

PURPOSE:
.solocode accumulates: one session record per run, one restore snapshot per write
run, and usage logs that only grow. None of it is dangerous, all of it is unbounded.
This tool makes the accumulation visible and reversible: a dry run by default, and
aged entries move to .solocode/archive/ instead of disappearing.

HARD RULES:
- Never touch .solocode/reviews or .solocode/task-receipts. They are evidence.
- Nothing changes without --apply.
- Nothing is deleted unless --purge is given, and then only inside the archive.

LAYOUT:
- .solocode/restore/<id>/
- .solocode/sessions/<id>.json
- .solocode/*-usage.jsonl
- .solocode/archive/<category>/<name>/
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

SOLOCODE_DIR = Path(".solocode")
RESTORE_DIR = SOLOCODE_DIR / "restore"
SESSIONS_DIR = SOLOCODE_DIR / "sessions"
ARCHIVE_DIR = SOLOCODE_DIR / "archive"
USAGE_GLOB = "*-usage.jsonl"
PROTECTED_DIRS = ("reviews", "task-receipts")

_ARCHIVE_CATEGORIES = ("restore", "sessions", "usage")


def _root_path(root: str | Path) -> Path:
    return root if isinstance(root, Path) else Path(root)


def _solocode_root(root: Path) -> Path:
    return root / SOLOCODE_DIR


def _is_protected_relpath(rel_path: str | Path) -> bool:
    parts = Path(rel_path).parts
    return any(part in PROTECTED_DIRS for part in parts)


def _assert_not_protected(root: Path, candidate: Path, raw_label: str) -> None:
    if _is_protected_relpath(raw_label):
        raise ValueError(f"Refusing to touch protected path: {raw_label}")
    solocode_resolved = _solocode_root(root).resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(solocode_resolved):
        raise ValueError(f"Refusing path outside .solocode: {raw_label}")
    for protected in PROTECTED_DIRS:
        protected_root = (solocode_resolved / protected).resolve()
        if resolved == protected_root or resolved.is_relative_to(protected_root):
            raise ValueError(f"Refusing to touch protected directory: {raw_label}")


def _dir_bytes(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for child in path.rglob("*"):
            if child.is_file() and not child.is_symlink():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _file_bytes(path: Path) -> int:
    if path.is_symlink() or not path.is_file():
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _mtime_key(path: Path) -> tuple[float, str]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (mtime, path.name)


def _timestamp_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")


def _write_bytes_atomically(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def usage_size(root: str | Path) -> dict[str, int]:
    """Return bytes per usage log under .solocode/*-usage.jsonl."""
    root_path = _root_path(root)
    solocode = _solocode_root(root_path)
    if not solocode.is_dir():
        return {}
    sizes: dict[str, int] = {}
    for path in sorted(solocode.glob(USAGE_GLOB)):
        if path.is_file() and not path.is_symlink():
            sizes[path.name] = _file_bytes(path)
    return sizes


def plan(
    root: str | Path,
    *,
    keep_restore: int = 3,
    keep_sessions: int = 200,
    keep_usage_lines: int = 5000,
) -> dict[str, Any]:
    """Report what apply would move without changing anything on disk."""
    if keep_restore < 0 or keep_sessions < 0 or keep_usage_lines < 0:
        raise ValueError("Keep counts must be non-negative")

    root_path = _root_path(root)

    restore_root = root_path / RESTORE_DIR
    restore_entries: list[Path] = []
    if restore_root.is_dir():
        restore_entries = sorted(
            (
                entry
                for entry in restore_root.iterdir()
                if entry.is_dir()
                and not entry.is_symlink()
                and entry.name not in PROTECTED_DIRS
            ),
            key=_mtime_key,
            reverse=True,
        )
    restore_keep = restore_entries[:keep_restore]
    restore_move = restore_entries[keep_restore:]
    restore_total_bytes = sum(_dir_bytes(p) for p in restore_entries)
    restore_move_bytes = sum(_dir_bytes(p) for p in restore_move)

    sessions_root = root_path / SESSIONS_DIR
    session_entries: list[Path] = []
    if sessions_root.is_dir():
        session_entries = sorted(
            (
                entry
                for entry in sessions_root.glob("*.json")
                if entry.is_file() and not entry.is_symlink()
            ),
            key=_mtime_key,
            reverse=True,
        )
    sessions_keep = session_entries[:keep_sessions]
    sessions_move = session_entries[keep_sessions:]
    sessions_total_bytes = sum(_file_bytes(p) for p in session_entries)
    sessions_move_bytes = sum(_file_bytes(p) for p in sessions_move)

    solocode = _solocode_root(root_path)
    usage_entries: list[dict[str, Any]] = []
    usage_keep_lines_total = 0
    usage_move_lines_total = 0
    usage_total_bytes = 0
    usage_move_bytes = 0
    if solocode.is_dir():
        for log_path in sorted(solocode.glob(USAGE_GLOB)):
            if not log_path.is_file() or log_path.is_symlink():
                continue
            raw = log_path.read_bytes()
            lines = raw.splitlines(keepends=True)
            total_lines = len(lines)
            if keep_usage_lines == 0:
                kept_lines: list[bytes] = []
                moved_lines = lines
            else:
                kept_lines = lines[-keep_usage_lines:]
                moved_lines = lines[:-keep_usage_lines]
            usage_total_bytes += len(raw)
            usage_keep_lines_total += len(kept_lines)
            if moved_lines:
                moved_bytes = sum(len(line) for line in moved_lines)
                usage_move_lines_total += len(moved_lines)
                usage_move_bytes += moved_bytes
                usage_entries.append(
                    {
                        "path": log_path.relative_to(root_path).as_posix(),
                        "total_lines": total_lines,
                        "keep_lines": len(kept_lines),
                        "move_lines": len(moved_lines),
                        "bytes": len(raw),
                        "move_bytes": moved_bytes,
                    }
                )

    return {
        "restore": [p.relative_to(root_path).as_posix() for p in restore_move],
        "sessions": [p.relative_to(root_path).as_posix() for p in sessions_move],
        "usage": usage_entries,
        "counts": {
            "restore": {
                "keep": len(restore_keep),
                "move": len(restore_move),
                "bytes": restore_total_bytes,
                "move_bytes": restore_move_bytes,
            },
            "sessions": {
                "keep": len(sessions_keep),
                "move": len(sessions_move),
                "bytes": sessions_total_bytes,
                "move_bytes": sessions_move_bytes,
            },
            "usage": {
                "keep": usage_keep_lines_total,
                "move": usage_move_lines_total,
                "bytes": usage_total_bytes,
                "move_bytes": usage_move_bytes,
            },
        },
    }


def _validate_plan(root_path: Path, retention_plan: dict[str, Any]) -> None:
    if not isinstance(retention_plan, dict):
        raise TypeError("plan must be a dict")
    for key in retention_plan:
        if key in PROTECTED_DIRS:
            raise ValueError(f"Refusing to touch protected directory: {key}")
    archive_root = (root_path / ARCHIVE_DIR).resolve()
    for category in _ARCHIVE_CATEGORIES:
        items = retention_plan.get(category, [])
        if not isinstance(items, list):
            raise TypeError(f"plan[{category!r}] must be a list")
        for item in items:
            raw_path = item["path"] if isinstance(item, dict) else str(item)
            if Path(raw_path).is_absolute():
                raise ValueError(f"Refusing absolute path in plan: {raw_path}")
            candidate = root_path / raw_path
            _assert_not_protected(root_path, candidate, raw_path)
            resolved = candidate.resolve()
            if resolved == archive_root or resolved.is_relative_to(archive_root):
                raise ValueError(f"Refusing archive path in apply plan: {raw_path}")


def apply(
    root: str | Path,
    retention_plan: dict[str, Any],
    *,
    archive: bool = True,
) -> dict[str, Any]:
    """Move planned entries to .solocode/archive/, or delete if archive=False."""
    root_path = _root_path(root)
    _validate_plan(root_path, retention_plan)

    timestamp = _timestamp_label()
    archive_root = root_path / ARCHIVE_DIR

    moved_restore: list[str] = []
    moved_sessions: list[str] = []
    rotated_usage: list[str] = []
    archived_paths: list[str] = []
    deleted_paths: list[str] = []

    for item in retention_plan.get("restore", []):
        rel_str = item["path"] if isinstance(item, dict) else str(item)
        src = root_path / rel_str
        if not src.exists() or src.is_symlink():
            continue
        if archive:
            dest_dir = archive_root / "restore" / timestamp
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            shutil.move(str(src), str(dest))
            archived_paths.append(dest.relative_to(root_path).as_posix())
        else:
            shutil.rmtree(src)
            deleted_paths.append(rel_str)
        moved_restore.append(rel_str)

    for item in retention_plan.get("sessions", []):
        rel_str = item["path"] if isinstance(item, dict) else str(item)
        src = root_path / rel_str
        if not src.exists() or src.is_symlink():
            continue
        if archive:
            dest_dir = archive_root / "sessions" / timestamp
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            shutil.move(str(src), str(dest))
            archived_paths.append(dest.relative_to(root_path).as_posix())
        else:
            src.unlink()
            deleted_paths.append(rel_str)
        moved_sessions.append(rel_str)

    for item in retention_plan.get("usage", []):
        if isinstance(item, dict):
            rel_str = str(item["path"])
            keep_lines = int(item.get("keep_lines", 0))
        else:
            rel_str = str(item)
            keep_lines = 0
        if keep_lines < 0:
            raise ValueError("keep_lines must be non-negative")
        src = root_path / rel_str
        if not src.is_file() or src.is_symlink():
            continue
        lines = src.read_bytes().splitlines(keepends=True)
        if keep_lines == 0:
            kept: list[bytes] = []
            removed = lines
        else:
            if len(lines) <= keep_lines:
                continue
            kept = lines[-keep_lines:]
            removed = lines[:-keep_lines]
        if not removed:
            continue
        if archive:
            dest_dir = archive_root / "usage" / timestamp
            dest = dest_dir / src.name
            _write_bytes_atomically(dest, b"".join(removed))
            archived_paths.append(dest.relative_to(root_path).as_posix())
        else:
            deleted_paths.append(rel_str)
        _write_bytes_atomically(src, b"".join(kept))
        rotated_usage.append(rel_str)

    return {
        "restore": moved_restore,
        "sessions": moved_sessions,
        "usage": rotated_usage,
        "archived": archived_paths,
        "deleted": deleted_paths,
        "counts": {
            "restore": len(moved_restore),
            "sessions": len(moved_sessions),
            "usage": len(rotated_usage),
        },
    }


def _collect_purge_targets(
    archive_root: Path,
    current: Path,
    cutoff: float,
    *,
    is_category_dir: bool = False,
) -> list[Path]:
    targets: list[Path] = []
    if not current.exists():
        return targets
    for child in sorted(current.iterdir()):
        resolved_child = child.resolve()
        if not resolved_child.is_relative_to(archive_root):
            continue
        if child.is_symlink() or child.is_file():
            try:
                if child.lstat().st_mtime < cutoff:
                    targets.append(child)
            except OSError:
                continue
        elif child.is_dir():
            if is_category_dir and child.name in _ARCHIVE_CATEGORIES:
                targets.extend(
                    _collect_purge_targets(
                        archive_root, child, cutoff, is_category_dir=False
                    )
                )
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                targets.append(child)
            else:
                targets.extend(
                    _collect_purge_targets(
                        archive_root, child, cutoff, is_category_dir=False
                    )
                )
    return targets


def _prune_empty_dirs(archive_root: Path, directory: Path) -> None:
    if not directory.is_dir() or directory == archive_root:
        return
    for child in sorted(directory.iterdir()):
        if child.is_dir() and not child.is_symlink():
            _prune_empty_dirs(archive_root, child)
    try:
        if not any(directory.iterdir()):
            directory.rmdir()
    except OSError:
        pass


def plan_purge(root: str | Path, *, older_than_days: int = 30) -> list[str]:
    """Return archive entries older than older_than_days without deleting."""
    if older_than_days < 0:
        raise ValueError("older_than_days must be non-negative")
    root_path = _root_path(root)
    archive_dir = root_path / ARCHIVE_DIR
    if not archive_dir.is_dir() or archive_dir.is_symlink():
        return []
    archive_resolved = archive_dir.resolve()
    cutoff = time.time() - (older_than_days * 86400)
    targets = _collect_purge_targets(
        archive_resolved, archive_dir, cutoff, is_category_dir=True
    )
    return [t.relative_to(root_path).as_posix() for t in targets]


def purge(root: str | Path, *, older_than_days: int = 30) -> list[str]:
    """Delete archive entries older than older_than_days; return what was removed."""
    if older_than_days < 0:
        raise ValueError("older_than_days must be non-negative")
    root_path = _root_path(root)
    archive_dir = root_path / ARCHIVE_DIR
    if not archive_dir.is_dir() or archive_dir.is_symlink():
        return []
    archive_resolved = archive_dir.resolve()
    cutoff = time.time() - (older_than_days * 86400)
    targets = _collect_purge_targets(
        archive_resolved, archive_dir, cutoff, is_category_dir=True
    )
    removed: list[str] = []
    for target in targets:
        resolved_target = target.resolve()
        if resolved_target == archive_resolved or not resolved_target.is_relative_to(
            archive_resolved
        ):
            continue
        rel = target.relative_to(root_path).as_posix()
        if target.is_symlink() or target.is_file():
            target.unlink()
            removed.append(rel)
        elif target.is_dir():
            shutil.rmtree(target)
            removed.append(rel)
    _prune_empty_dirs(archive_resolved, archive_dir)
    return removed


def format_report(retention_plan: dict[str, Any], *, applied: bool) -> str:
    """Format a per-category summary table with keep, move, and bytes."""
    mode = "APPLIED" if applied else "DRY RUN"
    lines = [
        f"Retention ({mode})",
        f"{'Category':<12} {'Keep':>8} {'Move':>8} {'Bytes':>12}",
        "-" * 43,
    ]
    counts = retention_plan.get("counts", {})
    for category in _ARCHIVE_CATEGORIES:
        row = counts.get(category, {})
        keep_val = int(row.get("keep", 0))
        move_val = int(row.get("move", 0))
        bytes_val = int(row.get("bytes", 0))
        lines.append(f"{category:<12} {keep_val:>8} {move_val:>8} {bytes_val:>12}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="retention.py",
        description="Dry-run-first retention and archiving for .solocode state.",
    )
    parser.add_argument("--root", default=Path("."), type=Path, help="Workspace root")
    parser.add_argument(
        "--keep-restore", default=3, type=int, help="Restore snapshots to keep"
    )
    parser.add_argument(
        "--keep-sessions", default=200, type=int, help="Session records to keep"
    )
    parser.add_argument(
        "--keep-usage-lines",
        default=5000,
        type=int,
        help="Tail lines to keep per usage log",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Archive aged entries (and execute --purge if requested)",
    )
    parser.add_argument(
        "--purge",
        default=None,
        type=int,
        metavar="DAYS",
        help="Delete archive entries older than DAYS (requires --apply)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    retention_plan = plan(
        args.root,
        keep_restore=args.keep_restore,
        keep_sessions=args.keep_sessions,
        keep_usage_lines=args.keep_usage_lines,
    )
    sizes = usage_size(args.root)

    applied_result: dict[str, Any] | None = None
    purged: list[str] | None = None
    purge_preview: list[str] | None = None

    if args.apply:
        applied_result = apply(args.root, retention_plan, archive=True)
        if args.purge is not None:
            purged = purge(args.root, older_than_days=args.purge)
    elif args.purge is not None:
        purge_preview = plan_purge(args.root, older_than_days=args.purge)

    if args.json:
        payload: dict[str, Any] = {
            "dry_run": not args.apply,
            "plan": retention_plan,
            "usage_size": sizes,
        }
        if applied_result is not None:
            payload["applied"] = applied_result
        if purged is not None:
            payload["purged"] = purged
        if purge_preview is not None:
            payload["purge_plan"] = purge_preview
        print(json.dumps(payload, indent=2))
        return 0

    print(format_report(retention_plan, applied=args.apply))
    if purge_preview is not None:
        print(
            f"Purge preview (older than {args.purge}d): {len(purge_preview)} entries (pass --apply to delete)"
        )
    if purged is not None:
        print(f"Purged (older than {args.purge}d): {len(purged)} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
