#!/usr/bin/env python3
"""Unit tests for tools/jev_decide.py: validation, the wire shape, and the CLI.

No network: `env_file.provider_endpoint` and `urllib.request.urlopen` are monkeypatched in every
test that reaches the transport.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import jev_decide  # noqa: E402

STATE = "My payments have failed for three days."

QUESTIONS = {
    "is_urgent": {
        "type": "noul",
        "instructions": "Does this request need urgent attention?",
    },
    "department": {
        "type": "choice",
        "instructions": "Which team should handle it?",
        "criteria": {
            "billing": "charges and payments",
            "shipping": "delivery",
            "returns": "refunds",
        },
    },
    "frustration": {
        "type": "score",
        "instructions": "How frustrated is the customer?",
        "criteria": ["Calm", "Frustrated", "Very angry"],
    },
}

RESPONSE = {
    "model": "typesafe/jev",
    "answers": {
        "is_urgent": {"type": "noul", "noul": 0.96},
        "department": {
            "type": "choice",
            "choice": "billing",
            "confidence": 0.89,
            "probabilities": {"billing": 0.91, "shipping": 0.05, "returns": 0.04},
        },
        "frustration": {
            "type": "score",
            "score": 1.41,
            "confidence": 0.5,
            "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
            "probabilities": {"0": 0.02, "1": 0.55, "2": 0.43},
        },
    },
    "usage": {"input_tokens": 84, "output_tokens": 3},
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


def test_build_request_preserves_the_three_types_verbatim():
    payload = jev_decide.build_request(jev_decide.DEFAULT_MODEL, STATE, QUESTIONS)

    assert payload == {
        "model": jev_decide.DEFAULT_MODEL,
        "state": STATE,
        "questions": QUESTIONS,
    }
    # Verbatim, not rebuilt: criteria descriptions and level order must survive untouched.
    assert payload["questions"] is QUESTIONS


def test_build_request_rejects_an_unknown_type_naming_the_question():
    questions = {"urgent": {"type": "yesno", "instructions": "Is this urgent?"}}

    with pytest.raises(ValueError, match="urgent"):
        jev_decide.build_request(jev_decide.DEFAULT_MODEL, STATE, questions)


def test_build_request_rejects_a_choice_without_criteria():
    questions = {"department": {"type": "choice", "instructions": "Which team?"}}

    with pytest.raises(ValueError, match="department"):
        jev_decide.build_request(jev_decide.DEFAULT_MODEL, STATE, questions)


def test_build_request_rejects_a_score_with_one_level():
    questions = {
        "frustration": {
            "type": "score",
            "instructions": "How frustrated is the customer?",
            "criteria": ["Calm"],
        }
    }

    with pytest.raises(ValueError, match="frustration"):
        jev_decide.build_request(jev_decide.DEFAULT_MODEL, STATE, questions)


def test_build_request_enforces_the_question_limit():
    hundred = {
        f"q{index}": {"type": "noul", "instructions": "Is this urgent?"}
        for index in range(100)
    }

    assert (
        len(
            jev_decide.build_request(jev_decide.DEFAULT_MODEL, STATE, hundred)[
                "questions"
            ]
        )
        == 100
    )

    too_many = dict(hundred)
    too_many["q100"] = {"type": "noul", "instructions": "Is this urgent?"}
    with pytest.raises(ValueError, match="100"):
        jev_decide.build_request(jev_decide.DEFAULT_MODEL, STATE, too_many)


def test_decide_posts_the_documented_shape(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = {
            key.lower(): value for key, value in request.header_items()
        }
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(RESPONSE)

    monkeypatch.setattr(
        jev_decide.env_file,
        "provider_endpoint",
        lambda provider, kind="chat": (
            "https://api.example/provider/v1/chat/completions",
            "test-key",
        ),
    )
    monkeypatch.setattr(jev_decide.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(jev_decide, "USAGE_LOG", tmp_path / "usage.jsonl")

    assert jev_decide.decide(STATE, QUESTIONS) == RESPONSE

    assert captured["url"] == "https://api.example/provider/v1/systemone"
    assert captured["headers"]["user-agent"] == jev_decide.UA
    assert captured["body"] == {
        "model": jev_decide.DEFAULT_MODEL,
        "state": STATE,
        "questions": QUESTIONS,
    }

    logged = json.loads((tmp_path / "usage.jsonl").read_text(encoding="utf-8").strip())
    assert logged["state_chars"] == len(STATE)
    assert logged["input_tokens"] == 84
    assert STATE not in json.dumps(logged)


def test_decide_turns_an_http_error_into_jev_decide_error(monkeypatch, tmp_path):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error": {"type": "invalid_request_error"}}'),
        )

    monkeypatch.setattr(
        jev_decide.env_file,
        "provider_endpoint",
        lambda provider, kind="chat": (
            "https://api.example/provider/v1/chat/completions",
            "test-key",
        ),
    )
    monkeypatch.setattr(jev_decide.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(jev_decide, "USAGE_LOG", tmp_path / "usage.jsonl")

    with pytest.raises(jev_decide.JevDecideError) as failure:
        jev_decide.decide(STATE, QUESTIONS)

    assert failure.value.status == 400
    assert "HTTP 400" in str(failure.value)
    assert "invalid_request_error" in str(failure.value)


def test_main_returns_2_when_the_key_is_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        jev_decide.env_file,
        "provider_endpoint",
        lambda provider, kind="chat": (
            "https://api.example/provider/v1/chat/completions",
            "",
        ),
    )
    monkeypatch.setattr(jev_decide, "USAGE_LOG", tmp_path / "usage.jsonl")

    code = jev_decide.main(["--state", STATE, "--ask", "urgent:noul:Is this urgent?"])

    assert code == 2
    assert "[jev_decide]" in capsys.readouterr().err


def test_main_json_prints_the_response(monkeypatch, tmp_path, capsys):
    questions_file = tmp_path / "questions.json"
    questions_file.write_text(json.dumps(QUESTIONS), encoding="utf-8")
    monkeypatch.setattr(jev_decide, "decide", lambda *args, **kwargs: RESPONSE)

    code = jev_decide.main(
        ["--state", STATE, "--questions-json", str(questions_file), "--json"]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out) == RESPONSE


def test_ask_parses_name_type_and_instructions(monkeypatch, capsys):
    seen: dict = {}

    def fake_decide(state, questions, *, model=jev_decide.DEFAULT_MODEL, timeout=60):
        seen["state"] = state
        seen["questions"] = questions
        return {"answers": {}}

    monkeypatch.setattr(jev_decide, "decide", fake_decide)

    assert (
        jev_decide.main(["--state", STATE, "--ask", "urgent:noul:Is this urgent?"]) == 0
    )
    assert seen["questions"] == {
        "urgent": {"type": "noul", "instructions": "Is this urgent?"}
    }

    # Only the first two colons split, so instructions keep their own colons.
    assert (
        jev_decide.main(["--state", STATE, "--ask", "urgent:noul:Is this: urgent?"])
        == 0
    )
    assert seen["questions"]["urgent"]["instructions"] == "Is this: urgent?"
