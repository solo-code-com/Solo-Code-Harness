#!/usr/bin/env node
/**
 * executor-mode.js — PreToolUse hook for Solo-Code-Harness
 *
 * Kilo-engine equivalent of tools/guard_hook.py's executor-mode gate.
 * Default-ON: the orchestrator does not write files directly, it delegates
 * to a worker (Kilo CLI) and verifies the result. Scope is level (a) --
 * Write/Edit/MultiEdit only. Bash stays open: it is how the orchestrator
 * runs the verification gates it still owns.
 *
 * Toggle: .solocode/executor-mode (relative to process.cwd(), the project
 * root Kilo hooks are invoked from). Absent or unreadable file means the
 * gate is ENABLED -- fail closed, not open, so a fresh clone or a deleted
 * toggle does not silently disable the gate.
 *
 * Exit codes:
 *   0 = ALLOW
 *   2 = BLOCK
 */

'use strict';

const fs = require('fs');
const path = require('path');

const MAX_STDIN = 1024 * 1024;

const OFF_VALUES = new Set(['off', '0', 'disabled', 'false', 'no']);

// Delegation plumbing must stay writable, or executor mode could never be
// turned off from inside a session, and the handoff brief to Gemini/Kilo CLI
// could never be written in the first place.
const ALLOWED_PREFIXES = [
  '.gemini/antigravity/handoff/inbox/',
  '.solocode/executor-mode',
];

function projectRoot() {
  return process.cwd();
}

function executorModeEnabled(root) {
  const statePath = path.join(root, '.solocode', 'executor-mode');
  let raw;
  try {
    raw = fs.readFileSync(statePath, 'utf8');
  } catch {
    return true; // Absent or unreadable -> default-on.
  }
  const value = raw.split('#', 1)[0].trim().toLowerCase();
  return !OFF_VALUES.has(value);
}

function toPosix(p) {
  return p.split(path.sep).join('/');
}

function executorModeExempt(filePath, root) {
  if (!filePath) return false;
  let relStr;
  try {
    const abs = path.isAbsolute(filePath) ? filePath : path.join(root, filePath);
    relStr = toPosix(path.relative(root, abs));
  } catch {
    relStr = toPosix(filePath).replace(/^\.\//, '');
  }
  return ALLOWED_PREFIXES.some(prefix => relStr.startsWith(prefix));
}

let raw = '';
let truncated = false;

process.stdin.setEncoding('utf8');
process.stdin.resume();
process.stdin.on('data', chunk => {
  if (raw.length < MAX_STDIN) {
    const remaining = MAX_STDIN - raw.length;
    raw += chunk.substring(0, remaining);
    if (chunk.length > remaining) truncated = true;
  } else {
    truncated = true;
  }
});

process.stdin.on('end', () => {
  process.stdout.write(raw);

  if (truncated) {
    process.stderr.write('[ExecutorMode] Hook input truncated (>1MB) — allowing, cannot inspect\n');
    process.exit(0);
  }

  let input;
  try {
    input = JSON.parse(raw);
  } catch {
    process.exit(0);
  }

  // Scope, stated honestly: this gates Write/Edit/MultiEdit only. Explicit
  // tool-name check as defense-in-depth -- do not rely solely on the
  // hooks.json matcher to keep Bash out of this gate.
  const WRITE_TOOLS = new Set(['Write', 'Edit', 'MultiEdit']);
  const toolName = input?.tool_name || '';
  if (!WRITE_TOOLS.has(toolName)) {
    process.exit(0);
  }

  const filePath = input?.tool_input?.file_path || input?.tool_input?.file || '';
  const root = projectRoot();

  if (!executorModeEnabled(root)) {
    process.exit(0);
  }

  if (executorModeExempt(filePath, root)) {
    process.exit(0);
  }

  process.stderr.write(
    `\n[ExecutorMode] BLOCKED — executor mode is ON. The orchestrator does not ` +
    `write files directly. Route this change to a worker:\n` +
    `  python tools/kilo_cli_delegate.py "<self-contained task naming ${filePath}>" --with-tools\n` +
    `Then verify the result yourself (read the file back, run the gates) before ` +
    `accepting it. Note: workers can misreport which path they wrote -- confirm ` +
    `with git status.\n` +
    `To disable: echo off > .solocode/executor-mode\n\n`
  );
  process.exit(2);
});
