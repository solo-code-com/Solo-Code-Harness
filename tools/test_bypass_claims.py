#!/usr/bin/env python3
"""Tests for tools/check_bypass_claims.py.

Each test builds a throwaway git repository in tmp_path, stages files into it,
and asserts on ``check_bypass_claims.find_findings()`` / ``main()`` against real
``git ls-files`` output, matching the house pattern of tools/test_text_hygiene.py.

Usage:
    python -m pytest tools/test_bypass_claims.py -q
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "check_bypass_claims.py"


def _load_tool():
    """Load check_bypass_claims.py by path: tests pass an explicit root, not the repo."""
    spec = importlib.util.spec_from_file_location(
        "check_bypass_claims_under_test", TOOL
    )
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


def _tracked(root: Path, rel: str, data: str) -> None:
    """Write ``data`` to ``rel`` and stage it so git ls-files reports it."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8", newline="\n")
    _git(root, "add", rel)


# --- positive cases -----------------------------------------------------------


def test_unread_bypass_before_equals_reported(tmp_path: Path) -> None:
    """The real defect: a doc names FOO_BYPASS=1 and nothing reads it."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/notes.md", "To skip the gate, set FOO_BYPASS=1.\n")
    findings = tool.find_findings(root)
    assert findings is not None
    assert len(findings) == 1
    assert "FOO_BYPASS" in findings[0]
    assert "nothing reads it" in findings[0]


def test_unread_override_in_backticks_reported(tmp_path: Path) -> None:
    """A backticked BAR_OVERRIDE with no reader is reported too."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/notes.md", "Set `BAR_OVERRIDE` to force the policy.\n")
    findings = tool.find_findings(root)
    assert findings is not None
    assert any("BAR_OVERRIDE" in finding for finding in findings)


# --- negative cases: the four false-positive shapes ---------------------------


def test_describes_the_defect_is_not_reported(tmp_path: Path) -> None:
    """Shape 1: a line describing the defect (correcting the record) must not fire."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(
        root,
        "SPEC.md",
        "| check_bypass_claims | hatch duoc doc phai duoc doc | "
        "gate-guard.js print `FOO_BYPASS=1` but the hatch no longer exists |\n",
    )
    assert tool.find_findings(root) == []


def test_verb_in_different_clause_is_not_reported(tmp_path: Path) -> None:
    """Shape 2: the token belongs to a different clause, not an escape-hatch claim."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(
        root,
        "README.md",
        "The enforcement note mentions FOO_BYPASS only in passing, next to "
        "pre_compact.py.\n",
    )
    assert tool.find_findings(root) == []


def test_vietnamese_negation_is_not_reported(tmp_path: Path) -> None:
    """Shape 3: a Vietnamese negation -- the seam is not yet enforced."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/notes.md", "The seam `FOO_BYPASS` chua duoc enforce.\n")
    assert tool.find_findings(root) == []


def test_indirect_python_read_is_not_reported(tmp_path: Path) -> None:
    """Shape 4: a read via os.environ.get must count even though it is not a bracket."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/notes.md", "Set FOO_BYPASS=1 to skip the gate.\n")
    _tracked(root, "src/hook.py", "import os\nos.environ.get('FOO_BYPASS')\n")
    assert tool.find_findings(root) == []


# --- remaining negative cases -------------------------------------------------


def test_read_through_process_env_is_not_reported(tmp_path: Path) -> None:
    """A name read through process.env.NAME is genuinely read, so no finding."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/notes.md", "Set FOO_BYPASS=1 to skip the gate.\n")
    _tracked(root, "src/gate.js", "process.env.FOO_BYPASS;\n")
    assert tool.find_findings(root) == []


def test_removal_marker_line_is_not_reported(tmp_path: Path) -> None:
    """A line that carries a removal marker opts out."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/notes.md", "`FOO_BYPASS=1` (removed).\n")
    assert tool.find_findings(root) == []


def test_lowercase_token_is_not_reported(tmp_path: Path) -> None:
    """Only SCREAMING_SNAKE names count; lowercase is not an escape-hatch claim."""
    tool = _load_tool()
    root = _repo(tmp_path)
    _tracked(root, "docs/notes.md", "set foo_bypass=1 to skip the gate.\n")
    assert tool.find_findings(root) == []


def test_untracked_file_is_ignored(tmp_path: Path) -> None:
    """Only git-tracked markdown counts; untracked scratch is invisible."""
    tool = _load_tool()
    root = _repo(tmp_path)
    path = root / "docs" / "untracked.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("set FOO_BYPASS=1 to skip.\n", encoding="utf-8", newline="\n")
    assert tool.find_findings(root) == []


def test_exit_codes(tmp_path: Path) -> None:
    """Clean repo exits 0; a finding exits 1."""
    tool = _load_tool()
    root = _repo(tmp_path)
    assert tool.main([str(root)]) == 0
    _tracked(root, "docs/notes.md", "set FOO_BYPASS=1 to skip.\n")
    assert tool.main([str(root)]) == 1
