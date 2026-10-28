#!/usr/bin/env python3
"""
Lint Budget — ratchet for rules not yet in `.ruff.toml`
=======================================================
`ruff check .` reports "All checks passed" while 38 security/robustness
findings sit in the tree, because `.ruff.toml`'s `select` list omits the `S`
(bandit) and `BLE` (blind-except) rule families. Twenty `# noqa: S603` /
`# noqa: BLE001` comments are scattered through the repo suppressing rules
that were never enabled -- inert comments that read like a security check is
running.

Turning the families on outright is not available: `.ruff.toml` is protected
config (see CLAUDE.md), and a 38-error gate would just be switched off. So
this gate runs the extra families itself and holds the count under a budget
that is lowered as findings are fixed -- the count can only go down.

Budget lives in `tools/config/lint-budget.json`.

Usage:
    python tools/check_lint_budget.py            # enforce the budget
    python tools/check_lint_budget.py --list     # show current findings
"""

from __future__ import annotations

import json
import subprocess  # noqa: S404 — runs this repo's own pinned linter
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUDGET_FILE = ROOT / "tools" / "config" / "lint-budget.json"

# Rule families deliberately absent from `.ruff.toml`'s `select`, so `ruff
# check .` cannot see them. S101 (assert) is excluded because pytest asserts
# are correct usage, not a finding -- including it would add ~324 lines of
# noise that can never reach zero.
EXTRA_SELECT = "S,BLE"
EXTRA_IGNORE = "S101"


def load_budget(budget_file: Path = BUDGET_FILE) -> int:
    """Read maxFindings. Raises on a missing or malformed budget.

    Deliberately strict: a budget file that silently defaults to a large
    number is a gate that passes for the wrong reason.
    """
    payload = json.loads(budget_file.read_text(encoding="utf-8"))
    value = payload.get("maxFindings")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(
            f"{budget_file.name} must define a non-negative integer maxFindings"
        )
    return value


def count_findings(root: Path = ROOT) -> tuple[int, str]:
    """Return (finding count, raw ruff output).

    ruff exits 1 when it finds violations, which is the expected case here --
    only a hard failure (exit >1, e.g. bad config) is an error worth raising.

    `concise` output is one line per finding. The default `full` format adds
    source-context lines, which counted 381 for the same 38 findings.
    """
    proc = subprocess.run(  # noqa: S603,S607 — fixed argv, no shell
        ["ruff", "check", "--config", str(ROOT / ".ruff.toml"), "--select",  # noqa: S607 -- partial path
         EXTRA_SELECT, "--ignore", EXTRA_IGNORE, "--output-format", "concise",
         "--quiet", "--no-cache", str(root)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if proc.returncode > 1:
        raise RuntimeError(f"ruff failed: {proc.stderr.strip()}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return len(lines), proc.stdout


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__.strip())
        return 0

    if "--list" in sys.argv:
        _, raw = count_findings()
        print(raw or "(no findings)")
        return 0

    import shutil

    if shutil.which("ruff") is None:
        print("[SKIP] lint budget: ruff not installed on PATH")
        return 0

    try:
        budget = load_budget()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"[FAIL] lint budget: {exc}")
        return 1

    try:
        actual, _ = count_findings()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"[FAIL] lint budget: {exc}")
        return 1

    print(f"Extra rule families : {EXTRA_SELECT} (ignoring {EXTRA_IGNORE})")
    print(f"Findings            : {actual}")
    print(f"Budget              : {budget}")

    if actual > budget:
        print(
            f"\n[FAIL] {actual - budget} finding(s) over budget.\n"
            f"  See them: python tools/check_lint_budget.py --list\n"
            f"  Fix them, or justify a raise in {BUDGET_FILE.name} in review."
        )
        return 1

    if actual < budget:
        print(
            f"\n[OK] Under budget. Lower maxFindings to {actual} in "
            f"{BUDGET_FILE.name} so the gain cannot be silently lost."
        )
        return 0

    print("\n[OK] At budget.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
