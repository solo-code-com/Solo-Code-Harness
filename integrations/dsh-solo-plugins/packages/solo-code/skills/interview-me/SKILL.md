---
name: interview-me
description: "Extracts what the user actually wants instead of what they think they should want. Achieves this through one-question-at-a-time interview until ~95% confidence about the underlying intent. Use when an ask is underspecified (\"build me X\" without \"for whom\" or \"why now\"), when the user explicitly invokes (\"interview me\", \"grill me\", \"are we sure?\", \"stress-test my thinking\"), or when you catch yourself silently filling in ambiguous requirements before any plan, spec, or code exists."
---

# Interview Me

## Overview

What people ask for and what they actually want are different things. They ask for "a dashboard" because that's what one asks for, not because a dashboard solves their problem. They say "make it faster" without a number to hit.

The cheapest moment to find this gap is before any plan, spec, or code exists. Once you've started building, switching costs are real, and the user will rationalize the wrong thing into a "good enough" thing. The misfit gets locked in.

This skill closes the gap before it costs anything. The other Define-phase skills assume you already know roughly what you want: `idea-refine` generates variations from an idea, `spec-driven-development` writes the requirements down, `doubt-driven-development` stress-tests a plan after you've drafted one. Interview-me is the part before all of those, where you ask one question at a time, with your best guess attached, until you can predict what the user is going to say before they say it.

## When to Use

Apply this skill when:

- The ask is missing at least one of: **who** the user is, **why** they want it, what **success** looks like, what the binding **constraint** is
- The request is conventional rather than specific ("build me X", "make it faster") and you can't unpack the convention without guessing
- You're tempted to start with assumptions you haven't surfaced
- The user hasn't said which value they're optimizing for when two reasonable ones are in tension (simplicity vs. flexibility, cost vs. speed)
- The user explicitly invokes: "interview me", "grill me", "before we start, are we sure?", "stress-test my thinking"

**When NOT to use:**

- The ask is unambiguous and self-contained ("rename this variable", "fix this typo")
- The user has explicitly asked for speed over verification
- Pure information requests ("how does X work?", "what does this code do?")
- Mechanical operations (renames, formats, file moves)
- You already have ≥95% confidence; re-read the stop condition below before assuming you don't

## Loading Constraints

This skill needs a live, responsive user. **Do not invoke in non-interactive contexts** like CI pipelines, scheduled runs, `/loop`, or autonomous-loop. If you're in one of those and the ask is underspecified, flag that as a blocker for the user instead of guessing.

## Grilling cadence (3 rules)

1. **One question at a time.** Ask, wait for answer, then ask next. No batches.
2. **Every question carries `GUESS:`** with your recommended answer. User reacts faster to a wrong guess than they generate from scratch.
3. **Facts vs. Decisions.** If a *fact* can be found by exploring the codebase, look it up rather than asking me. The *decisions*, though, are mine — put each one to me and wait for my answer. Distinguish sharply: facts are discoverable truths (file paths, existing patterns, API signatures, current behavior); decisions are choices only the human can make (priorities, trade-offs, what to optimize for, what to build first).
4. **Can you answer by exploring the codebase?** Read files instead of asking. Only ask for what *only* the user can provide: intent, priorities, trade-offs, domain context.

---

## The Process

### Step 1: Hypothesize, with a confidence number

Before asking anything, write down your current best read of what the user wants in **one sentence**, plus an honest confidence number (0–100%):

```
HYPOTHESIS: You want a way to answer "how are we doing?" in standup, and "dashboard" was the convention that came to mind.
CONFIDENCE: ~30% — missing: who it's for, what "metrics" means in context, and what success looks like
```

The number forces honesty. If you wrote down a high number but can't actually predict the user's reactions to the next three questions you'd ask, the number is wrong. Start at the confidence level you can defend.

When confidence is below ~70%, append a brief reason on the same line — what's still unresolved or missing. This tells the user exactly what the interview needs to surface, and prevents the number from being a vague signal.

### Step 2: Ask one question at a time, each with a guess attached

Format:

```
Q: <one focused question>
GUESS: <your hypothesis for the answer, with the reasoning that produced it>
```

Wait for the user to react before asking the next question. (See Grilling Cadence above — rules 1 and 2.)

**Why attach a guess:** Per rule 2 — user reacts faster to a wrong guess than they generate from scratch. Mitigate sycophancy by being visibly willing to be wrong; occasionally guess in a direction you expect pushback.

### Step 3: Listen for "want vs. should want"

The most dangerous answers are the ones where the user says what a thoughtful answer *sounds like* rather than what they actually want. Watch for:

- Answers that pattern-match best-practice talk ("I want it to be scalable", "clean architecture") without specifics
- Answers that defer to convention ("the way most apps do it", "the standard approach")
- Phrases like "I should probably…", "I think I'm supposed to…", "good engineering practice says…"
- Buzzwords as goals — when "modern", "scalable", "robust" are the answer instead of a specific outcome

When you hear these, the question to ask is:

> *"If you didn't have to justify this to anyone, what would you actually want?"*

That single question often does more work than the previous five.

### Step 4: Restate intent in the user's own words

When your confidence is high, write back what you now think the user wants. Keep it tight (5–8 lines), use their language where possible, and structure it so the user can confirm or correct line by line:

```
Here's what I now think you want:

- Outcome:      <one line>
- User:         <one line — who benefits>
- Why now:      <one line — what changed>
- Success:      <one line — how we know it worked>
- Constraint:   <one line — the binding limit>
- Out of scope: <one line — what we're explicitly not doing>

Yes / no / refine?
```

Including "Out of scope" is non-negotiable. Half of misalignment is silent disagreement about what is *not* being built.

### Step 5: Confirm — explicit yes, not "whatever you think"

The gate is an explicit "yes." The following are **not** yes:

- "Whatever you think is best." → The user is delegating, which means they don't have 95% confidence either. Re-ask with two concrete options framed as a choice.
- "Sounds good." → Ambiguous. Ask: "Anything you'd refine?" Silence isn't confirmation.
- "Sure, let's go." → Often a polite exit, not an endorsement. Same follow-up.
- Silence followed by "okay let's start." → The user has given up on the interview, not converged. Stop and ask whether you've missed something.

If they correct you, fold the correction in and restate. Loop until you get an explicit yes.

### The 95% Confidence Stop

You're done when you can answer yes to this:

> *Can I predict the user's reaction to the next three questions I would ask?*

If yes, you have shared understanding. Stop interviewing and produce the restate. If no, you're not done; ask the next question.

This is a checkable test, not a vibe. It also has a floor: if you've gone several rounds and still can't predict, that's information about the ask, not a reason to keep grinding. Stop and tell the user: "I've asked X questions and I still can't predict your reactions. Something foundational is missing. Want to step back?"

## Output

The output of this skill is a **confirmed statement of intent**: the restate from Step 4, with an explicit yes from Step 5. That's the deliverable. Specs, plans, and task lists are downstream; they consume the intent this skill produces.

If the user wants the intent to persist (a multi-session project, a handoff to another collaborator), offer to save it to `docs/intent/[topic].md`. Only save if they confirm.

## Example

**Without `interview-me`:** User asks "build me a dashboard for our metrics." Agent starts proposing chart libraries — assumes who, what metrics, what success looks like.

**With `interview-me`:** Agent states hypothesis with confidence number. Asks questions one at a time with guesses. Two rounds in, discovers actual need isn't "a dashboard" but "a list of experiments across docs." One question saved building the wrong artifact.

## Interaction with Other Skills

- **`idea-refine`**: downstream. If the confirmed intent is "I want X but I don't know how to scope it," hand off to `idea-refine` to generate variations against the now-explicit intent.
- **`spec-driven-development`**: downstream. If the confirmed intent is concrete ("I want X for Y users with Z success criteria"), hand off to `spec-driven-development` to write it down.
- **`planning-and-task-breakdown`**: two hops downstream of this skill (after the spec).
- **`doubt-driven-development`**: opposite end of the timeline. Interview-me is pre-decision intent extraction; doubt-driven is post-decision artifact review. Both catch divergence, but at different moments.
- **`source-driven-development`**: orthogonal. Interview-me clarifies what the user wants; SDD verifies framework facts. They don't compete.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "The ask is clear enough" | If you can't write the desired outcome in one sentence, it's not clear. |
| "Asking wastes their time" | 4-6 targeted questions vs. building the wrong thing — the latter costs far more. |
| "I'll figure it out as I build" | Switching costs after code exists are 10x. Discovery during implementation is rework. |
| "They said 'whatever you think'" | That's delegation, not decision. Re-ask with two concrete options. |
| "If I attach my guess, I'm leading" | Leading is the point. Reacting is faster than generating from scratch. Mitigate sycophancy, not leading. |
| "We've talked enough" | Can you predict reactions to next three questions? If not, you're not done. |

## Red Flags

- Three or more questions in a single message: that's batching, not interviewing
- A question without your hypothesis attached: that's surveying, not committing
- Accepting "whatever you think is best" as a terminal answer
- Producing a spec, plan, or task list before the user has explicitly confirmed your restate
- Questions framed as "what would be best practice?" instead of "what do you actually want?"
- The user gives a sophistication-signaling answer ("scalable", "clean", "modern") and you accept it without probing whether it's what they actually want
- Three or more rounds without your confidence visibly rising: you're asking the wrong questions, step back and reframe
- A confidence number below ~70% with no reason attached: the user can't help close the gap if they don't know what's missing
- Saving the intent doc before the user has confirmed (the doc itself implies a yes the user didn't give)
- Skipping the "Out of scope" line in the restate (silent disagreement about non-goals is half of misalignment)

## Verification

- [ ] Hypothesis with confidence number stated in first turn
- [ ] Questions one at a time, each with guess attached
- [ ] At least one "what would you actually want?" probe when user gave convention-signaling answer
- [ ] Concrete restate (Outcome/User/Why/Success/Constraint/Out of scope) confirmed by user
- [ ] Explicit yes received (not "whatever you think" or "sounds good")
- [ ] At stop point, agent could predict reactions to next three questions
