#!/usr/bin/env python3
"""Unit tests for tools/check_model_catalog.py's model-role resolution."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import check_model_catalog  # noqa: E402


def test_role_issue_names_a_model_id_the_catalog_does_not_declare(tmp_path):
    """A role outside the catalogs is reported, naming both the role and its id."""
    roles = tmp_path / "model-roles.json"
    roles.write_text(
        json.dumps({"roles": {"default-executor": "deepseek/not-a-model"}}),
        encoding="utf-8",
    )

    issues = check_model_catalog.model_role_issues(roles)

    assert len(issues) == 1
    assert "default-executor" in issues[0]
    assert "deepseek/not-a-model" in issues[0]
