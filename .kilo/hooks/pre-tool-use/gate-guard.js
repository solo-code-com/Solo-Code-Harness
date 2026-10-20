#!/usr/bin/env node
/**
 * gate-guard.js — PreToolUse hook for Solo-Code-Harness
 *
 * Validates shell commands with 7 category patterns + safe alternatives.
 * Upgraded from Claude Code version validate_bash.py.
 *
 * Categories:
 *   BLOCK (exit 2), WARN, PATH, MODE, SED, SEMANTIC, PIPE
 *
 * Exit codes: 0 = ALLOW, 2 = BLOCK
 *
 * Cross-platform tool name support:
 *   Claude Code: Bash | Kilo: bash | Gemini: run_command, execute
 *   Copilot: runCommand, terminal | Cursor: execute_command
 */

'use strict';

const MAX_STDIN = 1024 * 1024;

// ─── BLOCK PATTERNS (exit 2) ───────────────────────────────────────────────
// Kept 1:1 with .claude/hooks/guard.py. Kilo previously enforced a strictly
// weaker set than Claude: rm_system_dir, rm_temp_linux, chmod_chown_system and
// thirteen others were missing here, so `rm -rf /etc` blocked under Claude and
// passed under Kilo.
//
// `rm` must sit in COMMAND position -- start of line, after a shell separator, or
// as an xargs target. A bare `rm\s+` also matched `docker run --rm -v /var/...`
// and `rg "rm /" .`, and blocked both. An over-block is worse than a miss here:
// a guard that blocks routine work gets switched off.
//
// The `bash|sh -c '...'` and `sudo`/`env` wrappers are unwrapped by
// normalizeCommand() below before patterns run, exactly as guard.py unwraps them
// in normalize_command(). Handling them here instead caused a divergence: Kilo
// blocked `echo "bash -c 'rm -rf /'"` (matched the quoted wrapper) while Claude
// allowed it.
const RM_CMD = '(?:^|[;&|\\n]\\s*(?:(?:sudo\\s+)?(?:bash|sh)\\s+-c\\s+[\'"]?\\s*)?|\\bxargs\\s+(?:-\\S+\\s+)*|\\b(?:sudo|env|nice|nohup|command|time)\\s+(?:\\w+=\\S*\\s+)*)rm\\s+(?:--\\s+)?';
// PowerShell/cmd counterpart of command position, with the wrappers that run a
// nested command: `rg 'Remove-Item x -Recurse -Force' .` must stay allowed, since
// that text only names the invocation.
const PS_CMD = '(?:^|[;&|\\n]\\s*)(?:(?:powershell|pwsh|cmd)(?:\\.exe)?\\s+(?:[-/]\\S+\\s+)*[\'"]?\\s*)?';
// At least one destructive flag (`+` on a flag+separator pair): a flagless `rm`
// cannot remove a directory, and matching zero flags made `rm *.tmp` and
// `rm /tmp/test.pid` look like recursive wipes. A short bundle may mix in benign
// flags (`-rfv`) -- requiring only r/R/f/F let `rm -rfv /` through -- but at
// least one of r/R/f/F must appear, or rm_system_dir would catch
// `rm -v /var/log/app.log`. The separator may be a zero-width boundary at
// end-of-command so `rm / -rf` (flags last) matches too.
const RM_FLAG = '(?:-[a-zA-Z]*[rRfF][a-zA-Z]*|--(?:recursive|force)|--)';
const RM_FLAGS = '(?:' + RM_FLAG + '(?:\\s+|(?=[;&|\\n]|$)))+';
// A path resolving to a filesystem root: `/`, `//`, `/./`, `/.`, `/../`, plus
// the Windows `C:/` and git-bash `/c/` spellings. An optional leading quote is
// part of the path, so `rm -rf '/'` / `rm -rf "/"` are still roots.
const ROOT_PATH = '[\'"]?(?:/(?:/|\\.+/?)*|[A-Za-z]:[/\\\\]+|/[A-Za-z]/+)';
// A catastrophic target that may appear BEFORE the flags, as in `rm / -rf`:
// ROOT_PATH plus the whole-system trees and tmp. A routine file under one of
// those dirs (`rm /tmp/x.pid`) still fails the required `\s+<flags>`.
const TARGET_PATH = '(?:' + ROOT_PATH + '(?:(?:etc|usr|var|bin|lib(?:64)?|boot|sbin|opt|root|sys|proc|dev|tmp)/?)?|~|\\*)';
// Windows spellings of the same catastrophe; TARGET_PATH carries the Unix trees.
const WIN_SYSTEM = '[\'"]?[A-Za-z]:[/\\\\]*(?:(?:Windows|WinNT|Users|ProgramData|System32|SysWOW64|Program Files(?: \\(x86\\))?)(?:[/\\\\]|\\s|$|"|\')|(?=\\s|$|\\*|"|\'))';
const BLOCK_PATTERNS = [
  // A shell separator terminates a root token exactly as whitespace does:
  // `rm -rf /; next` is the same wipe, and it is what an unwrapped
  // `bash -c 'rm -rf /; echo done'` leaves behind.
  { name: 'rm_root', pattern: new RegExp(RM_CMD + RM_FLAGS + ROOT_PATH + '(?:\\s|$|\\*|"|\'|;|&|\\|)') },
  // A root target that is not the first argument: `rm -rf build /`.
  { name: 'rm_root_after_other_target', pattern: new RegExp(RM_CMD + RM_FLAGS + '[^&|;\\n]*\\s+' + ROOT_PATH + '(?:\\s|$|\\*|"|\')') },
  { name: 'rm_flags_after_target', pattern: new RegExp(RM_CMD + '(?:[-\\w]+\\s+)*' + TARGET_PATH + '\\s+(?:[-\\w]+\\s+)*' + RM_FLAGS) },
  { name: 'rm_home', pattern: new RegExp(RM_CMD + RM_FLAGS + '~') },
  { name: 'rm_wildcard', pattern: new RegExp(RM_CMD + RM_FLAGS + '\\*') },
  { name: 'rm_relative_wildcard', pattern: new RegExp(RM_CMD + RM_FLAGS + '\\./(?:\\s|$|\\*)') },
  // A root-relative path that escapes back to root via `..` (`/home/../`,
  // `C:/../`, `/c/../`): ROOT_PATH stops at the first segment, so the dirs*
  // fragment steps over the segments before the `..`. The `..` must end the path
  // (optionally chained) so a file named `..backup`, or a targeted path like
  // `/home/../workspace/build`, is not blocked.
  { name: 'rm_root_escape', pattern: new RegExp(RM_CMD + RM_FLAGS + ROOT_PATH + '(?:[^/\\s]+[/\\\\])*\\.\\.(?:[/\\\\]\\.\\.)*[/\\\\]?(?=\\s|$|[;&|*])') },
  { name: 'rm_no_preserve', pattern: new RegExp(RM_CMD + '.*(?:--no-preserve-root|--preserve-root[=\\s]+no)') },
  { name: 'rm_system_dir', pattern: new RegExp(RM_CMD + RM_FLAGS + ROOT_PATH + '(?:etc|usr|var|bin|lib(?:64)?|boot|sbin|opt|root|sys|proc|dev)(?:/|\\s|$|"|\'|;|&|\\|)') },
  { name: 'rm_temp_linux', pattern: new RegExp(RM_CMD + RM_FLAGS + '/tmp/') },
  { name: 'rm_temp_win', pattern: new RegExp(RM_CMD + RM_FLAGS + '\\$?(?:env:)?TEMP\\b', 'i') },
  { name: 'del_temp_win', pattern: /(?:^|[;&|]\s*)del\s+(?:\/f\s+)?\/[qs]\s+\$?(?:env:)?TEMP\b/i },
  // Windows spellings of the Unix system trees TARGET_PATH carries. Measured open
  // on 2026-10-16 in both engines: `rm -rf C:/Windows`, `Remove-Item -Recurse C:\`.
  // A plain directory stays allowed; only a drive root or a system tree matches.
  { name: 'rm_win_system', pattern: new RegExp(RM_CMD + RM_FLAGS + WIN_SYSTEM, 'i') },
  { name: 'win_remove_recurse_root', pattern: new RegExp(PS_CMD + 'Remove-Item\\b[^|;&\\n]*-Recurse\\b[^|;&\\n]*' + WIN_SYSTEM, 'i') },
  // Flag order reversed: `Remove-Item C:/ -Recurse` stayed open on 2026-10-16
  // because the pattern above only reads flag-then-target.
  { name: 'win_remove_root_then_recurse', pattern: new RegExp(PS_CMD + 'Remove-Item\\b[^|;&\\n]*' + WIN_SYSTEM + '[^|;&\\n]*-Recurse\\b', 'i') },
  { name: 'force_push_main', pattern: /(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+\s+)*push\b[^&|;\n]*(?:--force|-f)\s+[^&|;\n]*(?:main|master)/ },
  // The segment stops at a shell separator, because
  // `git push origin main && npm install --force` was being read as a force-push.
  // `-fu` style bundles count (a bare `-f\b` missed them). `--force-with-lease`
  // stays allowed: it is the safe alternative this harness recommends. Anchored
  // to command position so `rg 'git push --force main'` and a commit message that
  // merely NAMES a force-push are not blocked. Options between `git` and `push`
  // are allowed (`git -C . push --force`).
  { name: 'force_push_any', pattern: /(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+\s+)*push\b[^&|;\n]*(?:--force\b(?!-)|(?:^|\s)(?!-{2})-[a-zA-Z]*f[a-zA-Z]*\b)/ },
  // The `+branch` refspec force-pushes without any flag. AGENTS.md names this
  // exact form as forbidden, and nothing matched it.
  { name: 'force_push_refspec', pattern: /(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+\s+)*push\b[^&|;\n]*\s\+[^\s&|;]+/ },
  // Deleting a protected branch on the remote: a force-push in effect, and
  // nothing matched it before. Anchored to command position so a search that
  // only NAMES it (`rg 'git push --delete main'`) is not blocked; git accepts
  // `--delete <remote> <branch>`, so an optional remote may precede the branch.
  // The name must be terminated (not `main-fix`), and the `:main` refspec only
  // counts when the colon opens a token, so a push to `<src>:main` (which
  // writes) is left alone.
  { name: 'force_push_delete_main', pattern: /(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+\s+)*push\b[^&|;\n]*(?:--delete\s+|-d\s+|(?:^|\s):)(?:[^\s&|;]+\s+)?(?:refs\/heads\/)?(?:main|master)(?![\w-])/ },
  // Anchored to the subcommand so a commit message that merely mentions the
  // phrase is not blocked. `--ha`/`--har` count: git takes abbreviations.
  { name: 'git_reset_hard', pattern: /(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+\s+)*reset\b[^&|;\n]*--ha(?:rd?)?/ },
  { name: 'git_clean_force', pattern: /(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+\s+)*clean\s+-f/ },
  { name: 'drop_table', pattern: /DROP\s+(?:TABLE|DATABASE)/i },
  { name: 'truncate_table', pattern: /TRUNCATE\s+TABLE/i },
  { name: 'dd_raw', pattern: /dd\s+if=/ },
  { name: 'dd_device_write', pattern: /dd\s+.*of=\/dev\// },
  { name: 'mkfs', pattern: /mkfs\./ },
  { name: 'shred', pattern: /shred\s+/ },
  { name: 'dev_write', pattern: />\s*\/dev\/sd[a-z]/ },
  // Anchored to command position; wrappers (`sudo`, `bash -c`) are handled by
  // normalizeCommand() so a search that merely NAMES the pipe is not blocked
  // while a wrapped one is caught and the engines agree. Covers piping into a
  // shell (sh/bash/zsh/ksh, with or without sudo) and into an interpreter that
  // executes the payload (python, perl, ruby, node, pwsh, iex).
  { name: 'curl_pipe_shell', pattern: /(?:^|[;&|]\s*)(?:curl|wget)\s+[^|&;\n]*\|\s*(?:(?:sudo|env)\s+(?:\w+=\S*\s+)*)?(?:(?:ba|z|k)?sh\b|python[0-9.]*\b|perl\b|ruby\b|node\b|pwsh\b|powershell\b|iex\b)/ },
  // The PowerShell download-and-execute idiom (`irm <url> | iex`), which runs
  // whatever the URL returns with no file on disk. Anchored to command position
  // so `rg 'irm x | iex'` is not blocked. Piping to Out-File is a different,
  // legitimate shape.
  { name: 'powershell_download_pipe_iex', pattern: /(?:^|[;&|]\s*)(?:irm|iwr|Invoke-WebRequest|Invoke-RestMethod)\b[^|&;\n]*\|\s*(?:iex|Invoke-Expression)\b/i },
  { name: 'chmod_chown_system', pattern: /(?:chmod|chown)\s+-R\s+(?:[^\/\s]+\s+)*\/(?:etc|usr|var|bin|lib(?:64)?|boot|sbin|opt|root|sys|proc|dev)(?:\/|\s|$)/ },
  { name: 'win_del_force', pattern: /(?:^|[;&|]\s*)del\s+\/f\s+\/s/ },
  { name: 'win_rd_recursive', pattern: /(?:^|[;&|]\s*)(?:rd|rmdir)\s+(?:.*\s)?\/s\b/i },
  { name: 'win_del_any', pattern: /(?:^|[;&|]\s*)del\s+(?:.*\s)?\/[qsf]\b/i },
  { name: 'win_remove_recursive', pattern: new RegExp(PS_CMD + 'Remove-Item\\b[^|;&\\n]*-Recurse\\b[^|;&\\n]*-Force\\b', 'i') },
  { name: 'win_format_volume', pattern: /(?:^|[;&|]\s*)Format-Volume\b/i },
  { name: 'win_stop_computer', pattern: /(?:^|[;&|]\s*)Stop-Computer\b/i },
  { name: 'win_restart_computer', pattern: /(?:^|[;&|]\s*)Restart-Computer\b/i },
  // Windows disk/registry/shadow-copy destruction the port never carried over.
  // Anchored to command position like format_disk, so `rg 'reg delete'` is not
  // blocked while Get-Disk / `reg query` / `vssadmin list shadows` stay allowed.
  // `reg delete` blocks a single-value delete (`... /v Cache`) on purpose -- the
  // operator asked for it wholesale; a registry write is not regenerable.
  { name: 'win_clear_disk', pattern: /(?:^|[;&|]\s*)clear-disk\b/i },
  { name: 'win_reg_delete', pattern: /(?:^|[;&|]\s*)reg(?:\.exe)?\s+delete\b/i },
  { name: 'win_vssadmin_delete', pattern: /(?:^|[;&|]\s*)vssadmin(?:\.exe)?\s+delete\b/i },
  // Anchored to a real format invocation: `format` in command position with a
  // drive letter or /fs: switch. A bare /\bformat\s/ also matched
  // `--output-format json`, so the guard blocked ruff and grep -- and a guard
  // that blocks routine tooling teaches people to work around it.
  { name: 'format_disk', pattern: /(?:^|[;&|]\s*)format\s+(?:\/\S+\s+)*[a-zA-Z]:/i },
  { name: 'diskpart', pattern: /(?:^|[;&|]\s*)diskpart\b/ },
  { name: 'shutdown_system', pattern: /(?:^|[;&|]\s*)(?:shutdown|reboot|halt)\b/ },
];

// ─── SECRET PATTERNS (exit 2) ─────────────────────────────────────────────
// Shapes that mean a literal credential was typed into the command text. Kept
// 1:1 with .claude/hooks/guard.py's SECRET_PATTERNS (ids and regexes), which is
// the reference: it blocked these commands while Kilo allowed them -- the last
// measured divergence of the guard review (2026-10-16). The JS form of each
// regex mirrors the dsh bridge's SECRET_PATTERNS
// (integrations/dsh-solo-plugins/.../solo-policy/src/rules.js) rather than
// importing it, because a hook has to stand alone with no dependencies.
//
// This is a SEPARATE list from BLOCK_PATTERNS on purpose: BLOCK_PATTERNS keeps
// its fixed 44-entry parity with guard.py's destructive set, and the secrets
// are counted on their own so that parity does not move. The scan runs on the
// raw command string; file contents stay secret-scan.js's territory (a warning).
const SECRET_PATTERNS = [
  { name: 'aws_access_key', pattern: /(?:AKIA|ASIA)[A-Z0-9]{16}/ },
  { name: 'aws_secret_key', pattern: /(?:aws|amazon).{0,20}(?:secret|key|token).{0,10}[:=]\s*["'][A-Za-z0-9\/+=]{20,}/i },
  { name: 'generic_api_key', pattern: /(?:api[_-]?key|apikey|secret|password)\s*[:=]\s*["'](?!\$)[^"']{8,}["']/i },
  { name: 'env_assignment_secret', pattern: /(?:api[_-]?key|secret|token|password|passwd|credential)[a-z_-]*\s*[:=]\s*(?![^\s"']*(?:x{3}|\*{3}))[^\s"'$][^\s"']{15,}/i },
  { name: 'private_key_pem', pattern: /-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----/ },
  { name: 'jwt_token', pattern: /eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/ },
  { name: 'github_token', pattern: /(?:gh[pousr]_|github[_-]?pat[_-]?|github[_-]?token[_-]?)[A-Za-z0-9_]{20,}/i },
  { name: 'google_api_key', pattern: /AIza[0-9A-Za-z_-]{35}/ },
  { name: 'slack_token', pattern: /xox[baprs]-[0-9A-Za-z-]{10,}/ },
  { name: 'stripe_key', pattern: /(?:sk|pk)_(?:test|live)_[0-9a-zA-Z]{24,}/ },
  { name: 'mongodb_uri', pattern: /mongodb(?:\+srv)?:\/\/[^:]+:[^@]+@/ },
  { name: 'postgres_uri', pattern: /postgres(?:ql)?:\/\/[^:]+:[^@]+@/ },
  { name: 'redis_uri', pattern: /redis:\/\/[^:]+:[^@]+@/ },
  { name: 'hardcoded_token', pattern: /(?:token|bearer)\s*[:=]\s*["'][A-Za-z0-9._\-+\/=]{20,}["']/i },
  { name: 'discord_webhook', pattern: /https:\/\/discord(?:app)?\.com\/api\/webhooks\/\d+\/[A-Za-z0-9_-]+/i },
  { name: 'basic_auth', pattern: /https?:\/\/[^:]+:[^@]+@/ },
  { name: 'anthropic_key', pattern: /sk-ant-[A-Za-z0-9\-_]{24,}/ },
  { name: 'openai_project_key', pattern: /sk-proj-[A-Za-z0-9\-_]{20,}/ },
  { name: 'npm_token', pattern: /npm_[A-Za-z0-9]{36}/ },
  { name: 'gitlab_pat', pattern: /glpat-[A-Za-z0-9\-_]{20,}/ },
  { name: 'digitalocean_token', pattern: /dop_v1_[A-Za-z0-9]{64}/ },
  { name: 'bearer_header', pattern: /Bearer\s+[A-Za-z0-9._\-+\/=]{20,}/i },
  { name: 'openai_legacy_key', pattern: /sk-[A-Za-z0-9]{20,}/ },
];

// ─── WARN PATTERNS ─────────────────────────────────────────────────────────
const WARN_PATTERNS = [
  { name: 'npm_global', pattern: /npm\s+install\s+-g/,
    message: 'npm install -g: global install — prefer local or npx' },
  { name: 'pip_solo', pattern: /pip\s+install\s+(?!-r|\.)/,
    message: 'pip install: consider adding to requirements.txt' },
  { name: 'curl_pipe_bash', pattern: /curl\s+.*\|\s*(?:ba)?sh/,
    message: 'curl | sh: piping to shell is unsafe. Review script first' },
  { name: 'eval_injection', pattern: /(?:eval|exec)\(.*\$/,
    message: 'eval/exec with variable input — code injection risk' },
  { name: 'debug_code', pattern: /console\.log\(|debugger;/,
    message: 'Debug code detected — remove before committing' },
];

// ─── PATH VALIDATION ───────────────────────────────────────────────────────
const PATH_WARN = [
  { name: 'tmp_volatile', pattern: /\b\/tmp\//,
    message: 'Writing to /tmp — data lost on reboot. Use persistent path.' },
  { name: 'tilde_expand', pattern: /\b~\//,
    message: 'Tilde ~ in script — may not expand as expected. Use $HOME.' },
  { name: 'deep_relative', pattern: /(?<!\w)\.\.\/\.\.\//,
    message: 'Deep relative path — fragile. Use absolute or project-relative.' },
];

// ─── MODE/PERMISSION VALIDATION ─────────────────────────────────────────────
const MODE_WARN = [
  { name: 'chmod_777', pattern: /chmod\s+.*777/,
    message: 'chmod 777 — world-writable. Use 755 or 644 instead.' },
  { name: 'chmod_plusx', pattern: /chmod\s+.*\+x\s+(?!.*\.sh)/,
    message: 'chmod +x on non-script file — verify intent.' },
];

// ─── SED/SYNTAX VALIDATION ─────────────────────────────────────────────────
const SED_WARN = [
  { name: 'piped_sed', pattern: /sed\s+.*\|.*sed/,
    message: 'Piped sed commands — fragile. Use single sed with -e.' },
  { name: 'sed_no_backup', pattern: /sed\s+-i\s+(?!.*\.bak)/,
    message: 'sed -i without .bak — no backup. Use sed -i.bak.' },
];

// ─── SEMANTIC VALIDATION ───────────────────────────────────────────────────
const SEMANTIC_WARN = [
  { name: 'kill_minus9', pattern: /kill\s+-9/,
    message: 'kill -9 is SIGKILL — no cleanup. Try kill -15 first.' },
  { name: 'docker_rm_force', pattern: /docker\s+rm\s+-f/,
    message: 'docker rm -f — force remove. May orphan resources.' },
  { name: 'npm_audit_force', pattern: /npm\s+audit\s+fix\s+--force/,
    message: 'npm audit fix --force — may break deps. Review first.' },
  { name: 'git_stash_drop', pattern: /git\s+stash\s+drop/,
    message: 'git stash drop — irreversible. Use git stash pop first.' },
];

// ─── PIPED DESTRUCTIVE ─────────────────────────────────────────────────────
const PIPE_DESTRUCTIVE = [
  { name: 'xargs_rm', pattern: /xargs\s+rm/,
    message: 'xargs rm — batch deletion. Review what xargs receives.' },
  { name: 'find_delete', pattern: /find\s+.*-delete/,
    message: 'find -delete — direct deletion. Use -print first to preview.' },
  { name: 'find_exec_rm', pattern: /find\s+.*-exec\s+rm/,
    message: 'find -exec rm — deletion via find. Use -print first to preview.' },
  { name: 'batch_branch_delete', pattern: /git\s+branch\s+.*\|.*xargs.*git\s+branch\s+-D/,
    message: 'Batch branch deletion — verify which branches will be deleted first.' },
  { name: 'kubectl_delete_all', pattern: /kubectl\s+delete\s+.*--all/,
    message: 'kubectl delete --all — deletes all resources in namespace.' },
];

// ─── SAFE ALTERNATIVES ─────────────────────────────────────────────────────
const SAFE_ALTERNATIVES = [
  { cmd: 'rm -rf', alt: 'mv <path> /tmp/<name>-backup first, then rm after verifying' },
  { cmd: 'rm ',    alt: 'Use mv to trash or add .bak suffix before deleting' },
  { cmd: 'git reset --hard', alt: 'Use git stash + git reset --soft to preserve changes' },
  { cmd: 'git push --force', alt: 'Use git push --force-with-lease to avoid overwriting' },
  { cmd: 'git branch -D', alt: 'Use git branch -d (lowercase) for merged branches only' },
  { cmd: 'docker rm -f', alt: 'Use docker stop first, then docker rm' },
  { cmd: 'kill -9', alt: 'Use kill -15 (SIGTERM) first for clean shutdown' },
];

// ─── Helpers ────────────────────────────────────────────────────────────────

// ─── Shell dialects (how a quoted payload ends) ─────────────────────────────
// Port of guard.py's DIALECTS. `escape` is the character that escapes the
// closing quote (null when the quote is literal -- POSIX single quotes never
// escape), `doubled` marks a doubled quote as the in-string quote (`""` in
// PowerShell and cmd) rather than the end of the payload, and `escapable` is
// the set of characters `escape` may escape (null for "any"). PowerShell's
// backtick and cmd's `^` escape the closing quote and nothing else, so their
// set holds only `"` -- decoding every escape rewrote `-Recurse`t-Force` into
// `-Recurset-Force` and let a wipe through. POSIX double quotes are the narrow
// case: the
// shell removes a backslash only before `$`, a backtick, `"`, `\` or a
// newline, so `\m` stays a literal backslash-m. Decoding it into `m` was the
// round-4 over-block that made `bash -c "r\m -rf /"` look like a wipe. Every
// shell/quote pair the wrapper pattern below can produce has an entry, so a
// lookup cannot fail open on a shape a caller forgot to add.
const POSIX_DOUBLE_ESCAPES = new Set(['$', '`', '"', '\\', '\n']);
// The Windows shells escape the closing quote with their own escape character
// (a backtick-quote in PowerShell, `^"` in cmd) and NOTHING else -- `t is a
// tab, a real word separator, so decoding it glues two flag words into one and
// hides the wipe from the patterns. Only the quote character may be consumed.
const WIN_QUOTE_ESCAPE = new Set(['"']);
const DIALECTS = {
  'bash:single': { escape: null, doubled: false, escapable: null },
  'sh:single': { escape: null, doubled: false, escapable: null },
  'bash:double': { escape: '\\', doubled: false, escapable: POSIX_DOUBLE_ESCAPES },
  'sh:double': { escape: '\\', doubled: false, escapable: POSIX_DOUBLE_ESCAPES },
  'cmd:single': { escape: null, doubled: false, escapable: null },
  'cmd:double': { escape: '^', doubled: true, escapable: WIN_QUOTE_ESCAPE },
  'powershell:single': { escape: null, doubled: true, escapable: null },
  'powershell:double': { escape: '`', doubled: true, escapable: WIN_QUOTE_ESCAPE },
  'pwsh:single': { escape: null, doubled: true, escapable: null },
  'pwsh:double': { escape: '`', doubled: true, escapable: WIN_QUOTE_ESCAPE },
};

// The wrappers whose payload is executed, at command position. `bq` is the
// opening quote when there is one; a `-c` payload may also be UNQUOTED
// (`sh -c rm\ -rf\ /`), in which case the group matches empty and the payload
// is the argument run. The `^` anchor replaces Python's match(text, index) --
// this is matched against text.slice(index).
const WRAPPER_RE = /^(?:(?:(?:sudo\s+)?(?:env|nohup|nice|command|time)\s+)*(?:sudo\s+)?(?<shell>bash|sh)\s+-c\s+(?<bq>['"]|(?=\S))|(?<win>powershell|pwsh|cmd)(?:\.exe)?\s+(?:[-/]\S+\s+)*(?<wq>['"]))/i;

// Port of guard.py's dialect_of().
function dialectOf(shell, quote) {
  return DIALECTS[`${shell.toLowerCase()}:${quote === "'" ? 'single' : 'double'}`];
}

// Port of guard.py's unescape_payload(): remove one level of a dialect's
// escapes (`\X` -> `X`), which is what makes a nested wrapper written with
// escaped quotes match, and what the shell itself does to an unquoted `-c`
// argument before it runs the words. Only a pair the shell really treats as
// an escape is consumed (`escapable`, or null for "any"): a POSIX double quote
// escapes just `$`, a backtick, `"`, `\` and a newline, so `\m` is left alone
// while `\"` still decodes for the nested-wrapper case.
function unescapePayload(payload, escape, escapable) {
  if (!escape) return payload;
  let out = '';
  let index = 0;
  while (index < payload.length) {
    if (
      payload[index] === escape &&
      index + 1 < payload.length &&
      (!escapable || escapable.has(payload[index + 1]))
    ) {
      out += payload[index + 1];
      index += 2;
      continue;
    }
    out += payload[index];
    index += 1;
  }
  return out;
}

// Port of guard.py's find_closing_quote(): [end, escapedAt], or null when no
// closing quote can be located for that dialect (the caller must then leave the
// text alone rather than guess where the payload ends).
function findClosingQuote(text, start, quote, escape, doubled) {
  let index = start;
  let escapedAt = -1;
  while (index < text.length) {
    const char = text[index];
    if (
      doubled &&
      char === quote &&
      index + 1 < text.length &&
      text[index + 1] === quote
    ) {
      index += 2;
      continue;
    }
    if (char === quote) {
      if (escape === null) return [index, escapedAt];
      let run = 0;
      let cursor = index - 1;
      while (cursor >= start && text[cursor] === escape) {
        run += 1;
        cursor -= 1;
      }
      if (run % 2 === 0) return [index, escapedAt];
      if (escapedAt < 0) escapedAt = index;
    }
    index += 1;
  }
  return null;
}

// Port of guard.py's unwrap_command_wrappers(): a quoted payload after a
// `bash|sh -c` or a Windows `powershell|pwsh|cmd` wrapper is executed, so its
// separators are real command separators. The wrapper is unwrapped only where
// the quote state and the command position both agree it is executable, so
// search data that merely NAMES one stays untouched. Round 10 reads the
// payload's end with the shell's own lexicon, unescapes the payload before
// scanning it, unescapes an unquoted `-c` argument, and bails PER WRAPPER
// instead of per command -- the old all-or-nothing bail stripped the
// unwrapping from a clean wrapper later on the same line. Any ambiguity still
// leaves the text exactly as it came in.
function unwrapCommandWrappers(command) {
  if (!command || typeof command !== 'string') return '';

  function rewrite(text) {
    let out = '';
    let index = 0;
    let quote = null;
    let commandPosition = true;

    while (index < text.length) {
      if (quote === null && commandPosition) {
        const match = text.slice(index).match(WRAPPER_RE);
        if (match) {
          const shell = (match.groups.shell || match.groups.win).toLowerCase();
          const wrapperQuote = match.groups.bq || match.groups.wq;
          const payloadStart = index + match[0].length;

          if (wrapperQuote) {
            const dialect = dialectOf(shell, wrapperQuote);
            const found = findClosingQuote(
              text,
              payloadStart,
              wrapperQuote,
              dialect.escape,
              dialect.doubled
            );
            if (found) {
              const payloadEnd = found[0];
              const escapedAt = found[1];
              const escapedSeparator =
                escapedAt >= 0 && /[;&|\n]/.test(text.slice(escapedAt, payloadEnd));
              // A closing quote another backslash follows is the `'\''`
              // concatenation: the payload text is only half of what the shell
              // sees, so do not read it.
              const trailingEscape =
                payloadEnd + 1 < text.length && text[payloadEnd + 1] === '\\';
              // An escaped quote the payload closes right after is the third
              // disagreement about where the payload ends: read as an escape
              // the payload runs on, read as the delimiter it ends at the
              // escaped quote. Leave the text alone so the raw pair decides --
              // `bash -c "echo \""; rg "echo; rm -rf /" .` prints a
              // quote and searches, it does not wipe.
            const escapedClosesPayload =
              escapedAt >= 0 && payloadEnd === escapedAt + 1;
            if (!escapedSeparator && !trailingEscape && !escapedClosesPayload) {
                let body = unescapePayload(
                  text.slice(payloadStart, payloadEnd),
                  dialect.escape,
                  dialect.escapable
                );
                // A single-quoted shell has no escape character, so its
                // in-string quote is the doubled pair. findClosingQuote only
                // SKIPS that pair, so it still reaches the patterns as two
                // balanced empty spans and the text between them reads in
                // command position. Halve it to the one quote the shell sees.
                // The double-quoted dialects keep `""` for their payload-end
                // walk: rewriting it there could let a leading `""` mask a
                // separator.
                if (dialect.doubled && dialect.escape === null) {
                  body = body.split(wrapperQuote + wrapperQuote).join(wrapperQuote);
                }
                out += rewrite(body);
                index = payloadEnd + 1;
                commandPosition = false;
                continue;
              }
            }
          } else {
            // An unquoted `-c` argument: the shell removes the backslash
            // escapes before the words reach `sh`, so `sh -c rm\ -rf\ /` runs
            // `rm -rf /`. The argument run ends at the next separator.
            let runEnd = payloadStart;
            while (runEnd < text.length && !';&|\n'.includes(text[runEnd])) runEnd += 1;
            // An unquoted `-c` argument escapes ANY next character, so its
            // escapable set is null.
            out += rewrite(
              unescapePayload(text.slice(payloadStart, runEnd), '\\', null)
            );
            index = runEnd;
            commandPosition = false;
            continue;
          }

          // Per-wrapper bail: keep this wrapper verbatim and carry on after its
          // opening quote, so a clean wrapper later on the line is still
          // unwrapped. Resuming with no quote state is what lets the scan reach
          // it; the text itself is left as it came in.
          out += text.slice(index, payloadStart);
          index = payloadStart;
          commandPosition = false;
          continue;
        }
      }

      const char = text[index];
      out += char;

      if (quote === null) {
        // A quote behind a backslash is a literal quote in POSIX, so it must
        // not open a span: `bash -c "echo C:\"; bash ...` would otherwise
        // swallow the rest of the line and hide the wrapper.
        if (
          (char === "'" || char === '"') &&
          (index === 0 || text[index - 1] !== '\\')
        ) {
          quote = char;
        } else if (';&|\n'.includes(char)) {
          commandPosition = true;
        } else if (!/\s/.test(char)) {
          commandPosition = false;
        }
      } else if (char === quote) {
        if (quote === "'") {
          quote = null;
        } else {
          let run = 0;
          let cursor = index - 1;
          while (cursor >= 0 && text[cursor] === '\\') {
            run += 1;
            cursor -= 1;
          }
          if (run % 2 === 0) quote = null;
        }
      }

      index += 1;
    }

    return out;
  }

  return rewrite(command);
}

// Port of guard.py's normalize_command(): both engines must unwrap the same
// wrappers or their verdicts diverge (Python normalises `sudo git push …`, so
// without this Kilo allowed what Claude blocked). Rules match Python exactly:
// collapse whitespace, strip a leading sudo / `env KEY=VAL…` / `bash|sh -c`,
// strip a fully surrounding quote pair, strip a leading path.
function normalizeCommand(command) {
  if (!command || typeof command !== 'string') return '';
  let cmd = command.replace(/\s+/g, ' ').trim();
  cmd = unwrapCommandWrappers(cmd);
  for (;;) {
    const prev = cmd;
    cmd = cmd.replace(/^sudo\s+/, '');
    cmd = cmd.replace(/^env(?:\s+\w+=[^\s]*)+\s+/, '');
    cmd = cmd.replace(/^(?:bash|sh)\s+-c\s+/, '');
    cmd = cmd.replace(/^(?:nohup|nice|command|time)\s+/, '');
    cmd = cmd.replace(/^(['"])(.*)\1$/, '$2');
    if (cmd === prev) break;
  }
  cmd = cmd.replace(/^\/?(?:[\w.-]+\/)+([\w.-]+)/, '$1');
  return cmd;
}

// Blank quoted spans that contain a shell separator. `rg 'echo x; diskpart' .` was
// blocked because the `;` inside the quotes read as a separator, putting `diskpart`
// in command position. Only spans containing a separator are blanked, so a quoted
// root keeps its protection (`rm -rf '/'` must stay blocked) and a wrapper's quoted
// command is left alone (unwrapCommandWrappers unwraps it before this runs only
// when it is at executable command position; quoted search data stays untouched).
function maskSeparatorQuotes(command) {
  if (!command || typeof command !== 'string') return '';
  let out = '';
  let index = 0;
  while (index < command.length) {
    const char = command[index];
    if (char !== "'" && char !== '"') {
      out += char;
      index += 1;
      continue;
    }
    let end = index + 1;
    while (end < command.length) {
      if (command[end] !== char) {
        end += 1;
        continue;
      }
      let run = 0;
      let cursor = end - 1;
      while (cursor >= 0 && command[cursor] === '\\') {
        run += 1;
        cursor -= 1;
      }
      if (run % 2 === 0) break;
      if (end + 1 < command.length && command[end + 1] === char) {
        end += 1;
        continue;
      }
      break;
    }
    if (end >= command.length) {
      out += command.slice(index);
      break;
    }
    const span = command.slice(index, end + 1);
    out += /[;&|]/.test(span) ? "''" : span;
    index = end + 1;
  }
  return out;
}

function checkPatterns(patterns, command) {
  for (const { name, pattern, message } of patterns) {
    try {
      if (pattern.test(command)) {
        return { name, message: message || name };
      }
    } catch {}
  }
  return null;
}

// ─── Text a command runs without naming it ──────────────────────────────────
// Ports of guard.py's _EVAL_ARGUMENT / _PIPE_INTO_SHELL / quoted_spans() /
// executable_payloads(): `eval` runs its argument -- quoted or not, since it
// concatenates its arguments, so an unquoted run executes too -- and a pipeline
// stage that IS a shell runs the text the input side PRINTS, quoted or not. The
// shell may be named by a path (`/bin/bash`), not only a bare identifier. Both
// read the MASKED text, so quoted search data stays data
// (`rg "echo 'rm -rf /' | bash" .` never fires).
const EVAL_ARGUMENT = /(?:^|[;&|\n]\s*)eval\s+(?<q>['"]?)/gi;
const PIPE_INTO_SHELL =
  /(?:^|[;&\n])(?<input>[^;&\n]*?)\|\s*(?:(?:sudo|env)\s+(?:\w+=\S*\s+)*)?(?:\/?(?:[\w.-]+\/)+)?(?:bash|sh|zsh|ksh|pwsh|powershell)(?:\.exe)?(?![\w.-])/gi;

// Bound on the recursion into embedded payloads, so a pathological nesting
// cannot walk forever.
const MAX_PAYLOAD_DEPTH = 4;

// Port of guard.py's quoted_spans(): the contents of each quoted span, with the
// quote characters removed.
function quotedSpans(text) {
  const spans = [];
  let index = 0;
  while (index < text.length) {
    const char = text[index];
    if (char !== "'" && char !== '"') {
      index += 1;
      continue;
    }
    const end = text.indexOf(char, index + 1);
    if (end === -1) break;
    spans.push(text.slice(index + 1, end));
    index = end + 1;
  }
  return spans;
}

// Port of guard.py's executable_payloads().
function executablePayloads(masked) {
  const payloads = [];
  for (const match of masked.matchAll(EVAL_ARGUMENT)) {
    const quote = match.groups.q;
    const start = match.index + match[0].length;
    if (quote) {
      const dialect = dialectOf('bash', quote);
      const found = findClosingQuote(
        masked,
        start,
        quote,
        dialect.escape,
        dialect.doubled
      );
      if (found) {
        payloads.push(
          unescapePayload(
            masked.slice(start, found[0]),
            dialect.escape,
            dialect.escapable
          )
        );
      }
    } else {
      // An unquoted argument run: `eval` concatenates its arguments, so the run
      // up to the next separator is the text it executes. The shell strips a
      // backslash before any character, so the unescape set is null ("any"),
      // exactly as for an unquoted `-c` payload.
      let runEnd = start;
      while (runEnd < masked.length && !';|&\n'.includes(masked[runEnd])) runEnd += 1;
      payloads.push(unescapePayload(masked.slice(start, runEnd), '\\', null));
    }
  }
  for (const match of masked.matchAll(PIPE_INTO_SHELL)) {
    const inputSide = match.groups.input;
    payloads.push(...quotedSpans(inputSide));
    // The shell runs whatever the piped text is, quoted or not: with no quotes
    // anywhere (`echo rm -rf / | bash`) the argument run after the printing
    // command is the script, so the words contribute a payload too.
    const words = inputSide.split(/\s+/).filter(Boolean);
    if (words.length > 1) payloads.push(words.slice(1).join(' '));
  }
  return payloads;
}

// Port of guard.py's find_destructive(): the same two candidates, plus the
// recursion into the text a command runs without naming it, bounded by
// MAX_PAYLOAD_DEPTH. Both engines must reach the same verdict on the same
// command, so this has to stay a line-for-line mirror.
function findDestructive(command, depth = 0) {
  const unwrapped = unwrapCommandWrappers(command);
  const normalized = normalizeCommand(unwrapped);
  for (const raw of [unwrapped, normalized]) {
    if (!raw) continue;
    const candidate = maskSeparatorQuotes(raw);
    const hit = checkPatterns(BLOCK_PATTERNS, candidate);
    if (hit) return hit;
    if (depth < MAX_PAYLOAD_DEPTH) {
      for (const payload of executablePayloads(candidate)) {
        if (!payload) continue;
        const nested = findDestructive(payload, depth + 1);
        if (nested) return nested;
      }
    }
  }
  return null;
}

function checkAlternatives(command) {
  return SAFE_ALTERNATIVES
    .filter(({ cmd }) => command.includes(cmd))
    .map(({ cmd, alt }) => `  ${cmd} → ${alt}`);
}

function isSensitivePath(filePath) {
  const sensitive = [/\.env(?:\.\w+)?$/i, /credentials\./i, /secrets?\./i, /\.pem$/i, /\.key$/i, /id_rsa/];
  return sensitive.some(p => p.test(filePath));
}

// ─── Main ───────────────────────────────────────────────────────────────────
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
    process.stderr.write('[GateGuard] Input truncated (>1MB) — allowing, cannot inspect\n');
    process.exit(0);
  }

  let input;
  try { input = JSON.parse(raw); } catch { process.exit(0); }

  // Cross-platform shell tool names
  const SHELL_TOOLS = new Set([
    'Bash', 'bash',           // Claude Code, Kilo
    'run_command', 'execute', // Gemini/Antigravity
    'runCommand', 'terminal', // Copilot
    'execute_command', 'shell', 'RunCommand', // Cursor, generic
  ]);

  const toolName = input.tool_name || '';
  const toolInput = input.tool_input || {};

  if (SHELL_TOOLS.has(toolName)) {
    const command = toolInput.command || toolInput.CommandLine || toolInput.cmd || toolInput.commandLine || '';

    // 1. BLOCK destructive patterns (raw, then unwrapped -- mirrors guard.py's
    //    find_destructive: unwrap and normalise, mask separator-bearing quoted
    //    spans so a search pattern or a commit message that merely NAMES a
    //    command is not read as one, then recurse into the text a command runs
    //    without naming it).
    const blocked = findDestructive(command);
    if (blocked) {
      const alts = checkAlternatives(command);
      process.stderr.write(
        `\n[GateGuard] BLOCKED: ${blocked.message}\n` +
        `  Command: ${command.substring(0, 200)}\n` +
        (alts.length > 0 ? `  Safer alternatives:\n${alts.join('\n')}\n` : '') +
        '\n'
      );
      process.exit(2);
    }

    // 1b. BLOCK a secret typed into the command text. Mirrors guard.py's
    //     find_secret() on the raw command: a key pasted onto a command line is
    //     the same leak as one written to a file, and Kilo used not to scan
    //     commands at all. File contents stay secret-scan.js's job (a warning).
    const secret = checkPatterns(SECRET_PATTERNS, command);
    if (secret) {
      process.stderr.write(
        `\n[GateGuard] BLOCKED: possible secret '${secret.name}' in command. ` +
        `Use environment variables instead.\n` +
        `  Command: ${command.substring(0, 200)}\n\n`
      );
      process.exit(2);
    }

    // 2-7. WARN categories
    const categories = [
      { label: 'WARNING', patterns: WARN_PATTERNS },
      { label: 'PATH', patterns: PATH_WARN },
      { label: 'MODE', patterns: MODE_WARN },
      { label: 'SED', patterns: SED_WARN },
      { label: 'SEMANTIC', patterns: SEMANTIC_WARN },
      { label: 'DESTRUCTIVE_PIPE', patterns: PIPE_DESTRUCTIVE },
    ];

    for (const { label, patterns } of categories) {
      const hit = checkPatterns(patterns, command);
      if (hit) {
        process.stderr.write(`[GateGuard] ${label}: ${hit.message}\n`);
      }
    }

    // Safe alternatives for non-blocking destructive commands
    const alts = checkAlternatives(command);
    for (const a of alts) {
      process.stderr.write(`[GateGuard] SAFE_ALT: ${a}\n`);
    }
  }

  // Sensitive file writes
  const filePath = toolInput.file_path || toolInput.path || toolInput.TargetFile || toolInput.AbsolutePath || toolInput.DirectoryPath || toolInput.uri || '';
  if (filePath && isSensitivePath(filePath)) {
    process.stderr.write(
      `[GateGuard] WARNING: Writing to sensitive file: ${filePath}\n` +
      `  Ensure no secrets are being committed.\n`
    );
  }

  process.exit(0);
});
