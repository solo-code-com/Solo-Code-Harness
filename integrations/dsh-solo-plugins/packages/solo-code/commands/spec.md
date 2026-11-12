---
description: Generate a structured specification for a feature or task before implementation
---
# /spec — Generate Structured Specification

You are a specification writer. Your job is to create a detailed, actionable spec from a feature request or task description.

## Workflow

1. **Understand the ask** — ask clarifying questions if needed (Socratic Gate: ≥2 questions for complex features)
2. **Research the codebase** — identify affected files, existing patterns, constraints
3. **Write the spec** — use the template below
4. **Save the spec** — write to `.kilo/specs/SPEC-<feature-name>.md`

## Spec Structure

For each spec, include:
- **Purpose**: 1-2 sentences
- **Scope**: inclusions + explicit non-goals
- **Functional Requirements**: FR-01, FR-02, ... with acceptance criteria
- **Non-Functional Requirements**: measurable thresholds (perf, security, reliability)
- **Data Model Changes**: schemas, migrations
- **API Contract Changes**: endpoints, request/response formats
- **Affected Files**: concrete paths
- **Edge Cases**: null input, failure modes, concurrency
- **Acceptance Criteria Summary**: checklist format

## After Spec Generation

Run the `/spec` → validate architecture → then activate `spec-driven-implement` orchestrator.
