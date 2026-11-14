export const meta = {
  name: 'crud-module',
  description: 'Generate the model, repository, service, api and test files for one entity, then lint and type check them',
  phases: ['Generate', 'Verify'],
}

const input = args || {}

if (typeof input.entityName !== 'string' || input.entityName.trim() === '') {
  throw new Error('crud-module: missing required arg "entityName" (a non-empty string)')
}

if (input.entitySchema === null || typeof input.entitySchema !== 'object' || Array.isArray(input.entitySchema)) {
  throw new Error('crud-module: missing required arg "entitySchema" (an object describing the entity fields)')
}

const entityName = input.entityName.trim()
const entitySchema = JSON.stringify(input.entitySchema)
const outputDir = typeof input.outputDir === 'string' && input.outputDir !== '' ? input.outputDir : 'src/'
const baseDir = outputDir.endsWith('/') ? outputDir : outputDir + '/'

phase('Generate')

const components = [
  {
    name: 'model',
    path: baseDir + 'models/' + entityName + '.model.js',
    requirements: 'Define the persisted shape of the entity and its per-field validation, derived from the entity schema.',
  },
  {
    name: 'repository',
    path: baseDir + 'repositories/' + entityName + '.repository.js',
    requirements: 'Expose create, findById, list, update and delete. Use parameterized queries and never interpolate a value into SQL.',
  },
  {
    name: 'service',
    path: baseDir + 'services/' + entityName + '.service.js',
    requirements: 'Hold the business rules, call the repository, and throw typed errors for the not-found and conflict cases.',
  },
  {
    name: 'api',
    path: baseDir + 'api/' + entityName + '.api.js',
    requirements: 'Wire the five REST routes to the service, validate body and params, and map each typed error to its status code.',
  },
  {
    name: 'tests',
    path: baseDir + 'tests/' + entityName + '.test.js',
    requirements: 'Cover every service operation plus the not-found and conflict paths, with at least one boundary case per field.',
  },
]

const generated = await parallel(
  components.map((component) => () =>
    agent(
      [
        'Write the ' + component.name + ' component for the "' + entityName + '" entity.',
        'Entity schema: ' + entitySchema,
        'Write exactly one file, at ' + component.path + ', and do not modify any other file.',
        'Requirements: ' + component.requirements,
      ].join('\n'),
      {
        label: component.name,
        schema: {
          type: 'object',
          required: ['path', 'exports', 'summary'],
          properties: {
            path: { type: 'string', description: 'The file written, which must be ' + component.path },
            exports: { type: 'array', items: { type: 'string' }, description: 'Names exported from the file' },
            summary: { type: 'string', description: 'One sentence on what the file does' },
          },
        },
      },
    ),
  ),
)

const written = generated.filter(Boolean)

phase('Verify')

const verification = await agent(
  [
    'Run the project linter and the project type check over the files generated for "' + entityName + '":',
    written.length > 0 ? written.map((result) => result.path).join('\n') : '(no component agent returned a file)',
    'Report every problem they print. Do not edit the files.',
  ].join('\n'),
  {
    label: 'verify',
    schema: {
      type: 'object',
      required: ['ok', 'issues'],
      properties: {
        ok: { type: 'boolean', description: 'True when the linter and the type check both pass' },
        issues: { type: 'array', items: { type: 'string' }, description: 'One entry per reported problem' },
      },
    },
  },
)

return { entity: entityName, components: written, verification }
