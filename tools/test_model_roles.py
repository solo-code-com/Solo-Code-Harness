#!/usr/bin/env python3
"""
Model Role Tests
================
Guards for tools/config/model-roles.json -- the single place where each agent's
model is assigned (read by opencode_engine._build_agent_models).

Background: only .kilo/agents/jev.md declared a model, so the other 14 agents
inherited the session default. A reviewer then ran on the same model family as
the author it reviewed, and missed what the author missed.

Usage:
    python -m pytest tools/test_model_roles.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import opencode_engine  # noqa: E402

MODEL_ROLES = ROOT / "tools" / "config" / "model-roles.json"


def _roles_config() -> dict:
    return json.loads(MODEL_ROLES.read_text(encoding="utf-8"))


def _catalog_ids() -> set[str]:
    """Every `<provider>/<model>` id the declared model catalogs can resolve."""
    ids: set[str] = set()
    for prefix, models in (
        ("commandcode", opencode_engine._PROVIDER_MODELS),
        ("openrouter", opencode_engine._OPENROUTER_MODELS),
    ):
        for key, entry in models.items():
            ids.add(f"{prefix}/{key}")
            ids.add(f"{prefix}/{entry['modelID']}")
    return ids


def test_every_kilo_agent_has_a_role():
    """All 15 .kilo/agents/*.md stems appear in the mapping, under a known role."""
    stems = {p.stem for p in (ROOT / ".kilo" / "agents").glob("*.md")}
    config = _roles_config()

    assert len(stems) == 15
    assert stems <= set(config["agents"])
    assert set(config["agents"].values()) <= set(config["roles"])


def test_every_mapped_model_id_resolves_to_a_catalog_entry():
    """No invented ids: each role's id resolves in _PROVIDER_MODELS/_OPENROUTER_MODELS."""
    catalog = _catalog_ids()
    config = _roles_config()

    unresolved = {
        role: mid for role, mid in config["roles"].items() if mid not in catalog
    }
    assert unresolved == {}


def test_build_agent_models_covers_most_agents():
    """The generator emits a model override for the bulk of the roster."""
    models = opencode_engine._build_agent_models()

    assert len(models) >= 10
    assert all(list(entry) == ["model"] for entry in models.values())
    assert models["jev"]["model"] in (
        opencode_engine._JEV_AGENT_MODEL,
        opencode_engine._JEV_AGENT_FALLBACK,
    )
