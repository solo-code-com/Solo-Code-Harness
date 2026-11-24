"""Tests for tools/post_run_checks.py."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import post_run_checks as checks


def _memory(root: Path, engine: str, name: str, chars: int) -> Path:
    path = root / engine / "memory" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * chars, encoding="utf-8")
    return path


def test_memory_over_the_block_cap_is_flagged(tmp_path):
    _memory(tmp_path, ".kilo", "MEMORY.md", checks.MEMORY_CAP_BLOCK + 1)
    issues = checks.memory_issues(tmp_path)
    assert [i["severity"] for i in issues] == ["block"]


def test_memory_between_warn_and_block_is_a_warning(tmp_path):
    _memory(tmp_path, ".copilot", "MEMORY.md", checks.MEMORY_CAP_WARN + 1)
    issues = checks.memory_issues(tmp_path)
    assert [i["severity"] for i in issues] == ["warn"]


def test_small_memory_is_silent(tmp_path):
    _memory(tmp_path, ".kilo", "MEMORY.md", 10)
    assert checks.memory_issues(tmp_path) == []


def test_archive_is_exempt_from_the_cap(tmp_path):
    _memory(tmp_path, ".kilo", "decisions-archive.md", checks.MEMORY_CAP_BLOCK * 5)
    assert checks.memory_issues(tmp_path) == []


def test_missing_scanner_is_not_an_error(tmp_path):
    assert checks.secret_issues(tmp_path) == []


def test_failed_scan_is_reported_as_blocking(tmp_path, monkeypatch):
    scanner = tmp_path / checks.SECRET_SCANNER
    scanner.parent.mkdir(parents=True)
    scanner.write_text("# fake\n", encoding="utf-8")
    monkeypatch.setattr(
        checks.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1, stdout="1 issue(s) found.", stderr=""
        ),
    )
    issues = checks.secret_issues(tmp_path)
    assert issues and issues[0]["severity"] == "block"


def test_strict_exits_three_only_on_a_blocking_finding(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(checks, "collect", lambda root: [])
    assert checks.main(["--directory", str(tmp_path), "--strict"]) == 0

    monkeypatch.setattr(
        checks,
        "collect",
        lambda root: [{"kind": "secret", "severity": "block", "path": str(root)}],
    )
    assert checks.main(["--directory", str(tmp_path), "--strict"]) == 3
    assert "[post-run]" in capsys.readouterr().out
