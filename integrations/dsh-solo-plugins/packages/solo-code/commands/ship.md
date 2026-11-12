---
description: Pre-launch checklist: security scan, test suite, lint, schema validation, garden drift detection. Gate all changes before deployment.
---
Run the complete pre-launch checklist:

**Gate 1 — Security**
- Run `python .github/scripts/security_scan.py .`
- If secrets found: BLOCK launch, report all findings

**Gate 2 — Tests**
- Run `python -m pytest tools/ -q`
- If tests fail: BLOCK launch, report failures

**Gate 3 — Lint**
- Run `ruff check .`
- If lint errors: report but do not block (lint is advisory)

**Gate 4 — Schema Validation**
- Run `python tools/validate_schemas.py`
- If schema errors: BLOCK launch

**Gate 5 — Drift Detection**
- Run `python tools/garden.py`
- If drift detected: BLOCK launch, show which files drifted

**Gate 6 — Guard Hook**
- Run `python -m pytest tools/test_claude_guard.py -q`
- If tests fail: BLOCK launch

Report a clear PASS/FAIL for each gate. Overall: READY or BLOCKED with specific reasons.
