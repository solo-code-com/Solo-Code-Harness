---
description: Run all Solo-Code verification gates in sequence
---
# /gate-check — Verification Gates

Run all verification gates in order. Stop at the first failure.

## Context

- Current branch: execute `git branch --show-current` to display
- Last commit: execute `git log --oneline -1` to display
- Changed files: execute `git diff --stat HEAD` to display

## Gates

### Gate 1: Security Scan
```bash
python .github/scripts/security_scan.py .
```
Expected: exit 0, "No issues found."

### Gate 2: Lint
```bash
ruff check .
```
Expected: exit 0, no errors.

### Gate 3: Garden (Drift Detection)
```bash
python tools/garden.py
```
Expected: exit 0, "Total drift issues: 0".

### Gate 4: Integration Tests
```bash
python tools/test_integration.py
```
Expected: all gates green.

### Gate 5: Harness Tests
```bash
python -m pytest tools/ -q
```
Expected: all tests pass.

### Gate 6: Eval Score
```bash
python .github/scripts/eval_harness.py .
```
Expected: exit 0, "All evals passed" (59/59).

### Gate 7: Debug Artifacts
Check for leftover debug statements:
- `console.log` / `console.debug` in `.ts`, `.tsx`, `.js`, `.jsx`
- `print()` with `# debug` in `.py`
- `pdb.set_trace()` or `breakpoint()` in `.py`
- `debugger` statement in any file
- Exclude `node_modules/`, `.venv/`, `.git/`, `__pycache__/`

## Report Format

```
SOLO-CODE GATE CHECK
====================
Branch: <branch>
Commit: <hash>
Files changed: <count>

[PASS] Gate 1: Security Scan    — (no issues found)
[PASS] Gate 2: Lint             — (0 errors)
[PASS] Gate 3: Garden           — (0 errors, 0 warnings)
[PASS] Gate 4: Integration      — (all gates green)
[PASS] Gate 5: Harness Tests    — (<N> passed)
[PASS] Gate 6: Eval Score       — (<score>/100)
[PASS] Gate 7: Debug Artifacts  — (none found)

VERDICT: ALL GATES PASSED
```

If any gate fails, explain the failure and suggest fixes. Do NOT proceed with commit/push until all gates pass.
