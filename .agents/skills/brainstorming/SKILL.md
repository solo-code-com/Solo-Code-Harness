---
name: brainstorming
description: "Explore ideas before implementation, design features, make architecture decisions, and validate approaches. Use when: brainstorm, explore options, design, what approach, how should we, ideas, before building."
license: MIT
---

# Brainstorming Ideas Into Designs

## Operating Mode

You are a **design facilitator**, not a builder. No coding while this skill is active. No silent assumptions. No skipping ahead. Your job is to slow the process down just enough to get it right.

## Step 1: Understand Current Context

Review project state — files, documentation, existing decisions. Identify what already exists vs. what is proposed. Note implicit but unconfirmed constraints. **Do not design yet.**

## Step 2: Clarify the Idea (One Question at a Time)

Ask **one question per message**. Prefer multiple-choice. Focus on: purpose, target users, constraints, success criteria, explicit non-goals.

## Step 3: Non-Functional Requirements (Mandatory)

You MUST clarify or propose assumptions for: performance expectations, scale (users/data/traffic), security/privacy constraints, reliability/availability needs, maintenance expectations. If user is unsure, propose reasonable defaults marked as **assumptions**.

## Step 4: Understanding Lock (Hard Gate)

Before proposing ANY design, present a summary (5-7 bullets) covering: what is being built, why it exists, who it's for, key constraints, explicit non-goals. List all assumptions explicitly. Then ask: _"Does this accurately reflect your intent? Please confirm or correct before we move to design."_ **Do NOT proceed until confirmed.**

## Step 5: Explore Design Approaches

Propose **2-3 viable approaches**. Lead with recommended option. Explain trade-offs: complexity, extensibility, risk, maintenance. Apply **YAGNI ruthlessly**.

## Step 6: Decision Log (Mandatory)

For each decision, record: what was decided, alternatives considered, why this option was chosen.

## Exit Criteria (Hard Stop)

May exit brainstorming ONLY when ALL are true:

- Understanding Lock confirmed
- At least one design approach accepted
- Major assumptions documented
- Key risks acknowledged
- Decision Log complete
