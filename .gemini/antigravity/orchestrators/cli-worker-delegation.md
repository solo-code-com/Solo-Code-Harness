---
description: CLI Worker Delegation — Antigravity orchestrator dispatches tasks to OpenCode, DeepSeek V4.1 Flash, and Codex
mode: orchestrator
---

# CLI Worker Delegation Orchestrator

Antigravity acts as the central coordinator: planning, formulating self-contained briefs, fencing worker execution scopes, and rigorously verifying the resulting diffs and quality gates.

## Phase 1: Task Decomposition & Briefing
**Orchestrator**: Antigravity
**Action**:
1. Identify the files to be touched.
2. Formulate a self-contained brief containing exact target paths, acceptance criteria, and forbidden edits.
3. Lock the target directory via `tools/shared_state.py` if performing multi-agent or concurrent writes.

## Phase 2: Worker Dispatch
**Workers**:
- **DeepSeek V4.1 Flash (Command Code / OpenCode Engine)** for narrow coding, mechanical edits, or test writing:
  ```bash
  # Targeted one-shot generation
  python tools/model_api.py "<brief>" --model commandcode/deepseek/deepseek-v4.1-flash

  # Headless execution
  python tools/opencode_delegate.py "<brief>" --model commandcode/deepseek/deepseek-v4.1-flash
  ```
- **Codex (GPT-6.1 Sol)** for independent logic review, security auditing, and design critique:
  ```bash
  python tools/codex_delegate.py "<review brief>" --model gpt-6.1-sol --effort medium
  ```

## Phase 3: Verification & Quality Gates
**Orchestrator**: Antigravity
**Action**:
1. Review `git diff` against the brief's permitted scope. Reject any unrequested refactoring.
2. Re-run all evidence commands reported by workers. Never trust self-reported confidence.
3. Execute standard repository gates:
   ```bash
   python tools/garden.py
   python tools/check_stdlib_only.py
   python .github/scripts/security_scan.py .
   ```

## Rules
- One clear responsibility per worker dispatch.
- Every worker is context-blind: inline all required specifications into the brief.
- The orchestrator retains final authority and commits the changes.
