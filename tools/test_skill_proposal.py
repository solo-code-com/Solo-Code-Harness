#!/usr/bin/env python3
"""Tests for tools/skill_proposal.py.

Usage:
    python -m pytest tools/test_skill_proposal.py -q
"""

from __future__ import annotations

import importlib.util
import json
import subprocess  # noqa: S404 - invokes Python CLI under test
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "skill_proposal.py"

EXPECTED_MANIFEST_FIELDS = {
    "slug",
    "created",
    "expires",
    "canary",
    "rationale",
    "evidence",
    "state",
    "promoted_at",
    "last_run",
}


def _load_tool():
    spec = importlib.util.spec_from_file_location("skill_proposal_under_test", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _py_canary(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _run_cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed test argv, no shell
        [sys.executable, str(TOOL), "--root", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_propose_passing_canary_stores_skill_and_proposed_state(tmp_path: Path) -> None:
    """1. Passing canary writes SKILL.md, state='proposed', and last_run.ok=True."""
    sp = _load_tool()
    now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
    manifest = sp.propose(
        tmp_path,
        "good-skill",
        "# Good Skill\nAlways verify outputs.\n",
        _py_canary("import sys; print('canary-ok'); sys.exit(0)"),
        rationale="Prevent unverified claims",
        evidence="incident-42",
        now=now,
    )

    pdir = tmp_path / sp.PROPOSAL_DIR / "good-skill"
    assert (pdir / "SKILL.md").read_text(encoding="utf-8").startswith("# Good Skill")
    assert (pdir / "proposal.json").is_file()
    assert manifest["state"] == "proposed"
    assert manifest["last_run"]["ok"] is True
    assert "canary-ok" in manifest["last_run"]["output"]
    assert manifest["promoted_at"] is None


def test_propose_failing_canary_rejects_keeps_files_and_cli_exits_2(
    tmp_path: Path,
) -> None:
    """2. Failing canary records state='rejected', keeps files, and CLI exits 2."""
    sp = _load_tool()
    manifest = sp.propose(
        tmp_path,
        "bad-skill",
        "# Bad Skill\nBroken canary.\n",
        _py_canary("import sys; print('boom'); sys.exit(1)"),
        rationale="Testing failure",
    )

    pdir = tmp_path / sp.PROPOSAL_DIR / "bad-skill"
    assert manifest["state"] == "rejected"
    assert manifest["last_run"]["ok"] is False
    assert "boom" in manifest["last_run"]["output"]
    assert (pdir / "SKILL.md").is_file()
    assert (pdir / "proposal.json").is_file()

    skill_src = tmp_path / "incoming.md"
    skill_src.write_text("# CLI Failing Skill\nBody.\n", encoding="utf-8")
    proc = _run_cli(
        tmp_path,
        "propose",
        "--slug",
        "cli-bad-skill",
        "--skill",
        str(skill_src),
        "--canary",
        f'"{sys.executable}" -c "import sys; sys.exit(1)"',
        "--rationale",
        "Check CLI exit code 2",
        "--evidence",
        "ev-1",
        "--evidence",
        "ev-2",
        "--json",
    )
    assert proc.returncode == 2
    cli_manifest = json.loads(proc.stdout)
    assert cli_manifest["state"] == "rejected"
    assert cli_manifest["evidence"] == "ev-1\nev-2"
    cli_dir = tmp_path / sp.PROPOSAL_DIR / "cli-bad-skill"
    assert (cli_dir / "SKILL.md").is_file()
    assert (cli_dir / "proposal.json").is_file()


def test_check_flips_rejected_to_proposed_after_canary_passes(tmp_path: Path) -> None:
    """3. check() flips state from rejected to proposed once the canary passes."""
    sp = _load_tool()
    canary = _py_canary(
        "from pathlib import Path; import sys; "
        "sys.exit(0 if Path('pass.flag').exists() else 1)"
    )
    initial = sp.propose(
        tmp_path,
        "flaky-skill",
        "# Flaky Skill\nNeeds flag.\n",
        canary,
        rationale="Verify check transitions",
    )
    assert initial["state"] == "rejected"

    pdir = tmp_path / sp.PROPOSAL_DIR / "flaky-skill"
    (pdir / "pass.flag").write_text("ok\n", encoding="utf-8")

    updated = sp.check(tmp_path, "flaky-skill")
    assert updated["state"] == "proposed"
    assert updated["last_run"]["ok"] is True

    (pdir / "pass.flag").unlink()
    relapsed = sp.check(tmp_path, "flaky-skill")
    assert relapsed["state"] == "rejected"
    assert relapsed["last_run"]["ok"] is False


def test_promote_refuses_rejected_proposal_and_writes_nothing(tmp_path: Path) -> None:
    """4. promote() refuses a rejected proposal and writes nothing."""
    sp = _load_tool()
    sp.propose(
        tmp_path,
        "rejected-skill",
        "# Rejected\nDo not promote.\n",
        _py_canary("import sys; sys.exit(1)"),
        rationale="Should stay rejected",
    )
    manifest_file = tmp_path / sp.PROPOSAL_DIR / "rejected-skill" / "proposal.json"
    before_bytes = manifest_file.read_bytes()

    with pytest.raises(ValueError):
        sp.promote(tmp_path, "rejected-skill")

    assert manifest_file.read_bytes() == before_bytes
    assert not (tmp_path / ".kilo" / "skill" / "rejected-skill").exists()

    proc = _run_cli(tmp_path, "promote", "rejected-skill")
    assert proc.returncode == 2
    assert manifest_file.read_bytes() == before_bytes


def test_promote_sets_promoted_at_and_returns_destination_without_creating_it(
    tmp_path: Path,
) -> None:
    """5. promote() sets promoted_at and returns .kilo/skill/<slug> without creating it."""
    sp = _load_tool()
    t0 = datetime(2026, 11, 1, 8, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 11, 2, 9, 30, 0, tzinfo=timezone.utc)
    sp.propose(
        tmp_path,
        "ready-skill",
        "# Ready Skill\nGood to promote.\n",
        _py_canary("import sys; sys.exit(0)"),
        rationale="Ready for manual move",
        now=t0,
    )

    dest = sp.promote(tmp_path, "ready-skill", now=t1)
    assert dest == tmp_path / ".kilo" / "skill" / "ready-skill"
    assert not dest.exists()
    assert not (tmp_path / ".kilo").exists()

    saved = json.loads(
        (tmp_path / sp.PROPOSAL_DIR / "ready-skill" / "proposal.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["state"] == "promoted"
    assert saved["promoted_at"] == t1.isoformat()


def test_expired_lists_past_expires_and_excludes_fresh(tmp_path: Path) -> None:
    """6. expired() returns stale slugs and omits fresh ones for a frozen now."""
    sp = _load_tool()
    old_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    fresh_time = datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc)
    frozen_now = datetime(2026, 11, 15, 0, 0, 0, tzinfo=timezone.utc)

    sp.propose(
        tmp_path,
        "old-skill",
        "# Old\nExpired.\n",
        _py_canary("import sys; sys.exit(0)"),
        rationale="Old",
        now=old_time,
    )
    sp.propose(
        tmp_path,
        "fresh-skill",
        "# Fresh\nStill valid.\n",
        _py_canary("import sys; sys.exit(0)"),
        rationale="Fresh",
        now=fresh_time,
    )

    stale = sp.expired(tmp_path, now=frozen_now)
    assert stale == ["old-skill"]


def test_propose_refuses_existing_slug_empty_and_oversized_skill(
    tmp_path: Path,
) -> None:
    """7. propose() refuses duplicate slugs, empty skill text, and > 64 KB files."""
    sp = _load_tool()
    sp.propose(
        tmp_path,
        "dup-skill",
        "# Original\nFirst.\n",
        _py_canary("import sys; sys.exit(0)"),
        rationale="First",
    )

    with pytest.raises(FileExistsError):
        sp.propose(
            tmp_path,
            "dup-skill",
            "# Overwrite\nSecond.\n",
            _py_canary("import sys; sys.exit(0)"),
            rationale="Duplicate",
        )

    assert "First." in (
        tmp_path / sp.PROPOSAL_DIR / "dup-skill" / "SKILL.md"
    ).read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        sp.propose(
            tmp_path,
            "empty-skill",
            "   \n\t ",
            _py_canary("import sys; sys.exit(0)"),
            rationale="Empty",
        )

    oversized = "a" * (64 * 1024 + 1)
    with pytest.raises(ValueError):
        sp.propose(
            tmp_path,
            "huge-skill",
            oversized,
            _py_canary("import sys; sys.exit(0)"),
            rationale="Too large",
        )


def test_manifest_round_trips_through_json_with_expected_fields(
    tmp_path: Path,
) -> None:
    """8. Manifest round-trips through JSON with all required fields intact."""
    sp = _load_tool()
    created = datetime(2026, 11, 10, 14, 15, 0, tzinfo=timezone.utc)
    canary = _py_canary("import sys; print('hello'); sys.exit(0)")

    manifest = sp.propose(
        tmp_path,
        "roundtrip-skill",
        "# Roundtrip\nCheck JSON fields.\n",
        canary,
        rationale="Schema contract",
        evidence="trace-99",
        now=created,
    )

    manifest_path = tmp_path / sp.PROPOSAL_DIR / "roundtrip-skill" / "proposal.json"
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert set(loaded.keys()) == EXPECTED_MANIFEST_FIELDS
    assert loaded == manifest
    assert loaded["slug"] == "roundtrip-skill"
    assert loaded["created"] == created.isoformat()
    assert loaded["expires"] == (created + timedelta(days=90)).isoformat()
    assert loaded["canary"] == canary
    assert loaded["rationale"] == "Schema contract"
    assert loaded["evidence"] == "trace-99"
    assert loaded["state"] == "proposed"
    assert loaded["promoted_at"] is None
    assert set(loaded["last_run"].keys()) == {"at", "ok", "output"}
    assert loaded["last_run"]["ok"] is True


def test_new_slug_list_order_and_output_tail(tmp_path: Path) -> None:
    """Bonus coverage: new_slug normalization, newest-first list, and 2000-char tail."""
    sp = _load_tool()
    slug = sp.new_slug(
        "Café & Résumé: Very Long Skill Title Exceeding Forty Characters!"
    )
    assert len(slug) <= 40
    assert slug.isascii()
    assert not slug.endswith("-")
    assert slug == "cafe-resume-very-long-skill-title-exceed"

    t1 = datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 2, 0, 0, 0, tzinfo=timezone.utc)
    sp.propose(
        tmp_path,
        "older",
        "# Older\nBody.\n",
        _py_canary("import sys; sys.exit(0)"),
        rationale="Older",
        now=t1,
    )
    sp.propose(
        tmp_path,
        "newer",
        "# Newer\nBody.\n",
        _py_canary("import sys; print('x' * 2500); sys.exit(0)"),
        rationale="Newer",
        now=t2,
    )

    listed = sp.list_proposals(tmp_path)
    assert [item["slug"] for item in listed] == ["newer", "older"]
    assert len(listed[0]["last_run"]["output"]) == 2000
