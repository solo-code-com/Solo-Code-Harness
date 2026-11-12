---
description: Generate and execute comprehensive tests for code changes.
---
# /test — Test Generation & Execution

## Sub-commands
- `/test` — Run all tests
- `/test [file/feature]` — Generate tests for specific target
- `/test coverage` — Show coverage report

## Behavior
### Generate Tests
1. **Analyze the code** — Identify functions, edge cases, dependencies to mock
2. **Generate test cases** — Happy path, error cases, edge cases
3. **Write tests** — Use project's test framework, follow existing patterns
4. **AAA Pattern** — Arrange → Act → Assert

### Run Tests
- Execute and report pass/fail counts
- On failure: show expected vs actual, suggest fix
- Run from project root

## Key Principles
- Test behavior, not implementation
- One assertion per test (when practical)
- Descriptive test names: "should [expected] when [condition]"
- Mock external dependencies
