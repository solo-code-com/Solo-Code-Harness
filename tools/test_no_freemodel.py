"""Regression guard: the retired `freemodel` provider must not return to a generated artifact.

The provider was dropped from the project by hand (`.env`, `.env.template`), so the generator,
the template and the agent manifest are pinned here. The assertions read generated artifacts
only, never the historical record: `docs/`, `SPEC.md`, `README.md` and `.kilo/memory/` keep their
`freemodel` mentions on purpose.

Usage:
    python -m pytest tools/test_no_freemodel.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import env_file  # noqa: E402


def test_env_template_declares_no_freemodel_variable() -> None:
    """The template ships keys, not a retired gateway."""
    text = (ROOT / ".env.template").read_text(encoding="utf-8")
    assert "freemodel" not in text.lower()
    assert "cc.freemodel.dev" not in text


def test_generated_opencode_config_has_no_freemodel_provider() -> None:
    """`.opencode/opencode.json` is generated, so a stale block would return on next run."""
    data = json.loads(
        (ROOT / ".opencode" / "opencode.json").read_text(encoding="utf-8")
    )
    assert "freemodel" not in data["providers"]


def test_env_file_provider_map_has_no_freemodel_entry() -> None:
    """`provider_endpoint`/`check_model_catalog` resolve through this map."""
    assert "freemodel" not in env_file.PROVIDERS


def test_agent_yaml_tags_have_no_freemodel() -> None:
    text = (ROOT / "agent.yaml").read_text(encoding="utf-8")
    assert "freemodel" not in text
