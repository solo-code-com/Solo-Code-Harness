---
name: "rules-git"
description: "Auto-loaded for all sessions. Enforces conventional commits and safe git practices."
---

# Git Workflow Rules

> Auto-loaded for all sessions. Enforces conventional commits and safe git practices.

## Commit Format

```
<type>: <short description>

Co-Authored-By: Solo-Code <admin@solo-code.com>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `style`, `security`

### Examples

```
feat: add Stripe checkout webhook handler
fix: resolve race condition in session create
refactor: extract auth middleware to shared module
security: upgrade bcrypt to v5.0.1 to patch authentication bypass
```

## Branch Naming

```
<type>/<description>
```
- `feat/user-auth`
- `fix/login-timeout`
- `refactor/database-layer`

## PR Workflow

1. Create feature branch from `main`
2. Implement with TDD (tests first)
3. Self-review: run `verify.sh` locally
4. Create PR with summary + test plan
5. Address review comments
6. Squash merge to `main`

## Safety Rules (NON-NEGOTIABLE)

- **NEVER force push to main/master**
- **NEVER `git reset --hard` on shared branches**
- **NEVER commit secrets** — use `.env` files with `.gitignore`
- **NEVER skip hooks** (`--no-verify`) unless explicitly justified
- **ALWAYS pull before push** — rebase on latest main
- **ALWAYS scan for secrets** before committing:
  ```bash
  python .github/scripts/security_scan.py .
  ```

## Pre-Commit Checklist

- [ ] All tests pass locally
- [ ] No hardcoded secrets (run `security_scan.py`)
- [ ] Conventional commit message
- [ ] Changes are focused — one concern per commit
- [ ] No commented-out code or debug statements
- [ ] Documentation updated if API changed
