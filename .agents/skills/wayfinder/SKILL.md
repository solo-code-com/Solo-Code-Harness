---
name: wayfinder
description: Plan a huge chunk of work — more than one agent session can hold — as a shared map of investigation tickets. Chart the route through fog, resolve one ticket per session, until the way to the destination is clear.
disable-model-invocation: true
---

# Wayfinder — Multi-Session Planning

A loose idea has arrived — too big for one agent session, wrapped in fog. Wayfinding is about finding the **way** to a destination, not charging at building it. This skill charts the way as a **shared map** in `.kilo/plans/<slug>/map.md`, then works its tickets one at a time until the route is clear.

The map is domain-agnostic — engineering work, architecture decisions, data migration, whatever fits the shape.

## Plan, don't do

Wayfinder produces **decisions, not deliverables**. Each ticket resolves a decision or investigation. The map is done when nothing is left to decide before someone builds the thing. An effort can override this in its **Notes** — carrying execution into the map — but by default, produce decisions, not deliverables.

## The Map

The map is a single file: `.kilo/plans/<slug>/map.md`. Tickets are sub-files in `.kilo/plans/<slug>/tickets/`.

The map is an **index**, not a store. It lists decisions made and points at tickets that hold their detail. A decision lives in exactly one place — its ticket — so the map never restates it, only gists it and links.

```markdown
# Wayfinder Map: [Destination Name]

## Destination

<what reaching the end of this map looks like — the spec, decision, or change.
One or two lines; every session orients to it before choosing a ticket.>

## Notes

<domain context; skills every session should consult; standing preferences>

## Decisions so far

<!-- one line per closed ticket: gist + link -->

- [<closed ticket title>](tickets/001-name.md) — one-line summary of the answer

## Not yet specified

<!-- in-scope fog you can't ticket yet; graduates as the frontier advances -->

## Out of scope

<!-- work ruled beyond the destination; closed, never graduates -->
```

### Ticket body

```markdown
# [Ticket Title]

- Type: research | prototype | grilling | task
- Mode: HITL (human in the loop) | AFK (agent alone)
- Blocked by: [link to blocking ticket, if any]

## Question

<the decision or investigation this ticket resolves>
```

### Ticket status management

- **Pending**: created, not yet claimed
- **In progress**: assigned (you claimed it)
- **Blocked**: waiting on another ticket's resolution
- **Resolved**: question answered, decision recorded

Track status in each ticket file's frontmatter or first line. No external tracker needed.

## Ticket Types

| Type | Mode | Description |
|------|------|-------------|
| **Research** | AFK | Read docs, source code, APIs. Produce a cited `.md` summary. Use when knowledge outside current context is needed. |
| **Prototype** | HITL | Build a throwaway artifact to answer "how should it look/behave." Use the `/spike` skill. |
| **Grilling** | HITL | Interview the user one question at a time. Use the `/interview-me` skill. |
| **Task** | HITL/AFK | Manual work that unblocks a decision (provisioning access, moving data). Agent drives alone where possible; otherwise hands human a checklist. |

## Fog of war

The map is *deliberately* incomplete. Beyond live tickets lies the **fog of war** — the dim view of decisions you can tell are coming but can't yet pin down, because they hang on questions still open.

**Fog or ticket?** The test is whether you can state the question precisely *now* — not whether you can answer it now.
- **Ticket when** the question is already sharp — even if blocked.
- **Not yet specified when** you can't phrase it that sharply yet.

Resolving a ticket clears the fog ahead of it, graduating whatever's now specifiable into fresh tickets.

## Out of scope

Fog gathers *toward* the destination. The destination fixes the scope, so work beyond it is **out of scope** — not fog, not part of **Not yet specified**. List it in **Out of scope**: work consciously ruled out of *this* effort.

Out-of-scope work never graduates. It returns only if the destination is redrawn.

## Invocation

Two modes. Either way, **never resolve more than one ticket per session.**

### Chart the map

User invokes with a loose idea.

1. **Name the destination.** Run `/interview-me` to pin down what this map is finding its way to. The destination fixes the scope — settle it first.
2. **Map the frontier.** Grill again, **breadth-first**: fan out across the whole space, surfacing open decisions and first steps. **If this surfaces no fog** — the way is already clear, small enough for one session — stop and tell the user they don't need a map.
3. **Create the map** file and **Not yet specified** section. Decisions-so-far starts empty.
4. **Create the tickets you can specify now.** Wire blocking edges in a second pass (tickets need names before they can reference each other). What you can't specify stays in **Not yet specified**.
5. Stop — charting is one session's work.

### Work through the map

User invokes with a map path.

1. **Load the map** — low-res view, not every ticket body.
2. **Choose the ticket.** Take the first unblocked, unclaimed ticket. **Claim it** by marking it `In progress` before any work.
3. **Resolve it.** Zoom as needed: fetch related or closed ticket bodies on demand. Invoke skills named in **Notes**.
4. **Record the resolution** in the ticket file, **mark it Resolved**, and **append a one-line gist** to the map's **Decisions so far**.
5. **Add newly-surfaced tickets.** Graduate any fog the answer has made specifiable, clearing each graduated patch from **Not yet specified**.
6. If the answer reveals a ticket sits beyond the destination, **rule it out of scope** — add to **Out of scope**, mark ticket closed.
