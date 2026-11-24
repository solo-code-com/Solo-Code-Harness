---
name: custom-deploy-workflow
description: "Project inspection and deployment preparation workflow for staging/production. Use when: prepare deploy, deploy project, release version, check deploy."
license: MIT
---

# Custom Deploy Workflow

This skill guides the AI on how to prepare and verify source code before deploying or releasing a new version.

## Execution Process (4 Steps)

### Step 1: Sync Source Code & Verify Branch
- Ensure you are on the correct branch (e.g., `main` or `develop`).
- Run `git status` to ensure there are no uncommitted changes.

### Step 2: Run Static Checks
- Run linting tools available in the project (e.g., `npm run lint` or `ruff check .`).
- Scan for secrets to ensure no sensitive keys are accidentally committed:
  ```bash
  python .github/scripts/security_scan.py .
  ```

### Step 3: Run Test Suite
- Execute the entire test suite to ensure no features are broken (regression):
  ```bash
  npm test
  # Or the corresponding command for the project
  ```

### Step 4: Create Deploy Check-list
- List the major changes in this version.
- Determine if there are any environment variable (`.env`) or database schema changes.
- Report the test results with a specific verdict:
  - `READY TO DEPLOY`: All checks passed, no errors.
  - `BLOCKED`: Lint errors, test failures, or secret leaks detected. Must be fixed before proceeding.

---

## Practical Examples (BAD -> GOOD)

<!-- slide -->
### Example 1: Pre-deploy Verification Report
**BAD (Verbose and non-specific report):**
> I ran the tests and everything seems fine. No major errors were found. You can proceed to deploy to staging. I think it will work well.

**GOOD (Clear, specific, contains a verdict):**
> ### Pre-Deploy Verification Report
> - **Linting & Code Style:** 0 ruff errors, 0 warnings.
> - **Security Scan:** No secret leaks detected.
> - **Tests:** 12/12 unit tests passed.
>
> **Verdict:** `READY TO DEPLOY`
