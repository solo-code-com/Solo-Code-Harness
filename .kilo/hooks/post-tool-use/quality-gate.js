#!/usr/bin/env node
/**
 * quality-gate.js — PostToolUse hook for Solo-Code-Harness
 *
 * Runs lightweight formatter/linter checks after file edits.
 * Supports: Prettier, Biome, Ruff (Python), gofmt (Go).
 * Ported from ECC's quality-gate.js.
 *
 * Exit codes:
 *   0 = always (advisory -- findings go to stderr; a PostToolUse hook runs
 *       after the write, so it cannot block one)
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const MAX_STDIN = 1024 * 1024;

function exec(command, args, cwd = process.cwd()) {
  return spawnSync(command, args, {
    cwd,
    encoding: 'utf8',
    env: process.env,
    timeout: 15000,
    windowsHide: true,
  });
}

function log(msg) {
  process.stderr.write(`${msg}\n`);
}

/**
 * Find project root by looking for common marker files.
 */
function findProjectRoot(startDir) {
  let dir = path.resolve(startDir || process.cwd());
  const markers = ['package.json', 'pyproject.toml', 'go.mod', 'Cargo.toml', 'biome.json', '.prettierrc', '.git'];
  const root = path.parse(dir).root;

  while (dir !== root) {
    if (markers.some(m => fs.existsSync(path.join(dir, m)))) {
      return dir;
    }
    dir = path.dirname(dir);
  }
  return process.cwd();
}

/**
 * Check which formatter is configured in the project.
 */
function detectFormatter(projectRoot) {
  if (fs.existsSync(path.join(projectRoot, 'biome.json'))) return 'biome';
  if (fs.existsSync(path.join(projectRoot, 'biome.jsonc'))) return 'biome';
  if (fs.existsSync(path.join(projectRoot, '.prettierrc'))) return 'prettier';
  if (fs.existsSync(path.join(projectRoot, 'prettier.config.js'))) return 'prettier';
  if (fs.existsSync(path.join(projectRoot, 'package.json'))) {
    try {
      const pkg = JSON.parse(fs.readFileSync(path.join(projectRoot, 'package.json'), 'utf8'));
      if (pkg.prettier) return 'prettier';
    } catch {}
  }
  return null;
}

function runQualityGate(filePath) {
  if (!filePath || !fs.existsSync(filePath)) return;

  filePath = path.resolve(filePath);
  const ext = path.extname(filePath).toLowerCase();
  const projectRoot = findProjectRoot(path.dirname(filePath));
  const formatter = detectFormatter(projectRoot);

  // JS/TS — Prettier or Biome
  if (['.ts', '.tsx', '.js', '.jsx', '.json', '.md'].includes(ext)) {
    if (formatter === 'prettier') {
      const result = exec('npx', ['prettier', '--check', filePath], projectRoot);
      if (result.status !== 0) {
        log(`[QualityGate] Prettier check failed for ${path.relative(process.cwd(), filePath)}. Run: npx prettier --write "${filePath}"`);
      }
    } else if (formatter === 'biome') {
      const result = exec('npx', ['@biomejs/biome', 'check', filePath], projectRoot);
      if (result.status !== 0) {
        log(`[QualityGate] Biome check failed for ${path.relative(process.cwd(), filePath)}`);
      }
    }
  }

  // Python — Ruff
  if (ext === '.py') {
    const result = exec('ruff', ['format', '--check', filePath]);
    if (result.status !== 0) {
      log(`[QualityGate] Ruff check failed for ${path.relative(process.cwd(), filePath)}. Run: ruff format "${filePath}"`);
    }
  }

  // Go — gofmt
  if (ext === '.go') {
    const result = exec('gofmt', ['-l', filePath]);
    if (result.stdout && result.stdout.trim()) {
      log(`[QualityGate] gofmt check failed for ${path.relative(process.cwd(), filePath)}. Run: gofmt -w "${filePath}"`);
    }
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
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
    process.exit(0);
  }

  // Disable quality gate if opted out
  if (String(process.env.QUALITY_GATE_DISABLE || '').toLowerCase() === '1') {
    process.exit(0);
  }

  try {
    const input = JSON.parse(raw);
    const filePath = String(input?.tool_input?.file_path || '');
    runQualityGate(filePath);
  } catch {
    // Ignore parse errors
  }

  process.exit(0);
});
