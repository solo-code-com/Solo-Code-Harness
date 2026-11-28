# Harness Review Checklist

> Run through this before shipping a harness to production or handing it off.
> A failing item is a blocker; a skipped item needs a written justification.

## Agent instructions (AGENTS.md)

- [ ] Project overview is accurate and up to date
- [ ] Repository structure reflects the current layout
- [ ] Tool permissions are explicit — allowed, restricted, and not-allowed are all specified
- [ ] Verification gates are defined and commands are correct
- [ ] No ambiguous instructions that could be interpreted multiple ways

## Hook system

- [ ] hooks.json is valid JSON with all lifecycle stages (PreToolUse, PostToolUse, SessionStart, SessionEnd)
- [ ] gate-guard.js blocks all destructive patterns (rm -rf, DROP TABLE, git push --force, etc.)
- [ ] secret-scan.js catches hardcoded API keys, passwords, and tokens
- [ ] config-protection.js blocks modification of linter/formatter configuration
- [ ] context-monitor.js has warning thresholds and output trim configured
- [ ] session-start.js resets state cleanly
- [ ] session-end.js emits session summary with token estimates and costs
- [ ] All hook scripts pass `node -c` syntax check

## Tool design

- [ ] Each tool has a clear, unambiguous name
- [ ] Tool schemas are minimal — no optional fields that the agent won't use
- [ ] Error messages tell the agent what to do next, not just what went wrong
- [ ] Tool return values are consistent (same shape on success and failure)
- [ ] No tool does more than one conceptual thing

## Context delivery

- [ ] Context is scoped to what the agent needs for this task — not the entire codebase
- [ ] Long-lived state (plans, decisions, progress) is in files, not in the prompt
- [ ] Context compaction strategy is defined for multi-session tasks
- [ ] Output trim thresholds are configured for high-volume tools (Bash, Grep, Glob)
- [ ] No sensitive data (secrets, credentials) in agent-accessible context
- [ ] Token logging and cost estimation enabled

## Planning artifacts

- [ ] PLAN.md exists for non-trivial tasks
- [ ] Milestones have explicit verification commands
- [ ] Scope boundaries (in-scope / out-of-scope) are written down
- [ ] IMPLEMENT.md captures decisions and deviations as they happen
- [ ] `/plan`, `/decide`, `/verify` commands are functional
- [ ] `.github/pull_request_template.md` exists and includes all verification gates

## Permissions & sandbox

- [ ] Agent runs with the minimum permissions needed for the task
- [ ] Destructive operations require explicit confirmation
- [ ] Network access is scoped if possible
- [ ] File system access is scoped to project directories
- [ ] kilo.jsonc deny rules cover all critical destructive operations

## Verification loop

- [ ] Tests exist for the agent's outputs (`tools/test_*.py`, `eval_harness.py`, `check_skips.py`, `.kilo/hooks/pre-tool-use/executor-mode.js` behavior)
- [ ] The agent can run the verification command itself (`checklist.py`)
- [ ] Verification runs automatically on task completion, not just on PR
- [ ] Eval criteria are written down before the task starts, not after
- [ ] Security scan passes before any commit
- [ ] No-skips policy enforced: `python .github/scripts/check_skips.py tools/`
- [ ] `security-allowlist.txt` is read as an audit trail only — no tool enforces it (see its header)

## Observability

> Every artifact in this section is written by `.kilo/hooks/*`, which run only under
> the Kilo engine, and Kilo no longer runs in this project. `.kilo/state/` is gitignored
> and ships empty, so an unticked box here is expected rather than a failure. If the
> hook layer is ever run again, judge freshness from each file's own contents rather
> than its mtime.

- [ ] Session start/end hooks capture duration, tool calls, token estimates
- [ ] Token usage logged per tool call (.kilo/state/token-log.jsonl) - only when the Kilo hook layer runs
- [ ] Session logs persisted (.kilo/state/sessions/) - only when the Kilo hook layer runs
- [ ] Governance events captured (.kilo/logs/governance-events.jsonl) - written by .kilo/hooks/pre-tool-use/governance-capture.js only when a Kilo hook records an event; its presence is not evidence that Kilo ran
- [ ] Cost estimates visible in session summary

## When this harness component should be removed

> Every harness component exists because the model can't do something yet.
> Document what capability improvement would make this component unnecessary.

| Component | Exists because | Can be removed when |
|---|---|---|
| gate-guard.js | Model doesn't reliably refuse destructive commands | Model has built-in safety refusal for rm -rf, DROP TABLE, force push |
| secret-scan.js | Model doesn't detect hardcoded secrets in context | Model integrates secret detection in code generation |
| context-monitor.js | No built-in context budget awareness in model | Models manage context window autonomously with compaction API |
| config-protection.js | Model may overwrite linter/formatter configs | Model respects configuration file boundaries natively |
| session-start.js | State must be initialized per session | Model runtime handles state lifecycle internally |
| session-end.js | Observability must be captured at session boundary | Runtime provides native telemetry and cost tracking |
| security_scan.py | CI must independently verify code safety | Model output is guaranteed safe by construction |
| checklist.py | Manual verification pipeline needed | Integrated CI/CD with agent-native checkpoints |
| check_skips.py | Model/developer may add skip() markers that silently degrade coverage | All test frameworks enforce skip-justification natively |
| security-allowlist.txt | Human-readable audit trail for risky call sites — NOT enforced by any tool (see its header) | — |
| eval_harness.py | Harness behavior must be independently tested | Harness is verified by model provider's compliance testing |

---

*Last reviewed: 2026-06-06*
