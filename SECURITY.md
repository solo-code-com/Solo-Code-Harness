# Security Policy for Solo-Code CLI

## Reporting a Vulnerability

If you discover a security vulnerability in Solo-Code CLI, please report it
responsibly:

**Email:** [admin@solo-code.com](mailto:admin@solo-code.com)

Do **not** file public GitHub issues for security vulnerabilities.

Please include:
- A detailed description of the vulnerability
- Steps to reproduce
- Relevant logs, screenshots, or proof-of-concept code
- Affected version(s)

## Scope

This policy covers security issues in:

- The Solo-Code CLI harness (`tools/` scripts, `.kilo/`, `.opencode/`, `.gemini/`)
- Verification scripts (`.github/scripts/`)
- Guard hook (`.kilo/hooks/pre-tool-use/executor-mode.js`)
- All agent, skill, hook, and instruction artifacts

## Response & Disclosure

- We will acknowledge your report within 48 hours.
- Security fixes will be released as soon as practical after verification.
- We will credit reporters who follow responsible disclosure (unless you request anonymity).

## Security Architecture

Solo-Code CLI is a zero-dependency harness (Python stdlib only, Node.js plugins only).
Key security boundaries:

| Boundary | Mechanism |
|----------|-----------|
| Destructive command prevention | Guard plugin v2.5 — 41 destructive patterns blocked |
| Secret detection | Pre-commit hooks + `security_scan.py` — 11 secret patterns |
| Git safety | No force-push to main, commit message scanning |
| Config protection | 19 protected config files (ESLint, Prettier, Ruff, etc.) |
| Input validation | All user input treated as untrusted |

For architecture details, see `.kilo/instruction/security-patterns.md` and
`AGENTS.md` Security Rules section.
