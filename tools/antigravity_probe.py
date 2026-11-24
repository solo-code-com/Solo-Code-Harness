#!/usr/bin/env python3
"""Diagnose the Antigravity (``agy.exe``) headless worker integration.

Read-only by default: reports whether the binary is discoverable and which
models it offers, and flags whether the expected ``gemini-3.8-flash-*`` ids are
present. Pass ``--live`` to also run one tiny read-only delegation, which costs
account quota.

The headless permission behavior this probe documents (a plain read-only run
cannot answer the ``command`` prompt, so ``run_command`` is auto-denied while the
result still reports ``SUCCESS``) is why the wrapper inspects ``denied_actions``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.antigravity_delegate import (  # noqa: E402
    DEFAULT_MODEL,
    build_prompt,
    find_agy_binary,
)

EXPECTED_MODELS = (
    "gemini-3.8-flash-low",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-high",
)


def parse_models_output(output: str) -> list[str]:
    """Extract model ids from ``agy.exe models`` output (id<TAB>label per line)."""
    models: list[str] = []
    for line in output.splitlines():
        name = line.split("\t", 1)[0].strip()
        if not name or name.lower().startswith("fetching"):
            continue
        models.append(name)
    return models


def list_models(agy_binary: str) -> list[str]:
    """Return the model ids agy advertises, merging stdout and stderr."""
    try:
        proc = subprocess.run(  # noqa: S603 -- binary is discovered or explicitly supplied by the caller
            [agy_binary, "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_models_output(proc.stdout) or parse_models_output(proc.stderr)


def build_report(agy_binary: str | None) -> dict[str, Any]:
    """Assemble the static diagnostic report without spending quota."""
    report: dict[str, Any] = {
        "binary": agy_binary,
        "found": agy_binary is not None,
        "default_model": DEFAULT_MODEL,
        "expected_models": list(EXPECTED_MODELS),
        "models": [],
        "missing_models": [],
    }
    if not agy_binary:
        return report
    models = list_models(agy_binary)
    report["models"] = models
    report["missing_models"] = [m for m in EXPECTED_MODELS if m not in models]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Antigravity headless worker diagnostic"
    )
    parser.add_argument(
        "--agy-bin", help="Path to agy executable (default: auto-discover)"
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON")
    args = parser.parse_args(argv)

    agy_binary = args.agy_bin or find_agy_binary()
    report = build_report(agy_binary)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if not report["found"]:
            print(
                "[antigravity_probe] agy binary NOT found; install Antigravity CLI or pass --agy-bin"
            )
            return 1
        print(f"[antigravity_probe] binary: {report['binary']}")
        print(f"[antigravity_probe] models: {len(report['models'])}")
        print(f"[antigravity_probe] default: {report['default_model']}")
        if report["missing_models"]:
            print(
                f"[antigravity_probe] MISSING expected models: {', '.join(report['missing_models'])}"
            )
        else:
            print("[antigravity_probe] all expected gemini-3.8-flash ids present")

    # Surface the read-only prompt policy so the caller knows what a run implies.
    prompt = build_prompt("noop", allow_dir=None, auto_approve=False, guardrail=True)
    if not args.json:
        print(f"[antigravity_probe] read-only policy: {'READ-ONLY MODE' in prompt}")
    return 0 if report["found"] and not report["missing_models"] else 1


if __name__ == "__main__":
    sys.exit(main())
