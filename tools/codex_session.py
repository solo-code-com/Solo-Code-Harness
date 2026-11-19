#!/usr/bin/env python3
"""Codex CLI lifecycle adapter for Solo-Code shared state.

Codex CLI has no project lifecycle hooks, so invoke ``start`` and ``end``
around a session (the launcher does this for interactive sessions).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from pathlib import Path

# tomllib is stdlib in Python 3.11+
if sys.version_info >= (3, 11):
    import tomllib as _toml
else:
    _toml = None  # type: ignore[assignment]

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.shared_state import SharedState

ROOT = Path(__file__).resolve().parent.parent
# Codex keeps its provider and model config here; the launcher does not export it.
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
# Never record a model name that may not exist: a wrong name is worse than none.
FALLBACK_MODEL = "unknown"


def _model(model: str | None = None) -> str:
    """Resolve the model: explicit argument, env var, Codex config, fallback."""
    if model:
        return model
    from_env = os.environ.get("CODEX_MODEL")
    if from_env:
        return from_env
    if _toml is not None:
        try:
            with CODEX_CONFIG.open("rb") as fh:
                configured = _toml.load(fh).get("model")
        except (OSError, ValueError):
            configured = None
        if isinstance(configured, str) and configured:
            return configured
    return FALLBACK_MODEL


def start(session_id: str, model: str | None = None) -> int:
    with SharedState() as state:
        recent = state.get_recent_sessions(limit=3)
        print(f"[codex] session {session_id} started (model={_model(model)})")
        for item in recent:
            print(f"  [{item['engine']}] {item['summary'][:120]}")
    return 0


def end(session_id: str, summary: str, model: str | None = None) -> int:
    try:
        changed = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        ).stdout.splitlines()
    except subprocess.TimeoutExpired:
        changed = []
    with SharedState() as state:
        state.add_session_entry(
            engine="codex",
            model=_model(model),
            session_id=session_id,
            summary=summary,
            files_changed=changed,
        )
    print(f"[codex] session {session_id} recorded ({len(changed)} changed files)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "end"))
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--summary", default="Codex session completed")
    parser.add_argument(
        "--model", default=None, help="model name to record (default: resolved)"
    )
    args = parser.parse_args()
    session_id = args.session_id or str(uuid.uuid4())
    if args.action == "start":
        return start(session_id, args.model)
    return end(session_id, args.summary, args.model)


if __name__ == "__main__":
    raise SystemExit(main())
