---
allowed-tools: Bash(git *), Bash(gh pr *), Bash(python .github/scripts/security_scan.py:*), Read, Grep
description: Complete git workflow — commit, push, and create a pull request in one step
---

## Context

- Current branch: !`git branch --show-current`
- Staged files: !`git diff --cached --name-only`
- Unstaged changes: !`git diff --name-only`
- Diff stats: !`git diff --stat HEAD`

## Task

Complete workflow: stage → commit → push → pull request.

### 1. Branch Check
If on `main` or `master`, create a new feature branch.
Branch naming: `feature/<slug>`, `fix/<slug>`, `refactor/<slug>` based on change type.
Example: `feature/add-ralph-loop`, `fix/hook-stdin-crash`.

### 2. Commit
Follow the `/commit` workflow:
- Analyze the diff, draft a conventional commit message
- Confirm with the user
- Stage and commit

### 3. Push
Push to origin: `git push -u origin <branch-name>`.
Handle authentication errors gracefully.

### 4. Create Pull Request
Use `gh pr create`:
- Title: same as the commit subject line
- Body template:

```markdown
## Summary
- <bullet point 1>
- <bullet point 2>
- <bullet point 3>

## Changes
| File | Description |
|------|-------------|
| `path/to/file` | What changed and why |

## Verification
- [ ] `python .github/scripts/security_scan.py .` passes
- [ ] `python .github/scripts/checklist.py .` passes
- [ ] All tests pass
- [ ] No debug statements left in production code
```

### 5. Output
- Branch name
- Commit hash
- PR URL

## Error Handling
- If `gh` CLI is not installed: output the PR title and body for manual creation
- If push fails (no remote): warn the user
- If the PR already exists: show the existing PR URL
- Never force push to main/master

## What NOT to do
- Never force push
- Never skip hooks (`--no-verify`)
- Never commit secrets, `.env` files, or credentials
- Never modify CI/CD config (`.github/workflows/`)
