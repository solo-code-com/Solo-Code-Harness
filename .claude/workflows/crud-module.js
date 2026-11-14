export const meta = {
  name: 'crud-module',
  description: 'Generate the model, repository, service, api and test files for one entity, then lint and type check them',
  phases: ['Contract', 'Generate', 'Verify'],
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

phase('Contract')

const contract = await agent(
  [
    'Define the naming and structure conventions for the "' + entityName + '" CRUD module.',
    'Five independent agents will write these files next and never see each other, so this contract is the only thing keeping them consistent:',
    components.map((component) => component.name + ' -> ' + component.path).join('\n'),
    'The repository exposes exactly five methods: create, findById, list, update and delete. The service must call them by exactly those names.',
    'Set importPaths to the path from the tests file to each of the other files, written exactly as it must appear inside an import statement.',
    'Entity schema: ' + entitySchema,
  ].join('\n'),
  {
    label: 'contract',
    schema: {
      type: 'object',
      required: ['modelClass', 'serviceClass', 'repositoryClass', 'importPaths', 'methodNames', 'injectionPattern'],
      properties: {
        modelClass: { type: 'string', description: 'PascalCase class name for the model, such as Product' },
        serviceClass: { type: 'string', description: 'PascalCase class name for the service, such as ProductService' },
        repositoryClass: { type: 'string', description: 'PascalCase class name for the repository, such as ProductRepository' },
        importPaths: {
          type: 'object',
          required: ['model', 'repository', 'service', 'api'],
          description: 'Relative import path from the tests file to each layer',
          properties: {
            model: { type: 'string' },
            repository: { type: 'string' },
            service: { type: 'string' },
            api: { type: 'string' },
          },
        },
        methodNames: {
          type: 'object',
          required: ['create', 'findById', 'list', 'update', 'delete'],
          description: 'Method names on the service class',
          properties: {
            create: { type: 'string' },
            findById: { type: 'string' },
            list: { type: 'string' },
            update: { type: 'string' },
            delete: { type: 'string' },
          },
        },
        injectionPattern: {
          type: 'string',
          enum: ['class-constructor', 'singleton'],
          description: 'How the service receives the repository',
        },
      },
    },
  },
)

if (!contract) {
  throw new Error('crud-module: the contract agent returned no result for ' + entityName)
}

const contractNote = 'Follow this contract exactly - do not invent names or patterns it does not list: ' + JSON.stringify(contract)

phase('Generate')

const generated = await parallel(
  components.map((component) => () =>
    agent(
      [
        'Write the ' + component.name + ' component for the "' + entityName + '" entity.',
        'Entity schema: ' + entitySchema,
        'Write exactly one file, at ' + component.path + ', and do not modify any other file.',
        'Requirements: ' + component.requirements,
        contractNote,
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
