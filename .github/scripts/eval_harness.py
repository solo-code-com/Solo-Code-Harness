#!/usr/bin/env python3
"""
Solo-Code Harness Evaluator
============================
Evaluates harness behavior: hook correctness, security enforcement,
context management, and artifact integrity. Zero external dependencies.

Usage:
    python .github/scripts/eval_harness.py .              # Run all evals
    python .github/scripts/eval_harness.py . --suite security  # Security only
    python .github/scripts/eval_harness.py . --suite all --verbose

Suites:
    security    — Gate guard, secret scan, permission patterns
    hooks       — Hook file integrity, syntax, registration
    artifacts   — AGENTS.md, checklist, templates
    context     — State files, compaction thresholds
    all         — Everything (default)
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────


class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


@dataclass
class EvalResult:
    name: str
    passed: bool
    message: str = ""
    suite: str = ""


@dataclass
class EvalRunner:
    project: Path
    verbose: bool = False
    results: list[EvalResult] = field(default_factory=list)

    def ok(self, name: str, suite: str = "", msg: str = "") -> None:
        self.results.append(EvalResult(name, True, msg, suite))
        if self.verbose:
            print(f"  {Colors.GREEN}PASS{Colors.ENDC} {name}")

    def fail(self, name: str, suite: str = "", msg: str = "") -> None:
        self.results.append(EvalResult(name, False, msg, suite))
        print(f"  {Colors.RED}FAIL{Colors.ENDC} {name}: {msg}")

    def assert_true(
        self, condition: bool, name: str, suite: str = "", msg: str = ""
    ) -> None:
        if condition:
            self.ok(name, suite, msg)
        else:
            self.fail(name, suite, msg)

    def assert_file(self, filepath: Path, name: str, suite: str = "") -> None:
        if filepath.is_file():
            self.ok(name, suite)
        else:
            self.fail(name, suite, f"Missing: {filepath}")

    def run_node_check(self, script: Path) -> tuple[bool, str]:
        """Run a Node.js script -c (syntax check) and return (ok, message)."""
        try:
            result = subprocess.run(
                ["node", "-c", str(script)],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(self.project),
            )
            if result.returncode == 0:
                return True, ""
            return False, result.stderr.strip()
        except FileNotFoundError:
            return False, "node not found"
        except subprocess.TimeoutExpired:
            return False, "timeout"

    # ── Security Suite ────────────────────────────────────────────────────

    def eval_security_patterns(self) -> None:
        suite = "security"
        print(f"\n{Colors.BOLD}  Security Patterns{Colors.ENDC}")

        scan = self.project / ".github" / "scripts" / "security_scan.py"
        self.assert_file(scan, "security_scan.py exists", suite)

        # Run security scan — it should pass on the project itself
        try:
            result = subprocess.run(
                [sys.executable, str(scan), str(self.project)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(self.project),
            )
            self.assert_true(
                result.returncode == 0,
                "Security scan passes on own codebase",
                suite,
                f"exit={result.returncode}",
            )
        except Exception as e:
            self.fail("Security scan execution", suite, str(e))

        # Check kilo.jsonc permission patterns
        config = self.project / "kilo.jsonc"
        if config.is_file():
            content = config.read_text(encoding="utf-8")
            self.assert_true(
                "rm -rf /*" in content, "Gate guard: rm -rf blocked in config", suite
            )
            self.assert_true(
                "DROP TABLE" in content,
                "Gate guard: DROP TABLE blocked in config",
                suite,
            )
            self.assert_true(
                "git push --force" in content,
                "Gate guard: force push blocked in config",
                suite,
            )

        # Check AGENTS.md has security rules
        agents = self.project / "AGENTS.md"
        if agents.is_file():
            content = agents.read_text(encoding="utf-8")
            self.assert_true(
                "security_scan.py" in content,
                "AGENTS.md: references security scan",
                suite,
            )
            self.assert_true(
                "Never hardcode credentials" in content,
                "AGENTS.md: no hardcoded credentials rule",
                suite,
            )

    # ── Hooks Suite ───────────────────────────────────────────────────────

    def eval_hooks_integrity(self) -> None:
        suite = "hooks"
        print(f"\n{Colors.BOLD}  Hook Integrity{Colors.ENDC}")

        hooks_json = self.project / ".kilo" / "hooks" / "hooks.json"
        self.assert_file(hooks_json, "hooks.json exists", suite)

        if hooks_json.is_file():
            try:
                data = json.loads(hooks_json.read_text(encoding="utf-8"))
                hook_types = data.get("hooks", {})
                all_hooks = []
                for hook_list in hook_types.values():
                    all_hooks.extend(hook_list)
                self.assert_true(
                    len(all_hooks) >= 6,
                    f"At least 6 hooks registered (found {len(all_hooks)})",
                    suite,
                )
            except json.JSONDecodeError as e:
                self.fail("hooks.json is valid JSON", suite, str(e))

        # Verify each hook script exists and has valid syntax
        hook_map = {
            "gate-guard": "pre-tool-use/gate-guard.js",
            "secret-scan": "pre-tool-use/secret-scan.js",
            "config-protection": "pre-tool-use/config-protection.js",
            "governance-capture": "pre-tool-use/governance-capture.js",
            "quality-gate": "post-tool-use/quality-gate.js",
            "console-log-check": "post-tool-use/console-log-check.js",
            "context-monitor": "post-tool-use/context-monitor.js",
            "edit-accumulator": "post-tool-use/edit-accumulator.js",
            "session-start": "session/session-start.js",
            "session-end": "session/session-end.js",
        }
        hook_dir = self.project / ".kilo" / "hooks"
        for name, relpath in hook_map.items():
            script = hook_dir / relpath
            self.assert_file(script, f"Hook file: {name}", suite)
            if script.is_file():
                ok, err = self.run_node_check(script)
                self.assert_true(ok, f"Hook syntax: {name}", suite, err)

    # ── Artifacts Suite ───────────────────────────────────────────────────

    def eval_artifacts(self) -> None:
        suite = "artifacts"
        print(f"\n{Colors.BOLD}  Artifacts{Colors.ENDC}")

        agents = self.project / "AGENTS.md"
        self.assert_file(agents, "AGENTS.md exists", suite)
        if agents.is_file():
            content = agents.read_text(encoding="utf-8")
            required = [
                ("Known Constraints", "Known Constraints section"),
                ("Not Allowed", "Not Allowed section"),
                ("Escalation", "Escalation section"),
                ("Verification Gates", "Verification Gates section"),
                ("Security Rules", "Security Rules section"),
                ("Git Commit Convention", "Git Commit Convention section"),
                ("Behavior Rules", "Behavior Rules section"),
            ]
            for pattern, label in required:
                self.assert_true(pattern in content, f"AGENTS.md: {label}", suite)

            # Must not contain dead template markers
            self.assert_true(
                "<!-- One paragraph:" not in content,
                "AGENTS.md: no unfilled template markers",
                suite,
            )

        # Checklist exists
        checklist = self.project / ".github" / "scripts" / "checklist.py"
        self.assert_file(checklist, "checklist.py exists", suite)

        # HARNESS_CHECKLIST exists
        hc_path = self.project / ".kilo" / "instruction" / "harness-checklist.md"
        self.assert_file(hc_path, "harness-checklist.md exists", suite)

        # IMPLEMENT.md template exists
        implement_tmpl = self.project / ".kilo" / "templates" / "IMPLEMENT.md"
        self.assert_file(implement_tmpl, "IMPLEMENT.md template exists", suite)

        # Commands directory has expected commands
        cmd_dir = self.project / ".kilo" / "command"
        expected_cmds = ["plan", "verify", "debug", "test", "brainstorm", "decide"]
        for cmd in expected_cmds:
            self.assert_file(cmd_dir / f"{cmd}.md", f"Command /{cmd} exists", suite)

    # ── Context Suite ─────────────────────────────────────────────────────

    def eval_context_management(self) -> None:
        suite = "context"
        print(f"\n{Colors.BOLD}  Context Management{Colors.ENDC}")

        ctx_monitor = (
            self.project / ".kilo" / "hooks" / "post-tool-use" / "context-monitor.js"
        )
        if ctx_monitor.is_file():
            content = ctx_monitor.read_text(encoding="utf-8")
            self.assert_true(
                "OUTPUT_TRIM_LINES" in content,
                "Context monitor: output size threshold defined",
                suite,
            )
            self.assert_true(
                "checkToolOutputSize" in content,
                "Context monitor: observe-only design (no output mutation)",
                suite,
            )
            self.assert_true(
                "NEVER modifies" in content or "DO NOT modify" in content,
                "Context monitor: docs state output is never modified",
                suite,
            )
            self.assert_true(
                "buildTrimmedResponse" not in content
                and "trimToolOutput" not in content,
                "Context monitor: no legacy output-trimming functions",
                suite,
            )
            self.assert_true(
                "TOOL_CALL_WARN_1" in content and "TOOL_CALL_WARN_2" in content,
                "Context monitor: two-stage warning thresholds",
                suite,
            )
            self.assert_true(
                "TOKEN_LOG_FILE" in content,
                "Context monitor: token logging configured",
                suite,
            )
            self.assert_true(
                "HIGH_OUTPUT_TOOLS" in content,
                "Context monitor: high-output tool list defined",
                suite,
            )
            self.assert_true(
                "estimateTokens" in content,
                "Context monitor: token estimation logic",
                suite,
            )

        # Session hooks have observability
        session_end = self.project / ".kilo" / "hooks" / "session" / "session-end.js"
        if session_end.is_file():
            content = session_end.read_text(encoding="utf-8")
            self.assert_true(
                "Session Summary" in content, "Session end: emits summary report", suite
            )
            self.assert_true(
                "estimatedCostUSD" in content or "cost" in content.lower(),
                "Session end: tracks cost estimation",
                suite,
            )
            self.assert_true(
                "formatDuration" in content,
                "Session end: human-readable duration",
                suite,
            )

        # State directory
        state_dir = self.project / ".kilo" / "state"
        self.assert_true(
            state_dir.is_dir(), "State directory exists (.kilo/state)", suite
        )

    # ── Summary ───────────────────────────────────────────────────────────

    def print_summary(self) -> bool:
        suites = {}
        for r in self.results:
            s = r.suite or "general"
            if s not in suites:
                suites[s] = {"passed": 0, "failed": 0, "results": []}
            suites[s]["results"].append(r)
            if r.passed:
                suites[s]["passed"] += 1
            else:
                suites[s]["failed"] += 1

        print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.CYAN}  EVAL SUMMARY{Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.ENDC}")

        total_pass = 0
        total_fail = 0
        for name, s in suites.items():
            total = s["passed"] + s["failed"]
            bar = f"{s['passed']}/{total} passed"
            color = Colors.GREEN if s["failed"] == 0 else Colors.RED
            print(f"  {name:12s}  {color}{bar}{Colors.ENDC}")
            total_pass += s["passed"]
            total_fail += s["failed"]

        total = total_pass + total_fail
        print(f"\n  {'Total':12s}  {total_pass}/{total} passed, {total_fail} failed")

        all_pass = total_fail == 0
        if all_pass:
            print(f"\n{Colors.GREEN}  All evals passed{Colors.ENDC}")
        else:
            print(f"\n{Colors.RED}  {total_fail} eval(s) FAILED{Colors.ENDC}")
            if self.verbose:
                for r in self.results:
                    if not r.passed:
                        print(f"    - [{r.suite}] {r.name}: {r.message}")

        return all_pass


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Solo-Code Harness Evaluator")
    parser.add_argument("project", help="Project path to evaluate")
    parser.add_argument(
        "--suite",
        choices=["security", "hooks", "artifacts", "context", "all"],
        default="all",
        help="Eval suite to run (default: all)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show all checks, not just failures",
    )
    args = parser.parse_args()

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"{Colors.RED}Project path does not exist: {project}{Colors.ENDC}")
        sys.exit(1)

    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}  SOLO-CODE HARNESS EVALUATOR{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.ENDC}")
    print(f"  Project: {project}")

    runner = EvalRunner(project, verbose=args.verbose)

    suites = {
        "security": [runner.eval_security_patterns],
        "hooks": [runner.eval_hooks_integrity],
        "artifacts": [runner.eval_artifacts],
        "context": [runner.eval_context_management],
    }

    if args.suite == "all":
        for suite_name, funcs in suites.items():
            print(f"\n{Colors.HEADER}-- Suite: {suite_name} --{Colors.ENDC}")
            for fn in funcs:
                fn()
    else:
        for fn in suites[args.suite]:
            fn()

    all_pass = runner.print_summary()
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
