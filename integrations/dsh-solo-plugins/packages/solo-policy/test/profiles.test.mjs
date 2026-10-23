import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

// Structural validation of the two provider profiles. They are Cordis patches,
// so a full check needs the DSH kernel; these assertions catch the mistakes that
// do not — a missing profile, a profile that never names its key environment
// variable, one that forgets to install the plugins, or one that carries a
// credential value instead of a reference to the environment.

const here = dirname(fileURLToPath(import.meta.url))
const profilesDir = join(here, '..', '..', '..', 'profiles')

const EXPECTED = [
  'direct-deepseek.cordis.patch.yml',
  'openrouter-deepseek.cordis.patch.yml',
]

test('the two provider profiles are present', () => {
  const found = readdirSync(profilesDir)
    .filter((name) => name.endsWith('.cordis.patch.yml'))
    .sort()
  assert.deepEqual(found, EXPECTED.slice().sort())
})

test('each profile names its key env and installs both plugins', () => {
  for (const name of EXPECTED) {
    const text = readFileSync(join(profilesDir, name), 'utf8')
    assert.match(text, /apiKeyEnv:\s*[A-Z0-9_]+/, `${name} never names an apiKeyEnv`)
    assert.match(text, /@solo-code\/dsh-solo-policy/, `${name} does not install solo-policy`)
    assert.match(text, /@solo-code\/dsh-solo-guidance/, `${name} does not install solo-guidance`)
  }
})

test('no profile carries a credential value', () => {
  for (const name of EXPECTED) {
    const text = readFileSync(join(profilesDir, name), 'utf8')
    assert.doesNotMatch(text, /(?:sk-[A-Za-z0-9]{8,}|user_[A-Za-z0-9]{10,}|Bearer\s+\S{10,})/, `${name} looks like it carries a key`)
  }
})
