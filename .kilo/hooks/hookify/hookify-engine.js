#!/usr/bin/env node
/**
 * hookify-engine.js — Config Loader & Rule Engine for Solo-Code-Harness
 *
 * Loads rules from .kilo/hookify/*.md files (YAML frontmatter + markdown body).
 * Evaluates rules against hook input and returns blocking/warning decisions.
 *
 * Rule file format:
 *   .kilo/hookify/<rule-name>.md
 *   ---
 *   name: block-dangerous-rm
 *   enabled: true
 *   event: bash
 *   pattern: rm\s+-rf
 *   action: block
 *   ---
 *   ⚠️ Dangerous command detected!
 *
 * Exit codes: 0 (pass-through), 2 (blocked -- a rule with `action: block` matched).
 *
 * Usage: node hookify-engine.js <eventType>
 *   eventType: bash | file | stop | prompt | all
 */

'use strict';

const fs = require('fs');
const path = require('path');

const RULES_DIR = path.join(process.cwd(), '.kilo', 'hookify');
const LRU_SIZE = 128;

// ─── LRU Regex Cache ────────────────────────────────────────────────────────
const regexCache = new Map();

function compileRegex(pattern) {
  if (regexCache.has(pattern)) return regexCache.get(pattern);
  try {
    const re = new RegExp(pattern, 'i');
    regexCache.set(pattern, re);
    if (regexCache.size > LRU_SIZE) {
      const first = regexCache.keys().next().value;
      regexCache.delete(first);
    }
    return re;
  } catch {
    return null;
  }
}

// ─── YAML Frontmatter Parser ────────────────────────────────────────────────

function parseFrontmatter(content) {
  if (!content.startsWith('---')) return null;

  const endIdx = content.indexOf('---', 3);
  if (endIdx === -1) return null;

  const fmText = content.slice(3, endIdx).trim();
  const body = content.slice(endIdx + 3).trim();

  const fm = {};
  const lines = fmText.split('\n');

  for (const line of lines) {
    const stripped = line.trim();
    if (!stripped || stripped.startsWith('#')) continue;

    const colonIdx = stripped.indexOf(':');
    if (colonIdx === -1) continue;

    const key = stripped.slice(0, colonIdx).trim();
    const value = stripped.slice(colonIdx + 1).trim().replace(/^["']|["']$/g, '');

    if (value === 'true') fm[key] = true;
    else if (value === 'false') fm[key] = false;
    else if (value.startsWith('[') && value.endsWith(']')) {
      // Simple array: [value1, value2]
      fm[key] = value.slice(1, -1).split(',').map(v => v.trim().replace(/^["']|["']$/g, ''));
    }
    else fm[key] = value;
  }

  return { frontmatter: fm, message: body };
}

// ─── Rule Loading ───────────────────────────────────────────────────────────

function loadRules(eventType) {
  const rules = [];
  if (!fs.existsSync(RULES_DIR)) return rules;

  const files = fs.readdirSync(RULES_DIR).filter(f => f.endsWith('.md'));
  for (const file of files) {
    try {
      const content = fs.readFileSync(path.join(RULES_DIR, file), 'utf8');
      const parsed = parseFrontmatter(content);
      if (!parsed) continue;

      const { frontmatter: fm, message } = parsed;
      if (!fm.enabled) continue;

      // Filter by event
      const ruleEvent = fm.event || 'all';
      if (ruleEvent !== 'all' && ruleEvent !== eventType) continue;

      rules.push({
        name: fm.name || file.replace('.md', ''),
        event: ruleEvent,
        pattern: fm.pattern || null,
        action: fm.action || 'warn',
        toolMatcher: fm.tool_matcher || null,
        conditions: fm.conditions || null,
        message,
      });
    } catch {}
  }

  return rules;
}

// ─── Rule Evaluation ────────────────────────────────────────────────────────

function extractField(rule, toolName, toolInput, inputData) {
  // Direct pattern matching on command/file_path/new_text
  const toolInputObj = toolInput || {};

  // Cross-platform shell tool names
  const SHELL_TOOLS = new Set(['Bash', 'bash', 'run_command', 'execute', 'runCommand', 'terminal', 'execute_command', 'shell', 'RunCommand']);

  // bash event: check command
  if (rule.event === 'bash' && SHELL_TOOLS.has(toolName)) {
    return toolInputObj.command || '';
  }

  // file event: check file_path, old_string, new_string
  if (rule.event === 'file') {
    const parts = [];
    if (toolInputObj.file_path) parts.push(toolInputObj.file_path);
    if (toolInputObj.old_string) parts.push(toolInputObj.old_string);
    if (toolInputObj.new_string || toolInputObj.content) parts.push(toolInputObj.new_string || toolInputObj.content);
    return parts.join(' ');
  }

  // stop event: check stop reason
  if (rule.event === 'stop' && inputData) {
    return inputData.reason || '';
  }

  // prompt event: check user prompt
  if (rule.event === 'prompt' && inputData) {
    return inputData.user_prompt || '';
  }

  // Generic: check all available string fields
  const fields = [];
  if (typeof toolInput === 'string') fields.push(toolInput);
  else if (toolInputObj) {
    for (const v of Object.values(toolInputObj)) {
      if (typeof v === 'string') fields.push(v);
    }
  }
  return fields.join(' ');
}

function ruleMatches(rule, toolName, toolInput, inputData) {
  if (!rule.pattern) return false;

  const fieldValue = extractField(rule, toolName, toolInput, inputData);
  if (!fieldValue) return false;

  // Check tool matcher if specified
  if (rule.toolMatcher) {
    const matchers = rule.toolMatcher.split('|');
    if (!matchers.includes(toolName) && rule.toolMatcher !== '*') return false;
  }

  const re = compileRegex(rule.pattern);
  if (!re) return false;

  return re.test(fieldValue);
}

function evaluateRules(rules, toolName, toolInput, inputData) {
  const blocking = [];
  const warnings = [];

  for (const rule of rules) {
    if (ruleMatches(rule, toolName, toolInput, inputData)) {
      if (rule.action === 'block') blocking.push(rule);
      else warnings.push(rule);
    }
  }

  if (blocking.length > 0) {
    const msgs = blocking.map(r => `**[${r.name}]**\n${r.message}`);
    return {
      hookSpecificOutput: {
        permissionDecision: 'deny'
      },
      systemMessage: msgs.join('\n\n'),
    };
  }

  if (warnings.length > 0) {
    const msgs = warnings.map(r => `**[${r.name}]**\n${r.message}`);
    return { systemMessage: msgs.join('\n\n') };
  }

  return {};
}

// ─── Main ───────────────────────────────────────────────────────────────────
const eventType = process.argv[2] || 'all';

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.resume();
process.stdin.on('data', chunk => { raw += chunk; });
process.stdin.on('end', () => {
  let input = {};
  try { input = JSON.parse(raw); } catch {}

  const toolName = input.tool_name || input.toolName || '';
  const toolInput = input.tool_input || input.toolInput || {};
  const hookEventName = input.hook_event_name || '';

  // Cross-platform tool name sets
  const SHELL_TOOLS = new Set(['Bash', 'bash', 'run_command', 'execute', 'runCommand', 'terminal', 'execute_command', 'shell', 'RunCommand']);
  const EDIT_TOOLS = new Set(['Write', 'Edit', 'MultiEdit', 'write', 'edit', 'edit_file', 'write_file', 'fileEditor', 'editFile', 'writeFile']);

  // Determine event type from hook event or tool name
  let effectiveEvent = eventType;
  if (effectiveEvent === 'all') {
    if (hookEventName === 'Stop') effectiveEvent = 'stop';
    else if (hookEventName === 'UserPromptSubmit') effectiveEvent = 'prompt';
    else if (SHELL_TOOLS.has(toolName)) effectiveEvent = 'bash';
    else if (EDIT_TOOLS.has(toolName)) effectiveEvent = 'file';
  }

  const rules = loadRules(effectiveEvent);
  const result = evaluateRules(rules, toolName, toolInput, input);

  // Always pass through input, append any decisions
  process.stdout.write(raw);
  if (result.systemMessage) {
    process.stderr.write(result.systemMessage + '\n');
  }
  if (result.hookSpecificOutput?.permissionDecision === 'deny') {
    process.stderr.write('[Hookify] Rule matched — operation blocked.\n');
    process.exit(2);
  }

  process.exit(0);
});
