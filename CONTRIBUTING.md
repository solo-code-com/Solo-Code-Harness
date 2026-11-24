# Contributing to Solo-Code CLI

Thank you for considering contributing to the Solo-Code AI Agent Harness.

## What We Build

Solo-Code is a discipline layer for AI coding agents — rules, skills, hooks, and verification gates that transform any AI coding assistant into a disciplined engineer. We maintain the harness across three engines: OpenCode, Kilo Code, and Gemini/Antigravity.

## How to Contribute

### Reporting Issues

- Search [existing issues](https://github.com/solo-code-io/solo-code-cli/issues) before filing a new one.
- Include clear steps to reproduce, expected vs actual behavior, and platform/engine version.
- Use the issue templates if available.

### Submitting Changes

1. **Fork** the repository.
2. Create a **feature branch** from `main`:
   ```
   git checkout -b feat/my-change
   ```
3. Follow the **git commit convention** described in `AGENTS.md`:
   ```
   feat(scope): short description

   Co-Authored-By: Solo-Code <admin@solo-code.com>
   ```
4. Run the **verification gates** before committing:
   ```
   make check
   ```
   This runs: lint (ruff), schema validation, garden drift detection, harness tests, and security scan.
5. Submit a **pull request** against `main`.

### Development Setup

Running the harness needs only Python 3.10+ and Node 18+ — its runtime code is
stdlib-only. The verification gates need two dev tools, declared in the `dev`
extra of `pyproject.toml`: `pytest` (tests) and `ruff` (lint).

```bash
# Clone
git clone https://github.com/solo-code-io/solo-code-cli.git
cd solo-code-cli

# Install dev tools (pytest + ruff) into the interpreter make uses
python -m pip install -e ".[dev]"

# Run verification gates
make check

# Generate harness artifacts
make generate
```

`make test` and `make check` call `python -m pytest`, so pytest must live in
whatever interpreter `python` resolves to. An activated virtualenv shadows the
system Python and hides its packages — if `python -m pytest` reports
`No module named pytest`, install the dev extra into that virtualenv.

### Coding Standards

- **Python**: Follow PEP 8. Use `ruff` for linting and formatting.
- **JavaScript/Node**: ESLint where configured. Keep plugins self-contained.
- **Skills**: Write SKILL.md following the `writing-great-skills` skill format.
- **Agents**: YAML frontmatter with `max_turns`, `model`, `skills`, `status_contract`.
- **Zero external dependencies** in Python code. Stdlib only.
- **Match existing patterns** — read nearby files before writing new code.

### Feature Gates

New features must pass the two-filter system documented in `.kilo/memory/project-conventions.md`:
1. **DNA fit**: Does this belong in a config/rule/skill/script layer?
2. **Complexity budget**: Is the benefit worth the added maintenance?

Infrastructure concerns (Docker, servers, external SDKs, package registries) are explicitly out of scope.

## Questions?

Open a [discussion](https://github.com/solo-code-io/solo-code-cli/discussions) or ask in issues.
