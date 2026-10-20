#!/usr/bin/env python3
"""
Master Checklist Runner
========================
Orchestrates validation checks in priority order.

Usage:
    python .github/scripts/checklist.py .                    # Core checks
    python .github/scripts/checklist.py . --url <URL>        # Include performance

Priority:
    P0: Security Scan | P1: Lint & Type | P2: Tests | P3: UX | P4: SEO | P5: Perf
"""

import argparse
import subprocess
import sys
from pathlib import Path


class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


# Detect if we need to fall back to ASCII symbols
# (e.g. on Windows with non-UTF-8 encoding)
try:
    "✓".encode(sys.stdout.encoding or "utf-8")
    SYM_OK = "✓"
    SYM_BAD = "✗"
    SYM_WARN = "~"
except UnicodeEncodeError:
    SYM_OK = "PASS"
    SYM_BAD = "FAIL"
    SYM_WARN = "SKIP"


def print_header(text: str):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text.center(60)}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.ENDC}\n")


def print_step(text: str):
    print(f"{Colors.BOLD}{Colors.BLUE}[ ] {text}{Colors.ENDC}")


def print_success(text: str):
    print(f"{Colors.GREEN}[{SYM_OK}] {text}{Colors.ENDC}")


def print_warning(text: str):
    print(f"{Colors.YELLOW}[{SYM_WARN}] {text}{Colors.ENDC}")


def print_error(text: str):
    print(f"{Colors.RED}[{SYM_BAD}] {text}{Colors.ENDC}")


def coverage_ratchet_check(project_path: Path) -> dict | None:
    """Run the coverage ratchet when the project has opted into one.

    Returns the run_check result, or None when the project carries no budget
    file -- a project that never opted in is not failed by this gate. Extracted
    from the P2 block so both branches are unit-testable without executing the
    real ratchet (which runs the whole suite under coverage).
    """
    gate = project_path / "tools" / "coverage_gate.py"
    budget = project_path / "tools" / "config" / "coverage-budget.json"
    if not (gate.exists() and budget.exists()):
        print_warning("No coverage budget found, skipping coverage ratchet")
        return None
    return run_check(
        "Coverage Ratchet",
        [sys.executable, str(gate)],
        timeout=900,
    )


def boundary_audit_check(project_path: Path) -> dict | None:
    """Run the boundary audit in strict mode.

    Strict is mandatory: without it every harness-dir finding is downgraded to a
    warning and the script returns 0, so the "Boundary Audit" gate could never
    fail. Its limit, stated rather than implied: findings are extension-shaped --
    a file whose extension is on the harness allowlist passes, and root-level
    files stay warnings in either mode. A project source file copied into a
    harness dir can therefore still slip through; this gate catches assets, not
    content.
    """
    auditor = project_path / ".github" / "scripts" / "boundary_audit.py"
    if not auditor.exists():
        print_warning("boundary_audit.py not found, skipping Boundary Audit")
        return None
    return run_check(
        "Boundary Audit",
        [sys.executable, str(auditor), str(project_path), "--strict"],
        timeout=60,
    )


def run_check(
    name: str, command: list[str], timeout: int = 120, required: bool = True
) -> dict:
    """Run a validation check and capture results.

    ``required`` separates a mandatory tool from an optional one. A missing
    required tool is a FAILURE — the gate cannot verify what it claims to
    verify — while a missing optional tool is a clean SKIP.
    """
    print_step(f"Running: {name}")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
        )
        passed = result.returncode == 0
        if passed:
            print_success(f"{name}: PASSED")
        else:
            print_error(f"{name}: FAILED")
            if result.stderr:
                print(f"  {result.stderr[:300]}")
        return {
            "name": name,
            "passed": passed,
            "output": result.stdout,
            "skipped": False,
        }
    except subprocess.TimeoutExpired:
        print_error(f"{name}: TIMEOUT")
        return {"name": name, "passed": False, "output": "", "skipped": False}
    except FileNotFoundError:
        if required:
            print_error(f"{name}: required tool not found on PATH")
            return {"name": name, "passed": False, "output": "", "skipped": False}
        print_warning(f"{name}: optional tool not installed, skipping")
        return {"name": name, "passed": True, "output": "", "skipped": True}


def print_summary(results: list[dict]) -> bool:
    """Print final summary report."""
    print_header("CHECKLIST SUMMARY")
    passed = sum(1 for r in results if r["passed"] and not r.get("skipped"))
    failed = sum(1 for r in results if not r["passed"] and not r.get("skipped"))
    skipped = sum(1 for r in results if r.get("skipped"))

    print(
        f"Total: {len(results)} "
        f"| {Colors.GREEN}Passed: {passed}{Colors.ENDC} "
        f"| {Colors.RED}Failed: {failed}{Colors.ENDC} "
        f"| {Colors.YELLOW}Skipped: {skipped}{Colors.ENDC}\n"
    )

    for r in results:
        icon = (
            f"{Colors.YELLOW}{SYM_WARN}{Colors.ENDC}"
            if r.get("skipped")
            else (
                f"{Colors.GREEN}{SYM_OK}{Colors.ENDC}"
                if r["passed"]
                else f"{Colors.RED}{SYM_BAD}{Colors.ENDC}"
            )
        )
        print(f"  {icon} {r['name']}")

    if failed > 0:
        print_error(f"\n{failed} check(s) FAILED — fix before proceeding")
        return False
    print_success("\nAll checks PASSED")
    return True


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface, separate from main() so it can be tested without running
    the gate: checking that `--fast` exists and defaults correctly beats asserting
    it by executing a five-minute checklist."""
    parser = argparse.ArgumentParser(description="Master validation checklist")
    parser.add_argument("project", help="Project path to validate")
    parser.add_argument("--url", help="URL for performance checks")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip the coverage ratchet (it re-runs the whole suite, ~3 min). Use "
        "while iterating; run the full gate before a commit.",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    project_path = Path(args.project).resolve()
    if not project_path.exists():
        print_error(f"Project path does not exist: {project_path}")
        sys.exit(1)

    print_header("SOLO-CODE CHECKLIST")
    print(f"Project: {project_path}")

    results = []

    # P0: Security scan (scan for hardcoded secrets, exclude vendor dirs)
    print_header("P0: SECURITY")
    scanner = project_path / ".github/scripts/security_scan.py"
    if scanner.exists():
        results.append(
            run_check(
                "Secret Scan",
                [sys.executable, str(scanner), str(project_path), "--strict"],
                timeout=120,
            )
        )
    else:
        print_warning("security_scan.py not found, skipping secret scan")

    # P1: Lint & Type Check
    print_header("P1: CODE QUALITY")
    results.append(
        run_check("Ruff Linter", ["ruff", "check", str(project_path)], timeout=120)
    )

    npm = project_path / "package.json"
    if npm.exists():
        results.append(
            run_check(
                "ESLint",
                ["npx", "eslint", ".", "--max-warnings", "0"],
                timeout=120,
                required=False,
            )
        )
    else:
        print_warning("No package.json found, skipping ESLint")

    # P2: Tests
    print_header("P2: TESTS")

    # Boundary Audit — ensure no project code leaked into harness dirs
    boundary_result = boundary_audit_check(project_path)
    if boundary_result is not None:
        results.append(boundary_result)

    # Harness eval
    eval_script = project_path / ".github" / "scripts" / "eval_harness.py"
    if eval_script.exists():
        results.append(
            run_check(
                "Harness Eval",
                [sys.executable, str(eval_script), str(project_path)],
                timeout=120,
            )
        )
    else:
        print_warning("eval_harness.py not found, skipping Harness Eval")

    # Guard hook syntax check (Claude Code -- .opencode/tests/test-guard.mjs
    # was removed in v4.0.0 along with the OpenCode engine)
    guard_hook = project_path / ".claude" / "hooks" / "guard.py"
    if guard_hook.exists():
        results.append(
            run_check(
                "Guard Hook Syntax",
                [
                    sys.executable,
                    "-c",
                    f"import py_compile; py_compile.compile(r'{guard_hook}', doraise=True)",
                ],
                timeout=30,
            )
        )
    else:
        print_warning("guard.py not found, skipping Guard Hook check")

    # Python pytest suite under tools/
    tools_dir = project_path / "tools"
    if tools_dir.is_dir() and any(tools_dir.rglob("test_*.py")):
        # timeout=300, not 120: the suite runs ~95-130s on Windows (the guard
        # parity corpus launches node + python per case), so 120 sat on the
        # boundary and produced intermittent "Pytest: TIMEOUT" failures under
        # load. The other long steps (coverage, npm) already allow 300-900s.
        results.append(
            run_check(
                "Pytest",
                [sys.executable, "-m", "pytest", str(tools_dir)],
                timeout=300,
            )
        )
    else:
        print_warning("No tools/ test suite found, skipping pytest")

    coverage_result = None if args.fast else coverage_ratchet_check(project_path)
    if coverage_result is not None:
        results.append(coverage_result)

    if npm.exists():
        results.append(
            run_check(
                "Tests",
                ["npm", "test", "--", "--passWithNoTests"],
                timeout=300,
                required=False,
            )
        )
    else:
        print_warning("No package.json found, skipping npm tests")

    # P3: Build check
    print_header("P3: BUILD")
    if npm.exists():
        results.append(
            run_check("Build", ["npm", "run", "build"], timeout=300, required=False)
        )

    all_passed = print_summary(results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
