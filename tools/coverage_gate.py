#!/usr/bin/env python3
"""
Coverage Gate — per-file coverage ratchet
==========================================
Tracks test coverage per file and prevents coverage from decreasing.
Coverage can only stay the same or improve, never regress, except that a
production file may lose up to COVERAGE_TOLERANCE (1.0 point) without failing,
because files exercised through subprocesses are measured noisily; such drops
are still reported as notes.

Budget lives in `tools/config/coverage-budget.json` with format:
{
  "platforms": {
    "win32": {
      "path/to/file.py": 85.5,
      "another/file.py": 100.0
    }
  },
  "test_counts": {
    "win32": {
      "tools/test_x.py": 12
    }
  }
}

Each platform owns its section because coverage percentages are not comparable
across platforms: `tools/test_win_sandbox.py` is 100% on Windows and 51.8% on
Linux (its tests skip there), and `tools/session_registry.py` is 92.1% against
83.5% because the Windows branch never runs on Linux. One shared floor therefore
fails on every platform but the one it was measured on.

Budget keys are POSIX-relative paths (`tools/garden.py`), never OS-native ones,
so the same key is valid on Windows, macOS and Linux.

Test files keep their entries but are never compared numerically, because their
percentage measures how much of the test code this platform ran (a POSIX-only test
skips on Windows), not how much production code the suite exercises. Their
presence is still required: a budgeted test file missing from the run means its
tests were deleted, renamed or failed to collect.

`test_counts` closes the last gap in that rule. A test file whose test bodies were
emptied still collects and still appears in the run, so its presence proves
nothing, and its percentage is not compared. Each platform records how many tests
every test file collected there; a file that collects fewer fails, and --capture
emits those baselines as `TEST_COUNTS` chunks.

Usage:
    python tools/coverage_gate.py             # enforce this platform's budget
    python tools/coverage_gate.py --update    # rewrite this platform's section
    python tools/coverage_gate.py --report    # show current coverage vs budget
    python tools/coverage_gate.py --capture   # emit a baseline when this platform has none
    python tools/coverage_gate.py --force-capture  # re-emit the baseline even when one exists
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

# Percentage points a production file may lose before the ratchet fails.
# A file exercised through a subprocess is measured by the parent's import, not the
# child's execution, so its number is noisy: measured on CI, `.github/scripts/
# boundary_audit.py` recorded 65.1% on the linux baseline and 64.2% on the very next
# enforcing run of the same commit, with no code change. Treating that swing as a
# regression reddens a green tree at random; a drop within this many points is a
# note instead, so it stays visible without failing the gate.
COVERAGE_TOLERANCE = 1.0

# Percentage below which a test file's coverage has collapsed rather than skipped.
# A test file's percentage swings with platform skips, so it cannot use the 1.0
# production tolerance; but a fall to ~0% is not a skip - it is a deleted test
# body, which still collects the same number of tests and still appears in the
# run. 20 points absorbs a real platform gap while still catching total loss.
TEST_FILE_COVERAGE_FLOOR = 20.0


def is_test_file(path: str) -> bool:
    """A `test_` basename marks a test file, whatever directory holds it."""
    return Path(path).name.startswith("test_")


def load_budget(budget_file: Path = BUDGET_FILE) -> dict[str, dict[str, float]]:
    """Read the coverage budget as one section per platform.

    Returns empty dict if file doesn't exist (first run).
    Raises on malformed JSON.

    A file still in the OLD shape - a single top-level `"files"` map - is read as
    `{"win32": <those files>}` and not as a neutral budget. Those numbers were
    measured on Windows, so an older checkout keeps working while a Linux run no
    longer enforces Windows percentages it cannot match.
    """
    if not budget_file.exists():
        # `return` here made load_budget() return None, and --report then did
        # None.keys() -> AttributeError. Latent only while the budget file exists.
        return {}

    try:
        payload = json.loads(budget_file.read_text(encoding="utf-8"))
        platforms = payload.get("platforms")

        if platforms is None:
            # OLD shape: one flat map, measured on the Windows runner.
            files = payload.get("files", {})
            if not isinstance(files, dict):
                raise ValueError("budget must contain 'files' dict")
            platforms = {"win32": files} if files else {}

        if not isinstance(platforms, dict):
            raise ValueError("budget must contain 'platforms' dict")

        # Validate every platform section the same way
        for platform, files in platforms.items():
            if not isinstance(files, dict):
                raise ValueError(f"platform {platform} must map to a 'files' dict")
            for path, coverage in files.items():
                if (
                    not isinstance(coverage, int | float)
                    or coverage < 0
                    or coverage > 100
                ):
                    raise ValueError(
                        f"Invalid coverage for {platform}:{path}: {coverage} "
                        "(must be 0-100)"
                    )

        return platforms
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Malformed budget file: {exc}") from exc


def load_test_counts(
    budget_file: Path = BUDGET_FILE,
) -> dict[str, dict[str, int]]:
    """Read the collected-test baselines, one section per platform.

    Returns empty dict when the file is absent or has no `test_counts` section,
    which means no platform has a count baseline yet. Raises on malformed JSON or a
    section that is not `{platform: {path: int}}`.

    Kept separate from load_budget() so the coverage ratchet keeps reading exactly
    the shape it was written against: `test_counts` is a sibling of `platforms`,
    not a field inside it.
    """
    if not budget_file.exists():
        return {}

    try:
        payload = json.loads(budget_file.read_text(encoding="utf-8"))
        sections = payload.get("test_counts", {})

        if not isinstance(sections, dict):
            raise ValueError("budget must contain a 'test_counts' dict")

        for platform, counts in sections.items():
            if not isinstance(counts, dict):
                raise ValueError(
                    f"test_counts for {platform} must map to a 'file: count' dict"
                )
            for path, count in counts.items():
                # bool is an int subclass, so True would pass as a count of 1.
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise ValueError(
                        f"Invalid test count for {platform}:{path}: {count} "
                        "(must be a non-negative integer)"
                    )

        return sections
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Malformed budget file: {exc}") from exc


def save_budget(
    coverage_data: dict[str, float],
    budget_file: Path = BUDGET_FILE,
    platform: str | None = None,
    test_counts: dict[str, int] | None = None,
) -> None:
    """Save one platform's coverage budget, leaving the other sections as they are.

    Only `platform` (the running one by default) is replaced: a baseline measured
    on another platform cannot be reproduced here, so rewriting it would lower or
    raise a number this run has no evidence for.

    `test_counts`, when given, replaces this platform's collected-test section the
    same way. None preserves whatever the file already holds, so a caller that only
    measured coverage cannot drop the counts.
    """
    platform = platform or sys.platform
    budget_file.parent.mkdir(parents=True, exist_ok=True)

    platforms: dict[str, dict[str, float]] = {}
    counts: dict[str, dict[str, int]] = {}
    if budget_file.exists():
        # A malformed budget cannot be re-read, so the other platforms' sections are
        # unrecoverable from it. Refuse instead of rewriting from an empty dict: that
        # silently dropped every sibling section the file still held.
        try:
            platforms = load_budget(budget_file)
            counts = load_test_counts(budget_file)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"cannot update {budget_file}: {exc} - refusing to overwrite it"
            ) from exc
    platforms[platform] = dict(sorted(coverage_data.items()))
    if test_counts is not None:
        counts[platform] = dict(sorted(test_counts.items()))

    payload: dict[str, object] = {"platforms": platforms}
    if counts:
        payload["test_counts"] = counts

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
        [sys.executable, "-m", "pytest", "--version"],
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
            sys.executable,
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


def collect_test_counts() -> dict[str, int]:
    """Count how many tests each test file collects on this platform.

    `pytest --collect-only -q` prints one `path::test_name` line per collected test,
    plus a trailing `N tests collected` summary that contains no `::` and is ignored
    by construction. A run that fails prints one warning and returns {} rather than
    reporting "no tests", so a collection error can never make the count check look
    satisfied.
    """
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tools/",
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            cwd=ROOT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[WARN] test collection failed: {exc} - test counts were not verified")
        return {}

    if proc.returncode != 0:
        print(
            f"[WARN] test collection failed (exit {proc.returncode}) - "
            "test counts were not verified"
        )
        return {}

    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        if "::" not in line:
            continue
        path = Path(line.split("::", 1)[0].strip()).as_posix()
        counts[path] = counts.get(path, 0) + 1
    return counts


def check_ratchet(
    platforms: dict[str, dict[str, float]],
    current: dict[str, float],
    notes: list[str] | None = None,
    test_files: set[str] | None = None,
) -> tuple[bool, list[str]]:
    """Check if coverage decreased for any file on the current platform.

    Only `platforms[sys.platform]` is compared: another platform's baselines were
    measured where some of this run's tests skip, so comparing them would report
    regressions that do not exist. A missing section passes here because main()
    has already printed the documented [SKIP] line - a skip that is announced,
    never inferred from an empty comparison.

    A drop within COVERAGE_TOLERANCE is appended to `notes`, not `violations`:
    subprocess-driven files are noisy, but the drop must stay visible.

    Test files keep their own rule: they are never compared against the production
    tolerance, because their percentage tracks platform skips. `test_files` names
    the ones to floor-check (derived from the budget's `test_` basenames when None);
    only a fall from above TEST_FILE_COVERAGE_FLOOR to below it fails, and only
    when the file ran - an absent file is the clearer message above.

    Returns:
        (passed, violations) where violations lists files with decreased coverage
    """
    if notes is None:
        notes = []
    violations = []

    budget = platforms.get(sys.platform)
    if budget is None:
        return True, []

    # Exempt files are not compared, so an oscillating live-API file can neither
    # raise a violation nor be counted as a path-mismatch casualty. It must still
    # run, though: an absent entry means its tests were deleted or failed to collect.
    # Test files are skipped for the same reason: their number tracks which tests
    # this platform executed, not how much production code the suite reaches.
    tracked = {
        path: old_coverage
        for path, old_coverage in budget.items()
        if path not in RATCHET_EXEMPT and not is_test_file(path)
    }
    for path in sorted(budget):
        if path in RATCHET_EXEMPT and path not in current:
            violations.append(
                f"{path} is exempt from coverage, but it did not run at all - "
                "deleted tests, or a collection error?"
            )
        elif is_test_file(path) and path not in current:
            violations.append(
                f"{path} is a test file, so its coverage is not compared, but it "
                "did not run at all - deleted tests, or a collection error?"
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
            if diff <= COVERAGE_TOLERANCE:
                notes.append(
                    f"  [NOTE] {file_path}: {old_coverage:.1f}% -> "
                    f"{new_coverage:.1f}% (-{diff:.1f}%, within tolerance)"
                )
            else:
                violations.append(
                    f"  {file_path}: {old_coverage:.1f}% -> {new_coverage:.1f}% "
                    f"(-{diff:.1f}%)"
                )

    # A test file whose bodies were deleted still collects and still appears in the
    # run, so it never reaches the "did not run at all" message. Its percentage
    # collapses instead, which is the only signal left - the count check catches a
    # deleted function, this catches emptied bodies.
    test_paths = (
        test_files
        if test_files is not None
        else {path for path in budget if is_test_file(path)}
    )
    for file_path in sorted(test_paths):
        # The live-API exemption covers the floor too: its percentage tracks the
        # provider, not the suite, so it is never floored.
        if file_path in RATCHET_EXEMPT or file_path not in budget:
            continue
        if file_path not in current:
            continue
        old_coverage = budget[file_path]
        new_coverage = current[file_path]
        if new_coverage < TEST_FILE_COVERAGE_FLOOR < old_coverage:
            violations.append(
                f"  {file_path}: {old_coverage:.1f}% -> {new_coverage:.1f}% "
                f"(below the {TEST_FILE_COVERAGE_FLOOR:.0f}% test-file floor) - "
                "test bodies were deleted, not skipped"
            )

    return len(violations) == 0, violations


def check_test_counts(
    recorded: dict[str, int] | None,
    collected: dict[str, int],
    notes: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """Check that no test file collects fewer tests than its recorded baseline.

    A test file whose bodies were deleted still collects and still appears in the
    coverage run, so the coverage ratchet cannot see it: test files are excluded
    from the numeric comparison, and presence alone is proven by the shell of the
    file. The collected count can see it, per file and per platform - the same
    scoping the coverage baselines use, because platform-only tests legitimately
    collect on one platform and not another.

    `recorded` is None when the platform has no `test_counts` section: that is a
    note, not a failure, and coverage enforcement carries on.

    Returns:
        (passed, violations) where violations lists files that lost tests
    """
    if notes is None:
        notes = []
    if recorded is None:
        notes.append(
            f"  [NOTE] test-count enforcement is not configured for {sys.platform} - "
            "run python tools/coverage_gate.py --update to record it"
        )
        return True, []

    violations = []
    for path in sorted(recorded):
        old_count = recorded[path]
        # A recorded file missing from the run collects zero tests: same failure,
        # and the message names both numbers so the -N is the drop that was seen.
        new_count = collected.get(path, 0)
        if new_count < old_count:
            violations.append(
                f"  {path}: {old_count} collected tests -> {new_count} "
                f"(-{old_count - new_count}) - tests were deleted"
            )

    return len(violations) == 0, violations


BASELINE_CHUNK_SIZE = 3000
"""Characters per emitted baseline chunk.

GitHub truncates an annotation message at 4096 characters, and the Linux section
has 121 entries, so the single captured line arrived cut in half and unusable.
3000 leaves room for the `::notice::` prefix the workflow adds.
"""


def chunk_baseline(
    section: dict[str, float | int], chunk_size: int = BASELINE_CHUNK_SIZE
) -> list[str]:
    """Split one platform's compact JSON section into annotation-safe chunks.

    Chunks are plain slices of the compact JSON, so joining them in order gives
    back the exact string `json.loads` accepts - no per-chunk JSON, which would
    not concatenate into a document.
    """
    payload = json.dumps(section, separators=(",", ":"), sort_keys=True)
    return [
        payload[offset : offset + chunk_size]
        for offset in range(0, len(payload), chunk_size)
    ] or [payload]


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
        # Read the existing budget before paying for a coverage run: a malformed
        # file must fail here and stay untouched, not after the suite has run.
        # A missing file is a first run, and load_budget() returns {} for it.
        try:
            existing = load_budget().get(sys.platform, {})
            # Validate the sibling section too, so a malformed test_counts refuses
            # here, before the suite runs, rather than at save time.
            load_test_counts()
        except (OSError, ValueError) as exc:
            print(f"[FAIL] coverage budget {BUDGET_FILE}: {exc}")
            return 1

        print("Running coverage...")
        try:
            current = run_coverage()
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            print(f"[FAIL] coverage run: {exc}")
            return 1

        # A live-API file's coverage tracks the network, not the suite, so it is
        # never a baseline. Drop it from this run, and re-add a pre-existing entry
        # only when the file actually ran: a deleted file must not outlive itself.
        # The other platforms' sections are not measured by this run, so only
        # this platform's baseline is refreshed; save_budget() keeps the rest.
        skipped = [path for path in current if path in RATCHET_EXEMPT]
        for path in skipped:
            del current[path]
        for path, saved_coverage in existing.items():
            if path in RATCHET_EXEMPT and path in skipped:
                current[path] = saved_coverage

        # Counted after coverage because the coverage run takes minutes, and a
        # failed collection must not replace a recorded baseline with {}.
        counts = collect_test_counts()

        try:
            save_budget(current, test_counts=counts or None)
        except (OSError, ValueError) as exc:
            print(f"[FAIL] saving budget: {exc}")
            return 1
        if skipped:
            print(
                f"[SKIP] {len(skipped)} live-API file(s) not written to the budget: "
                "coverage depends on whether the provider answered"
            )
        print(f"[OK] Updated budget: {len(current)} files tracked")
        if counts:
            print(f"     Test counts recorded for {len(counts)} file(s)")
        else:
            print("     Test counts NOT recorded: collection failed")
        print(f"     Saved to: {BUDGET_FILE}")
        return 0

    if "--report" in sys.argv:
        try:
            # Another platform's baselines would read as mass regressions here.
            budget = load_budget().get(sys.platform, {})
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

    # `--force-capture` (or `--capture --force`) re-measures a platform that already
    # has a baseline. The committed baseline goes stale when the code it was measured
    # against changes: the ratchet then fails, and the plain --capture stays silent
    # because a baseline is present, so the new numbers never reach the log.
    force_capture = "--force-capture" in sys.argv or (
        "--capture" in sys.argv and "--force" in sys.argv
    )
    if "--capture" in sys.argv or force_capture:
        try:
            platforms = load_budget()
            recorded_counts = load_test_counts()
        except (OSError, ValueError) as exc:
            print(f"[FAIL] coverage budget: {exc}")
            return 1

        # A workflow cannot commit, so an unbudgeted runner hands its measured
        # baseline back through the log for a human to paste into the budget. The
        # two sections are captured independently: a platform can have coverage but
        # no counts (the section was added later), and that half is still useful.
        has_baseline = sys.platform in platforms
        has_counts = recorded_counts.get(sys.platform) is not None

        if has_baseline and has_counts and not force_capture:
            return 0

        if force_capture or not has_baseline:
            try:
                current = run_coverage()
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                print(f"[FAIL] coverage run: {exc}")
                return 1

            # The set --update would write for a platform with no history: a
            # live-API file stays out, because its number tracks the provider, not
            # the suite.
            for path in [path for path in current if path in RATCHET_EXEMPT]:
                del current[path]

            chunks = chunk_baseline(current)
            count = len(chunks)
            for index, chunk in enumerate(chunks, start=1):
                print(f"COVERAGE_BASELINE {sys.platform} {index}/{count} {chunk}")

        if not has_counts:
            counts = collect_test_counts()
            # {} means collection failed: collect_test_counts() already warned, and
            # emitting an empty baseline would claim the platform collects no tests.
            if counts:
                chunks = chunk_baseline(counts)
                count = len(chunks)
                for index, chunk in enumerate(chunks, start=1):
                    print(f"TEST_COUNTS {sys.platform} {index}/{count} {chunk}")

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
        platforms = load_budget()
        test_counts = load_test_counts()
    except (OSError, ValueError) as exc:
        print(f"[FAIL] coverage budget: {exc}")
        return 1

    if not platforms:
        print("[WARN] No coverage budget found. Run with --update to create one.")
        return 0

    if sys.platform not in platforms:
        enforced = ", ".join(sorted(platforms))
        print(
            f"[SKIP] no coverage baseline for {sys.platform}; the enforced "
            f"baselines are: {enforced}. Run --update on this platform to add one."
        )
        return 0

    # An empty section enforces nothing, so a green [OK] would claim a guarantee the
    # budget does not hold. Fail instead, and name the command that fills it.
    if not platforms[sys.platform]:
        print(
            f"[FAIL] the baseline for {sys.platform} is empty - "
            "run --update on this platform"
        )
        return 1

    print("Running coverage...")
    try:
        current = run_coverage()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"[FAIL] coverage run: {exc}")
        return 1

    notes: list[str] = []
    tracked_test_files = {
        path for path in platforms[sys.platform] if is_test_file(path)
    }
    passed, violations = check_ratchet(platforms, current, notes, tracked_test_files)

    recorded_test_counts = test_counts.get(sys.platform)
    collected_test_counts = collect_test_counts()
    counts_passed, count_violations = check_test_counts(
        recorded_test_counts, collected_test_counts, notes
    )
    passed = passed and counts_passed
    violations.extend(count_violations)

    print(f"Files tracked       : {len(platforms[sys.platform])}")
    print(f"Files in current run: {len(current)}")
    if recorded_test_counts is not None:
        print(f"Test files tracked  : {len(recorded_test_counts)}")

    for note in notes:
        print(note)

    if not passed:
        print(f"\n[FAIL] {len(violations)} coverage/test-count violation(s):")
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
        old_coverage = platforms[sys.platform].get(file_path, 0.0)
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
