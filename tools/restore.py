#!/usr/bin/env python3
"""A delegated worker can corrupt a file, and git checkout is the wrong tool to fix
it: it would also discard the operator uncommitted work. This module captures
the content of every file in scope before a run, and can put the tree back byte
for byte afterwards. Capture is cheap because it uses the same ignore rules as
the workspace snapshot, and restore never touches a path outside the recorded
root. Nothing here runs automatically: deleting a worker output is a decision,
not a cleanup."""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

try:
    from tools import workspace_snapshot
except ImportError:
    import workspace_snapshot  # noqa: F401


class RestoreError(RuntimeError):
    """A snapshot could not be taken or restored."""


RESTORE_DIR = Path(".solocode") / "restore"
DEFAULT_MAX_BYTES = 100 * 1024 * 1024

LAST_PARTIAL_NOTE: str | None = None
LAST_STOPPED_PATH: str | None = None


@dataclass(frozen=True)
class Entry:
    path: str
    kind: str  # 'file' or 'link'
    value: str  # sha256 for a file, the target for a link
    size: int
    mode: str


@dataclass(frozen=True)
class Snapshot:
    run_id: str
    root: Path
    directory: Path
    created: str
    partial: bool
    entries: list[Entry]


class CountsDict(dict):
    """Dictionary of operation counts supporting both canonical and past-tense keys."""

    def __getitem__(self, key: str) -> int:
        aliases = {"restored": "restore", "created": "create", "deleted": "delete"}
        return super().__getitem__(aliases.get(key, key))

    def get(self, key: str, default: int | None = None) -> int:
        aliases = {"restored": "restore", "created": "create", "deleted": "delete"}
        val = super().get(aliases.get(key, key), default)
        return default if val is None else val


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def new_run_id(now: datetime.datetime | None = None) -> str:
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is not None:
        now = now.astimezone(datetime.timezone.utc)
    return f"restore-{now.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}"


def _verify_safe_path(root: Path, path_str: str) -> Path:
    target = (root / path_str).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError(f"Path outside recorded root: {path_str}")
    return target


def capture(
    root: Path,
    directory: Path,
    *,
    run_id: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Snapshot:
    """Copy every in-scope file under `directory` into the restore store.

    Scope is the directory plus the snapshot ignore rules, so node_modules and
    .solocode are never copied. The copy stops at max_bytes and marks the
    snapshot partial rather than filling the disk or running forever.
    """
    global LAST_PARTIAL_NOTE, LAST_STOPPED_PATH
    LAST_PARTIAL_NOTE = None
    LAST_STOPPED_PATH = None

    root_path = root if isinstance(root, pathlib.Path) else Path(root)
    dir_path = directory if isinstance(directory, pathlib.Path) else Path(directory)

    abs_root = root_path.resolve()
    abs_dir = (
        dir_path.resolve()
        if dir_path.is_absolute()
        else (root_path / dir_path).resolve()
    )

    if not abs_dir.is_relative_to(abs_root):
        raise ValueError(f"Directory {directory} is outside root {root}")
    if not abs_dir.exists():
        raise FileNotFoundError(f"Directory not found: {abs_dir}")

    if run_id is None:
        run_id = new_run_id()

    store = abs_root / RESTORE_DIR / run_id
    files_dir = store / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    entries: list[Entry] = []
    total_bytes = 0
    partial = False

    for dirpath, dirnames, filenames in os.walk(abs_dir, followlinks=False):
        pruned_dirnames: list[str] = []
        for d in sorted(dirnames):
            sub_dir = Path(dirpath) / d
            rel_sub = sub_dir.relative_to(abs_root)
            # Judged against the captured directory, not the repository root: a
            # git worktree lives under .solocode, so a root-relative test would
            # call every path in it ignored and capture nothing.
            if workspace_snapshot.is_snapshot_ignored(sub_dir.relative_to(abs_dir)):
                continue
            if workspace_snapshot.is_reparse_point(sub_dir):
                rel_str = rel_sub.as_posix()
                st = sub_dir.lstat()
                entries.append(
                    Entry(
                        path=rel_str,
                        kind="link",
                        value=workspace_snapshot.read_reparse_target(sub_dir),
                        size=st.st_size,
                        mode=f"{stat.S_IMODE(st.st_mode):o}",
                    )
                )
                continue
            pruned_dirnames.append(d)
        dirnames[:] = pruned_dirnames

        for f in sorted(filenames):
            item_path = Path(dirpath) / f
            rel_item = item_path.relative_to(abs_root)
            if workspace_snapshot.is_snapshot_ignored(item_path.relative_to(abs_dir)):
                continue
            rel_str = rel_item.as_posix()
            if workspace_snapshot.is_reparse_point(item_path):
                st = item_path.lstat()
                entries.append(
                    Entry(
                        path=rel_str,
                        kind="link",
                        value=workspace_snapshot.read_reparse_target(item_path),
                        size=st.st_size,
                        mode=f"{stat.S_IMODE(st.st_mode):o}",
                    )
                )
            elif item_path.is_file():
                st = item_path.stat()
                file_size = st.st_size
                if total_bytes + file_size > max_bytes:
                    partial = True
                    LAST_STOPPED_PATH = rel_str
                    LAST_PARTIAL_NOTE = f"Stopped at {rel_str}: adding {file_size} bytes would exceed max_bytes ({max_bytes})"
                    break
                dest = files_dir / rel_item
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item_path, dest)
                try:  # noqa: SIM105
                    shutil.copymode(item_path, dest)
                except OSError:
                    pass
                total_bytes += file_size
                entries.append(
                    Entry(
                        path=rel_str,
                        kind="file",
                        value=_sha256(item_path),
                        size=file_size,
                        mode=f"{stat.S_IMODE(st.st_mode):o}",
                    )
                )
        if partial:
            break

    created_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if not entries:
        raise RestoreError(
            f"nothing to capture under {directory}: it is empty, or it sits "
            "inside a path the workspace snapshot ignores"
        )

    snapshot = Snapshot(
        run_id=run_id,
        root=root_path,
        directory=dir_path,
        created=created_str,
        partial=partial,
        entries=entries,
    )

    manifest_data = {
        "run_id": snapshot.run_id,
        "root": str(snapshot.root),
        "directory": str(snapshot.directory),
        "created": snapshot.created,
        "partial": snapshot.partial,
        "stopped_at": LAST_STOPPED_PATH,
        "entries": [dataclasses.asdict(e) for e in snapshot.entries],
    }

    manifest_file = store / "manifest.json"
    with manifest_file.open("w", encoding="utf-8") as handle:
        json.dump(manifest_data, handle, indent=2)

    return snapshot


def load(root: Path, run_id: str) -> Snapshot:
    root_path = root if isinstance(root, pathlib.Path) else Path(root)
    manifest_file = root_path / RESTORE_DIR / run_id / "manifest.json"
    if not manifest_file.is_file():
        raise FileNotFoundError(f"Snapshot manifest not found: {manifest_file}")
    with manifest_file.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    entries = [Entry(**e) for e in data["entries"]]
    return Snapshot(
        run_id=data["run_id"],
        root=Path(data.get("root", str(root_path))),
        directory=Path(data["directory"]),
        created=data["created"],
        partial=bool(data["partial"]),
        entries=entries,
    )


def list_snapshots(root: Path) -> list[str]:
    root_path = root if isinstance(root, pathlib.Path) else Path(root)
    restore_dir = root_path / RESTORE_DIR
    if not restore_dir.is_dir():
        return []
    snapshots: list[tuple[str, str]] = []
    for entry in restore_dir.iterdir():
        if not entry.is_dir():
            continue
        manifest_file = entry / "manifest.json"
        if not manifest_file.is_file():
            continue
        run_id = entry.name
        created_time = ""
        try:
            with manifest_file.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
                created_time = data.get("created", "")
        except (OSError, ValueError):
            # An unreadable manifest still names a directory worth listing; the
            # run id sorts it instead of its timestamp.
            created_time = ""
        sort_key = created_time or run_id
        snapshots.append((sort_key, run_id))
    snapshots.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [run_id for _, run_id in snapshots]


def plan(root: Path, snapshot: Snapshot) -> dict[str, list[str]]:
    """What apply would do, without writing.

    Keys: 'restore' (differs from the capture), 'create' (recorded but missing
    now), 'delete' (exists now, was not in the capture), 'unchanged'.
    """
    root_path = root if isinstance(root, pathlib.Path) else Path(root)
    abs_root = root_path.resolve()

    if snapshot.directory.is_absolute():
        abs_walk_dir = snapshot.directory.resolve()
    else:
        abs_walk_dir = (root_path / snapshot.directory).resolve()

    if not abs_walk_dir.is_relative_to(abs_root):
        raise ValueError(f"Directory {snapshot.directory} is outside root {root}")

    entries_by_path: dict[str, Entry] = {e.path: e for e in snapshot.entries}
    found_paths: set[str] = set()

    result: dict[str, list[str]] = {
        "restore": [],
        "create": [],
        "delete": [],
        "unchanged": [],
    }

    if abs_walk_dir.exists():
        for dirpath, dirnames, filenames in os.walk(abs_walk_dir, followlinks=False):
            pruned_dirnames: list[str] = []
            for d in sorted(dirnames):
                sub_dir = Path(dirpath) / d
                rel_sub = sub_dir.relative_to(abs_root)
                if workspace_snapshot.is_snapshot_ignored(
                    sub_dir.relative_to(abs_walk_dir)
                ):
                    continue
                if workspace_snapshot.is_reparse_point(sub_dir):
                    rel_str = rel_sub.as_posix()
                    found_paths.add(rel_str)
                    if rel_str in entries_by_path:
                        entry = entries_by_path[rel_str]
                        if (
                            entry.kind == "link"
                            and workspace_snapshot.read_reparse_target(sub_dir)
                            == entry.value
                        ):
                            result["unchanged"].append(rel_str)
                        else:
                            result["restore"].append(rel_str)
                    else:
                        result["delete"].append(rel_str)
                    continue
                pruned_dirnames.append(d)
            dirnames[:] = pruned_dirnames

            for f in sorted(filenames):
                item_path = Path(dirpath) / f
                rel_item = item_path.relative_to(abs_root)
                if workspace_snapshot.is_snapshot_ignored(
                    item_path.relative_to(abs_walk_dir)
                ):
                    continue
                rel_str = rel_item.as_posix()
                found_paths.add(rel_str)
                if rel_str in entries_by_path:
                    entry = entries_by_path[rel_str]
                    if workspace_snapshot.is_reparse_point(item_path):
                        if (
                            entry.kind == "link"
                            and workspace_snapshot.read_reparse_target(item_path)
                            == entry.value
                        ):
                            result["unchanged"].append(rel_str)
                        else:
                            result["restore"].append(rel_str)
                    elif item_path.is_file():
                        if entry.kind == "file":
                            current_hash = _sha256(item_path)
                            current_mode = f"{stat.S_IMODE(item_path.stat().st_mode):o}"
                            modes_match = int(current_mode, 8) == int(entry.mode, 8)
                            if current_hash == entry.value and modes_match:
                                result["unchanged"].append(rel_str)
                            else:
                                result["restore"].append(rel_str)
                        else:
                            result["restore"].append(rel_str)
                else:
                    result["delete"].append(rel_str)

    for entry in snapshot.entries:
        if entry.path not in found_paths:
            result["create"].append(entry.path)

    for key in result:
        result[key].sort()

    return result


def _has_reparse_component(root: Path, path_str: str) -> bool:
    rel = Path(path_str)
    if rel.is_absolute() or ".." in rel.parts:
        return False
    cur = root
    for part in rel.parts:
        cur = cur / part
        if os.path.lexists(cur) and workspace_snapshot.is_reparse_point(cur):
            return True
    return False


def apply(
    root: Path,
    snapshot: Snapshot,
    *,
    delete_new: bool = False,
) -> dict[str, int]:
    """Put the captured content back.

    Without delete_new, files the worker created are reported but kept: deleting
    is destructive and the operator opts into it by name.
    """
    root_path = root if isinstance(root, pathlib.Path) else Path(root)
    abs_root = root_path.resolve()

    store_files = abs_root / RESTORE_DIR / snapshot.run_id / "files"
    if (
        not store_files.is_dir()
        and (snapshot.root / RESTORE_DIR / snapshot.run_id / "files").is_dir()
    ):
        store_files = snapshot.root / RESTORE_DIR / snapshot.run_id / "files"

    planned = plan(root_path, snapshot)
    entries_by_path: dict[str, Entry] = {e.path: e for e in snapshot.entries}

    write_ops = [("restore", path_str) for path_str in planned["restore"]] + [
        ("create", path_str) for path_str in planned["create"]
    ]
    for _, path_str in write_ops:
        if not _has_reparse_component(root_path, path_str):
            _verify_safe_path(root_path, path_str)
    if delete_new:
        for path_str in planned["delete"]:
            if not _has_reparse_component(root_path, path_str):
                _verify_safe_path(root_path, path_str)

    restored_count = 0
    created_count = 0
    skipped_count = 0

    for op_kind, path_str in write_ops:
        entry = entries_by_path[path_str]
        target = root_path / path_str
        if _has_reparse_component(root_path, path_str):
            print(f"[restore] refusing to write through a reparse point: {path_str}")
            skipped_count += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.kind == "file":
            source = store_files / path_str
            if not source.is_file():
                raise FileNotFoundError(f"Restore store file missing: {source}")
            if target.is_symlink() or target.is_file():
                target.unlink(missing_ok=True)
            shutil.copyfile(source, target)
            try:  # noqa: SIM105
                os.chmod(target, int(entry.mode, 8))
            except OSError:
                pass
        elif entry.kind == "link":
            if target.is_symlink() or target.is_file():
                target.unlink(missing_ok=True)
            os.symlink(entry.value, target)
        if op_kind == "restore":
            restored_count += 1
        else:
            created_count += 1

    deleted_count = 0
    if delete_new:
        for path_str in planned["delete"]:
            if _has_reparse_component(root_path, path_str):
                continue
            target = root_path / path_str
            if target.is_symlink() or target.is_file():
                target.unlink(missing_ok=True)
                deleted_count += 1

    return CountsDict(
        restore=restored_count,
        create=created_count,
        delete=deleted_count,
        unchanged=len(planned["unchanged"]),
        skipped=skipped_count,
    )


def prune(root: Path, keep: int = 5) -> list[str]:
    """Delete the oldest snapshots beyond `keep`; return what was removed. This is
    the only function here that deletes, and it only deletes inside RESTORE_DIR.
    """
    root_path = root if isinstance(root, pathlib.Path) else Path(root)
    restore_base = (root_path / RESTORE_DIR).resolve()
    if not restore_base.is_dir():
        return []
    snapshots = list_snapshots(root_path)
    if len(snapshots) <= keep:
        return []
    to_remove = snapshots[keep:]
    removed: list[str] = []
    for run_id in to_remove:
        target = (restore_base / run_id).resolve()
        if not target.is_relative_to(restore_base):
            continue
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(run_id)
    return removed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="restore.py",
        description="Workspace capture and byte-for-byte restore.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    p_capture = subparsers.add_parser("capture", help="Capture files under directory")
    p_capture.add_argument(
        "--directory", required=True, type=Path, help="Directory to capture"
    )
    p_capture.add_argument(
        "--root", default=Path("."), type=Path, help="Workspace root"
    )
    p_capture.add_argument(
        "--max-bytes",
        default=DEFAULT_MAX_BYTES,
        type=int,
        help="Maximum bytes to capture",
    )

    p_list = subparsers.add_parser("list", help="List captured snapshots")
    p_list.add_argument("--root", default=Path("."), type=Path, help="Workspace root")

    p_show = subparsers.add_parser("show", help="Show snapshot details")
    p_show.add_argument("run_id", help="Snapshot run ID")
    p_show.add_argument("--root", default=Path("."), type=Path, help="Workspace root")

    p_apply = subparsers.add_parser("apply", help="Apply snapshot to workspace")
    p_apply.add_argument("run_id", help="Snapshot run ID")
    p_apply.add_argument("--root", default=Path("."), type=Path, help="Workspace root")
    p_apply.add_argument("--yes", action="store_true", help="Execute restore")
    p_apply.add_argument(
        "--delete-new",
        action="store_true",
        help="Delete new files created after capture",
    )

    p_prune = subparsers.add_parser("prune", help="Prune old snapshots")
    p_prune.add_argument("--root", default=Path("."), type=Path, help="Workspace root")
    p_prune.add_argument(
        "--keep", default=5, type=int, help="Number of snapshots to keep"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "capture":
        snap = capture(args.root, args.directory, max_bytes=args.max_bytes)
        print(
            f"Captured snapshot {snap.run_id} ({len(snap.entries)} entries, partial={snap.partial})"
        )
        return 0
    elif args.subcommand == "list":
        for snap_id in list_snapshots(args.root):
            print(snap_id)
        return 0
    elif args.subcommand == "show":
        snap = load(args.root, args.run_id)
        print(f"Run ID: {snap.run_id}")
        print(f"Root: {snap.root}")
        print(f"Directory: {snap.directory}")
        print(f"Created: {snap.created}")
        print(f"Partial: {snap.partial}")
        print(f"Entries ({len(snap.entries)}):")
        for entry in snap.entries:
            print(
                f"  [{entry.kind}] {entry.path} ({entry.size} bytes, mode {entry.mode})"
            )
        return 0
    elif args.subcommand == "apply":
        snap = load(args.root, args.run_id)
        p = plan(args.root, snap)
        if not args.yes:
            print(f"Plan for snapshot {snap.run_id}:")
            for key in ("restore", "create", "delete", "unchanged"):
                print(f"  {key} ({len(p[key])}):")
                for item in p[key]:
                    print(f"    {item}")
            return 0
        counts = apply(args.root, snap, delete_new=args.delete_new)
        print(f"Applied snapshot {snap.run_id}:")
        for key, val in counts.items():
            print(f"  {key}: {val}")
        return 0
    elif args.subcommand == "prune":
        removed = prune(args.root, keep=args.keep)
        print(f"Pruned {len(removed)} snapshots:")
        for r in removed:
            print(f"  {r}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
