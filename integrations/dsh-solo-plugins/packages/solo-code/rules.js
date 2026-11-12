/**
 * Pure policy classification for the Solo-Code DSH plugin. Kept free of Cordis
 * imports so a deployment can test its rules without booting a harness.
 *
 * DESTRUCTIVE_PATTERNS mirrors the named pattern list in the Solo-Code guard
 * (`.claude/hooks/guard.py` in g-harness). Ids are the guard's ids, so a diff
 * between the two lists stays mechanical.
 */

const PRIVILEGE_COMMAND = /(?:^|[;&|\n])\s*(?:sudo\b|runas\b|chmod\s+777\b)/i
const DEPENDENCY_COMMAND = /(?:^|[;&|\n])\s*(?:npm|pnpm|yarn|pip|pip3|uv|cargo)\s+(?:install|add|remove|uninstall)\b/i
const NETWORK_COMMAND = /(?:^|[;&|\n])\s*(?:curl|wget|Invoke-WebRequest|Invoke-RestMethod)\b/i
const GIT_PUBLISH_COMMAND = /(?:^|[;&|\n])\s*git\s+(?:push|commit)\b/i
const PROTECTED_PATH = /(?:^|[\\/])(?:\.env(?:\.[^\\/]+)?|[^\\/]+\.(?:pem|key|p12|pfx))$/i

/** @typedef {'allow'|'ask'|'deny'} PolicyAction */

/**
 * Named destructive patterns. Anything matched here needs explicit approval,
 * because the Solo-Code guard would refuse it too.
 * @type {ReadonlyArray<{id: string, re: RegExp}>}
 */
export const DESTRUCTIVE_PATTERNS = Object.freeze([
  { id: 'rm_root', re: /rm\s+-[^\n]*(?:\s|^)\/(?:\s|$|\*)/ },
  { id: 'rm_home', re: /rm\s+-[^\n]*~/ },
  { id: 'rm_wildcard', re: /rm\s+-[^\n]*\*/ },
  { id: 'rm_relative_wildcard', re: /rm\s+-[^\n]*\.\/(?:\s|$|\*)/ },
  { id: 'rm_system_dir', re: /rm\s+-[^\n]*\/(?:etc|usr|var|bin|lib|boot|sbin|opt|sys|proc|dev)(?:\/|\s|$)/ },
  { id: 'rm_temp_linux', re: /rm\s+-[^\n]*\/tmp\// },
  { id: 'rm_temp_win', re: /rm\s+-[^\n]*\$?(?:env:)?TEMP\b/i },
  { id: 'rm_after_other_target', re: /rm\b[^\n]*;_?[^\n]*\srm\b/ },
  { id: 'force_push_main', re: /git\s+push\b[^&|;\n]*(?:--force|-f)\s+[^&|;\n]*(?:main|master)/ },
  { id: 'force_push_any', re: /git\s+push\b[^&|;\n]*--force\b(?!-)/ },
  { id: 'force_push_refspec', re: /git\s+push\b[^&|;\n]*\s\+[^\s&|;]+/ },
  { id: 'force_push_delete_main', re: /git\s+push\b[^&|;\n]*(?:--delete\s+|-d\s+|:)(?:[^\s&|;]+\s+)?(?:refs\/heads\/)?(?:main|master)(?![\w-])/ },
  { id: 'git_reset_hard', re: /git\s+reset\b[^&|;\n]*--ha(?:rd?)?/ },
  { id: 'git_clean_force', re: /git\s+clean\s+-f/ },
  { id: 'drop_table', re: /DROP\s+(?:TABLE|DATABASE)/i },
  { id: 'truncate_table', re: /TRUNCATE\s+TABLE/i },
  { id: 'dd_device_write', re: /dd\s+.*of=\/dev\// },
  { id: 'dd_raw', re: /dd\s+if=/ },
  { id: 'dev_write', re: />\s*\/dev\/(?:sd|hd|nvme|vd)/ },
  { id: 'mkfs', re: /mkfs\./ },
  { id: 'shred', re: /shred\s+/ },
  { id: 'chmod_chown_system', re: /(?:chmod|chown)\s+-R\s+(?:[^/\s]+\s+)*\/(?:etc|usr|var|bin|lib|boot|sbin|opt|root|sys|proc|dev)(?:\/|\s|$)/ },
  { id: 'win_del_force', re: /del\s+/i },
  { id: 'win_rd_recursive', re: /(?:rd|rmdir)\s+(?:.*\s)?\/s\b/i },
  { id: 'win_remove_recursive', re: /Remove-Item\s+.*-Recurse.*-Force/i },
  { id: 'win_format_volume', re: /(?:^|[;&|]\s*)Format-Volume\b/i },
  { id: 'win_clear_disk', re: /(?:^|[;&|]\s*)clear-disk\b/i },
  { id: 'win_reg_delete', re: /(?:^|[;&|]\s*)reg(?:\.exe)?\s+delete\b/i },
  { id: 'win_vssadmin_delete', re: /vssadmin\s+delete\b/i },
  { id: 'win_stop_computer', re: /(?:^|[;&|]\s*)Stop-Computer\b/i },
  { id: 'win_restart_computer', re: /(?:^|[;&|]\s*)Restart-Computer\b/i },
  { id: 'format_disk', re: /(?:^|[;&|]\s*)format\s+(?:\/\S+\s+)*[a-zA-Z]:/i },
  { id: 'diskpart', re: /(?:^|[;&|]\s*)diskpart\b/i },
  { id: 'shutdown_system', re: /(?:^|[;&|]\s*)(?:shutdown|reboot|halt)\b/i },
  { id: 'curl_pipe_shell', re: /(?:^|[;&|]\s*)(?:curl|wget)\s+[^|&;\n]*\|\s*(?:(?:sudo|env)\s+)?(?:(?:ba|z|k)?sh\b|python[0-9.]*\b|perl\b|ruby\b|node\b|pwsh\b|powershell\b|iex\b)/i },
  { id: 'powershell_download_pipe_iex', re: /(?:^|[;&|]\s*)(?:irm|iwr|Invoke-WebRequest|Invoke-RestMethod)\b[^|&;\n]*\|\s*(?:iex|Invoke-Expression)\b/i },
])

/** Tools whose arguments name a filesystem path the agent would change. */
export const WRITE_TOOLS = Object.freeze(new Set([
  'write',
  'edit',
  'apply_patch',
  'str_replace',
  'str_replace_editor',
  'create',
  'delete',
  'move',
]))

/** Tools that run a shell command or write raw terminal input. */
export const SHELL_TOOLS = Object.freeze(new Set([
  'pwsh',
  'bash',
  'shell',
  'terminal_send',
]))

/** Tool name for a filesystem path argument, when the call carries one. */
export function argumentPath(args) {
  if (args === null || typeof args !== 'object') return undefined
  for (const key of ['file_path', 'path', 'destination']) {
    if (typeof args[key] === 'string') return args[key]
  }
  return undefined
}

/** Shell text of a call, when the call carries one. */
export function shellText(args) {
  if (args === null || typeof args !== 'object') return undefined
  for (const key of ['command', 'script', 'text']) {
    if (typeof args[key] === 'string') return args[key]
  }
  return undefined
}

/** True when the path names a credential file the agent must not write. */
export function isProtectedPath(path) {
  return typeof path === 'string' && PROTECTED_PATH.test(path)
}

/**
 * Classify a shell command under the balanced Solo-Code policy.
 * @param {string} command
 * @param {{askOnDependencyChanges?: boolean, askOnNetwork?: boolean, askOnGitPublish?: boolean}} options
 * @returns {{action: PolicyAction, reason?: string}}
 */
export function classifyShellCommand(command, options = {}) {
  const destructive = DESTRUCTIVE_PATTERNS.find((pattern) => pattern.re.test(command))
  if (destructive !== undefined) {
    return { action: 'ask', reason: `destructive command requires approval (${destructive.id})` }
  }
  if (PRIVILEGE_COMMAND.test(command)) return { action: 'ask', reason: 'privileged command requires approval' }
  if (options.askOnDependencyChanges !== false && DEPENDENCY_COMMAND.test(command)) {
    return { action: 'ask', reason: 'dependency change requires approval' }
  }
  if (options.askOnNetwork !== false && NETWORK_COMMAND.test(command)) {
    return { action: 'ask', reason: 'network command requires approval' }
  }
  if (options.askOnGitPublish !== false && GIT_PUBLISH_COMMAND.test(command)) {
    return { action: 'ask', reason: 'git publication requires approval' }
  }
  return { action: 'allow' }
}

/**
 * Classify a model-facing DSH tool invocation.
 * @param {string} toolName
 * @param {unknown} args
 * @param {Record<string, unknown>} options
 * @returns {{action: PolicyAction, reason?: string}}
 */
export function classifyToolCall(toolName, args, options = {}) {
  const path = argumentPath(args)
  if (options.denyCredentialWrites !== false && isProtectedPath(path)) {
    return { action: 'deny', reason: 'credential file cannot be changed by an agent' }
  }
  if (WRITE_TOOLS.has(toolName)) {
    if (options.askOnWrites === true) return { action: 'ask', reason: 'file change requires approval' }
    return { action: 'allow' }
  }
  if (SHELL_TOOLS.has(toolName)) {
    const command = shellText(args)
    if (command !== undefined) return classifyShellCommand(command, options)
    return { action: 'allow' }
  }
  if (/^(?:git_commit|git_push)$/i.test(toolName) && options.askOnGitPublish !== false) {
    return { action: 'ask', reason: 'git publication requires approval' }
  }
  if (/^mcp__solo__(?:delegate|route)$/.test(toolName) && options.askOnDelegation !== false) {
    const dryRun = args !== null && typeof args === 'object' && args.dry_run === true
    if (!dryRun) return { action: 'ask', reason: 'a delegated worker can modify files; approve the brief' }
  }
  return { action: 'allow' }
}
