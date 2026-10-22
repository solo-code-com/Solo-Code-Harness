#!/usr/bin/env python3
"""
Coverage Gate — per-file coverage ratchet
==========================================
Tracks test coverage per file and prevents coverage from decreasing.
Coverage can only stay the same or improve, never regress.

Budget lives in `tools/config/coverage-budget.json` with format:
{
  "files": {
    "path/to/file.py": 85.5,
    "another/file.py": 100.0
  }
}

Budget keys are POSIX-relative paths (`tools/garden.py`), never OS-native ones,
so the same file is valid on Windows, macOS and Linux and CI can compare it
against a run on any platform.

Usage:
    python tools/coverage_gate.py             # enforce the budget
    python tools/coverage_gate.py --update    # update budget to current coverage
    python tools/coverage_gate.py --report    # show current coverage vs budget
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUDGET_FILE = ROOT / "tools" / "config" / "coverage-budget.json"
COVERAGE_FILE = ROOT / ".coverage"

# Live-API tests: coverage depends on whether the provider answered, so a drop here
# says nothing about lost tests. Exempt from the ratchet, never written by --update.
# The exemption covers a lower number only: the file must still appear in the run,
# because an absent entry means its tests were deleted or failed to collect.
RATCHET_EXEMPT = {"tools/test_e2e.py"}


def load_budget(budget_file: Path = BUDGET_FILE) -> dict[str, float]:
    """Read per-file coverage budget.

    Returns empty dict if file doesn't exist (first run).
    Raises on malformed JSON.
    """
    if not budget_file.exists():
        # `return` here made load_budget() return None, and --report then did
        # None.keys() -> AttributeError. Latent only while the budget file exists.
        return {}

    try:
        payload = json.loads(budget_file.read_text(encoding="utf-8"))
        files = payload.get("files", {})

        if not isinstance(files, dict):
            raise ValueError("budget must contain 'files' dict")

        # Validate all values are numeric
        for path, coverage in files.items():
            if not isinstance(coverage, int | float) or coverage < 0 or coverage > 100:
                raise ValueError(
                    f"Invalid coverage for {path}: {coverage} (must be 0-100)"
                )

        return files
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Malformed budget file: {exc}") from exc


def save_budget(
    coverage_data: dict[str, float], budget_file: Path = BUDGET_FILE
) -> None:
    """Save per-file coverage budget to JSON."""
    budget_file.parent.mkdir(parents=True, exist_ok=True)

    payload = {"files": dict(sorted(coverage_data.items()))}

    # newline="\n": on Windows the default translation writes CRLF, which
    # contradicts .gitattributes (`* text=auto eol=lf`) and leaves the file
    # dirty until Git renormalises it. Write LF on every platform.
    budget_file.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def run_coverage() -> dict[str, float]:
    """Run pytest with coverage and return per-file coverage percentages.

    Returns dict mapping file path to coverage percentage.
    """
    # Check if pytest-cov is available
    check_proc = subprocess.run(
        ["python", "-m", "pytest", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    if check_proc.returncode != 0:
        raise RuntimeError(
            "pytest not installed. Install with: pip install pytest pytest-cov"
        )

    # Run pytest with coverage
    proc = subprocess.run(
        [
            "python",
            "-m",
            "pytest",
            "tools/",
            "--ignore=deepseek-harness-master",
            "--cov=tools",
            "--cov=.claude",
            "--cov=.github/scripts",
            "--cov-report=json",
            "--cov-report=term-missing",
            "-q",
        ],
        capture_output=True,
        text=True,
        # The suite measured ~345s locally (1668 tests). checklist.py gives this gate a
        # 900s outer budget, so 840 leaves headroom for a slower CI runner while still
        # finishing before the outer limit trips.
        timeout=840,
        check=False,
        cwd=ROOT,
    )

    # Coverage writes to coverage.json by default
    coverage_json = ROOT / "coverage.json"

    if not coverage_json.exists():
        # Check if pytest-cov is the issue
        if "--cov" in proc.stderr or "unrecognized arguments" in proc.stderr:
            raise RuntimeError(
                "pytest-cov not installed. Install with: pip install pytest-cov"
            )
        raise RuntimeError(
            f"coverage.json not found. pytest output:\n{proc.stdout}\n{proc.stderr}"
        )

    try:
        data = json.loads(coverage_json.read_text(encoding="utf-8"))
    finally:
        # Clean up coverage files
        coverage_json.unlink(missing_ok=True)
        COVERAGE_FILE.unlink(missing_ok=True)

    # Extract per-file coverage
    files_coverage = {}
    for file_path, file_data in data.get("files", {}).items():
        summary = file_data.get("summary", {})
        percent = summary.get("percent_covered", 0.0)

        # Normalize path to relative from ROOT
        try:
            rel_path = Path(file_path).relative_to(ROOT).as_posix()
        except ValueError:
            # coverage.py writes relative paths when run from ROOT, so there is
            # nothing to strip. Keep the POSIX form anyway: on Windows the native
            # form (`tools\gate.py`) matches none of the committed budget keys.
            rel_path = Path(file_path).as_posix()

        files_coverage[rel_path] = round(percent, 2)

    return files_coverage


def check_ratchet(
    budget: dict[str, float], current: dict[str, float]
) -> tuple[bool, list[str]]:
    """Check if coverage decreased for any file.

    Returns:
        (passed, violations) where violations lists files with decreased coverage
    """
    violations = []

    # Exempt files are not compared, so an oscillating live-API file can neither
    # raise a violation nor be counted as a path-mismatch casualty. It must still
    # run, though: an absent entry means its tests were deleted or failed to collect.
    tracked = {
        path: old_coverage
        for path, old_coverage in budget.items()
        if path not in RATCHET_EXEMPT
    }
    for path in sorted(budget):
        if path in RATCHET_EXEMPT and path not in current:
            violations.append(
                f"{path} is exempt from coverage, but it did not run at all - "
                "deleted tests, or a collection error?"
            )

    # A whole-platform separator difference shows up as "every file dropped to
    # 0%". A genuine regression removes one or two files, never half of them, so
    # below that threshold the per-file report is still authoritative. When the
    # threshold trips, the present files are still compared: the diagnostic
    # explains the missing majority, not why a surviving file fell.
    missing = [path for path in tracked if path not in current]
    mismatch = bool(tracked) and len(missing) * 2 > len(tracked)
    if mismatch:
        violations.append(
            f"path mismatch: {len(missing)} of {len(tracked)} budget entries are "
            "missing from this run (Windows vs POSIX separators?) - run "
            "python tools/coverage_gate.py --update"
        )

    for file_path, old_coverage in tracked.items():
        if mismatch and file_path not in current:
            continue
        new_coverage = current.get(file_path, 0.0)

        if new_coverage < old_coverage:
            diff = old_coverage - new_coverage
            violations.append(
                f"  {file_path}: {old_coverage:.1f}% -> {new_coverage:.1f}% "
                f"(-{diff:.1f}%)"
            )

    return len(violations) == 0, violations


def harness_tests_present(root: Path) -> bool:
    """Does this checkout contain the harness's own tests?

    A deployed target is a runtime copy: `deploy.py` excludes `tools/test_*.py` by policy, so the
    budget's baselines cannot be reproduced there and every file reads 0%. That is a different kind
    of checkout, not a regression.
    """
    return any((root / "tools").glob("test_*.py"))


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__.strip())
        return 0

    if "--update" in sys.argv:
        print("Running coverage...")
        try:
            current = run_coverage()
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            print(f"[FAIL] coverage run: {exc}")
            return 1

        # A live-API file's coverage tracks the network, not the suite, so it is
        # never a baseline. Drop it from this run, and re-add a pre-existing entry
        # only when the file actually ran: a deleted file must not outlive itself.
        try:
            existing = load_budget()
        except (OSError, ValueError):
            existing = {}
        skipped = [path for path in current if path in RATCHET_EXEMPT]
        for path in skipped:
            del current[path]
        for path, saved_coverage in existing.items():
            if path in RATCHET_EXEMPT and path in skipped:
                current[path] = saved_coverage

        save_budget(current)
        if skipped:
            print(
                f"[SKIP] {len(skipped)} live-API file(s) not written to the budget: "
                "coverage depends on whether the provider answered"
            )
        print(f"[OK] Updated budget: {len(current)} files tracked")
        print(f"     Saved to: {BUDGET_FILE}")
        return 0

    if "--report" in sys.argv:
        try:
            budget = load_budget()
            current = run_coverage()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"[FAIL] coverage report: {exc}")
            return 1

        print("File Coverage Report")
        print("=" * 70)

        all_files = sorted(set(budget.keys()) | set(current.keys()))

        for file_path in all_files:
            # --report must not call an exempt file a regression while enforcement
            # passes it.
            if file_path in RATCHET_EXEMPT:
                continue

            old = budget.get(file_path)
            new = current.get(file_path)

            if old is None:
                print(f"  [NEW]  {file_path}: {new:.1f}%")
            elif new is None:
                print(f"  [GONE] {file_path}: was {old:.1f}%")
            elif new < old:
                print(
                    f"  [DOWN] {file_path}: {old:.1f}% -> {new:.1f}% (-{old - new:.1f}%)"
                )
            elif new > old:
                print(
                    f"  [UP]   {file_path}: {old:.1f}% -> {new:.1f}% (+{new - old:.1f}%)"
                )
            else:
                print(f"  [OK]   {file_path}: {new:.1f}%")

        return 0

    # Default: enforce budget
    # Measured on a real target: the gate failed with 40-odd files at 0% while the project was
    # perfectly fine, because a deployed copy ships no harness tests and the budget keeps the full
    # repo's baselines. Nothing to enforce is not the same as a regression.
    if not harness_tests_present(ROOT):
        print(
            "[SKIP] No tools/test_*.py in this checkout: a deployed runtime copy has no harness"
        )
        print(
            "       tests, so the coverage baselines do not apply here. Nothing to enforce."
        )
        return 0

    try:
        budget = load_budget()
    except (OSError, ValueError) as exc:
        print(f"[FAIL] coverage budget: {exc}")
        return 1

    if not budget:
        print("[WARN] No coverage budget found. Run with --update to create one.")
        return 0

    print("Running coverage...")
    try:
        current = run_coverage()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"[FAIL] coverage run: {exc}")
        return 1

    passed, violations = check_ratchet(budget, current)

    print(f"Files tracked       : {len(budget)}")
    print(f"Files in current run: {len(current)}")

    if not passed:
        print(f"\n[FAIL] Coverage decreased for {len(violations)} file(s):")
        for violation in violations:
            print(violation)
        print(
            "\nFix the coverage regressions, or run with --update to accept "
            "the new baseline (requires justification in review)."
        )
        return 1

    # Check for improvements
    improvements = []
    for file_path, new_coverage in current.items():
        old_coverage = budget.get(file_path, 0.0)
        if new_coverage > old_coverage:
            diff = new_coverage - old_coverage
            improvements.append(f"  {file_path}: +{diff:.1f}%")

    if improvements:
        print(f"\n[OK] Coverage improved for {len(improvements)} file(s):")
        for imp in improvements[:5]:  # Show first 5
            print(imp)
        if len(improvements) > 5:
            print(f"  ... and {len(improvements) - 5} more")
        print("\nRun with --update to lock in these gains.")
        return 0

    print("\n[OK] Coverage maintained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
