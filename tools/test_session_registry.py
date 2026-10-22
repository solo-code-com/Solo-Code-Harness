#!/usr/bin/env python3
"""Tests for tools/session_registry.py — the child-session registry (gap G7).

Every test points the registry at tmp_path, so nothing touches `.solocode/`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "session_registry.py"


def _load():
    spec = importlib.util.spec_from_file_location("session_registry_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _dead_pid() -> int:
    """A pid that certainly existed and certainly exited."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    return int(proc.pid)


def test_record_start_writes_a_running_record(tmp_path):
    mod = _load()
    run_id = mod.record_start(
        "opencode",
        model="commandcode/deepseek-v4.1-flash",
        argv=["opencode", "run"],
        log_path=tmp_path / "run.log",
        session_dir=tmp_path,
    )
    payload = json.loads((tmp_path / f"{run_id}.json").read_text(encoding="utf-8"))
    assert payload["run_id"] == run_id
    assert payload["worker"] == "opencode"
    assert payload["model"] == "commandcode/deepseek-v4.1-flash"
    assert payload["argv"] == ["opencode", "run"]
    assert payload["pid"] == os.getpid()
    assert payload["state"] == "running"
    assert payload["finished_at"] is None

    sessions = mod.list_sessions(session_dir=tmp_path)
    assert [s["run_id"] for s in sessions] == [run_id]
    assert sessions[0]["state"] == "running"


def test_record_finish_settles_the_record(tmp_path):
    mod = _load()
    run_id = mod.record_start("kilo", session_dir=tmp_path)
    assert mod.record_finish(run_id, 0, session_dir=tmp_path) is True

    settled = mod.list_sessions(session_dir=tmp_path)[0]
    assert settled["state"] == "exited"
    assert settled["exit_code"] == 0
    assert settled["finished_at"] is not None
    assert settled["worker"] == "kilo"  # finish must not clobber the rest


def test_record_finish_on_an_unknown_run_id_is_false(tmp_path):
    mod = _load()
    assert mod.record_finish("nosuchrun", 0, session_dir=tmp_path) is False


def test_record_finish_refuses_to_overwrite_a_settled_state(tmp_path):
    """The controller's verdict outranks the delegate that finishes afterwards."""
    mod = _load()
    run_id = mod.record_start("opencode", session_dir=tmp_path)
    assert mod.record_finish(run_id, None, state="interrupted", session_dir=tmp_path)

    assert mod.record_finish(run_id, 0, session_dir=tmp_path) is False
    settled = mod.list_sessions(session_dir=tmp_path)[0]
    assert settled["state"] == "interrupted"
    assert settled["exit_code"] is None  # the losing writer changed nothing


def test_record_finish_can_be_forced_when_the_caller_means_it(tmp_path):
    mod = _load()
    run_id = mod.record_start("opencode", session_dir=tmp_path)
    mod.record_finish(run_id, None, state="interrupted", session_dir=tmp_path)
    assert (
        mod.record_finish(
            run_id, 7, state="exited", only_if_running=False, session_dir=tmp_path
        )
        is True
    )
    assert mod.list_sessions(session_dir=tmp_path)[0]["exit_code"] == 7


def test_a_dead_pid_reports_stale_not_running(tmp_path):
    mod = _load()
    run_id = mod.record_start("codex", pid=_dead_pid(), session_dir=tmp_path)
    session = mod.list_sessions(session_dir=tmp_path)[0]
    assert session["run_id"] == run_id
    assert session["state"] == "stale"


def test_an_abandoned_interrupting_record_also_settles_as_stale(tmp_path):
    """A controller that dies mid-interrupt must not leave a live-looking record."""
    mod = _load()
    run_id = mod.record_start("opencode", pid=_dead_pid(), session_dir=tmp_path)
    mod.record_finish(run_id, None, state="interrupting", session_dir=tmp_path)
    assert mod.list_sessions(session_dir=tmp_path)[0]["state"] == "stale"


# ── retention ────────────────────────────────────────────────────────────────


def _old_record(mod, directory, run_id, stamp):
    """An already-settled record. It says `exited`, so the cap cannot depend on
    whether some fixed "fake" pid happens to be alive on the machine running it."""
    mod._write_atomically(
        directory / f"{run_id}.json",
        {
            "run_id": run_id,
            "worker": "fake",
            "pid": 4242,
            "state": "exited",
            "started_at": stamp,
            "finished_at": stamp,
        },
    )


def test_a_new_record_prunes_the_oldest_past_the_cap(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "MAX_SETTLED_RECORDS", 3)
    for index in range(5):
        _old_record(
            mod, tmp_path, f"old{index}", f"2020-01-0{index + 1}T00:00:00+00:00"
        )

    fresh = mod.record_start("opencode", session_dir=tmp_path)

    left = {s["run_id"] for s in mod.list_sessions(session_dir=tmp_path)}
    assert fresh in left  # the new record always survives
    # The cap keeps the three newest settled records and drops the two oldest.
    assert left == {fresh, "old2", "old3", "old4"}


def test_retention_never_drops_a_live_record(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "MAX_SETTLED_RECORDS", 1)
    live = mod.record_start("opencode", pid=os.getpid(), session_dir=tmp_path)
    for index in range(3):
        _old_record(
            mod, tmp_path, f"old{index}", f"2021-01-0{index + 1}T00:00:00+00:00"
        )

    mod.record_start("opencode", session_dir=tmp_path)

    left = {s["run_id"] for s in mod.list_sessions(session_dir=tmp_path)}
    assert live in left  # even though it is the oldest by timestamp
    assert "old0" not in left and "old1" not in left  # pruned to the cap of one
    assert len(left) == 3  # the live one, the new one, one settled survivor


def test_retention_can_be_switched_off(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "MAX_SETTLED_RECORDS", 0)
    for index in range(3):
        _old_record(
            mod, tmp_path, f"old{index}", f"2022-01-0{index + 1}T00:00:00+00:00"
        )
    mod.record_start("opencode", session_dir=tmp_path)
    assert len(mod.list_sessions(session_dir=tmp_path)) == 4


def test_list_skips_a_torn_or_foreign_file(tmp_path):
    mod = _load()
    (tmp_path / "torn.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "foreign.json").write_text('{"hello": 1}', encoding="utf-8")
    assert mod.list_sessions(session_dir=tmp_path) == []


def test_list_is_newest_first(tmp_path, monkeypatch):
    mod = _load()
    stamps = iter(["2026-10-15T00:00:01+00:00", "2026-10-15T00:00:02+00:00"])
    monkeypatch.setattr(mod, "_now", lambda: next(stamps))
    first = mod.record_start("a", session_dir=tmp_path)
    second = mod.record_start("b", session_dir=tmp_path)
    assert [s["run_id"] for s in mod.list_sessions(session_dir=tmp_path)] == [
        second,
        first,
    ]


def test_list_on_a_missing_directory_is_empty(tmp_path):
    mod = _load()
    assert mod.list_sessions(session_dir=tmp_path / "absent") == []


def test_pid_alive_self_dead_child_and_nonsense():
    mod = _load()
    assert mod.pid_alive(os.getpid()) is True
    assert mod.pid_alive(_dead_pid()) is False
    assert mod.pid_alive(0) is False
    assert mod.pid_alive(-1) is False


# ── zombie detection ─────────────────────────────────────────────────────────


def _fake_ps(monkeypatch, mod, stdout: str):
    """Make `mod`'s subprocess.run return a completed `ps` run with `stdout`."""

    def run(*args, **kwargs):
        return types.SimpleNamespace(stdout=stdout, returncode=0, stderr="")

    monkeypatch.setattr(mod.subprocess, "run", run)


def test_is_zombie_is_false_when_ps_is_unavailable(monkeypatch):
    """No `ps` on this platform: the pid must not be reported as a zombie."""
    mod = _load()

    def run(*args, **kwargs):
        raise OSError("ps: not found")

    monkeypatch.setattr(mod.subprocess, "run", run)
    assert mod._is_zombie(4242) is False


def test_is_zombie_is_true_for_a_zombie_stat(monkeypatch):
    """`stat` starts with Z for a reaped-but-not-yet-waited child."""
    mod = _load()
    for stdout in ("Z\n", "Z+\n"):
        _fake_ps(monkeypatch, mod, stdout)
        assert mod._is_zombie(4242) is True


def test_is_zombie_is_false_for_a_live_stat(monkeypatch):
    """Any other `stat` (sleeping, foreground) is a live process."""
    mod = _load()
    for stdout in ("S\n", "S+\n"):
        _fake_ps(monkeypatch, mod, stdout)
        assert mod._is_zombie(4242) is False
