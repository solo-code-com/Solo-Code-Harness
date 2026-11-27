"""Guard: FreeModel is allowed again but must not reach the GENERATED opencode config or the
provider maps, which stay on the harness own providers.

Operator directive 2026-11-27 re-enabled FreeModel for less-sensitive projects, so the template
may carry FREEMODEL_API. The generated `.opencode/opencode.json`, `env_file.PROVIDERS` and
`agent.yaml` still must not declare it.

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


def test_env_template_carries_the_freemodel_key() -> None:
    """FreeModel is allowed again; the template shows the key to fill."""
    text = (ROOT / ".env.template").read_text(encoding="utf-8")
    assert "FREEMODEL_API" in text


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
