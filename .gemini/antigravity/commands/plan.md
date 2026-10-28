---
description: Create an implementation plan for a complex task
allowed-tools: Read, Grep, Glob, Bash(git *)
---

# Implementation Plan

## 1. Understand
- What is the goal? What are the constraints?
- Explore related code in the codebase

## 2. Design
- What is the architecture?
- Which files need to change?
- What is the data flow?

## 3. Plan
Ordered steps with file paths and estimated changes.

## 4. Risk Assessment
- What could go wrong?
- Edge cases to handle
- Dependencies on other systems

## Plan Format
```
IMPLEMENTATION PLAN
===================
Goal: <summary>
Files: <N> to change
Steps (Goal-Driven):
1. [Action] → verify: [check]
2. [Action] → verify: [check]
Done when: <observable success criteria>
Risks: <risk> → <mitigation>
```
