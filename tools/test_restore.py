#!/usr/bin/env python3
"""Unit tests for workspace capture and restore tool."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from tools import restore  # noqa: E402


def test_capture_then_plan_is_all_unchanged(tmp_path: Path) -> None:
    """1. capture then plan is all unchanged."""
    root = tmp_path
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "b.txt").write_text("beta", encoding="utf-8")

    snap = restore.capture(root, root)
    p = restore.plan(root, snap)

    assert p["unchanged"] == ["a.txt", "b.txt"]
    assert p["restore"] == []
    assert p["create"] == []
    assert p["delete"] == []


def test_edited_file_plan_and_apply_restores_bytes_and_mode(tmp_path: Path) -> None:
    """2. After a captured file is edited, plan lists it under restore and apply puts the original bytes back, mode included."""
    root = tmp_path
    f = root / "sample.txt"
    f.write_text("original bytes", encoding="utf-8")
    orig_mode = stat.S_IMODE(f.stat().st_mode)

    snap = restore.capture(root, root)

    # Edit file content
    f.write_text("corrupted content by delegated worker", encoding="utf-8")

    p = restore.plan(root, snap)
    assert p["restore"] == ["sample.txt"]

    res = restore.apply(root, snap)
    assert res["restore"] == 1
    assert f.read_text(encoding="utf-8") == "original bytes"
    assert stat.S_IMODE(f.stat().st_mode) == orig_mode


def test_new_file_delete_behavior_with_and_without_delete_new(tmp_path: Path) -> None:
    """3. A file that did not exist at capture time appears under delete, and is only removed with delete_new=True; without it the file survives."""
    root = tmp_path
    (root / "base.txt").write_text("base file", encoding="utf-8")

    snap = restore.capture(root, root)

    new_file = root / "extra_untracked.txt"
    new_file.write_text("created by worker", encoding="utf-8")

    p = restore.plan(root, snap)
    assert p["delete"] == ["extra_untracked.txt"]

    # Without delete_new: file survives
    res_keep = restore.apply(root, snap, delete_new=False)
    assert res_keep["delete"] == 0
    assert new_file.is_file()
    assert new_file.read_text(encoding="utf-8") == "created by worker"

    # With delete_new=True: file is removed
    res_del = restore.apply(root, snap, delete_new=True)
    assert res_del["delete"] == 1
    assert not new_file.exists()


def test_deleted_file_recreated_by_apply(tmp_path: Path) -> None:
    """4. A deleted file is recreated by apply."""
    root = tmp_path
    sub_dir = root / "nested"
    sub_dir.mkdir(parents=True)
    f = sub_dir / "target.txt"
    f.write_text("must be restored", encoding="utf-8")

    snap = restore.capture(root, root)

    # Delete the file
    f.unlink()

    p = restore.plan(root, snap)
    assert p["create"] == ["nested/target.txt"]

    res = restore.apply(root, snap)
    assert res["create"] == 1
    assert f.is_file()
    assert f.read_text(encoding="utf-8") == "must be restored"


def test_symlink_captured_and_restored_as_link(tmp_path: Path, monkeypatch) -> None:
    """5. A symlink is captured as a link, not as the content of its target, and restored as a link."""
    root = tmp_path
    target = root / "target.txt"
    target.write_text("target content", encoding="utf-8")
    link = root / "link.txt"

    can_symlink = True
    try:
        link.symlink_to("target.txt")
    except OSError:
        can_symlink = False

    if can_symlink:
        snap = restore.capture(root, root)
        link_entries = [e for e in snap.entries if e.path == "link.txt"]
        assert len(link_entries) == 1
        assert link_entries[0].kind == "link"
        assert link_entries[0].value == "target.txt"

        # Remove link and verify plan & apply
        link.unlink()
        p = restore.plan(root, snap)
        assert "link.txt" in p["create"]

        restore.apply(root, snap)
        assert link.is_symlink()
        assert os.readlink(link) == "target.txt"
    else:
        # Fallback environment without Windows symlink creation privilege
        symlinks_created: list[tuple[str, str]] = []
        orig_is_symlink = Path.is_symlink
        link_is_present = True
        orig_unlink = Path.unlink

        def fake_is_symlink(self):
            if self.name == "link.txt":
                return link_is_present
            return orig_is_symlink(self)

        def fake_readlink(path):
            if str(path).endswith("link.txt"):
                return "target.txt"
            return os.readlink(path)

        def fake_symlink(src, dst):
            nonlocal link_is_present
            link_is_present = True
            symlinks_created.append((src, str(dst)))

        def fake_unlink(self, missing_ok=False):
            nonlocal link_is_present
            if self.name == "link.txt":
                link_is_present = False
            return orig_unlink(self, missing_ok=missing_ok)

        link.write_text("placeholder", encoding="utf-8")
        monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
        monkeypatch.setattr(Path, "unlink", fake_unlink)
        monkeypatch.setattr(os, "readlink", fake_readlink)
        monkeypatch.setattr(os, "symlink", fake_symlink)

        snap = restore.capture(root, root)
        link_entries = [e for e in snap.entries if e.path == "link.txt"]
        assert len(link_entries) == 1
        assert link_entries[0].kind == "link"
        assert link_entries[0].value == "target.txt"

        # Target content was not copied into restore files store
        store_target = root / restore.RESTORE_DIR / snap.run_id / "files" / "link.txt"
        assert not store_target.is_file()

        link.unlink()
        p = restore.plan(root, snap)
        assert "link.txt" in p["create"]

        restore.apply(root, snap)
        assert len(symlinks_created) == 1
        assert symlinks_created[0][0] == "target.txt"


def test_ignored_directory_skipped_and_not_in_plan(tmp_path: Path) -> None:
    """6. A directory that is_snapshot_ignored says to skip, such as node_modules, is never copied and a change inside it never shows in the plan."""
    root = tmp_path
    app_file = root / "app.py"
    app_file.write_text("valid_app = True", encoding="utf-8")

    nm_dir = root / "node_modules"
    nm_dir.mkdir(parents=True)
    nm_dep = nm_dir / "dependency.js"
    nm_dep.write_text("console.log(1)", encoding="utf-8")

    snap = restore.capture(root, root)
    snap_paths = [e.path for e in snap.entries]
    assert "app.py" in snap_paths
    assert not any("node_modules" in p for p in snap_paths)

    # Mutate files inside node_modules
    nm_dep.write_text("console.log(2)", encoding="utf-8")
    (nm_dir / "untracked.js").write_text("console.log(3)", encoding="utf-8")

    p = restore.plan(root, snap)
    for category, paths in p.items():
        assert not any("node_modules" in path for path in paths), (
            f"node_modules found in {category}"
        )


def test_max_bytes_marks_partial_and_does_not_raise(tmp_path: Path) -> None:
    """7. max_bytes small enough to stop halfway marks the snapshot partial and does not raise."""
    root = tmp_path
    (root / "a.txt").write_text("A" * 40, encoding="utf-8")
    (root / "b.txt").write_text("B" * 40, encoding="utf-8")
    (root / "c.txt").write_text("C" * 40, encoding="utf-8")

    # max_bytes = 50 allows a.txt (40 bytes), but adding b.txt (40 bytes -> 80) exceeds 50
    snap = restore.capture(root, root, max_bytes=50)

    assert snap.partial is True
    assert len(snap.entries) == 1
    assert snap.entries[0].path == "a.txt"
    assert restore.LAST_PARTIAL_NOTE is not None
    assert "Stopped at" in restore.LAST_PARTIAL_NOTE


def test_prune_keeps_newest_n_and_removes_older(tmp_path: Path) -> None:
    """8. prune keeps the newest N and removes older ones."""
    root = tmp_path
    (root / "content.txt").write_text("stable", encoding="utf-8")

    run_ids: list[str] = []
    for i in range(5):
        run_id = f"restore-20261124-10000{i}-11223{i}"
        snap = restore.capture(root, root, run_id=run_id)
        run_ids.append(snap.run_id)

    all_snaps = restore.list_snapshots(root)
    assert len(all_snaps) == 5

    removed = restore.prune(root, keep=2)
    assert len(removed) == 3
    assert set(removed) == {run_ids[0], run_ids[1], run_ids[2]}

    remaining = restore.list_snapshots(root)
    assert len(remaining) == 2
    assert remaining == [run_ids[4], run_ids[3]]


def test_cli_apply_without_yes_prints_plan_and_does_not_write(
    tmp_path: Path, capsys
) -> None:
    """CLI apply without --yes prints the plan and exits 0 without writing."""
    root = tmp_path
    f = root / "config.json"
    f.write_text('{"env": "prod"}', encoding="utf-8")

    snap = restore.capture(root, root)
    f.write_text('{"env": "corrupt"}', encoding="utf-8")

    # Run apply without --yes
    code = restore.main(["apply", snap.run_id, "--root", str(root)])
    assert code == 0

    captured = capsys.readouterr()
    assert f"Plan for snapshot {snap.run_id}:" in captured.out
    assert "restore (1):" in captured.out
    assert "config.json" in captured.out

    # File was not overwritten
    assert f.read_text(encoding="utf-8") == '{"env": "corrupt"}'

    # Run apply with --yes
    code_yes = restore.main(["apply", snap.run_id, "--root", str(root), "--yes"])
    assert code_yes == 0
    assert f.read_text(encoding="utf-8") == '{"env": "prod"}'


def test_cli_subcommands(tmp_path: Path, capsys) -> None:
    """Test capture, list, show, and prune via CLI."""
    root = tmp_path
    (root / "x.txt").write_text("hello", encoding="utf-8")

    # capture CLI
    capture_code = restore.main(
        ["capture", "--directory", str(root), "--root", str(root)]
    )
    assert capture_code == 0
    captured = capsys.readouterr()
    assert "Captured snapshot restore-" in captured.out

    snapshots = restore.list_snapshots(root)
    assert len(snapshots) == 1
    run_id = snapshots[0]

    # list CLI
    list_code = restore.main(["list", "--root", str(root)])
    assert list_code == 0
    captured_list = capsys.readouterr()
    assert run_id in captured_list.out

    # show CLI
    show_code = restore.main(["show", run_id, "--root", str(root)])
    assert show_code == 0
    captured_show = capsys.readouterr()
    assert f"Run ID: {run_id}" in captured_show.out
    assert "x.txt" in captured_show.out

    # prune CLI
    prune_code = restore.main(["prune", "--root", str(root), "--keep", "0"])
    assert prune_code == 0
    captured_prune = capsys.readouterr()
    assert "Pruned 1 snapshots:" in captured_prune.out
    assert len(restore.list_snapshots(root)) == 0


def test_capture_works_inside_an_ignored_parent(tmp_path):
    """A worktree lives under .solocode, which the snapshot ignores.

    Judging the target against the repository root called every path in it
    ignored, so capture returned nothing and the run had no undo at all.
    """
    root = tmp_path
    target = root / ".solocode" / "worktrees" / "w" / "probe"
    target.mkdir(parents=True)
    (target / "a.txt").write_text("x", encoding="utf-8")

    snap = restore.capture(root, target)
    assert len(snap.entries) == 1

    (target / "a.txt").write_text("y", encoding="utf-8")
    assert len(restore.plan(root, snap)["restore"]) == 1
