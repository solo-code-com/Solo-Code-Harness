"""Tests for tools/snapshot_testing.py"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

# Patch FIXTURES_DIR before importing to avoid creating dirs in CWD during tests
_tmp_fixtures = tempfile.mkdtemp()
_fixtures_path = Path(_tmp_fixtures)

with patch("tools.snapshot_testing.FIXTURES_DIR", _fixtures_path):
    from tools import snapshot_testing as st


# Override at module level too
st.FIXTURES_DIR = _fixtures_path


# ── normalize_output ───────────────────────────────────────────────────────────


def test_normalize_strips_timestamp():
    data = {"status": "ok", "timestamp": "2026-01-01T00:00:00"}
    result = st.normalize_output(data)
    assert "timestamp" not in result
    assert result["status"] == "ok"


def test_normalize_strips_created_at_updated_at():
    data = {"created_at": "x", "updated_at": "y", "v": 1}
    result = st.normalize_output(data)
    assert "created_at" not in result
    assert "updated_at" not in result


def test_normalize_path_becomes_basename():
    data = {"path": "/absolute/dir/file.py"}
    result = st.normalize_output(data)
    assert result["path"] == "file.py"


def test_normalize_nested_dict():
    data = {"outer": {"timestamp": "x", "v": 1}}
    result = st.normalize_output(data)
    assert "timestamp" not in result["outer"]
    assert result["outer"]["v"] == 1


def test_normalize_list_of_dicts():
    data = {"items": [{"timestamp": "x", "v": 1}, {"v": 2}]}
    result = st.normalize_output(data)
    assert "timestamp" not in result["items"][0]


def test_normalize_preserves_non_timestamp_keys():
    data = {"model": "deepseek", "tokens": 100}
    result = st.normalize_output(data)
    assert result == data


# ── record_snapshot / replay_snapshot ──────────────────────────────────────────


def test_record_creates_file():
    name = "test_record_creates"
    path = st.record_snapshot(name, {"status": "ok"})
    assert path.exists()
    path.unlink()


def test_record_normalizes_before_writing():
    name = "test_record_normalizes"
    st.record_snapshot(name, {"status": "ok", "timestamp": "2026"})
    loaded = json.loads((_fixtures_path / f"{name}.json").read_text())
    assert "timestamp" not in loaded
    (_fixtures_path / f"{name}.json").unlink()


def test_replay_returns_data():
    name = "test_replay"
    st.record_snapshot(name, {"v": 42})
    data = st.replay_snapshot(name)
    assert data == {"v": 42}
    (_fixtures_path / f"{name}.json").unlink()


def test_replay_missing_returns_none(capsys):
    result = st.replay_snapshot("does_not_exist_xyz")
    assert result is None
    captured = capsys.readouterr()
    assert "not found" in captured.err.lower() or "not found" in captured.out.lower()


# ── compare_snapshots ──────────────────────────────────────────────────────────


def test_compare_matching_returns_true():
    expected = {"status": "ok", "v": 1}
    actual = {"status": "ok", "v": 1}
    assert st.compare_snapshots(expected, actual, "t") is True


def test_compare_mismatch_returns_false():
    expected = {"status": "ok"}
    actual = {"status": "fail"}
    assert st.compare_snapshots(expected, actual, "t") is False


def test_compare_normalizes_actual():
    expected = {"status": "ok"}
    # actual has extra timestamp that should be stripped before compare
    actual = {"status": "ok", "timestamp": "2026-01-01"}
    assert st.compare_snapshots(expected, actual, "t") is True


# ── full record/compare cycle ──────────────────────────────────────────────────


def test_full_cycle_timestamp_invariant():
    name = "full_cycle"
    data_v1 = {"status": "ok", "timestamp": "2026-01-01T00:00:00", "model": "deepseek"}
    st.record_snapshot(name, data_v1)
    loaded = st.replay_snapshot(name)
    data_v2 = {"status": "ok", "timestamp": "2026-06-15T12:00:00", "model": "deepseek"}
    assert st.compare_snapshots(loaded, data_v2, name) is True
    (_fixtures_path / f"{name}.json").unlink()
