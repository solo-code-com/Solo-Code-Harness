/**
 * Solo-Code operating discipline for DeepSeek Harness.
 *
 * One Host plugin mounted at the profile root delivers:
 * - an approval policy on the `tools/pre-execute` waterfall seam,
 * - a fail-closed write gate that honours `executor-mode`,
 * - four runtime guidance skills.
 *
 * The DSH kernel is untouched: every contribution goes through a documented
 * seam and is registered as a Cordis effect, so disposing the fiber removes it.
 */

import { readFile } from 'node:fs/promises'
import { dirname, isAbsolute, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { classifyToolCall, WRITE_TOOLS } from './rules.js'
import { createSkillPackProvider, loadPack } from './skills.js'

const PACKAGE_ROOT = dirname(fileURLToPath(import.meta.url))

export { classifyShellCommand, classifyToolCall, isProtectedPath, DESTRUCTIVE_PATTERNS } from './rules.js'

export const name = 'solo-code'
export const inject = ['tools']

const OFF_VALUES = new Set(['off', '0', 'disabled', 'false', 'no'])

/** Skills contributed by this plugin; names stay kebab-case. */
export const SKILLS = Object.freeze([
  {
    name: 'solo-plan',
    description: 'Plan a multi-file change before editing anything.',
    whenToUse: 'A request spans more than one file, or the approach is still unsettled.',
    content: [
      '1. Name the scope: files to touch, files to leave alone, and the observable success check.',
      '2. List risks and the tests that would catch them.',
      '3. Write numbered steps as action -> verify.',
      '4. Get approval for anything destructive or outside the stated scope, then execute.',
    ].join('\n'),
  },
  {
    name: 'solo-debug',
    description: 'Investigate a failure with competing hypotheses instead of a guess.',
    whenToUse: 'A test, build, or runtime behaviour fails and the cause is not yet proven.',
    content: [
      '1. State at least two plausible causes.',
      '2. For each cause, run one command whose output would separate them, and keep the output.',
      '3. Fix the cause the evidence supports with the smallest change.',
      '4. Re-run the failing check and report the command with its exit code.',
    ].join('\n'),
  },
  {
    name: 'solo-review',
    description: 'Review a diff for correctness and security without editing files.',
    whenToUse: 'A diff, patch, or pull request needs review before it is accepted.',
    content: [
      '1. Read the diff, then the surrounding code it depends on.',
      '2. Report findings by severity with file and line evidence; omit style nits.',
      '3. Separate pre-existing defects from regressions introduced by the diff.',
      '4. Do not modify files during a review; state what you verified and how.',
    ].join('\n'),
  },
  {
    name: 'solo-verify',
    description: 'Verify a change with focused checks and report evidence, not confidence.',
    whenToUse: 'A change is claimed complete and needs its checks run.',
    content: [
      '1. Run the narrowest test that covers the change first.',
      '2. Then run the project gates the change can break.',
      '3. Report exact commands, exit codes, and remaining risk.',
      '4. Never claim a check passed that was not run in this turn.',
    ].join('\n'),
  },
])

/** Read the flag from config; YAML booleans already arrive as booleans. */
function flag(value, fallback) {
  if (typeof value === 'boolean') return value
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (normalized === 'true') return true
    if (normalized === 'false') return false
  }
  return fallback
}

/** Read the string from config. */
function text(value, fallback) {
  return typeof value === 'string' && value.length > 0 ? value : fallback
}

/** Normalize the row config once per activation. */
export function normalizeOptions(config = {}) {
  const source = config !== null && typeof config === 'object' ? config : {}
  return {
    writeGate: flag(source.writeGate, false),
    executorModeFile: text(source.executorModeFile, '.solocode/executor-mode'),
    askOnWrites: flag(source.askOnWrites, false),
    askOnDependencyChanges: flag(source.askOnDependencyChanges, true),
    askOnNetwork: flag(source.askOnNetwork, true),
    askOnGitPublish: flag(source.askOnGitPublish, true),
    askOnDelegation: flag(source.askOnDelegation, true),
    denyCredentialWrites: flag(source.denyCredentialWrites, true),
    skillPack: text(source.skillPack, 'full') === 'core' ? 'core' : 'full',
    includeCommands: flag(source.includeCommands, true),
    includeAgents: flag(source.includeAgents, true),
  }
}

/**
 * Resolve the executor-mode state. A missing or unreadable state file means
 * ON: the gate fails closed, matching Solo-Code's default-on rule.
 * @returns {Promise<boolean>}
 */
export async function executorModeOn(file, cwd = process.cwd()) {
  const target = isAbsolute(file) ? file : resolve(cwd, file)
  try {
    const content = await readFile(target, 'utf-8')
    return !OFF_VALUES.has(content.trim().toLowerCase())
  } catch {
    return true
  }
}

/** @returns {Promise<{kind: 'deny', reason: string} | {kind: 'ask', reason: string, displayReason: {en: string, vi: string}} | undefined>} */
async function decide(exec, options) {
  const policy = classifyToolCall(exec.name, exec.arguments, options)
  if (policy.action === 'deny') return { kind: 'deny', reason: policy.reason }
  if (policy.action === 'ask') {
    return {
      kind: 'ask',
      reason: policy.reason,
      displayReason: { en: policy.reason, vi: 'Solo-Code cần bạn duyệt thao tác này.' },
    }
  }
  if (!options.writeGate || !WRITE_TOOLS.has(exec.name)) return undefined
  if (!(await executorModeOn(options.executorModeFile))) return undefined
  return {
    kind: 'deny',
    reason: 'executor mode is on: delegate the write instead of editing directly',
  }
}

/**
 * Mount the policy handler and the guidance skills.
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {Record<string, unknown>} config
 */
export function apply(ctx, config) {
  const options = normalizeOptions(config)

  ctx.on('tools/pre-execute', async (exec, next) => {
    const decision = await decide(exec, options)
    return decision ?? next()
  })

  if (typeof ctx.get !== 'function') return
  const skills = ctx.get('skills')
  if (skills === undefined) return
  for (const skill of SKILLS) {
    ctx.effect(() => skills.register({ ...skill, source: 'bundled' }))
  }
  const pack = loadPack(PACKAGE_ROOT, {
    skills: options.skillPack !== 'core',
    commands: options.includeCommands,
    agents: options.includeAgents,
  })
  if (pack.length > 0 && typeof skills.registerProvider === 'function') {
    ctx.effect(() => skills.registerProvider(() => createSkillPackProvider(pack)))
  }
}
