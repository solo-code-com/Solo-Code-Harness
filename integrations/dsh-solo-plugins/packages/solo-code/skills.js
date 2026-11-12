/**
 * Skill pack loader for the Solo-Code plugin.
 *
 * The Solo-Code harness ships 53 skills plus 15 commands and 15 agents as
 * markdown prompt templates. DSH has no file-based agent or command format, so
 * this module projects all three onto the skills seam:
 * - skills stay model-invocable and keep their directory as the resource base;
 * - commands and agents become user-invocable prompt templates named
 *   `cmd-<name>` and `agent-<name>`.
 *
 * A provider keeps the catalog fast: `list` returns summaries built at
 * activation, `get` returns the body already read from disk.
 */

import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

/** Parse the leading `---` block of a markdown file. */
export function parseFrontmatter(text) {
  const normalized = text.replace(/^\uFEFF/, '')
  if (!normalized.startsWith('---')) return { fields: {}, body: normalized }
  const end = normalized.indexOf('\n---', 3)
  if (end === -1) return { fields: {}, body: normalized }
  const raw = normalized.slice(normalized.indexOf('\n') + 1, end)
  const body = normalized.slice(end + 4).replace(/^[\r\n]+/, '')
  const fields = {}
  for (const line of raw.split(/\r?\n/)) {
    const match = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/)
    if (!match) continue
    let value = match[2].trim()
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }
    fields[match[1]] = value
  }
  return { fields, body }
}

function readMarkdown(file) {
  const parsed = parseFrontmatter(readFileSync(file, 'utf-8'))
  return { fields: parsed.fields, body: parsed.body.trim() }
}

function skillEntries(root, directory) {
  const base = join(root, directory)
  if (!existsSync(base)) return []
  return readdirSync(base, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => {
      const file = join(base, entry.name, 'SKILL.md')
      if (!existsSync(file)) return undefined
      const { fields, body } = readMarkdown(file)
      const name = typeof fields.name === 'string' && fields.name.length > 0 ? fields.name : entry.name
      return {
        kind: 'skill',
        path: file,
        name,
        description: fields.description ?? 'Solo-Code skill',
        whenToUse: fields.whenToUse ?? fields.when_to_use,
        content: body,
        invocation: { modelInvocable: true, userInvocable: true },
        source: 'bundled',
        resourceBase: { kind: 'directory', path: join(base, entry.name) },
      }
    })
    .filter(Boolean)
}

function promptEntries(root, directory, prefix, kind) {
  const base = join(root, directory)
  if (!existsSync(base)) return []
  return readdirSync(base, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith('.md'))
    .map((entry) => {
      const file = join(base, entry.name)
      const { fields, body } = readMarkdown(file)
      const stem = entry.name.slice(0, -3)
      return {
        kind,
        path: file,
        name: `${prefix}${stem}`,
        description: fields.description ?? `${prefix}${stem} from the Solo-Code harness`,
        content: body,
        invocation: { modelInvocable: false, userInvocable: true },
        source: 'bundled',
        resourceBase: { kind: 'directory', path: base },
      }
    })
}

/**
 * Read the pack from a package root.
 * @param {string} root package directory
 * @param {{skills?: boolean, commands?: boolean, agents?: boolean}} options
 */
export function loadPack(root, options = {}) {
  const entries = []
  if (options.skills !== false) entries.push(...skillEntries(root, 'skills'))
  if (options.commands !== false) entries.push(...promptEntries(root, 'commands', 'cmd-', 'command'))
  if (options.agents !== false) entries.push(...promptEntries(root, 'agents', 'agent-', 'agent'))
  return entries
}

/** Build a borrowed skills provider over the loaded entries. */
export function createSkillPackProvider(entries) {
  const byName = new Map(entries.map((entry) => [entry.name, entry]))
  return {
    name: 'solo-code-pack',
    async list() {
      return entries.map((entry) => ({
        path: entry.path,
        name: entry.name,
        description: entry.description,
        whenToUse: entry.whenToUse,
        invocation: entry.invocation,
        source: entry.source,
        provider: 'solo-code-pack',
        resourceBase: entry.resourceBase,
        rank: 0,
        locator: entry.path,
      }))
    },
    async get(candidate) {
      const entry = byName.get(candidate.name)
      if (entry === undefined) return undefined
      return {
        path: entry.path,
        name: entry.name,
        description: entry.description,
        whenToUse: entry.whenToUse,
        invocation: entry.invocation,
        source: entry.source,
        provider: 'solo-code-pack',
        resourceBase: entry.resourceBase,
        content: entry.content,
      }
    },
  }
}
