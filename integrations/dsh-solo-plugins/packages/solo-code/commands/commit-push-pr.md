---
description: Complete git workflow — commit, push, and create a pull request in one step
---
# /commit-push-pr — Commit, Push, Create PR

Complete workflow: stage → commit → push → pull request.

## Workflow

### Step 1: Branch Check
- If on `main` or `master`: create a new feature branch
- Branch naming: `feature/<slug>`, `fix/<slug>`, `refactor/<slug>` based on change type
- Example: `feature/add-ralph-loop`, `fix/hook-stdin-crash`

### Step 2: Commit
Follow the `/commit` workflow:
- Analyze diff, draft conventional commit message
- Confirm with user
- Stage and commit

### Step 3: Push
- Push to origin: `git push -u origin <branch-name>`
- Handle authentication errors gracefully

### Step 4: Create Pull Request
Use `gh pr create`:
- Title: same as commit subject line
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

### Step 5: Output
- Branch name
- Commit hash
- PR URL

## Error Handling
- If `gh` CLI not installed: output the PR title + body for manual creation
- If push fails (no remote): warn user
- If PR already exists: show existing PR URL
- Never force push to main/master

## What NOT to do
- Never force push
- Never skip hooks (--no-verify)
- Never commit secrets, .env files, or credentials
- Never modify CI/CD config (.github/workflows/)
