export const meta = {
  name: 'data-migration',
  description: 'Migrate every file matching a pattern to a target schema, one file at a time, then validate the output',
  phases: ['Discover', 'Transform', 'Verify'],
}

const input = args || {}

if (typeof input.sourcePattern !== 'string' || input.sourcePattern.trim() === '') {
  throw new Error('data-migration: missing required arg "sourcePattern" (a glob for the files to migrate)')
}

if (input.targetSchema === null || typeof input.targetSchema !== 'object' || Array.isArray(input.targetSchema)) {
  throw new Error('data-migration: missing required arg "targetSchema" (an object describing the destination shape)')
}

if (typeof input.outputDir !== 'string' || input.outputDir.trim() === '') {
  throw new Error('data-migration: missing required arg "outputDir" (a directory for the migrated files)')
}

const sourcePattern = input.sourcePattern.trim()
const targetSchema = JSON.stringify(input.targetSchema)
const outputDir = input.outputDir.trim()
const baseDir = outputDir.endsWith('/') ? outputDir : outputDir + '/'
const maxFiles = typeof input.maxFiles === 'number' && input.maxFiles > 0 ? Math.floor(input.maxFiles) : 50
const cap = 4096

phase('Discover')

const discovered = await agent(
  [
    'List every file in this repository that matches the pattern ' + sourcePattern + '.',
    'Return repository-relative paths. Do not modify any file.',
  ].join('\n'),
  {
    label: 'discover',
    schema: {
      type: 'object',
      required: ['files'],
      properties: {
        files: { type: 'array', items: { type: 'string' }, description: 'Repository-relative paths of the matching files' },
      },
    },
  },
)

if (!discovered) {
  throw new Error('data-migration: the discover agent returned no result for ' + sourcePattern)
}

const files = Array.isArray(discovered.files) ? discovered.files : []

if (files.length > cap) {
  throw new Error(
    'data-migration: ' +
      files.length +
      ' files match ' +
      sourcePattern +
      ', above the ' +
      cap +
      '-item cap of one pipeline call. Narrow the pattern, or set maxFiles to ' +
      cap +
      ' or less to migrate a batch per run.',
  )
}

const selected = files.slice(0, Math.min(maxFiles, cap))

phase('Transform')

const migrated = await pipeline(selected, async (file) => {
  const name = String(file).split(/[\\/]/).pop()
  const outPath = baseDir + name

  const validated = await agent(
    'Read the single file ' + file + ' and report whether it matches the schema this file already declares. Name any field that does not conform. Do not write any file.',
    {
      label: 'validate:' + file,
      schema: {
        type: 'object',
        required: ['conforms', 'problems'],
        properties: {
          conforms: { type: 'boolean', description: 'True when the file matches its own declared schema' },
          problems: { type: 'array', items: { type: 'string' }, description: 'One entry per non-conforming field' },
        },
      },
    },
  )

  const transformed = await agent(
    [
      'Transform the single file ' + file + ' into the target shape: ' + targetSchema + '.',
      'Write nothing yet. Return the migrated content as an object.',
    ].join('\n'),
    {
      label: 'transform:' + file,
      schema: {
        type: 'object',
        required: ['record', 'notes'],
        properties: {
          record: { type: 'object', description: 'The migrated record' },
          notes: { type: 'string', description: 'What changed from the source shape' },
        },
      },
    },
  )

  const written = await agent(
    [
      'Write the migrated content of ' + file + ' to exactly this path: ' + outPath + '.',
      'Write that one file and nothing else. Do not read or write ' + file + ' itself, and do not touch the other files in ' + baseDir + '.',
      'Migrated content: ' + JSON.stringify(transformed === null ? null : transformed.record),
    ].join('\n'),
    {
      label: 'write:' + file,
      schema: {
        type: 'object',
        required: ['path', 'bytes'],
        properties: {
          path: { type: 'string', description: 'The path written, which must be ' + outPath },
          bytes: { type: 'number', description: 'Size of the file written, in bytes' },
        },
      },
    },
  )

  return { file, outPath, validated, transformed, written }
})

const results = migrated.filter(Boolean)

phase('Verify')

const verification = await agent(
  [
    'Validate every file under ' + baseDir + ' against this target schema: ' + targetSchema + '.',
    'Only these files were produced by this run:',
    results.length > 0 ? results.map((result) => result.outPath).join('\n') : '(no file was migrated)',
    'Report each file that does not conform, with the field that fails. Do not edit any file.',
  ].join('\n'),
  {
    label: 'verify',
    schema: {
      type: 'object',
      required: ['ok', 'issues'],
      properties: {
        ok: { type: 'boolean', description: 'True when every produced file conforms to the target schema' },
        issues: { type: 'array', items: { type: 'string' }, description: 'One entry per non-conforming file or field' },
      },
    },
  },
)

return { sourcePattern, fileCount: files.length, migrated: results, verification }
