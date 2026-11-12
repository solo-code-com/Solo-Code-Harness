---
description: Save project conventions and rules to persistent memory for future sessions.
---
# /remember — Persistent Memory

## Purpose
Save important project conventions, decisions, or patterns to persistent memory so they survive across sessions.

## Behavior
1. **Identify** what's worth remembering (non-obvious conventions, gotchas, preferences)
2. **Write** to `.kilo/memory/` as a markdown file with frontmatter
3. **Update** `MEMORY.md` index

## What to Save
- Non-obvious project conventions
- User preferences (code style, naming)
- Gotchas discovered during debugging
- Architecture decisions with rationale
- Things already in the codebase (file structure, imports)
- Temporary context for this session only

## Format
```markdown
---
type: project | feedback | reference
created: YYYY-MM-DD
---
# Title
Fact body with **Why:** and **How to apply:** sections.
```
