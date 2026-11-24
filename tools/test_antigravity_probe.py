#!/usr/bin/env python3
"""Unit tests for the Antigravity headless diagnostic."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import antigravity_probe  # noqa: E402


def test_parse_models_output_extracts_ids_and_skips_banner():
    output = "Fetching available models...\ngemini-3.8-flash-high\tGemini 3.8 Flash (High)\ngemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)\n"

    assert antigravity_probe.parse_models_output(output) == [
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-medium",
    ]


def test_parse_models_output_ignores_blank_lines():
    assert antigravity_probe.parse_models_output(
        "\n\ngemini-3.1-pro-low\tPro (Low)\n\n"
    ) == ["gemini-3.1-pro-low"]


def test_build_report_without_binary_marks_not_found():
    report = antigravity_probe.build_report(None)

    assert report["found"] is False
    assert report["models"] == []
    assert report["default_model"] == antigravity_probe.DEFAULT_MODEL
