#!/usr/bin/env python3
"""skills_engine.py - publish the .kilo/skill library to a vendor-neutral tree.

Why: the library used to ship only as `.claude/skills/`, an engine-shaped path
that disappears when that engine does. `.agents/skills/` is the location
OpenCode reads and no single CLI owns.

Text files are normalised to exactly one trailing newline: the repository''s own
end-of-file hook rejects anything else, so a verbatim copy would turn every
regeneration into a hook failure.
"""

from __future__ import annotations

import shutil
from pathlib import Path

TEXT_SUFFIXES = frozenset(
    {".md", ".txt", ".py", ".json", ".yml", ".yaml", ".sh", ".ps1", ".js", ".ts"}
)


def _normalized(rel: Path, data: bytes) -> bytes:
    """Bytes as the repository''s hooks expect them."""
    if rel.suffix.lower() in TEXT_SUFFIXES:
        return data.rstrip(b"\n") + b"\n"
    return data


def _copy_tree_if_changed(src: Path, dst: Path) -> None:
    """Synchronize a directory without rewriting an identical tree."""
    source_paths = {path.relative_to(src) for path in src.rglob("*")}
    source_files = {
        path.relative_to(src): _normalized(path.relative_to(src), path.read_bytes())
        for path in src.rglob("*")
        if path.is_file()
    }
    if dst.is_dir():
        destination_paths = {path.relative_to(dst) for path in dst.rglob("*")}
        destination_files = {
            path.relative_to(dst): path.read_bytes()
            for path in dst.rglob("*")
            if path.is_file()
        }
        if destination_paths == source_paths and destination_files == source_files:
            return
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    dst.mkdir(parents=True, exist_ok=True)
    for rel, data in source_files.items():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def generate(
    kilo_root: Path, dst_root: Path, *, skip_names: set[str] | None = None
) -> int:
    """Copy .kilo/skill/* into ``dst_root/skills`` and report the count."""
    src_dir = kilo_root / "skill"
    dst_dir = dst_root / "skills"
    if not src_dir.is_dir():
        print(f"[ERROR] Source skill directory not found: {src_dir}")
        return 1
    skip_names = skip_names or set()
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for skill_dir in sorted(p for p in src_dir.iterdir() if p.is_dir()):
        name = skill_dir.name
        if name in skip_names:
            print(f"  [SKIP] skills/{name} (skip list)")
            continue
        if not (skill_dir / "SKILL.md").is_file():
            print(f"  [SKIP] skills/{name} (no SKILL.md)")
            continue
        _copy_tree_if_changed(skill_dir, dst_dir / name)
        copied += 1
    print(f"Skills generated at {dst_dir}: {copied}")
    return 0
