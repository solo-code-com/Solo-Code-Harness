#!/usr/bin/env python3
"""
Codex Guard Tests
=================
Runs tools/codex_guard.py as a subprocess and asserts the block/allow decision
(exit code 2 = block, 0 = allow).

Codex CLI has no project hook API, so codex_guard.py is the only automated
preflight between a Codex-issued command and the filesystem. The predicates it
delegates to (.claude/hooks/guard.py) are covered by test_claude_guard.py; what
these tests pin is the wiring in codex_guard itself -- that each input mode
reaches the right predicate and that a block is reported as exit 2.

Usage:
    python -m pytest tools/test_codex_guard.py -v
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "tools" / "codex_guard.py"

# Two secret shapes, because guard.py's patterns treat them differently.
# `generic_api_key` only fires on a QUOTED value, so a shell-style bare
# `KEY=value` trips nothing unless the token carries a recognised prefix
# (anthropic_key / openai_project_key). See SECRET_PATTERNS in guard.py.
# These are fixtures for the detector under test, not credentials.
_ANTHROPIC_SECRET = "sk-ant-abcdefghijklmnopqrstuvwxyz0123"  # noqa: S105
_QUOTED_SECRET = "sk-abcdefghijklmnopqrstuvwxyz123456"  # noqa: S105


def _run(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke codex_guard, returning the completed process."""
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, str(GUARD), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=merged,
    )


def _norm(path: Path) -> str:
    """Match the lock key shared_state actually stores, on every platform."""
    from tools.shared_state import _normalise_lock_path

    return _normalise_lock_path(str(path))


def _active_lock_for(db: Path, path: Path) -> dict | None:
    """Return the active lock row for ``path`` in the isolated DB, if any."""
    from tools.shared_state import SharedState

    with SharedState(db) as state:
        for lock in state.get_active_locks():
            if lock["path"] == _norm(path):
                return lock
    return None


@pytest.fixture()
def isolated_db(tmp_path: Path) -> Path:
    """A throwaway shared-state DB so lock tests never touch production state."""
    db = tmp_path / "state" / "shared-state.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


@pytest.mark.parametrize(
    "command",
    ["git status", "python -m pytest tools/ -q", "ls tools"],
)
def test_benign_command_is_allowed(command: str) -> None:
    """Ordinary repo commands must pass, or the gate gets disabled."""
    result = _run("--command", command)
    assert result.returncode == 0, f"unexpectedly blocked: {result.stderr}"


@pytest.mark.parametrize(
    "command",
    ["rm -rf /", "rm -rf /tmp/anything", "DROP TABLE users"],
)
def test_destructive_command_is_blocked(command: str) -> None:
    """Destructive commands exit 2 so a wrapper can refuse to run them."""
    result = _run("--command", command)
    assert result.returncode == 2, f"not blocked: {command!r}"
    assert "BLOCKED" in result.stderr


def test_secret_in_command_is_blocked() -> None:
    """A credential must be caught even in a bare shell `KEY=value` form."""
    result = _run("--command", f"export ANTHROPIC_API_KEY={_ANTHROPIC_SECRET}")
    assert result.returncode == 2
    assert "BLOCKED" in result.stderr


def test_secret_in_content_is_blocked() -> None:
    """Content mode is the file-write path; a secret there must block."""
    result = _run("--content", f'api_key = "{_QUOTED_SECRET}"')
    assert result.returncode == 2
    assert "BLOCKED" in result.stderr


def test_benign_content_is_allowed() -> None:
    """Ordinary source text must pass content mode."""
    result = _run("--content", "def add(a, b):\n    return a + b\n")
    assert result.returncode == 0, f"unexpectedly blocked: {result.stderr}"


def test_command_and_content_are_mutually_exclusive() -> None:
    """Exactly one input mode must be supplied."""
    result = _run("--command", "git status", "--content", "x")
    assert result.returncode != 0
    # argparse exits 2 for usage errors; the security block also uses 2, so
    # assert on the message that distinguishes them.
    assert "BLOCKED" not in result.stderr


def test_requires_an_input_mode() -> None:
    """Calling with neither mode is a usage error, not a silent allow."""
    result = _run()
    assert result.returncode != 0
    assert "BLOCKED" not in result.stderr


def test_write_benign_content_creates_file(tmp_path: Path) -> None:
    """--write must write verified content to the target path."""
    target = tmp_path / "subdir" / "test_out.txt"
    content = "hello from verified codex write"
    result = _run("--content", content, "--path", str(target), "--write")
    assert result.returncode == 0, f"unexpectedly blocked: {result.stderr}"
    assert "WROTE:" in result.stdout
    assert target.exists()
    assert target.read_text(encoding="utf-8") == content


def test_write_secret_in_content_is_blocked(tmp_path: Path) -> None:
    """--write must not write to disk when secrets are detected."""
    target = tmp_path / "secret.txt"
    result = _run(
        "--content", f'api_key = "{_QUOTED_SECRET}"', "--path", str(target), "--write"
    )
    assert result.returncode == 2
    assert "BLOCKED" in result.stderr
    assert not target.exists()


def test_write_without_path_is_rejected() -> None:
    """--write requires --path."""
    result = _run("--content", "benign", "--write")
    assert result.returncode == 2
    assert "ERROR: --write requires --path" in result.stderr


def test_write_releases_lock_on_success(tmp_path: Path, isolated_db: Path) -> None:
    """A successful verified write must not leave its file lock behind."""
    target = tmp_path / "subdir" / "out.txt"
    env = {"CODEX_GUARD_STATE_DB": str(isolated_db)}
    result = _run("--content", "hello", "--path", str(target), "--write", env=env)
    assert result.returncode == 0, f"unexpectedly blocked: {result.stderr}"
    assert _active_lock_for(isolated_db, target) is None


def test_other_engine_lock_blocks_and_is_preserved(
    tmp_path: Path, isolated_db: Path
) -> None:
    """Another engine's lock blocks the write and must not be released by us."""
    from tools.shared_state import SharedState

    target = tmp_path / "subdir" / "out.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    with SharedState(isolated_db) as state:
        assert state.acquire_lock(
            str(target), engine="kilo", model="kilo-cli", session_id="other"
        )
    env = {"CODEX_GUARD_STATE_DB": str(isolated_db)}
    result = _run("--content", "hello", "--path", str(target), "--write", env=env)
    assert result.returncode == 2
    assert "BLOCKED" in result.stderr
    assert not target.exists()
    lock = _active_lock_for(isolated_db, target)
    assert lock is not None
    assert lock["locked_by"]["engine"] == "kilo"


def test_blocked_secret_does_not_acquire_lock(
    tmp_path: Path, isolated_db: Path
) -> None:
    """A secret block happens before the lock is taken, so no lock is left."""
    target = tmp_path / "secret.txt"
    env = {"CODEX_GUARD_STATE_DB": str(isolated_db)}
    result = _run(
        "--content",
        f'api_key = "{_QUOTED_SECRET}"',
        "--path",
        str(target),
        "--write",
        env=env,
    )
    assert result.returncode == 2
    assert "BLOCKED" in result.stderr
    assert not target.exists()
    assert _active_lock_for(isolated_db, target) is None


def test_failed_write_releases_lock(tmp_path: Path, isolated_db: Path) -> None:
    """If the write itself fails, the finally block must still release the lock."""
    target = tmp_path / "adir"
    target.mkdir()  # writing a file onto a directory fails after the lock is taken
    env = {"CODEX_GUARD_STATE_DB": str(isolated_db)}
    result = _run("--content", "hello", "--path", str(target), "--write", env=env)
    assert result.returncode != 0
    assert _active_lock_for(isolated_db, target) is None


def test_overlapping_invocations_use_distinct_lock_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared Codex session ID must not let invocations release each other's locks."""
    from tools import codex_guard

    owner_ids: list[str] = []

    class FakeState:
        def acquire_lock(self, _path: str, **kwargs: str) -> bool:
            owner_ids.append(kwargs["session_id"])
            return True

        def release_lock(self, _path: str, **kwargs: str) -> None:
            assert kwargs["session_id"] == owner_ids[-1]

        def close(self) -> None:
            pass

    monkeypatch.setattr(codex_guard, "_state", FakeState)
    monkeypatch.setenv("CODEX_SESSION_ID", "same-codex-session")
    for _ in range(2):
        monkeypatch.setattr(
            sys, "argv", [str(GUARD), "--content", "hello", "--path", "same.txt"]
        )
        assert codex_guard.main() == 0

    assert len(owner_ids) == 2
    assert len(set(owner_ids)) == 2
