#!/usr/bin/env node
/**
 * memory-manager.js — Memory file size gate for Solo-Code-Harness
 *
 * Runs as a PostToolUse hook on Write/Edit/MultiEdit operations.
 * Checks .kilo/memory/ files for character count limits.
 * Prevents memory bloat that exhausts context window.
 *
 * Thresholds:
 *   WARN at 4,000 chars — emit advisory
 *   HARD at 8,000 chars — block write, demand compaction
 *
 * Usage:
 *   node .kilo/hooks/post-tool-use/memory-manager.js
 *
 * Exit codes: 0 (pass/warn), 2 (hard block -- the signal every other blocking
 * hook in this repo uses; SPEC 8 and gate-guard both call that "BLOCK")
 */

'use strict';

const fs = require('fs');
const path = require('path');

// ─── Configuration ──────────────────────────────────────────────────────────
const MEMORY_DIR = path.join(process.cwd(), '.kilo', 'memory');
const WARN_CHARS = 4000;
const HARD_CHARS = 8000;
// Intentionally an allowlist, not every .md in MEMORY_DIR:
// decisions-archive.md is cold storage for entries pruned out of MEMORY.md
// (see its own header) -- it must NEVER be capped, since the whole point is
// it isn't loaded into session context and can grow without a token cost.
const MEMORY_FILES = ['MEMORY.md', 'project-conventions.md', 'harness-design-intent.md'];

// ─── Main ───────────────────────────────────────────────────────────────────
let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.resume();
process.stdin.on('data', chunk => { raw += chunk; });
process.stdin.on('end', () => {
  // Always pass through original output unchanged
  process.stdout.write(raw);

  let input;
  try {
    input = JSON.parse(raw);
  } catch {
    process.exit(0);
  }

  const toolName = input.tool_name || '';
  // Only check on file-writing operations
  if (!['Write', 'Edit', 'MultiEdit'].includes(toolName)) {
    process.exit(0);
  }

  const results = { warnings: [], blocks: [] };

  for (const file of MEMORY_FILES) {
    const filePath = path.join(MEMORY_DIR, file);
    if (!fs.existsSync(filePath)) continue;

    try {
      const content = fs.readFileSync(filePath, 'utf8');
      const charCount = content.length;
      const lineCount = content.split('\n').length;

      if (charCount > HARD_CHARS) {
        results.blocks.push({
          file,
          charCount: charCount.toLocaleString(),
          lineCount,
          limit: HARD_CHARS.toLocaleString(),
        });
      } else if (charCount > WARN_CHARS) {
        results.warnings.push({
          file,
          charCount: charCount.toLocaleString(),
          lineCount,
          limit: WARN_CHARS.toLocaleString(),
          remaining: (HARD_CHARS - charCount).toLocaleString(),
        });
      }
    } catch {}
  }

  // Emit warnings
  if (results.warnings.length > 0) {
    for (const w of results.warnings) {
      process.stderr.write(
        `\n[MemoryManager] ⚠ ${w.file}: ${w.charCount} chars (${w.lineCount} lines)\n` +
        `  Warning threshold: ${w.limit} chars. ${w.remaining} chars remaining before hard block.\n` +
        `  Suggest: MOVE (don't delete) the oldest/least-referenced entry to .kilo/memory/decisions-archive.md (uncapped, not auto-loaded).\n\n`
      );
    }
  }

  // Emit blocks
  if (results.blocks.length > 0) {
    for (const b of results.blocks) {
      process.stderr.write(
        `\n[MemoryManager] 🛑 BLOCKED: ${b.file}: ${b.charCount} chars (${b.lineCount} lines)\n` +
        `  Hard limit: ${b.limit} chars exceeded by ${(parseInt(b.charCount.replace(/,/g, '')) - HARD_CHARS).toLocaleString()} chars.\n` +
        `  Memory files loaded into EVERY session context. Every 1,000 chars ≈ 250 tokens (≈ $0.00007).\n` +
        `  Action: move (don't delete) the oldest/least-referenced entries to .kilo/memory/decisions-archive.md (uncapped, not auto-loaded -- still grep-able on demand).\n` +
        `  Detailed how-to docs still belong in .kilo/instruction/, not here.\n\n`
      );
    }
    process.exit(2); // Block the write
  }

  process.exit(0);
});
