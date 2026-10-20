#!/usr/bin/env python3
"""Tests for tools/check_text_hygiene.py.

Each test builds a throwaway git repository in tmp_path, stages files into it,
and asserts on ``check_text_hygiene.find_findings()`` / ``main()``, so the gate
is measured against real ``git ls-files`` output rather than a stubbed list.

Usage:
    python -m pytest tools/test_text_hygiene.py -q
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "check_text_hygiene.py"


def _load_tool():
    """Load check_text_hygiene.py by path: tests pass an explicit root, not the repo."""
    spec = importlib.util.spec_from_file_location("check_text_hygiene_under_test", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    """A fresh git repository rooted at a temp dir."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    return root


def _tracked(root: Path, rel: str, data: bytes) -> None:
    """Write ``data`` to ``rel`` and stage it so git ls-files reports it."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    _git(root, "add", rel)


def test_clean_repo_passes(tmp_path: Path) -> None:
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, ".kilo/skill.md", b"# skill\n")
    assert tool.find_findings(root) == []


def test_no_final_newline_reported(tmp_path: Path) -> None:
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/guide.md", b"no trailing newline")
    assert any("no-final-newline" in finding for finding in tool.find_findings(root))


def test_two_trailing_newlines_not_reported(tmp_path: Path) -> None:
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/guide.md", b"two newlines\n\n")
    assert tool.find_findings(root) == []


def test_trailing_space_reported(tmp_path: Path) -> None:
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/guide.md", b"trailing space \n")
    assert any("trailing-space" in finding for finding in tool.find_findings(root))


def test_form_feed_mid_line_not_reported(tmp_path: Path) -> None:
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/guide.md", b"ab\x0ccd\n")
    assert tool.find_findings(root) == []


def test_crlf_reported(tmp_path: Path) -> None:
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/guide.md", b"hello\r\n")
    assert any("crlf" in finding for finding in tool.find_findings(root))


def test_nul_byte_file_skipped(tmp_path: Path) -> None:
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/bin.dat", b"a\x00b\n")
    assert tool.find_findings(root) == []


def test_untracked_file_ignored(tmp_path: Path) -> None:
    tool = _load_tool()
    root = _repo(tmp_path)
    path = root / "docs" / "untracked.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"dirty  \n")
    assert tool.find_findings(root) == []


def test_state_directory_ignored(tmp_path: Path) -> None:
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, ".kilo/state/session.db", b"dirty  \n")
    assert tool.find_findings(root) == []


def test_exit_codes(tmp_path: Path) -> None:
    tool = _load_tool()
    root = _repo(tmp_path)
    assert tool.main([str(root)]) == 0
    _tracked(root, "docs/dirty.md", b"no trailing newline")
    assert tool.main([str(root)]) == 1
