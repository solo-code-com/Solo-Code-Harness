#!/usr/bin/env python3
"""Offline tests for tools/decision_probe.py: normalization, verdicts, fixtures, the replay path.

No network: every test that reaches the transport monkeypatches `urllib.request.urlopen`.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
for _entry in (str(ROOT), str(TOOLS)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import decision_probe  # noqa: E402

#: A known string, so "the fixture never carries the credential" is checkable by substring.
KEY = "test-key-not-a-real-credential"
INVALID_BODY = json.dumps(
    {
        "error": {
            "code": 400,
            "message": "Unknown parameter 'state'.",
            "status": "INVALID_ARGUMENT",
        }
    }
)
BILLING_BODY = json.dumps(
    {
        "error": {
            "code": 402,
            "message": "Resource has been exhausted (e.g. check quota).",
            "status": "RESOURCE_EXHAUSTED",
        }
    }
)
OK_BODY = json.dumps({"decisions": [{"score": 1}]})


class FakeResponse:
    """The context-manager shape `urlopen` returns, carrying one JSON body."""

    def __init__(self, body: str, status: int = 200) -> None:
        self.status = status
        self._raw = body.encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def http_error(status: int, body: str) -> urllib.error.HTTPError:
    """The exception `urlopen` raises for a 4xx, carrying the provider's body."""
    return urllib.error.HTTPError(
        decision_probe.URL, status, "error", {}, io.BytesIO(body.encode("utf-8"))
    )


def with_key(monkeypatch) -> None:
    """Make `credential()` return KEY without a real `.env`."""
    monkeypatch.setattr(
        decision_probe.env_file,
        "parse_env_file",
        lambda _path: {decision_probe.KEY_NAMES[0]: KEY},
    )


def test_a_400_unknown_parameter_normalizes_to_invalid_request():
    assert decision_probe.normalize(400, INVALID_BODY) == (
        400,
        "invalid_request",
        "Unknown parameter 'state'.",
    )


def test_a_402_body_is_billing_blocked(monkeypatch, capsys):
    with_key(monkeypatch)

    def fake_urlopen(request, timeout=None):
        raise http_error(402, BILLING_BODY)

    monkeypatch.setattr(decision_probe.urllib.request, "urlopen", fake_urlopen)

    assert decision_probe.main(["--matrix"]) == decision_probe.EXIT_BILLING
    assert "verdict: BILLING_BLOCKED" in capsys.readouterr().out


def test_a_200_body_is_reachable(monkeypatch, capsys):
    with_key(monkeypatch)

    monkeypatch.setattr(
        decision_probe.urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse(OK_BODY),
    )

    assert decision_probe.main(["--matrix"]) == decision_probe.EXIT_OK
    assert "verdict: REACHABLE" in capsys.readouterr().out


def test_record_writes_a_fixture_without_the_credential(monkeypatch, capsys, tmp_path):
    with_key(monkeypatch)
    monkeypatch.setattr(decision_probe, "PROBE_DIR", tmp_path / "decision-probes")

    def fake_urlopen(request, timeout=None):
        raise http_error(400, INVALID_BODY)

    monkeypatch.setattr(decision_probe.urllib.request, "urlopen", fake_urlopen)

    assert (
        decision_probe.main(["--record", "--label", "unit"]) == decision_probe.EXIT_OK
    )

    path = Path(capsys.readouterr().out.strip())
    assert path.is_file()
    assert path.name.endswith("-unit.json")

    raw = path.read_text(encoding="utf-8")
    assert KEY not in raw
    fixture = json.loads(raw)
    assert set(fixture) == {"ts", "url", "request", "status", "body"}
    assert fixture["url"] == decision_probe.URL
    assert fixture["status"] == 400
    # The recorded request is the exact bytes that were sent.
    assert fixture["request"] == json.dumps(
        decision_probe.RECORD_BODY, ensure_ascii=False
    )


def test_replay_prints_the_recorded_row_without_network(monkeypatch, capsys, tmp_path):
    with_key(monkeypatch)
    monkeypatch.setattr(decision_probe, "PROBE_DIR", tmp_path / "decision-probes")

    def fake_urlopen(request, timeout=None):
        raise http_error(402, BILLING_BODY)

    monkeypatch.setattr(decision_probe.urllib.request, "urlopen", fake_urlopen)
    assert (
        decision_probe.main(["--record", "--label", "unit"])
        == decision_probe.EXIT_BILLING
    )
    recorded = Path(capsys.readouterr().out.strip())

    def refuse(*_args, **_kwargs):
        raise AssertionError("--replay opened a network connection")

    monkeypatch.setattr(decision_probe.urllib.request, "urlopen", refuse)

    assert (
        decision_probe.main(["--replay", "--label", "unit"]) == decision_probe.EXIT_OK
    )

    out = capsys.readouterr().out
    assert f"| {recorded.stem} | 402 | billing_blocked |" in out
    assert "Resource has been exhausted" in out


def test_a_missing_credential_exits_2(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(decision_probe, "ROOT", tmp_path)
    for name in decision_probe.KEY_NAMES:
        monkeypatch.delenv(name, raising=False)

    assert decision_probe.main(["--matrix"]) == decision_probe.EXIT_CONFIG
    assert "[decision_probe]" in capsys.readouterr().err


def test_the_router_never_imports_the_probe():
    path = TOOLS / "router.py"
    if not path.is_file():
        pytest.skip("tools/router.py is not present in this checkout")

    assert "decision_probe" not in path.read_text(encoding="utf-8")
