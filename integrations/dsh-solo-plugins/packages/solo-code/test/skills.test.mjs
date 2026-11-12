import { test } from 'node:test'
import assert from 'node:assert/strict'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createSkillPackProvider, loadPack, parseFrontmatter } from '../skills.js'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))

test('frontmatter parser reads quoted fields and the body', () => {
  const parsed = parseFrontmatter('---\nname: demo\ndescription: "A demo skill."\n---\n\n# Body\ntext\n')
  assert.equal(parsed.fields.name, 'demo')
  assert.equal(parsed.fields.description, 'A demo skill.')
  assert.ok(parsed.body.startsWith('# Body'))
})

test('frontmatter parser tolerates missing or broken fences', () => {
  assert.deepEqual(parseFrontmatter('# No frontmatter').fields, {})
  assert.deepEqual(parseFrontmatter('---\nname: x\nno closing fence').fields, {})
})

test('the pack exposes 53 Solo-Code skills, 15 commands, and 15 agents', () => {
  const entries = loadPack(ROOT)
  const skills = entries.filter((entry) => entry.kind === 'skill')
  const commands = entries.filter((entry) => entry.kind === 'command')
  const agents = entries.filter((entry) => entry.kind === 'agent')
  assert.equal(skills.length, 53)
  assert.equal(commands.length, 15)
  assert.equal(agents.length, 15)
  assert.ok(skills.some((entry) => entry.name === 'agent-md-refactor'))
})

test('skills are model-invocable, commands and agents are user-invocable only', () => {
  const entries = loadPack(ROOT)
  const skill = entries.find((entry) => entry.name === 'systematic-debugging')
  const command = entries.find((entry) => entry.name === 'cmd-verify')
  const agent = entries.find((entry) => entry.name === 'agent-planner')
  assert.deepEqual(skill.invocation, { modelInvocable: true, userInvocable: true })
  assert.deepEqual(command.invocation, { modelInvocable: false, userInvocable: true })
  assert.deepEqual(agent.invocation, { modelInvocable: false, userInvocable: true })
  assert.ok(skill.content.includes('Iron Law'))
})

test('the core pack can be requested alone', () => {
  assert.deepEqual(loadPack(ROOT, { skills: false, commands: false, agents: false }), [])
})

test('provider lists candidates and returns the full body on get', async () => {
  const provider = createSkillPackProvider(loadPack(ROOT))
  const candidates = await provider.list()
  assert.ok(candidates.length >= 83)
  const candidate = candidates.find((item) => item.name === 'systematic-debugging')
  assert.ok(candidate.rank === 0)
  assert.equal(candidate.provider, 'solo-code-pack')
  const definition = await provider.get(candidate)
  assert.ok(definition.content.length > 100)
  assert.equal(definition.name, 'systematic-debugging')
  assert.equal(await provider.get({ name: 'missing-skill' }), undefined)
})
