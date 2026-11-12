---
description: Create a git commit with an automatically generated conventional commit message
---
# /commit — Git Commit

Create a well-formed git commit from staged and unstaged changes.

## Workflow

1. **Analyze current state**
   - Run `git status` for overview
   - Run `git diff` for unstaged changes
   - Run `git diff --cached` for staged changes
   - Run `git log --oneline -5` for recent commit style

2. **Review changes**
   - List each changed file with a 1-line summary
   - Identify the primary category: feat, fix, refactor, docs, test, chore, perf, style
   - Note any files that should NOT be committed (.env, credentials, large binaries)

3. **Draft commit message**
   - Format: `<type>(<scope>): <description>`
   - Types: feat, fix, refactor, docs, test, chore, perf, style
   - Description: imperative mood, ≤72 chars, explains WHY not WHAT
   - Body (optional): bullet points for significant changes
   - Footer: `Co-Authored-By: Solo-Code <admin@solo-code.com>`

4. **Present for confirmation**
   - Show the drafted message
   - List files to be committed
   - Ask user to confirm or edit

5. **Stage and commit**
   - Stage relevant files
   - Create commit
   - Show commit hash and status

## What NOT to commit
- Files matching `.env`, `.env.*`, `credentials.json`, `*.pem`, `*.key`
- Files in `.gitignore`
- Large binary files without explicit user approval
- `node_modules/`, `.venv/`, `__pycache__/`, `dist/`, `build/`

## Commit Style
Match the existing commit style from `git log --oneline -5`. Examples:

```
feat(hooks): add continual-learning memory across sessions

Fixes agent forgetting everything between sessions. Uses two-tier
SQLite memory: global (~/.kilo/learnings/) and local (.kilo/learnings/db/).
Auto-detects failure patterns and compacts old data.

Co-Authored-By: Solo-Code <admin@solo-code.com>
```

```
fix(gate-guard): prevent false blocks on large stdin payloads

Changed exit code from 2 (block) to 0 (allow) when hook input
exceeds 1MB. Large Write/Edit tools were being blocked incorrectly.

Co-Authored-By: Solo-Code <admin@solo-code.com>
```
