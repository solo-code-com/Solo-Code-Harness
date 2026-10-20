#!/usr/bin/env python3
"""Unit tests for tools/check_model_catalog.py's System One probe.

No network: `urllib.request.urlopen` and `fetch` are monkeypatched in every test that reaches
the transport.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import check_model_catalog  # noqa: E402

BASE = "https://api.example/provider/v1"
KEY = "test-key"

#: The body CommandCode documents for a one-question `/systemone` call (measured 2026-10-20).
ANSWERED = {
    "model": "typesafe/jev",
    "answers": {"urgent": {"type": "noul", "noul": 0.88}},
    "usage": {"input_tokens": 278, "output_tokens": 20},
}


class FakeResponse:
    """The context-manager shape `urlopen` returns, carrying one JSON body."""

    def __init__(self, body: dict) -> None:
        self._raw = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def answering(body: dict):
    """A `urlopen` stand-in that answers 200 with `body`."""

    def fake_urlopen(request, timeout=None):
        return FakeResponse(body)

    return fake_urlopen


def test_probe_reports_the_noul_probability(monkeypatch):
    monkeypatch.setattr(
        check_model_catalog.urllib.request, "urlopen", answering(ANSWERED)
    )

    assert check_model_catalog.probe_systemone(BASE, KEY, 5) == ("ok", "noul=0.88")


def test_probe_drifts_when_the_body_has_no_answers(monkeypatch):
    monkeypatch.setattr(
        check_model_catalog.urllib.request,
        "urlopen",
        answering({"model": "typesafe/jev", "usage": {"input_tokens": 1}}),
    )

    status, detail = check_model_catalog.probe_systemone(BASE, KEY, 5)

    assert status == "drift"
    assert detail


def test_probe_drifts_when_noul_is_out_of_range(monkeypatch):
    body = {"answers": {"urgent": {"type": "noul", "noul": 1.4}}}
    monkeypatch.setattr(check_model_catalog.urllib.request, "urlopen", answering(body))

    status, detail = check_model_catalog.probe_systemone(BASE, KEY, 5)

    assert status == "drift"
    assert "1.4" in detail


def test_probe_reports_an_http_error_as_unreachable(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error": {"type": "unsupported_model"}}'),
        )

    monkeypatch.setattr(check_model_catalog.urllib.request, "urlopen", fake_urlopen)

    status, detail = check_model_catalog.probe_systemone(BASE, KEY, 5)

    assert status == "unreachable"
    assert detail.startswith("HTTP 400: ")
    assert "unsupported_model" in detail


def test_probe_reports_a_network_error_as_unreachable(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(check_model_catalog.urllib.request, "urlopen", fake_urlopen)

    assert check_model_catalog.probe_systemone(BASE, KEY, 5) == (
        "unreachable",
        "URLError",
    )


def test_probe_posts_the_documented_request(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = {
            key.lower(): value for key, value in request.header_items()
        }
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(ANSWERED)

    monkeypatch.setattr(check_model_catalog.urllib.request, "urlopen", fake_urlopen)

    check_model_catalog.probe_systemone(BASE, KEY, 5)

    assert captured["url"].endswith("/systemone")
    assert captured["url"] == BASE + "/systemone"
    assert captured["headers"]["user-agent"] == "opencode/1.0"
    assert captured["body"]["model"] == "typesafe/jev"
    assert isinstance(captured["body"]["questions"], dict)
    assert captured["body"]["questions"] == check_model_catalog.SYSTEMONE_QUESTION


def test_main_counts_systemone_drift_but_not_unreachable(monkeypatch, capsys):
    def fake_fetch(base, path, key, timeout):
        return [{"id": "deepseek-v4.1-flash"}]

    monkeypatch.setattr(
        check_model_catalog,
        "PROVIDERS",
        {
            "commandcode": (
                "COMMANDCODE_BASE_URL",
                "/models",
                "COMMANDCODE_API_KEY",
                {},
                False,
            )
        },
    )
    monkeypatch.setattr(
        check_model_catalog,
        "RAW_ENV",
        {"COMMANDCODE_BASE_URL": BASE, "COMMANDCODE_API_KEY": KEY},
    )
    monkeypatch.setattr(check_model_catalog, "fetch", fake_fetch)
    monkeypatch.setattr(
        check_model_catalog.env_file, "load_project_env", lambda *args, **kwargs: {}
    )

    monkeypatch.setattr(
        check_model_catalog,
        "probe_systemone",
        lambda *args, **kwargs: ("drift", "no answers"),
    )
    assert check_model_catalog.main([]) == 1
    assert "typesafe/jev" in capsys.readouterr().out

    monkeypatch.setattr(
        check_model_catalog,
        "probe_systemone",
        lambda *args, **kwargs: ("unreachable", "URLError"),
    )
    assert check_model_catalog.main([]) == 0

    monkeypatch.setattr(
        check_model_catalog,
        "probe_systemone",
        lambda *args, **kwargs: ("ok", "noul=0.88"),
    )
    assert check_model_catalog.main([]) == 0
    assert "noul=0.88" in capsys.readouterr().out
