#!/usr/bin/env python3
"""Tests for tools/session_control.py — list / send / interrupt (gap G7).

The interrupt tests spawn real processes: a child that itself spawns a grandchild,
so a kill that only reaches the direct child is visible as a surviving grandchild.
Every spawned process is cleaned up in a `finally`, so a failing assertion cannot
leave a sleeper behind.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "session_control.py"
REGISTRY = Path(__file__).resolve().parent / "session_registry.py"

_SLEEP_CHILD = (
    "import subprocess, sys, time\n"
    "grand = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "open(sys.argv[1], 'w', encoding='utf-8').write(str(grand.pid))\n"
    "time.sleep(120)\n"
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mods():
    registry = _load("session_registry_for_control", REGISTRY)
    control = _load("session_control_under_test", SCRIPT)
    return registry, control


def _spawn_sleeper_tree(pid_file: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", _SLEEP_CHILD, str(pid_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not pid_file.is_file():
        time.sleep(0.05)
    assert pid_file.is_file(), "the child never reported its grandchild pid"
    return proc


def _force_cleanup(control, pids: list[int]) -> None:
    for pid in pids:
        if pid and control.session_registry.pid_alive(pid):
            with contextlib.suppress(OSError, ValueError):
                control.kill_tree(pid)


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_returns_every_recorded_session(mods, tmp_path):
    registry, control = mods
    registry.record_start("opencode", session_dir=tmp_path)
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--session-dir", str(tmp_path), "list"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    payload = json.loads(out.stdout)
    assert out.returncode == 0
    assert len(payload) == 1
    assert payload[0]["worker"] == "opencode"


# ── interrupt ────────────────────────────────────────────────────────────────


def test_interrupt_kills_the_whole_tree(mods, tmp_path):
    registry, control = mods
    pid_file = tmp_path / "grandchild.pid"
    proc = _spawn_sleeper_tree(pid_file)
    grandchild = int(pid_file.read_text(encoding="utf-8").strip())
    run_id = registry.record_start("fake", pid=proc.pid, session_dir=tmp_path)
    try:
        assert control.session_registry.pid_alive(proc.pid) is True
        assert control.session_registry.pid_alive(grandchild) is True

        result = control.interrupt(run_id, session_dir=tmp_path)

        assert result["killed"] is True
        assert control.session_registry.pid_alive(proc.pid) is False
        assert control.session_registry.pid_alive(grandchild) is False
        settled = registry.list_sessions(session_dir=tmp_path)[0]
        assert settled["state"] == "interrupted"
    finally:
        _force_cleanup(control, [proc.pid, grandchild])


def test_interrupt_claims_the_record_before_killing(mods, tmp_path, monkeypatch):
    """The claim is what makes the verdict survive the delegate finishing later."""
    registry, control = mods
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    run_id = registry.record_start("fake", pid=proc.pid, session_dir=tmp_path)
    seen: dict = {}
    real_kill = control.kill_tree

    def spy(pid):
        seen["state"] = registry.list_sessions(session_dir=tmp_path)[0]["state"]
        return real_kill(pid)

    monkeypatch.setattr(control, "kill_tree", spy)
    try:
        result = control.interrupt(run_id, session_dir=tmp_path)
        assert seen["state"] == "interrupting"  # claimed before the kill
        assert result["killed"] is True
        assert registry.list_sessions(session_dir=tmp_path)[0]["state"] == "interrupted"
    finally:
        _force_cleanup(control, [proc.pid])


def test_interrupt_reports_an_already_exited_session(mods, tmp_path):
    registry, control = mods
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    run_id = registry.record_start("fake", pid=proc.pid, session_dir=tmp_path)

    result = control.interrupt(run_id, session_dir=tmp_path)
    assert result["killed"] is False
    assert result["reason"] == "already exited"
    assert registry.list_sessions(session_dir=tmp_path)[0]["state"] == "exited"


def test_interrupt_unknown_run_id_is_reported(mods, tmp_path):
    _, control = mods
    result = control.interrupt("nosuchrun", session_dir=tmp_path)
    assert result["killed"] is False
    assert result["error"] == "unknown run id"


def test_kill_tree_refuses_the_controller_and_nonsense(mods):
    _, control = mods
    for pid in (os.getpid(), 0, 1, -5):
        with pytest.raises(ValueError):
            control.kill_tree(pid)


# ── send_message ─────────────────────────────────────────────────────────────


def test_send_message_is_unsupported_without_a_channel(mods, tmp_path):
    registry, control = mods
    run_id = registry.record_start("codex", pid=os.getpid(), session_dir=tmp_path)
    result = control.send_message(run_id, "do more", session_dir=tmp_path)
    assert result["status"] == "unsupported"
    assert result["delivered"] is False


def test_send_message_appends_to_a_declared_inbox(mods, tmp_path):
    registry, control = mods
    inbox = tmp_path / "inbox" / "run.txt"
    run_id = registry.record_start(
        "fake",
        pid=os.getpid(),
        channel="file",
        inbox_path=inbox,
        session_dir=tmp_path,
    )
    assert control.send_message(run_id, "first", session_dir=tmp_path)["delivered"]
    assert control.send_message(run_id, "second\n", session_dir=tmp_path)["delivered"]
    assert inbox.read_text(encoding="utf-8") == "first\nsecond\n"


def test_send_message_to_a_settled_session_is_refused(mods, tmp_path):
    registry, control = mods
    inbox = tmp_path / "inbox.txt"
    run_id = registry.record_start(
        "fake", pid=os.getpid(), channel="file", inbox_path=inbox, session_dir=tmp_path
    )
    registry.record_finish(run_id, 0, session_dir=tmp_path)
    result = control.send_message(run_id, "late", session_dir=tmp_path)
    assert result["delivered"] is False
    assert result["status"] == "session is exited"
    assert not inbox.exists()


# ── CLI wiring ───────────────────────────────────────────────────────────────


def test_cli_send_exits_two_when_unsupported(mods, tmp_path):
    registry, _ = mods
    run_id = registry.record_start("codex", pid=os.getpid(), session_dir=tmp_path)
    out = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--session-dir",
            str(tmp_path),
            "send",
            run_id,
            "x",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 2
    assert json.loads(out.stdout)["status"] == "unsupported"


# The CLI test above runs in a child process, which coverage cannot see. These
# call main() in-process so the dispatch branches are measured too.


def test_main_list_in_process(mods, tmp_path, capsys):
    registry, control = mods
    registry.record_start("kilo", session_dir=tmp_path)
    assert control.main(["--session-dir", str(tmp_path), "list"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["worker"] == "kilo"


def test_main_send_in_process_is_exit_two(mods, tmp_path, capsys):
    registry, control = mods
    run_id = registry.record_start("codex", pid=os.getpid(), session_dir=tmp_path)
    assert control.main(["--session-dir", str(tmp_path), "send", run_id, "x"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "unsupported"


def test_main_interrupt_in_process(mods, tmp_path, capsys):
    registry, control = mods
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    run_id = registry.record_start("fake", pid=proc.pid, session_dir=tmp_path)
    assert control.main(["--session-dir", str(tmp_path), "interrupt", run_id]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "already exited"


def test_main_without_a_command_prints_help(mods, capsys):
    _, control = mods
    assert control.main([]) == 1
    assert "usage" in capsys.readouterr().out.lower()


# ── error branches ───────────────────────────────────────────────────────────


def test_send_message_unknown_run_id(mods, tmp_path):
    _, control = mods
    result = control.send_message("nosuchrun", "x", session_dir=tmp_path)
    assert result["status"] == "unknown run id"
    assert result["delivered"] is False


def test_send_message_reports_a_write_failure(mods, tmp_path):
    registry, control = mods
    run_id = registry.record_start(
        "fake",
        pid=os.getpid(),
        channel="file",
        inbox_path=tmp_path,  # a directory: opening it for append must fail
        session_dir=tmp_path,
    )
    result = control.send_message(run_id, "x", session_dir=tmp_path)
    assert result["delivered"] is False
    assert str(result["status"]).startswith("write failed")


def test_interrupt_reports_a_record_without_a_pid(mods, tmp_path):
    _, control = mods
    run_id = "nopidrun"
    (tmp_path / f"{run_id}.json").write_text(
        json.dumps({"run_id": run_id, "worker": "fake", "pid": "not-an-int"}),
        encoding="utf-8",
    )
    result = control.interrupt(run_id, session_dir=tmp_path)
    assert result["killed"] is False
    assert result["error"] == "record carries no pid"


def test_wait_for_exit_times_out_on_a_live_process(mods):
    _, control = mods
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert control.wait_for_exit(proc.pid, timeout_s=0.2) is False
    finally:
        _force_cleanup(control, [proc.pid])


def test_kill_tree_on_a_dead_pid_still_reports_what_it_signalled(mods):
    _, control = mods
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    # taskkill /T /F /PID is unconditional, so Windows reports the pid even
    # though it was already gone; POSIX returns only what it actually signalled.
    expected = [proc.pid] if sys.platform == "win32" else []
    assert control.kill_tree(proc.pid) == expected


def test_posix_descendant_walk_is_a_no_op_where_ps_is_absent(mods, monkeypatch):
    """A missing `ps` must make the POSIX descendant walk return [], not raise."""

    def _no_ps(*args, **kwargs):
        raise OSError("ps not found")

    _, control = mods
    monkeypatch.setattr(control.subprocess, "run", _no_ps)
    assert control._descendant_pids_windows_free(os.getpid()) == []


# ── prune / forget ───────────────────────────────────────────────────────────


def _write_record(directory, run_id, *, state, stamp, pid=4242):
    (directory / f"{run_id}.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "worker": "fake",
                "pid": pid,
                "state": state,
                "started_at": stamp,
                "finished_at": stamp,
            }
        ),
        encoding="utf-8",
    )


def test_prune_deletes_old_settled_records_only(mods, tmp_path):
    registry, control = mods
    old = "2020-01-01T00:00:00+00:00"
    _write_record(tmp_path, "oldrun", state="exited", stamp=old)
    live_run = registry.record_start("fake", pid=os.getpid(), session_dir=tmp_path)

    result = control.prune(older_than_days=1, session_dir=tmp_path)

    assert result["removed"] == ["oldrun"]
    assert result["live"] == [live_run]
    assert [s["run_id"] for s in registry.list_sessions(session_dir=tmp_path)] == [
        live_run
    ]


def test_prune_keeps_a_recent_record(mods, tmp_path):
    registry, control = mods
    run_id = registry.record_start("fake", session_dir=tmp_path)
    registry.record_finish(run_id, 0, session_dir=tmp_path)

    result = control.prune(older_than_days=30, session_dir=tmp_path)

    assert result["removed"] == []
    assert result["kept"] == [run_id]
    assert len(registry.list_sessions(session_dir=tmp_path)) == 1


def test_prune_dry_run_deletes_nothing(mods, tmp_path):
    registry, control = mods
    _write_record(tmp_path, "oldrun", state="exited", stamp="2020-01-01T00:00:00+00:00")
    result = control.prune(older_than_days=1, session_dir=tmp_path, dry_run=True)
    assert result["removed"] == ["oldrun"]
    assert len(registry.list_sessions(session_dir=tmp_path)) == 1  # still on disk


def test_prune_treats_an_unreadable_stamp_as_keep(mods, tmp_path):
    registry, control = mods
    _write_record(tmp_path, "mystery", state="exited", stamp="not-a-timestamp")
    result = control.prune(older_than_days=1, session_dir=tmp_path)
    assert result["removed"] == []
    assert result["kept"] == ["mystery"]


def test_prune_keeps_a_record_with_no_timestamp_at_all(mods, tmp_path):
    registry, control = mods
    (tmp_path / "bare.json").write_text(
        json.dumps(
            {"run_id": "bare", "worker": "fake", "pid": 4242, "state": "exited"}
        ),
        encoding="utf-8",
    )
    result = control.prune(older_than_days=1, session_dir=tmp_path)
    assert result["removed"] == []
    assert result["kept"] == ["bare"]


def test_prune_reads_a_naive_timestamp_as_utc(mods, tmp_path):
    """A hand-written record without an offset must not look ancient by accident."""
    registry, control = mods
    _write_record(tmp_path, "naive", state="exited", stamp="2020-01-01T00:00:00")
    assert control.prune(older_than_days=1, session_dir=tmp_path)["removed"] == [
        "naive"
    ]
    assert registry.list_sessions(session_dir=tmp_path) == []


def test_forget_reports_a_missing_record(mods, tmp_path):
    registry, _ = mods
    assert registry.forget("nosuchrun", session_dir=tmp_path) is False


def test_cli_prune_in_process(mods, tmp_path, capsys):
    registry, control = mods
    _write_record(tmp_path, "oldrun", state="exited", stamp="2020-01-01T00:00:00+00:00")
    code = control.main(
        ["--session-dir", str(tmp_path), "prune", "--older-than-days", "1"]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["removed"] == ["oldrun"]
    assert registry.list_sessions(session_dir=tmp_path) == []
