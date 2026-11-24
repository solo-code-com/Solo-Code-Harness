---
name: domain-modeling
description: "Build and sharpen a project's shared language (glossary) in CONTEXT.md. Use when: terms are ambiguous, the same concept has multiple names, naming a new module/concept, onboarding to a fuzzy domain, or output is too verbose because no shared vocabulary exists."
license: MIT
---

# Domain Modeling

Maintain `CONTEXT.md` at the project root as the single source of shared language. Every term used in code, conversation, and documentation should trace back to a definition there. Consistent vocabulary reduces verbosity, prevents ambiguity, and lets the agent write tighter code.

This is the *active* discipline — challenging terms, inventing edge-case scenarios, and writing the glossary down the moment terms crystallise. Reading `CONTEXT.md` for vocabulary is a one-line habit any skill can do; this skill is for changing the model, not just consuming it.

---

## File structure

Most repos have a single context:

```
/
├── CONTEXT.md
├── docs/
│   └── adr/
└── src/
```

Create files **lazily** — only when you have something to write. If no `CONTEXT.md` exists, create one when the first term is resolved. If no `docs/adr/` exists, create it when the first ADR is needed.

---

## CONTEXT.md format

### Structure

```markdown
# {Context Name}

One or two sentence description.

## Language

**Order**:
A request to purchase one or more items.
*Avoid:* Purchase, transaction

**Invoice**:
A request for payment sent after delivery.
*Avoid:* Bill, payment request
```

### Rules

- **Be opinionated.** When multiple words exist for the same concept, pick the best one and list the others under `*Avoid:*`.
- **Keep definitions tight.** One or two sentences max. Define what it IS, not what it does.
- **Only project-specific terms.** General programming concepts (timeouts, error types, utility patterns) do not belong. Before adding a term, ask: is this unique to this context?
- **Add `## Relationships`** when contexts interact (e.g. "Ordering creates Orders; Fulfillment picks them").
- **Add `## Flagged ambiguities`** for terms that were historically confused and resolved.

### Example

```markdown
# Issue Tracker

The tool that hosts a repo's issues — GitHub Issues, Linear, or local markdown.

## Language

**Issue tracker**:
The tool that hosts a repo's issues.
*Avoid:* backlog manager, issue host

**Issue**:
A single tracked unit of work — a bug, task, or PRD.
*Avoid:* ticket (use only when quoting external systems)

## Relationships

- An **Issue tracker** holds many **Issues**.

## Flagged ambiguities

- "backlog" was previously used for both the tool and the body of work — resolved to **Issue tracker** for the tool.
```

---

## When to update (inline)

- **Naming a new module or concept** — if the name does not exist in `CONTEXT.md`, add it right there. Do not batch.
- **User uses a fuzzy term** — propose a precise canonical term. "You said 'account' — do you mean Customer or User? Those are different."
- **User's term conflicts with glossary** — call it out immediately. "Your glossary defines 'cancellation' as X, but you seem to mean Y."
- **Cross-reference with code** — when user states how something works, check if the code agrees. Surface contradictions.
- **Stress-test with scenarios** — when domain relationships are discussed, invent edge-case scenarios to force precision.

Update `CONTEXT.md` **inline** as each term is resolved. Do not batch updates.

---

## Relationship with memory & ADR

| Artifact | Purpose | Location |
|---|---|---|
| **`CONTEXT.md`** | Shared language / glossary | Project root |
| **`.claude/memory/` (Kilo: `.kilo/memory/`)** | Conventions, preferences, gotchas | Harness memory dir |
| **`docs/adr/`** | Architectural decisions and trade-offs | `docs/adr/` |

Do not duplicate. If a concept is a term → `CONTEXT.md`. If it is a convention → memory. If it is a decision → ADR.

---

## Completion criteria

- [ ] Every project-specific term has exactly one definition in `CONTEXT.md`
- [ ] Each definition includes `*Avoid:*` for synonyms that should not be used
- [ ] No fuzzy terms remain in active use across conversation and code
- [ ] `CONTEXT.md` contains no implementation details, specs, or scratch notes
- [ ] Lazy file created (if `CONTEXT.md` did not exist, it now does)
