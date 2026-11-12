---
name: writing-great-skills
description: "Reference for authoring and editing skills in this harness. Use when: create skill, edit skill, write SKILL.md, improve a skill, skill too long, skill not triggering, add a new capability to the harness."
license: MIT
---

# Writing Great Skills

A skill exists to wrangle **predictability** out of a stochastic system. The agent should take the same *process* every run — not necessarily produce the same output, but follow the same steps. Every rule below serves this goal.

---

## Source of truth

Skill source files live at `source/plugins/<plugin>/skills/<name>/SKILL.md`. **Edit only there.** After editing, run `make generate` to produce harness-specific outputs (`.claude/skills/`, `.kilo/skill/`, etc.). Never edit generated files directly.

---

## Invocation: model-invoked vs user-invoked

In this harness, **all skills are model-invoked** (no `disable-model-invocation`). The agent discovers skills via their `description` field, which sits in context every turn.

**Model-invoked pros/cons:**
- Agent can fire the skill autonomously + other skills can reference it
- Pays **context load** (description sits in window every turn)

**When to make a skill model-invoked:**
- The skill has distinct **trigger phrases** the agent encounters during normal work
- Another skill's workflow reaches it
- Removing it would break a cross-skill chain

The `description` is the skill's only agent-facing hook. Write it to do two jobs:

1. **State what the skill is** — one concise line
2. **List the branches that should trigger it** — distinct trigger phrases

**Rules:**
- Front-load the skill's **leading word** (see below) — the description is where it does invocation work
- **One trigger per branch.** Synonyms that rename a single branch are duplication. "build features using TDD ... asks for test-first development" is one branch written twice — collapse them
- **Cut identity already in the body.** Keep the description to triggers, plus any "when another skill needs..." reach clause

---

## Information hierarchy

A skill's content sits on a ladder ranked by how urgently the agent needs it:

1. **In-skill step** — ordered action in `SKILL.md`. Each step ends on a **completion criterion**: a checkable condition telling the agent work is done (e.g. "every model accounted for", not "produce a change list"). Vague criteria invite **premature completion**.
2. **In-skill reference** — definition, rule, or fact consulted on demand. Flat peer-sets are fine.
3. **External reference** — content pushed to a separate file, reached via a **context pointer** (reference in the skill text).

**Cap for this harness:** 12288 bytes (12 KB) per skill. **Target: 4–6 KB.** If you push past 6 KB, cut no-ops (see below), collapse redundant phrasing, and use leading words. Only consider splitting when content hits the 12 KB hard cap.

**Progressive disclosure** is the move down the ladder — out of `SKILL.md` into a linked file — so the top stays legible. This harness prefers **single-file** to avoid overhead. Only split when a skill genuinely exceeds the hard cap.

**Co-location:** keep a concept's definition, rules, and caveats under one heading. Reading one part should bring its neighbours.

---

## Completion criteria

Every step must end with a checkable condition:

```
BAD:  "Think about the edge cases"
GOOD: "List 3+ edge cases with inputs and expected outputs: [ ] done"
```

A demanding criterion drives thorough **legwork** — the digging the agent does within the work — even for flat reference skills ("every rule applied" binds as tightly as "every step done").

**Checklist format (preferred):**
```markdown
- [ ] Condition A met (evidence: specific output or test)
- [ ] Condition B met
```

---

## Leading words

A **leading word** is a compact concept already in the model's pretraining — `tight`, `red`, `deep module`, `seam`. Repeated throughout the skill text, it anchors behaviour in the fewest tokens by recruiting prior knowledge.

**How leading words serve predictability:**
- In the **body**: agent reaches for the same behaviour every time the word appears
- In the **description**: when the same word lives in prompts, docs, and code, the agent links that shared language to the skill and fires it more reliably

**Examples of leading words:**
| Restated phrase | Leading word |
|---|---|
| "fast, deterministic, low-overhead" | `tight` |
| "a loop you believe in, that goes red on the bug or nothing" | `red` |
| "many behaviours behind a small interface" | `deep module` |

**Warning for this harness (targeting DeepSeek-level models):** Every leading word **must** carry a short definition on first use. Do not rely entirely on model prior — write `tight (fast, deterministic, low-overhead)` not just `tight`.

**Exercise:** Scan every skill for restatements that a leading word could collapse. You win twice — fewer tokens *and* a sharper hook for the agent's thinking.

---

## Failure modes

Use these to diagnose issues with any skill:

| Mode | Symptom | Fix |
|---|---|---|
| **Premature completion** | Agent ends a step before it's done, attention slipping to "being done" | Sharpen the completion criterion first (cheap, local). If still fuzzy and rush persists, hide post-completion steps by splitting. |
| **Duplication** | Same meaning in more than one place | Collapse to single source of truth. |
| **Sediment** | Stale layers — adding feels safe, removing feels risky | Prune aggressively. Default fate of any skill without a pruning discipline. |
| **Sprawl** | Skill too long even when every line is live | Disclose reference behind pointers; split by branch or sequence. |
| **No-op** | Line the model already obeys by default — pays load, says nothing | Test: does it change behaviour vs default? A weak leading word (`be thorough` when agent is already thorough-ish) is a no-op; fix with a stronger word (`relentless`). |

**No-op test:** Read each sentence in isolation. If the model already does that by default (e.g. "write clear code"), delete the sentence. Be aggressive — most no-ops should go, not be rewritten.

---

## Trigger accuracy (manual — no automated checker exists)

A skill's `description` is the only agent-facing hook. If two descriptions
share trigger keywords, the wrong skill activates. There is **no automated
trigger eval in this harness** — check it by hand:

1. `grep -h '^description:' .kilo/skill/*/SKILL.md | sort` — read them as a
   set and look for two skills claiming the same keywords.
2. For any overlap, make each `description` name what makes it *distinct*
   (the trigger condition), not just its topic.
3. `python tools/garden.py` — verifies every skill has a valid SKILL.md and
   that all engines carry the same body.

A previous version of this section told you to add cases to a
`tools/schemas/skill_triggers.json` that does not exist and run `python
tools/eval.py --check-triggers`, which does not exist either (confirmed
with `git log --all` — they were never in this repo), so the loop could
not run. If you build such a checker, wire it into `garden.py` so it is
enforced rather than merely documented.

---

## Checklist before committing a skill

- [ ] Directory name matches `name` in frontmatter (case-sensitive)
- [ ] `description` includes "Use when:" with distinct trigger phrases
- [ ] File size ≤ 12288 bytes (target 4–6 KB)
- [ ] Registered in `source/plugins/<plugin>/plugin.json` (`components.skills`)
- [ ] Registered in `agent.yaml` (`skills:`, alphabetical order)
- [ ] `make generate` runs without error
- [ ] All gates pass: `make check` (or `python .github/scripts/checklist.py .`)
- [ ] Cross-linked from `using-agent-skills` router if applicable
- [ ] No no-ops — every sentence changes agent behaviour vs default
