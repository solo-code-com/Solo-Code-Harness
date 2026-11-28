#!/usr/bin/env node
/**
 * secret-scan.js — PreToolUse hook for Solo-Code-Harness
 *
 * Scans file content for hardcoded secrets before they're written.
 * Ported from ECC's governance-capture.js secret detection patterns.
 *
 * Exit codes:
 *   0 = ALLOW (no secrets found, an ignored path, or a finding that this hook
 *       only reports as a WARNING on stderr)
 *   2 = BLOCK (a secret detected where this hook refuses the write)
 *
 * Two tiers decide whether a finding blocks:
 *   - a FORMAT pattern (`sk-ant-...`, `AKIA...`, a PEM header, a connection URI)
 *     identifies a credential by shape, so it blocks in any non-prose file;
 *   - a HEURISTIC pattern keys on a name (`api_key = ...`) and needs the value
 *     to be read, so it blocks only in a config file, where a literal is
 *     expected. In code it warns: `password = current_password` is ordinary
 *     code, and a hook that blocks it is a hook people turn off. The CI gate
 *     (.github/scripts/security_scan.py) catches a quoted keyword value but not
 *     an unprefixed one in code -- a known gap, recorded in MEMORY.md.
 */

'use strict';

const MAX_STDIN = 1024 * 1024;

const SECRET_PATTERNS = [
  { name: 'aws_access_key', pattern: /(?:AKIA|ASIA)[A-Z0-9]{16}/ },
  { name: 'aws_secret_key', heuristic: true, pattern: /(?:aws|amazon).{0,20}(?:secret|key|token).{0,10}[:=]\s*["'][A-Za-z0-9/+=]{20,}["']/i },
  // `(?!\$)` keeps a quoted *reference* out of the block list: the old pattern
  // blocked `API_KEY="$(op read op://vault/key)"`, which pushed users toward
  // inlining the literal secret instead of resolving it at runtime. Mirrors
  // tools/guard_rules.py.
  { name: 'generic_api_key', heuristic: true, pattern: /(?:api[_-]?key|apikey|secret(?:[_-]?key)?|password|passwd)\s*[:=]\s*["'](?!\$)[^"']{8,}["']/i },
  // Unquoted `NAME=value` assignments. `generic_api_key` requires quotes, so
  // `export ANTHROPIC_API_KEY=<token>` passed straight through. Mirrors
  // tools/guard_rules.py, including its two exclusions: a `$`-leading value is
  // a reference, and `xxx`/`***` is a doc placeholder.
  { name: 'env_assignment_secret', heuristic: true, pattern: /(?:api[_-]?key|secret|token|password|passwd|credential)[a-z_-]*\s*[:=]\s*(?![^\s"']*(?:x{3}|\*{3}))[^\s"'$][^\s"']{15,}/i },
  { name: 'private_key_pem', pattern: /-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----/ },
  { name: 'jwt_token', pattern: /eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/ },
  { name: 'github_token', pattern: /(?:gh[pousr]_|github[_-]?pat[_-]?|github[_-]?token[_-]?)(?=[A-Za-z0-9_]*\d)[A-Za-z0-9_]{20,}/i },
  { name: 'google_api_key', pattern: /AIza[0-9A-Za-z_-]{35}/ },
  { name: 'slack_token', pattern: /xox[baprs]-[0-9A-Za-z-]{10,}/ },
  { name: 'stripe_key', pattern: /(?:sk|pk)_(?:test|live)_[0-9a-zA-Z]{24,}/ },
  // `[^:\s"'<>/?#]` keeps a benign URL out of the match: `http://localhost:3000`
  // followed by `@` in the path (`/@vite/client`) is not user:password auth, and
  // `postgres://localhost:5432/app` is not a credential URI. The same class of
  // bug in tools/guard_rules.py is a known gap -- the Python gate is what CI and
  // the delegation wrappers run.
  { name: 'mongodb_uri', pattern: /mongodb(?:\+srv)?:\/\/[^:\s"'<>/?#${}]+:[^@\s"'<>/?#${}]+@/ },
  { name: 'postgres_uri', pattern: /postgres(?:ql)?:\/\/[^:\s"'<>/?#${}]+:[^@\s"'<>/?#${}]+@/ },
  { name: 'redis_uri', pattern: /redis:\/\/[^:\s"'<>/?#${}]+:[^@\s"'<>/?#${}]+@/ },
  { name: 'hardcoded_token', heuristic: true, pattern: /(?:token|bearer)\s*[:=]\s*["'][A-Za-z0-9._\-+/=]{20,}["']/i },
  { name: 'discord_webhook', pattern: /https:\/\/discord(?:app)?\.com\/api\/webhooks\/\d+\/[A-Za-z0-9_-]+/i },
  { name: 'basic_auth', pattern: /https?:\/\/[^:\s"'<>/?#${}]+:[^@\s"'<>/?#${}]+@/ },
  // Prefixed-token formats. `generic_api_key` only fires on a QUOTED value, so
  // bare `KEY=sk-ant-...` shell/env forms passed straight through -- this
  // project's own Anthropic key format included. Length floors sit above
  // doc-placeholder length so README examples do not trip the gate.
  // Pinned by tools/test_secret_patterns.py.
  { name: 'anthropic_key', pattern: /sk-ant-[A-Za-z0-9\-_]{24,}/ },
  { name: 'openai_project_key', pattern: /sk-proj-[A-Za-z0-9\-_]{20,}/ },
  { name: 'npm_token', pattern: /npm_[A-Za-z0-9]{36}/ },
  { name: 'gitlab_pat', pattern: /glpat-[A-Za-z0-9\-_]{20,}/ },
  { name: 'digitalocean_token', pattern: /dop_v1_[A-Za-z0-9]{64}/ },
  // Authorization header form: no quotes, no "=", so `hardcoded_token` missed it.
  // `i` is required: `authorization: bearer <tok>` (lowercase) is what curl
  // examples use. Mirrors tools/guard_rules.py.
  { name: 'bearer_header', pattern: /Bearer\s+(?=[A-Za-z._\-+/=]*\d)[A-Za-z0-9._\-+/=]{20,}/i },
  // Legacy bare `sk-` keys with no recognised prefix. Mirrors guard_rules.py.
  // Does not overlap `anthropic_key`: the `-` in `sk-ant-` breaks the run.
  { name: 'openai_legacy_key', pattern: /sk-[A-Za-z0-9]{20,}/ },
];

// A heuristic finding blocks here, where a literal value belongs. Everything
// else is code or prose: a format pattern still blocks, a heuristic one warns.
const CONFIG_EXTENSIONS = new Set([
  '.env', '.env.local', '.env.development', '.env.production',
  '.yml', '.yaml', '.json', '.ini', '.cfg', '.conf', '.properties', '.toml',
  '.pem', '.key', '.crt', '.cert', '.p12', '.pfx', '.jks',
  '.p8', '.ppk',
]);

// Docs quote keys. Blocking a README edit over a documented example costs more
// than it protects, and a real key in prose is caught by the CI gate.
const PROSE_EXTENSIONS = new Set(['.md', '.mdx', '.txt', '.rst', '.adoc', '.log']);

// A synthetic value is not a leaked credential.
//
// The test is anchored: the WHOLE value must be a placeholder shape. A prefix
// match let `test1234!A9$zQ` through because its longest run was `test1234`,
// and an unbounded `your[-_ ].*` waved through any credential starting with
// `your_`. Neither the key NAME nor a substring of the value is tested.
const PLACEHOLDER_RE = new RegExp(
  '^(?:' +
    'x{4,}|0{4,}|\\*{3,}' + // masked
    '|<[^<>]{1,60}>' + // <your-key-here>
    '|todo|tbd|fixme|changeme|change[-_]me' +
    '|placeholder|redacted|hunter2' +
    '|test\\d{0,4}' + // test, test1, test1234
    '|(?:dummy|fake|sample|example|test)(?:[-_]?(?:key|token|secret|value|password|pass|data|api[-_]?key|here))?' +
    '|(?:your|my)[-_](?:key|token|secret|password|pass|api[-_]?key|access[-_]?key)(?:[-_]?here)?' +
    '|(?:key|token|secret|password)[-_]?(?:here|goes[-_]here)' +
    ')$',
  'i',
);

// `api_key = "ANTHROPIC_API_KEY"` names a variable; it is not the value.
const ENV_NAME_RE = /^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$/;

// `Authorization: Basic <b64>` is a credential even though it has a space.
const AUTH_SCHEME_RE = /^(?:basic|bearer|digest|token|bot)\s/i;

function isSynthetic(matchText) {
  const text = String(matchText);
  // The credential is the TRAILING quoted value, not the first one: a match can
  // span an earlier quoted placeholder (`aws_env = "test", aws_key = "wJal..."`),
  // and reading the first quote let that placeholder hide the real key.
  const quotedAll = [...text.matchAll(/["']([^"']*)["']/g)];
  const quoted = quotedAll.length ? quotedAll[quotedAll.length - 1][1] : null;
  const value = quoted !== null ? quoted : text.slice(text.search(/[:=]/) + 1).trim();

  if (PLACEHOLDER_RE.test(value)) return true;
  if (ENV_NAME_RE.test(value)) return true;
  // A quoted value with a space is prose ("Incorrect password") unless it
  // carries an auth scheme, which is a credential in its own right.
  if (quoted !== null && /\s/.test(value) && !AUTH_SCHEME_RE.test(value)) return true;
  return false;
}

/**
 * Scan content for secrets.
 * @param {string} content
 * @returns {Array<{name: string, line: number, heuristic: boolean}>}
 */
function scanSecrets(content) {
  if (!content || typeof content !== 'string') return [];

  const findings = [];
  const seen = new Set();
  const lines = content.split('\n');

  for (const { name, heuristic, pattern } of SECRET_PATTERNS) {
    // A line can hold two secrets. Python's `finditer` reports both, so walk
    // every match of a global copy instead of testing the line once.
    const flags = pattern.flags.includes('g') ? pattern.flags : pattern.flags + 'g';
    const re = new RegExp(pattern.source, flags);
    for (let i = 0; i < lines.length; i++) {
      for (const match of lines[i].matchAll(re)) {
        if (heuristic && isSynthetic(match[0])) continue;
        const key = name + ':' + (i + 1);
        if (!seen.has(key)) {
          seen.add(key);
          findings.push({ name, line: i + 1, heuristic: Boolean(heuristic) });
        }
      }
    }
  }

  return findings;
}

function extensionOf(filePath) {
  if (/(^|[\\/])\.env(\.[^\\/]*)?$/i.test(filePath)) return '.env';
  return '.' + filePath.split('.').pop().toLowerCase();
}

function isConfigFile(filePath) {
  return filePath ? CONFIG_EXTENSIONS.has(extensionOf(filePath)) : false;
}

function isProseFile(filePath) {
  return filePath ? PROSE_EXTENSIONS.has(extensionOf(filePath)) : false;
}

/**
 * Check if path is in an excluded directory.
 */
function isExcludedPath(filePath) {
  if (!filePath) return true;
  const excluded = ['node_modules', '.venv', 'venv', '__pycache__', '.git', 'dist', 'build', '.next', '.cache'];
  return excluded.some(dir => filePath.includes(`/${dir}/`) || filePath.includes(`\\${dir}\\`));
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
    process.stderr.write('[SecretScan] Input truncated, skipping scan\n');
    process.exit(0);
  }

  let input;
  try {
    input = JSON.parse(raw);
  } catch {
    process.exit(0);
  }

  const toolInput = input.tool_input || {};
  const filePath = toolInput.file_path || toolInput.path || toolInput.TargetFile || toolInput.AbsolutePath || toolInput.DirectoryPath || toolInput.uri || '';
  const content = toolInput.content || toolInput.new_str || toolInput.text || toolInput.CodeContent || toolInput.ReplacementContent || (toolInput.ReplacementChunks ? JSON.stringify(toolInput.ReplacementChunks) : '') || '';

  // Skip excluded paths
  if (isExcludedPath(filePath)) {
    process.exit(0);
  }

  const findings = scanSecrets(content);
  const prose = isProseFile(filePath);
  const config = isConfigFile(filePath);

  const blocking = prose ? [] : findings.filter(f => config || !f.heuristic);
  const warnings = findings.filter(f => !blocking.includes(f));

  if (warnings.length > 0) {
    process.stderr.write(
      `[SecretScan] WARNING — potential secrets detected in ${filePath}:\n` +
      warnings.map(f => `  Line ${f.line}: ${f.name}`).join('\n') +
      `\n  Verify this is not a real secret; the CI gate blocks on commit.\n`
    );
  }

  if (blocking.length > 0) {
    process.stderr.write(
      `\n[SecretScan] BLOCKED — potential secrets detected in ${filePath}:\n` +
      blocking.map(f => `  Line ${f.line}: ${f.name}`).join('\n') +
      `\n  Remove hardcoded secrets and use environment variables instead.\n\n`
    );
    process.exit(2);
  }

  process.exit(0);
});
