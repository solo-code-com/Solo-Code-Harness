---
name: research
description: Investigate a question against high-trust primary sources and capture the findings as a cited Markdown file. Use when the user wants a topic researched, docs or API facts gathered, or reading legwork delegated to a background sub-agent.
---

# Research — Background Documentation Investigation

Delegate investigation to a **background sub-agent** (`task` tool), so you keep working while it reads.

## Process

### 1. Identify the research question

Clarify the scope with the user: what to investigate, which sources to prioritize (official docs, source code, specs, first-party APIs), and the expected output format.

### 2. Dispatch a background sub-agent

Use the `task` tool to spawn a sub-agent with this instruction:

> Investigate [question] against **primary sources only** — official docs, source code, specs, first-party APIs — not secondary blog posts or tutorials. Follow every claim back to the source that owns it. Write findings to a single Markdown file, citing each claim's source inline. Save it where the repo keeps research notes; if no convention exists, create `docs/research/<topic>.md`.

### 3. Continue other work

While the sub-agent investigates, proceed with unrelated tasks. The research agent works independently.

### 4. Review results

When the sub-agent completes, verify:
- Every claim has a citation back to its primary source
- Findings are relevant and actionable
- File location follows repo conventions

## Sources hierarchy (highest trust first)

1. **Official documentation** — framework/library docs, language specs, RFCs
2. **Source code** — the actual implementation, type definitions, tests
3. **First-party APIs** — SDK references, API schemas, changelogs
4. **Specifications** — W3C, ECMA, IETF standards

> **Anti-pattern**: Blindly trusting Stack Overflow, Medium posts, or LLM-generated summaries as primary sources. These are secondary at best.

## Output format

```markdown
# Research: [Topic]

> Investigated by sub-agent on [date]

## Key findings

- [Finding 1] — Source: [link/commit/line]
- [Finding 2] — Source: [link/commit/line]

## Detailed findings

### [Subtopic 1]

[Explanation with inline citations]

### [Subtopic 2]

[Explanation with inline citations]

## Sources

1. [Source name](url) — relevance note
2. [Source name](url) — relevance note

## Recommendations

[Actionable recommendations based on findings]
```

## When to use

- "How does [library X] handle [feature Y] in version Z?"
- "What are the breaking changes in [framework] v[N]?"
- "Investigate the performance characteristics of [approach A] vs [approach B]"
- "Find the official recommended pattern for [scenario]"

## When NOT to use

- The answer is trivially available (reading one known file)
- The user needs an immediate answer (sub-agent takes time)
- The question is about project-internal logic (use `grep`/`read` directly)
