---
name: resolving-merge-conflicts
description: Resolve in-progress git merge or rebase conflicts systematically. Use when you hit conflicts during merge, rebase, or cherry-pick.
---

# Resolving Merge Conflicts

Resolve an in-progress git merge or rebase conflict with a disciplined, reproducible process.

## Process

### 1. See the current state

Check what operation is in progress (`git status`), list conflicting files, and read the current conflict state. Understand the merge/rebase direction:
- **Merge**: `ours` = current branch, `theirs` = branch being merged in
- **Rebase**: `ours` = branch being rebased onto (upstream), `theirs` = your commits being replayed

### 2. Find the primary sources for each conflict

For every conflicting hunk, understand **why** each side made its change:
- Read the commit messages (`git log <branch> --oneline`)
- Check related PRs or issues referenced in commit messages
- Read surrounding context in both versions of the file
- Identify the **intent** behind each change — what problem was being solved?

### 3. Resolve each hunk

- Preserve both intents where possible (merge both changes)
- Where incompatible, pick the one matching the merge's stated goal
- Note any trade-offs in the resolution commit message
- **Never invent new behavior** — the resolution should combine existing intent, not introduce new logic
- Always resolve; never `--abort` without explicit user instruction

### 4. Run automated checks

After resolving all conflicts but BEFORE committing:
- Run the project's type checker
- Run affected tests
- Run the full test suite if practical
- Run the linter
- Fix anything the merge broke

### 5. Finish the merge/rebase

- Stage all resolved files (`git add .`)
- If merging: commit with a clear message explaining resolution decisions
- If rebasing: `git rebase --continue` and repeat 1-5 for remaining commits
- Push when complete

## Safety rules

- **Never force-push** after resolving — always merge or rebase-continue
- **Never `git reset --hard`** during conflict resolution
- **Commit after each resolution step** during long rebases
- **Verify the build passes** before pushing

## After resolution

- Run `python .github/scripts/checklist.py .` if available
- Verify all tests pass on the resolved branch
- If this is a PR, note any non-trivial conflict resolutions in the PR description
