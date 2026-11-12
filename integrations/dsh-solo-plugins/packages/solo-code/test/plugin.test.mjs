import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { classifyToolCall, executorModeOn, normalizeOptions } from '../index.js'

test('destructive shell command asks for approval', () => {
  assert.equal(classifyToolCall('pwsh', { command: 'git reset --hard HEAD~1' }).action, 'ask')
  assert.equal(classifyToolCall('pwsh', { command: 'Remove-Item -Recurse -Force .' }).action, 'ask')
})

test('dependency, network, and git publication ask for approval', () => {
  assert.equal(classifyToolCall('pwsh', { command: 'pnpm add left-pad' }).action, 'ask')
  assert.equal(classifyToolCall('pwsh', { command: 'curl https://example.com' }).action, 'ask')
  assert.equal(classifyToolCall('pwsh', { command: 'git push origin main' }).action, 'ask')
})

test('routine commands and edits are allowed', () => {
  assert.equal(classifyToolCall('pwsh', { command: 'node -v' }).action, 'allow')
  assert.equal(classifyToolCall('edit', { file_path: 'src/index.ts' }).action, 'allow')
})

test('credential files cannot be written', () => {
  assert.equal(classifyToolCall('write', { file_path: 'F:/proj/.env' }).action, 'deny')
  assert.equal(classifyToolCall('edit', { file_path: 'certs/server.pem' }).action, 'deny')
  assert.equal(classifyToolCall('write', { file_path: 'src/config.json' }).action, 'allow')
})

test('write gate fails closed and opens only on explicit off values', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'solo-code-'))
  try {
    const missing = join(dir, 'missing')
    assert.equal(await executorModeOn(missing), true)
    const file = join(dir, 'executor-mode')
    await writeFile(file, 'off\n')
    assert.equal(await executorModeOn(file), false)
    await writeFile(file, 'on\n')
    assert.equal(await executorModeOn(file), true)
    await writeFile(file, 'garbage\n')
    assert.equal(await executorModeOn(file), true)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
})

test('delegation through the MCP server asks for approval', () => {
  assert.equal(classifyToolCall('mcp__solo__delegate', { executor: 'codex', prompt: 'fix it' }).action, 'ask')
  assert.equal(classifyToolCall('mcp__solo__route', { task: 'fix it', task_type: 'complex' }).action, 'ask')
  assert.equal(classifyToolCall('mcp__solo__route', { task: 'fix it', dry_run: true }).action, 'allow')
  assert.equal(classifyToolCall('mcp__solo__security_scan', { path: '.' }).action, 'allow')
})

test('config defaults are balanced', () => {
  const options = normalizeOptions({})
  assert.equal(options.writeGate, false)
  assert.equal(options.askOnWrites, false)
  assert.equal(options.denyCredentialWrites, true)
  assert.equal(normalizeOptions({ writeGate: 'true' }).writeGate, true)
})
