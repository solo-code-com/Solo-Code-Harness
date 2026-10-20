#!/usr/bin/env node
/**
 * config-protection.js — PreToolUse hook for Solo-Code-Harness
 *
 * Blocks modifications to linter/formatter config files.
 * Agents often modify configs to silence errors instead of fixing code.
 * Ported from ECC's config-protection.js.
 *
 * Exit codes:
 *   0 = ALLOW (not a config file, or first-time creation)
 *   2 = BLOCK (existing config file modification attempted)
 */

'use strict';

const fs = require('fs');
const path = require('path');

const MAX_STDIN = 1024 * 1024;

const PROTECTED_FILES = new Set([
  // ESLint (legacy + flat config)
  '.eslintrc', '.eslintrc.js', '.eslintrc.cjs', '.eslintrc.json',
  '.eslintrc.yml', '.eslintrc.yaml',
  'eslint.config.js', 'eslint.config.mjs', 'eslint.config.cjs',
  'eslint.config.ts', 'eslint.config.mts', 'eslint.config.cts',
  // Prettier
  '.prettierrc', '.prettierrc.js', '.prettierrc.cjs', '.prettierrc.json',
  '.prettierrc.yml', '.prettierrc.yaml',
  'prettier.config.js', 'prettier.config.cjs', 'prettier.config.mjs',
  // Biome
  'biome.json', 'biome.jsonc',
  // Ruff
  '.ruff.toml', 'ruff.toml',
  // Shell/Style/Markdown
  '.shellcheckrc',
  '.stylelintrc', '.stylelintrc.json', '.stylelintrc.yml',
  '.markdownlint.json', '.markdownlint.yaml', '.markdownlintrc',
  // Python
  '.flake8', '.pylintrc', 'tox.ini',
  // Go
  '.golangci.yml', '.golangci.yaml', '.golangci.json',
  // General
  '.editorconfig',
]);

// Project-level configs that should never be auto-modified
const PROJECT_CONFIGS = new Set([
  'pyproject.toml.lint', // we only block if it looks like a lint-only change
  'Cargo.toml.lint',
]);

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
    // ALLOW when input is truncated — blocking large payloads would break
    // valid Write/Edit/MultiEdit tools with big file contents.
    // Config protection is best-effort; we cannot inspect >1MB payloads.
    process.stderr.write('[ConfigProtect] Hook input truncated (>1MB) — allowing, cannot inspect\n');
    process.exit(0);
  }

  let input;
  try {
    input = JSON.parse(raw);
  } catch {
    process.exit(0);
  }

  const filePath = input?.tool_input?.file_path || input?.tool_input?.file || '';
  if (!filePath) {
    process.exit(0);
  }

  const basename = path.basename(filePath);

  if (PROTECTED_FILES.has(basename)) {
    // Allow first-time creation — no existing config to weaken
    let exists = true;
    try {
      fs.lstatSync(filePath);
    } catch (err) {
      if (err && err.code === 'ENOENT') {
        exists = false;
      }
    }

    if (!exists) {
      process.exit(0);
    }

    process.stderr.write(
      `\n[ConfigProtect] BLOCKED — modifying ${basename} is not allowed.\n` +
      `  Fix the source code to satisfy linter/formatter rules instead of weakening the config.\n\n`
    );
    process.exit(2);
  }

  process.exit(0);
});
