---
allowed-tools: Read, Edit, Grep, Glob, Bash(git *)
description: Enhance existing code — add features, improve error handling, optimize
---

## Context

- Current branch: !`git branch --show-current`
- Recent changes: !`git log --oneline -3`

## Task

### 1. Understand Current State
- Read the target file(s) fully
- Trace callers and callees
- Identify existing patterns and conventions

### 2. Plan Enhancement
- What exactly changes?
- What is the impact on callers?
- What edge cases arise?

### 3. Implement
- Make focused, minimal edits
- Read each file before editing
- Match existing style exactly
- Add error handling for new code paths

### 4. Verify
- Syntax is correct
- Existing tests pass
- New behavior works as expected
- No regression in related functionality
