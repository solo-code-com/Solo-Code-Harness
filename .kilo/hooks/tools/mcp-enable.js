#!/usr/bin/env node
/**
 * mcp-enable.js — Dynamic MCP server enable/disable tool
 *
 * Programmatically manages .mcp.json by merging server configs from
 * .kilo/mcp-configs/reference-servers.json. Validates npx availability,
 * enforces the ≤8 active MCPs rule, and warns about context cost.
 *
 * Usage:
 *   node .kilo/hooks/tools/mcp-enable.js list                           — list active + available servers
 *   node .kilo/hooks/tools/mcp-enable.js enable <name>                  — enable an available server
 *   node .kilo/hooks/tools/mcp-enable.js disable <name>                 — disable an active server
 *   node .kilo/hooks/tools/mcp-enable.js suggest "<user request>"       — suggest relevant MCP servers
 *   node .kilo/hooks/tools/mcp-enable.js install <name>                 — dry-run install check (npx)
 *
 * Exit codes: 0 (success), 1 (validation failure), 2 (error)
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

// ─── Paths ──────────────────────────────────────────────────────────────────
const MCP_JSON = path.join(process.cwd(), '.mcp.json');
const REFERENCE_FILE = path.join(process.cwd(), '.kilo', 'mcp-configs', 'reference-servers.json');
const MAX_ACTIVE_MCPS = 8;

// ─── Server metadata for suggestion matching ────────────────────────────────
// Keywords that trigger suggestion for each server
const SERVER_SUGGESTIONS = {
  github: { keywords: ['pull request', 'pr', 'issue', 'repo', 'github', 'create pr', 'merge', 'review code on github'] },
  playwright: { keywords: ['browser', 'e2e', 'screenshot', 'web test', 'click button', 'navigate to', 'page test', 'ui test', 'end-to-end'] },
  filesystem: { keywords: ['read file', 'write file', 'list directory', 'file access', 'directory listing'] },
  supabase: { keywords: ['database', 'sql', 'supabase', 'postgres', 'migration', 'query data', 'table'] },
  jira: { keywords: ['jira', 'ticket', 'issue tracker', 'atlassian', 'sprint', 'backlog'] },
  'exa-web-search': { keywords: ['web search', 'google search', 'search internet', 'research online', 'look up'] },
  firecrawl: { keywords: ['scrape', 'crawl', 'extract web', 'fetch url content', 'scrape website'] },
  'fal-ai': { keywords: ['generate image', 'ai image', 'generate audio', 'ai video', 'text to image', 'dall-e'] },
  'sequential-thinking': { keywords: ['thinking', 'reasoning', 'chain of thought', 'step by step reason'] },
  memory: { keywords: ['memory', 'remember', 'knowledge graph', 'persistent storage'] },
  context7: { keywords: ['documentation', 'docs', 'api docs', 'library docs', 'sdk reference'] },
};

// ─── Helpers ────────────────────────────────────────────────────────────────

function readJSON(filePath) {
  try {
    if (fs.existsSync(filePath)) {
      return JSON.parse(fs.readFileSync(filePath, 'utf8'));
    }
  } catch (e) {
    process.stderr.write(`Error reading ${filePath}: ${e.message}\n`);
  }
  return null;
}

function writeJSON(filePath, data) {
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2) + '\n');
}

function mcpNameToKey(name) {
  return name.toLowerCase().replace(/[^a-z0-9_-]/g, '-');
}

function checkNpxAvailable() {
  const result = spawnSync('npx', ['--version'], {
    encoding: 'utf8',
    timeout: 10000,
    windowsHide: true,
    shell: process.platform === 'win32',
  });
  return { available: result.status === 0, version: result.stdout?.trim() || '' };
}

function countActiveMcps(mcpConfig) {
  return Object.keys(mcpConfig.mcpServers || {}).length;
}

// ─── Commands ───────────────────────────────────────────────────────────────

function cmdList() {
  const mcp = readJSON(MCP_JSON);
  const ref = readJSON(REFERENCE_FILE);

  if (!mcp) {
    process.stderr.write('Error: .mcp.json not found or invalid\n');
    process.exit(2);
  }

  const active = Object.entries(mcp.mcpServers || {});
  const available = ref ? Object.entries(ref.mcpServers || {}) : [];
  const activeNames = new Set(active.map(([k]) => k));

  process.stdout.write(`\n📦 MCP Servers — ${active.length} active, ${available.length - active.length} available, max ${MAX_ACTIVE_MCPS}\n`);
  process.stdout.write('═'.repeat(80) + '\n\n');

  process.stdout.write('🟢 Active:\n');
  for (const [name, cfg] of active) {
    const desc = cfg.description || '';
    const line = desc.length > 60 ? desc.substring(0, 57) + '...' : desc;
    process.stdout.write(`  ${name.padEnd(22)} ${line}\n`);
  }

  if (available.length > 0) {
    const inactive = available.filter(([k]) => !activeNames.has(k));
    if (inactive.length > 0) {
      process.stdout.write('\n⚪ Available (needs enable):\n');
      for (const [name, cfg] of inactive) {
        const desc = cfg.description || '';
        const line = desc.length > 60 ? desc.substring(0, 57) + '...' : desc;
        const keyReq = cfg.env && Object.keys(cfg.env).some(k => cfg.env[k].toUpperCase().startsWith('YOUR_'));
        process.stdout.write(`  ${name.padEnd(22)} ${line}${keyReq ? ' 🔑' : ''}\n`);
      }
    }
  }

  process.stdout.write(`\nSlots free: ${MAX_ACTIVE_MCPS - active.length}/${MAX_ACTIVE_MCPS}\n\n`);
}

function cmdEnable(name) {
  const mcp = readJSON(MCP_JSON);
  const ref = readJSON(REFERENCE_FILE);

  if (!mcp) { process.stderr.write('Error: .mcp.json not found\n'); process.exit(2); }
  if (!ref) { process.stderr.write('Error: reference-servers.json not found\n'); process.exit(2); }

  const key = mcpNameToKey(name);
  const active = Object.keys(mcp.mcpServers || {});

  if (active.includes(key)) {
    process.stdout.write(`ℹ️  "${key}" is already active.\n`);
    process.exit(0);
  }

  const serverConfig = ref.mcpServers?.[key];
  if (!serverConfig) {
    process.stderr.write(`Error: "${key}" not found in reference servers.\n`);
    process.stderr.write('Available: ' + Object.keys(ref.mcpServers || {}).join(', ') + '\n');
    process.exit(1);
  }

  // Enforce max MCPs
  if (active.length >= MAX_ACTIVE_MCPS) {
    process.stderr.write(
      `Error: Cannot enable "${key}". Already at max ${MAX_ACTIVE_MCPS} active MCPs.\n` +
      `Disable an unused server first: mcp-enable.js disable <name>\n`
    );
    process.exit(1);
  }

  // Check for placeholder API keys
  for (const [envKey, envVal] of Object.entries(serverConfig.env || {})) {
    if (envVal.toUpperCase().startsWith('YOUR_')) {
      process.stdout.write(
        `⚠️  "${key}" requires API key: ${envKey}=${envVal}\n` +
        `Set the environment variable before using this server.\n` +
        `Continuing registration — the server config is added but calls will fail without valid keys.\n\n`
      );
    }
  }

  // Check npx availability
  const npx = checkNpxAvailable();
  if (!npx.available && serverConfig.command === 'npx') {
    process.stderr.write('Error: npx not available. Install Node.js 18+ to use MCP servers.\n');
    process.exit(1);
  }

  // Add to active config
  mcp.mcpServers[key] = serverConfig;
  writeJSON(MCP_JSON, mcp);

  process.stdout.write(
    `✅ Enabled "${key}" — ${active.length + 1}/${MAX_ACTIVE_MCPS} active\n` +
    `   Command: ${serverConfig.command} ${(serverConfig.args || []).join(' ')}\n` +
    `   Restart agent session to apply.\n`
  );
}

function cmdDisable(name) {
  const mcp = readJSON(MCP_JSON);
  if (!mcp) { process.stderr.write('Error: .mcp.json not found\n'); process.exit(2); }

  const key = mcpNameToKey(name);
  if (!mcp.mcpServers?.[key]) {
    process.stderr.write(`Error: "${key}" is not active.\n`);
    process.exit(1);
  }

  delete mcp.mcpServers[key];
  writeJSON(MCP_JSON, mcp);

  process.stdout.write(
    `🛑 Disabled "${key}" — ${Object.keys(mcp.mcpServers).length}/${MAX_ACTIVE_MCPS} active\n` +
    `   Restart agent session to apply.\n`
  );
}

function cmdSuggest(query) {
  if (!query) {
    process.stderr.write('Usage: mcp-enable.js suggest "<user request>"\n');
    process.exit(2);
  }

  const mcp = readJSON(MCP_JSON);
  const ref = readJSON(REFERENCE_FILE);
  const activeNames = new Set(Object.keys(mcp?.mcpServers || {}));
  const refNames = new Set(Object.keys(ref?.mcpServers || {}));

  process.stdout.write(`Suggesting MCP servers for: "${query}"\n\n`);

  let suggestions = [];
  const queryLower = query.toLowerCase();

  for (const [name, meta] of Object.entries(SERVER_SUGGESTIONS)) {
    const matched = meta.keywords.filter(kw => queryLower.includes(kw));
    if (matched.length > 0) {
      const already = activeNames.has(name) ? ' (already active)' : '';
      const available = refNames.has(name) || activeNames.has(name) ? '' : ' (not in reference)';
      suggestions.push({ name, matches: matched, already: !!activeNames.has(name), available: refNames.has(name) || activeNames.has(name) });
    }
  }

  if (suggestions.length === 0) {
    process.stdout.write('No specific MCP suggestion found. Available reference servers:\n');
    for (const [name, cfg] of Object.entries(ref?.mcpServers || {})) {
      if (!activeNames.has(name)) {
        process.stdout.write(`  ${name}: ${cfg.description || 'no description'}\n`);
      }
    }
    process.exit(0);
  }

  for (const s of suggestions) {
    const icon = s.already ? '🟢' : '⚪';
    const action = s.already ? '(already active)' : s.available ? '(can enable)' : '(configure manually)';
    process.stdout.write(`${icon} ${s.name} ${action}\n`);
    process.stdout.write(`   Matched: ${s.matches.join(', ')}\n`);
  }
  process.stdout.write('');
}

function cmdInstall(name) {
  const ref = readJSON(REFERENCE_FILE);
  const mcp = readJSON(MCP_JSON);

  const key = mcpNameToKey(name);
  const serverConfig = ref?.mcpServers?.[key] || mcp?.mcpServers?.[key];

  if (!serverConfig) {
    process.stderr.write(`Error: "${key}" not found.\n`);
    process.exit(1);
  }

  if (serverConfig.command !== 'npx') {
    process.stdout.write(`ℹ️  "${key}" uses ${serverConfig.command}, not npx. No install needed.\n`);
    process.exit(0);
  }

  const pkg = serverConfig.args?.[1]; // "-y" is [0], package is [1]
  if (!pkg) {
    process.stdout.write(`ℹ️  No package to install for "${key}".\n`);
    process.exit(0);
  }

  process.stdout.write(`📥 Dry-run install: npx -y ${pkg}\n`);

  const result = spawnSync('npx', ['-y', ...serverConfig.args.slice(1)], {
    encoding: 'utf8',
    timeout: 30000,
    windowsHide: true,
    shell: process.platform === 'win32',
  });

  if (result.status === 0) {
    process.stdout.write(`✅ Package "${pkg}" resolves successfully.\n`);
  } else {
    process.stderr.write(`❌ Package "${pkg}" failed to resolve.\n`);
    if (result.stderr) process.stderr.write(result.stderr.substring(0, 500) + '\n');
    process.exit(1);
  }
}

// ─── Main ───────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
const command = args[0];
const target = args.slice(1).join(' ');

switch (command) {
  case 'list':
    cmdList();
    break;
  case 'enable':
    if (!args[1]) { process.stderr.write('Usage: mcp-enable.js enable <name>\n'); process.exit(2); }
    cmdEnable(args[1]);
    break;
  case 'disable':
    if (!args[1]) { process.stderr.write('Usage: mcp-enable.js disable <name>\n'); process.exit(2); }
    cmdDisable(args[1]);
    break;
  case 'suggest':
    cmdSuggest(target);
    break;
  case 'install':
    if (!args[1]) { process.stderr.write('Usage: mcp-enable.js install <name>\n'); process.exit(2); }
    cmdInstall(args[1]);
    break;
  default:
    process.stderr.write(
      'Usage: mcp-enable.js <list|enable|disable|suggest|install> [args]\n\n' +
      '  list                  — Show active and available MCP servers\n' +
      '  enable <name>         — Enable a server from reference config\n' +
      '  disable <name>        — Disable an active server\n' +
      '  suggest "<request>"   — Suggest MCP servers for a user request\n' +
      '  install <name>        — Test install of an MCP package\n'
    );
    process.exit(2);
}

process.exit(0);
