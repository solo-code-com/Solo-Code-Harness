---
allowed-tools: Read, Grep, Glob, Bash(git *)
description: Brainstorm solutions with trade-off analysis
---

## Context

- Project structure: core files at root, source plugins in `source/plugins/`
- Current branch: !`git branch --show-current`

## Task

For the given problem, generate 2-3 distinct approaches. For each:

### Approach N: <name>
- **How it works**: 2-3 sentence summary
- **Files changed**: list specific paths
- **Pros**: 2-3 advantages
- **Cons**: 2-3 disadvantages or risks
- **Complexity**: Low / Medium / High
- **Time estimate**: Small / Medium / Large

### Recommendation
Pick one approach with reasoning. Consider:
- Which is simplest to implement correctly?
- Which introduces the least risk?
- Which aligns best with existing patterns?

### Edge Cases
List at least 3 edge cases the chosen approach must handle.
