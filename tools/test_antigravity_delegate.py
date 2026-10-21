#!/usr/bin/env python3
"""Unit tests for the Antigravity headless delegation wrapper."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    sys.path.insert(0, _entry)

import session_registry  # noqa: E402

from tools import antigravity_delegate, shared_state  # noqa: E402


@pytest.fixture(autouse=True)
def _registry_into_tmp(tmp_path, monkeypatch):
    """No unit test may write session records into the repo's .solocode/."""
    monkeypatch.setattr(session_registry, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(
        antigravity_delegate, "USAGE_LOG", tmp_path / "antigravity-usage.jsonl"
    )


def _as_popen(completed_cls, pid: int = 8180):
    """A Popen-shaped factory over the CompletedProcess-shaped fakes below.

    The wrapper now keeps a pid and calls communicate(), so these tests exercise
    the Popen seam; adapting here keeps each test's intent (streams, returncode)
    unchanged.
    """

    def factory(*_args, **_kwargs):
        process = completed_cls()
        process.pid = pid
        process.communicate = lambda timeout=None: (process.stdout, process.stderr)
        return process

    return factory


def _recording_popen(seen: dict, completed_cls, pid: int = 8180):
    """As above, but records the command it was asked to run."""

    def factory(command, *_args, **_kwargs):
        seen["command"] = command
        return _as_popen(completed_cls, pid)()

    return factory


def test_parse_ndjson_events_collects_deltas_and_result():
    stream = "\n".join(
        [
            '{"event":"init","conversation_id":"conv_1"}',
            '{"event":"step_update","step_update":{"text_delta":"HELLO "}}',
            '{"event":"step_update","step_update":{"text_delta":"WORLD"}}',
            '{"event":"result","result":{"status":"SUCCESS","response":"HELLO WORLD","usage":{"total_tokens":4}}}',
        ]
    )

    result = antigravity_delegate.parse_ndjson_events(stream)

    assert result["conversation_id"] == "conv_1"
    assert result["text"] == "HELLO WORLD"
    assert result["response"] == "HELLO WORLD"
    assert result["usage"] == {"total_tokens": 4}
    assert result["error"] is None


def test_parse_ndjson_events_uses_result_response_when_no_delta():
    result = antigravity_delegate.parse_ndjson_events(
        '{"event":"result","result":{"status":"SUCCESS","response":"DONE"}}'
    )

    assert result["text"] == "DONE"


def test_parse_ndjson_events_requires_result_event():
    result = antigravity_delegate.parse_ndjson_events('{"event":"init"}')
    assert result["error"] == "Antigravity stream ended without a result event"


def test_parse_ndjson_events_reports_unsuccessful_result():
    result = antigravity_delegate.parse_ndjson_events(
        '{"event":"result","result":{"status":"ERROR","error":"bad request"}}'
    )

    assert result["error"] == "bad request"


def test_resolve_allowed_directory_rejects_path_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()

    assert antigravity_delegate.resolve_allowed_directory("src", workspace) == (
        workspace / "src",
        "src",
    )
    with pytest.raises(ValueError, match="inside --target-dir"):
        antigravity_delegate.resolve_allowed_directory("..", workspace)
    with pytest.raises(ValueError, match="narrower directory"):
        antigravity_delegate.resolve_allowed_directory(".", workspace)


def test_changed_workspace_paths_detects_create_modify_and_delete():
    before = {"a.py": "one", "gone.py": "old"}
    after = {"a.py": "two", "new.py": "new"}

    assert antigravity_delegate.changed_workspace_paths(before, after) == [
        "a.py",
        "gone.py",
        "new.py",
    ]


def test_paths_outside_directory_uses_path_boundaries():
    paths = ["src/a.py", "src2/b.py", "README.md"]

    assert antigravity_delegate.paths_outside_directory(paths, "src") == [
        "src2/b.py",
        "README.md",
    ]
    assert antigravity_delegate.paths_outside_directory(paths, ".") == []


def test_build_prompt_is_read_only_without_auto_approval():
    prompt = antigravity_delegate.build_prompt(
        "inspect project", allow_dir=None, auto_approve=False, guardrail=True
    )

    assert "READ-ONLY MODE" in prompt
    assert "Allowed write directory" not in prompt


def test_build_prompt_names_writable_directory_when_auto_approved():
    prompt = antigravity_delegate.build_prompt(
        "edit code", allow_dir="src", auto_approve=True, guardrail=True
    )

    assert "Allowed write directory: src" in prompt
    assert "STRICT OPERATING CONSTRAINTS" in prompt


def test_snapshot_ignores_root_generated_dirs(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / ".venv" / "lib").mkdir(parents=True)
    (ws / ".pytest_temp_x").mkdir()
    (ws / "src" / "a.py").write_text("a", encoding="utf-8")
    (ws / ".venv" / "lib" / "v.py").write_text("v", encoding="utf-8")
    (ws / ".pytest_temp_x" / "t.py").write_text("t", encoding="utf-8")

    assert sorted(antigravity_delegate.snapshot_workspace(ws)) == ["src/a.py"]


def test_snapshot_keeps_workspace_under_ignored_name_in_its_path(tmp_path):
    """A workspace path containing 'build' must not prune every file."""
    ws = tmp_path / "build" / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("a", encoding="utf-8")

    before = antigravity_delegate.snapshot_workspace(ws)
    (ws / "src" / "a.py").write_text("changed", encoding="utf-8")
    after = antigravity_delegate.snapshot_workspace(ws)

    assert antigravity_delegate.changed_workspace_paths(before, after) == ["src/a.py"]


def test_snapshot_detects_change_in_nested_build_dir(tmp_path):
    """Only root-level build/dist are noise; a nested one can hold real content."""
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "docs" / "build").mkdir(parents=True)
    (ws / "docs" / "build" / "x.py").write_text("orig", encoding="utf-8")

    before = antigravity_delegate.snapshot_workspace(ws)
    (ws / "docs" / "build" / "x.py").write_text("out of scope", encoding="utf-8")
    after = antigravity_delegate.snapshot_workspace(ws)

    changed = antigravity_delegate.changed_workspace_paths(before, after)
    assert antigravity_delegate.paths_outside_directory(changed, "src") == [
        "docs/build/x.py"
    ]


def _wire_main(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "docs").mkdir()
    db = tmp_path / "state.db"
    monkeypatch.setattr(antigravity_delegate, "find_agy_binary", lambda: "dummy-agy")
    monkeypatch.setattr(
        antigravity_delegate,
        "SharedState",
        lambda *args, **kwargs: shared_state.SharedState(db),
    )
    return ws, db


def _wrapper_argv(ws: Path) -> list[str]:
    return ["task", "--target-dir", str(ws), "--allow-dir", "src", "--auto-approve"]


def _run_main(monkeypatch, ws, result):
    monkeypatch.setattr(antigravity_delegate, "run_agy_cli", lambda **kwargs: result)
    return antigravity_delegate.main(_wrapper_argv(ws))


def test_main_returns_zero_on_success(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)
    assert _run_main(monkeypatch, ws, ({"text": "done", "error": None}, 0)) == 0


def test_main_returns_two_on_worker_error(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)
    assert _run_main(monkeypatch, ws, ({"text": "", "error": "boom"}, 2)) == 2


def test_main_preserves_timeout_exit_code(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)
    assert (
        _run_main(monkeypatch, ws, ({"text": "", "error": "Timeout after 1s"}, 124))
        == 124
    )


def test_main_reports_scope_violation_before_worker_error(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)

    def fake_run(**kwargs):
        (kwargs["target_dir"] / "docs" / "escaped.py").write_text("x", encoding="utf-8")
        return {"text": "", "error": "boom"}, 2

    monkeypatch.setattr(antigravity_delegate, "run_agy_cli", fake_run)
    assert antigravity_delegate.main(_wrapper_argv(ws)) == 4


def test_main_reports_lock_conflict(monkeypatch, tmp_path):
    ws, db = _wire_main(monkeypatch, tmp_path)
    with shared_state.SharedState(db) as state:
        assert state.acquire_directory_lock(
            "src", engine="gemini", model="m", session_id="other"
        )

    assert _run_main(monkeypatch, ws, ({"text": "", "error": None}, 0)) == 3


def test_main_rejects_whole_workspace_allow_dir(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)
    with pytest.raises(SystemExit):
        antigravity_delegate.main(
            ["task", "--target-dir", str(ws), "--allow-dir", ".", "--auto-approve"]
        )


def test_main_requires_allow_dir_with_auto_approve(tmp_path):
    with pytest.raises(SystemExit):
        antigravity_delegate.main(
            ["task", "--target-dir", str(tmp_path), "--auto-approve"]
        )


def test_main_rejects_no_guardrail_with_auto_approve(tmp_path):
    (tmp_path / "src").mkdir()
    with pytest.raises(SystemExit):
        antigravity_delegate.main(
            [
                "task",
                "--target-dir",
                str(tmp_path),
                "--allow-dir",
                "src",
                "--auto-approve",
                "--no-guardrail",
            ]
        )


def test_run_agy_cli_maps_timeout_to_124(monkeypatch, tmp_path):
    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    proc = _as_popen(Completed)()
    calls = {"n": 0}

    def communicate(timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired("agy", 1)
        return "", ""

    proc.communicate = communicate
    monkeypatch.setattr(antigravity_delegate.subprocess, "Popen", lambda *a, **k: proc)
    control_stub = SimpleNamespace(kill_tree=lambda pid: [pid])
    monkeypatch.setattr(
        antigravity_delegate,
        "_load_sibling",
        lambda name: control_stub if name == "session_control" else session_registry,
    )
    result, code = antigravity_delegate.run_agy_cli(
        prompt="p",
        model="m",
        target_dir=tmp_path,
        agy_binary="agy",
        skip_permissions=False,
        timeout_s=1,
    )

    assert code == 124
    assert result["error"] == "Timeout after 1s"


def test_run_agy_cli_maps_oserror_to_one(monkeypatch, tmp_path):
    def raise_oserror(*args, **kwargs):
        raise OSError("not found")

    monkeypatch.setattr(antigravity_delegate.subprocess, "Popen", raise_oserror)
    _, code = antigravity_delegate.run_agy_cli(
        prompt="p",
        model="m",
        target_dir=tmp_path,
        agy_binary="agy",
        skip_permissions=False,
        timeout_s=1,
    )

    assert code == 1


def test_run_agy_cli_keeps_nonzero_exit_code(monkeypatch, tmp_path):
    class Completed:
        returncode = 3
        stdout = '{"event":"result","result":{"status":"SUCCESS","response":"ok"}}'
        stderr = ""

    monkeypatch.setattr(antigravity_delegate.subprocess, "Popen", _as_popen(Completed))
    _, code = antigravity_delegate.run_agy_cli(
        prompt="p",
        model="m",
        target_dir=tmp_path,
        agy_binary="agy",
        skip_permissions=True,
        timeout_s=5,
    )

    assert code == 3


def test_run_agy_cli_passes_conversation_id(monkeypatch, tmp_path):
    seen: dict[str, list[str]] = {}

    class Completed:
        returncode = 0
        stdout = '{"event":"result","result":{"status":"SUCCESS","response":"ok"}}'
        stderr = ""

    monkeypatch.setattr(
        antigravity_delegate.subprocess, "Popen", _recording_popen(seen, Completed)
    )
    antigravity_delegate.run_agy_cli(
        prompt="p",
        model="m",
        target_dir=tmp_path,
        agy_binary="agy",
        skip_permissions=False,
        timeout_s=5,
        conversation="conv_42",
    )

    assert seen["command"][-2:] == ["--conversation", "conv_42"]


def test_run_agy_cli_continue_latest_takes_no_id(monkeypatch, tmp_path):
    seen: dict[str, list[str]] = {}

    class Completed:
        returncode = 0
        stdout = '{"event":"result","result":{"status":"SUCCESS","response":"ok"}}'
        stderr = ""

    monkeypatch.setattr(
        antigravity_delegate.subprocess, "Popen", _recording_popen(seen, Completed)
    )
    antigravity_delegate.run_agy_cli(
        prompt="p",
        model="m",
        target_dir=tmp_path,
        agy_binary="agy",
        skip_permissions=False,
        timeout_s=5,
        continue_latest=True,
    )

    assert seen["command"][-1] == "--continue"


def test_main_rejects_conversation_with_continue_latest(tmp_path):
    (tmp_path / "src").mkdir()
    with pytest.raises(SystemExit):
        antigravity_delegate.main(
            [
                "task",
                "--target-dir",
                str(tmp_path),
                "--conversation",
                "conv_1",
                "--continue-latest",
            ]
        )


def test_find_agy_binary_prefers_path(monkeypatch):
    monkeypatch.setattr(
        antigravity_delegate.shutil, "which", lambda name: "C:/bin/agy.exe"
    )

    assert antigravity_delegate.find_agy_binary() == "C:/bin/agy.exe"


def test_find_agy_binary_uses_local_app_data(monkeypatch, tmp_path):
    exe = tmp_path / "agy" / "bin" / "agy.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(antigravity_delegate.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert antigravity_delegate.find_agy_binary() == str(exe)


def test_find_agy_binary_returns_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(antigravity_delegate.shutil, "which", lambda name: None)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(
        antigravity_delegate.Path, "home", classmethod(lambda cls: tmp_path)
    )

    assert antigravity_delegate.find_agy_binary() is None


def test_parse_ndjson_events_collects_denied_actions_and_tool_errors():
    """A denied tool is a failure signal even when result.status is SUCCESS (P1)."""
    stream = "\n".join(
        [
            '{"event":"step_update","step_update":{"step_type":"tool","state":"ERROR","tool_name":"run_command",'
            '"tool_info":{"error":{"type":"TOOL_ERROR","message":"permission check failed\\nsecond line"}}}}',
            '{"event":"result","result":{"status":"SUCCESS","response":"",'
            '"denied_actions":[{"action":"command","display_name":"RunCommand"}]}}',
        ]
    )

    result = antigravity_delegate.parse_ndjson_events(stream)

    assert result["denied_actions"] == [
        {"action": "command", "display_name": "RunCommand"}
    ]
    assert result["tool_errors"] == [
        {"tool": "run_command", "message": "permission check failed"}
    ]
    assert result["error"] is None


def test_build_prompt_allow_tools_keeps_read_only_but_permits_commands():
    prompt = antigravity_delegate.build_prompt(
        "audit", allow_dir=None, auto_approve=False, guardrail=True, allow_tools=True
    )

    assert "READ-ONLY MODE" in prompt
    assert "You MAY run" in prompt
    assert "Allowed write directory" not in prompt


def test_run_agy_cli_adds_skip_flag_when_requested(monkeypatch, tmp_path):
    seen: dict[str, list[str]] = {}

    class Completed:
        returncode = 0
        stdout = '{"event":"result","result":{"status":"SUCCESS","response":"ok"}}'
        stderr = ""

    monkeypatch.setattr(
        antigravity_delegate.subprocess, "Popen", _recording_popen(seen, Completed)
    )
    antigravity_delegate.run_agy_cli(
        prompt="p",
        model="m",
        target_dir=tmp_path,
        agy_binary="agy",
        skip_permissions=True,
        timeout_s=5,
    )

    assert "--dangerously-skip-permissions" in seen["command"]


def test_main_reports_denied_actions_as_failure(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)
    result = ({"text": "", "error": None, "denied_actions": [{"action": "command"}]}, 0)

    assert _run_main(monkeypatch, ws, result) == 5


def test_main_reports_empty_output_as_failure(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)

    assert _run_main(monkeypatch, ws, ({"text": "", "error": None}, 0)) == 2


def test_main_allows_tools_without_allow_dir(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)
    monkeypatch.setattr(
        antigravity_delegate,
        "run_agy_cli",
        lambda **kwargs: ({"text": "ok", "error": None}, 0),
    )

    assert (
        antigravity_delegate.main(["task", "--target-dir", str(ws), "--allow-tools"])
        == 0
    )


def test_main_rejects_allow_tools_with_auto_approve(tmp_path):
    (tmp_path / "src").mkdir()
    with pytest.raises(SystemExit):
        antigravity_delegate.main(
            [
                "task",
                "--target-dir",
                str(tmp_path),
                "--allow-tools",
                "--allow-dir",
                "src",
                "--auto-approve",
            ]
        )


def test_main_rejects_allow_dir_without_auto_approve(tmp_path):
    (tmp_path / "src").mkdir()
    with pytest.raises(SystemExit):
        antigravity_delegate.main(
            [
                "task",
                "--target-dir",
                str(tmp_path),
                "--allow-tools",
                "--allow-dir",
                "src",
            ]
        )


def test_main_rejects_bare_allow_dir_without_auto_approve(tmp_path):
    """--allow-dir alone is a no-op write scope; it must be rejected, not ignored."""
    (tmp_path / "src").mkdir()
    with pytest.raises(SystemExit):
        antigravity_delegate.main(
            ["task", "--target-dir", str(tmp_path), "--allow-dir", "src"]
        )


def test_make_streams_encoding_safe_relaxes_errors(monkeypatch):
    calls: dict[str, object] = {}

    class FakeStream:
        def reconfigure(self, **kwargs):
            calls.update(kwargs)

    monkeypatch.setattr(antigravity_delegate.sys, "stdout", FakeStream())
    monkeypatch.setattr(antigravity_delegate.sys, "stderr", FakeStream())

    antigravity_delegate._make_streams_encoding_safe()

    assert calls.get("errors") == "replace"


def test_make_streams_encoding_safe_tolerates_missing_reconfigure(monkeypatch):
    class Bare:
        pass

    monkeypatch.setattr(antigravity_delegate.sys, "stdout", Bare())
    monkeypatch.setattr(antigravity_delegate.sys, "stderr", Bare())

    antigravity_delegate._make_streams_encoding_safe()


def test_looks_like_quota_error_matches_markers():
    assert antigravity_delegate._looks_like_quota_error(
        "RESOURCE_EXHAUSTED: quota exceeded"
    )
    assert antigravity_delegate._looks_like_quota_error("429 Too Many Requests")
    assert antigravity_delegate._looks_like_quota_error("You have hit the rate limit")
    assert not antigravity_delegate._looks_like_quota_error("file not found")


def test_main_reports_quota_exhaustion_as_exit_six(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)
    result = (
        {"text": "", "error": "RESOURCE_EXHAUSTED: quota exceeded", "stderr": ""},
        2,
    )

    assert _run_main(monkeypatch, ws, result) == 6


# The key set and order every sibling worker writes to .solocode/*-usage.jsonl.
SIBLING_USAGE_KEYS = [
    "ts",
    "model",
    "prompt_chars",
    "elapsed_s",
    "session_id",
    "usage",
    "cost",
    "error",
    "phase",
    "run_id",
]


def _usage_rows() -> list[dict]:
    path = antigravity_delegate.USAGE_LOG
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_successful_run_appends_one_row_matching_sibling_shape(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)
    parsed = antigravity_delegate.parse_ndjson_events(
        '{"event":"init","conversation_id":"conv_1"}\n'
        '{"event":"result","result":{"status":"SUCCESS","response":"done",'
        '"usage":{"total_tokens":7,"input":5}}}'
    )
    parsed["cost"] = 0.25

    assert _run_main(monkeypatch, ws, (parsed, 0)) == 0

    rows = _usage_rows()
    assert len(rows) == 2
    start, finish = rows
    assert list(finish) == SIBLING_USAGE_KEYS
    assert start["phase"] == "start"
    assert finish["phase"] == "finish"
    assert start["run_id"] == finish["run_id"]
    assert finish["model"] == antigravity_delegate.DEFAULT_MODEL
    assert finish["prompt_chars"] > 0
    assert finish["session_id"] == "conv_1"
    assert finish["usage"] == {"total_tokens": 7, "input": 5}
    assert finish["cost"] == 0.25
    assert finish["error"] is None
    assert datetime.fromisoformat(finish["ts"]).tzinfo is not None


def test_failed_run_appends_a_row_carrying_the_error(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)

    assert _run_main(monkeypatch, ws, ({"text": "", "error": "boom"}, 2)) == 2

    rows = _usage_rows()
    assert len(rows) == 2
    assert rows[1]["error"] == "boom"
    assert rows[1]["usage"] is None


def test_denied_run_appends_a_row_carrying_the_denial(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)
    result = ({"text": "", "error": None, "denied_actions": [{"action": "command"}]}, 0)

    assert _run_main(monkeypatch, ws, result) == 5

    rows = _usage_rows()
    assert len(rows) == 2
    assert "denied permission for: command" in rows[1]["error"]


def test_unwritable_usage_log_does_not_raise_or_change_exit_code(monkeypatch, tmp_path):
    ws, _ = _wire_main(monkeypatch, tmp_path)
    blocked = tmp_path / "usage-log-is-a-directory"
    blocked.mkdir()
    monkeypatch.setattr(antigravity_delegate, "USAGE_LOG", blocked)

    assert _run_main(monkeypatch, ws, ({"text": "done", "error": None}, 0)) == 0
    assert _run_main(monkeypatch, ws, ({"text": "", "error": "boom"}, 2)) == 2


def test_start_row_exists_before_the_worker_is_spawned(monkeypatch, tmp_path):
    """The start row is on disk by the time the worker subprocess is spawned."""
    ws, _ = _wire_main(monkeypatch, tmp_path)

    seen_at_spawn = {}

    class Completed:
        returncode = 0
        stdout = '{"event":"result","result":{"status":"SUCCESS","response":"done"}}'
        stderr = ""

    def fake_popen(command, **kwargs):
        seen_at_spawn["rows"] = _usage_rows()
        return _as_popen(Completed)()

    monkeypatch.setattr(antigravity_delegate.subprocess, "Popen", fake_popen)

    assert antigravity_delegate.main(_wrapper_argv(ws)) == 0

    assert seen_at_spawn["rows"]
    start = seen_at_spawn["rows"][-1]
    assert start["phase"] == "start"
    finish = _usage_rows()[-1]
    assert finish["phase"] == "finish"
    assert finish["run_id"] == start["run_id"]
