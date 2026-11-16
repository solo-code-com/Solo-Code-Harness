export const meta = {
  name: 'api-from-spec',
  description: 'Generate route, handler, validation and tests for every endpoint in an API spec, then verify the contract',
  phases: ['Parse', 'Generate', 'Mount', 'Verify'],
}

const input = args || {}

if (typeof input.specFile !== 'string' || input.specFile.trim() === '') {
  throw new Error('api-from-spec: missing required arg "specFile" (path to the API spec)')
}

const specFile = input.specFile.trim()
const outputDir = typeof input.outputDir === 'string' && input.outputDir !== '' ? input.outputDir : 'src/api/'
const baseDir = outputDir.endsWith('/') ? outputDir : outputDir + '/'
const maxEndpoints = typeof input.maxEndpoints === 'number' && input.maxEndpoints > 0 ? Math.floor(input.maxEndpoints) : 40
const cap = 4096

const requestedModuleSystem = input.moduleSystem === undefined ? 'commonjs' : input.moduleSystem

if (requestedModuleSystem !== 'esm' && requestedModuleSystem !== 'commonjs') {
  throw new Error('api-from-spec: moduleSystem must be "esm" or "commonjs"')
}

const moduleSystem = requestedModuleSystem
const moduleRule =
  moduleSystem === 'esm'
    ? 'Use ES modules throughout: import and export. Never use require or module.exports.'
    : 'Use CommonJS throughout: require and module.exports. Never use import or export.'

phase('Parse')

const parsed = await agent(
  [
    'Read the API spec at ' + specFile + '.',
    'List every endpoint it defines, with its path, its HTTP method and its operationId.',
    'Do not write any file.',
  ].join('\n'),
  {
    label: 'parse-spec',
    schema: {
      type: 'object',
      required: ['endpoints'],
      properties: {
        endpoints: {
          type: 'array',
          items: {
            type: 'object',
            required: ['path', 'method'],
            properties: {
              path: { type: 'string', description: 'The URL path, such as /users/{id}' },
              method: { type: 'string', description: 'The lowercase HTTP method' },
              operationId: { type: 'string', description: 'The operationId the spec declares, when it declares one' },
            },
          },
        },
      },
    },
  },
)

if (!parsed) {
  throw new Error('api-from-spec: the parse agent returned no result for ' + specFile)
}

const endpoints = Array.isArray(parsed.endpoints) ? parsed.endpoints : []

if (endpoints.length > cap) {
  throw new Error(
    'api-from-spec: the spec defines ' +
      endpoints.length +
      ' endpoints, above the ' +
      cap +
      '-item cap of one pipeline call. Split the spec into smaller files, or set maxEndpoints to ' +
      cap +
      ' or less to generate a subset in this run.',
  )
}

const selected = endpoints.slice(0, Math.min(maxEndpoints, cap))

phase('Generate')

const generated = await pipeline(selected, async (endpoint) => {
  const target = String(endpoint.method).toUpperCase() + ' ' + endpoint.path
  const slug =
    typeof endpoint.operationId === 'string' && endpoint.operationId !== ''
      ? endpoint.operationId
      : String(endpoint.method) + '-' + String(endpoint.path).replace(/[^a-zA-Z0-9]+/g, '-')

  const route = await agent(
    [
      'Write the route descriptor for ' + target + ' at ' + baseDir + 'routes/' + slug + '.js.',
      'Export a plain object with module.exports = { method, path, handle }: method is this endpoint\'s lowercase HTTP method, path is its URL path, and handle is imported from ../handlers/' + slug + '.js as a named export.',
      'Do not import a web framework, do not create a router, and do not register anything. The mount phase owns the framework wiring.',
      moduleRule,
    ].join('\n'),
    {
      label: slug + ':route',
      schema: {
        type: 'object',
        required: ['file', 'summary'],
        properties: {
          file: { type: 'string', description: 'The route file touched' },
          summary: { type: 'string', description: 'One sentence on the change' },
        },
      },
    },
  )

  const handler = await agent(
    [
      'Write the handler for ' + target + ' at ' + baseDir + 'handlers/' + slug + '.js.',
      'Export one named function called handle that takes (req, res).',
      'Import validate and ValidationError from ../validation/' + slug + '.js. That module exports both by name.',
      'Call validate(req), passing the whole request object. On success it returns the validated input object; continue with the returned value.',
      'Every path must send a response. The success path must end with res.status with the endpoint success code and res.json with the payload. A handler that reaches the end of the function without calling res.status and res.json is a bug, even if it returns no error.',
      'Wrap the call in try/catch. When the caught error is a ValidationError, respond with status 400 and the error message. Any other thrown error is a server fault: respond with status 500 and do not leak the stack.',
      'Do not validate anything inline.',
      moduleRule,
    ].join('\n'),
    {
      label: slug + ':handler',
      schema: {
        type: 'object',
        required: ['file', 'summary'],
        properties: {
          file: { type: 'string', description: 'The handler file written' },
          summary: { type: 'string', description: 'One sentence on the handler' },
        },
      },
    },
  )

  const validation = await agent(
    [
      'Write the request validation for ' + target + ' at ' + baseDir + 'validation/' + slug + '.js.',
      'Export a class ValidationError that extends Error, sets this.name to ValidationError and this.status to 400.',
      'Export one named function called validate that takes the full request object (req), reads req.body and req.params from it, and returns the validated and coerced input object on success.',
      'On a bad request throw new ValidationError with a message naming the field at fault. Never return an error list: always throw.',
      'Cover every parameter and body field the spec declares.',
      moduleRule,
    ].join('\n'),
    {
      label: slug + ':validation',
      schema: {
        type: 'object',
        required: ['file', 'summary'],
        properties: {
          file: { type: 'string', description: 'The validation file written' },
          summary: { type: 'string', description: 'One sentence on the validation rules' },
        },
      },
    },
  )

  const tests = await agent(
    [
      'Write the tests for ' + target + ' at ' + baseDir + 'tests/' + slug + '.test.js.',
      'Use the built-in node:test runner with node:assert/strict. Do not use jest, mocha or any package that has to be installed.',
      'Import the handler from ../handlers/' + slug + '.js as a named export called handle.',
      'Import validate and ValidationError from ../validation/' + slug + '.js as named exports.',
      'Cover the success path and each declared error response.',
      'Assert that validate returns the validated input for a good request, and that it throws a ValidationError for each bad case.',
      'Exercise the 400 path by sending a request body that fails validation. Do not mock, patch, stub or reset modules: the validation function already throws on a bad body.',
      'Assert the success path positively: the success status code and the JSON body that was sent. Asserting only that a 400 was not sent is not enough, because a handler that never responds passes that check.',
      'Assert that handle responds 400 on that bad body.',
      moduleRule,
    ].join('\n'),
    {
      label: slug + ':tests',
      schema: {
        type: 'object',
        required: ['file', 'summary'],
        properties: {
          file: { type: 'string', description: 'The test file written' },
          summary: { type: 'string', description: 'One sentence on the coverage' },
        },
      },
    },
  )

  return { endpoint, slug, route, handler, validation, tests }
})

const files = generated.filter(Boolean)

const expectedFiles = files.flatMap((entry) => [
  baseDir + 'routes/' + entry.slug + '.js',
  baseDir + 'handlers/' + entry.slug + '.js',
  baseDir + 'validation/' + entry.slug + '.js',
  baseDir + 'tests/' + entry.slug + '.test.js',
])

if (selected.length > 0 && files.length === 0) {
  throw new Error('api-from-spec: every endpoint agent failed, so nothing was generated from ' + specFile)
}

const routeFiles = files
  .map((entry) => (entry && entry.route && entry.route.file ? entry.route.file : null))
  .filter(Boolean)

phase('Mount')

const mounted =
  routeFiles.length === 0
    ? null
    : await agent(
        [
          'These route descriptor files exist, and each exports an object with method, path and handle:',
          routeFiles.join('\n'),
          'Write ' + baseDir + 'routes/index.js.',
          'Use Express. Require express, create a router with express.Router(), then require each descriptor file above and call router[descriptor.method](descriptor.path, descriptor.handle) for each one. Export the router with module.exports.',
          'Do not modify the descriptor files themselves.',
          moduleRule,
        ].join('\n'),
        {
          label: 'mount',
          schema: {
            type: 'object',
            required: ['file', 'summary'],
            properties: {
              file: { type: 'string', description: 'The index file written' },
              summary: { type: 'string', description: 'One sentence on what it mounts' },
            },
          },
        },
      )

if (routeFiles.length > 0 && !mounted) {
  throw new Error('api-from-spec: the mount agent returned no result, so ' + baseDir + 'routes/index.js was not written')
}

phase('Verify')

const verification = await agent(
  [
    'Run the tests for the endpoints generated from ' + specFile + ' with node --test from the project root. Node does not accept a directory as an argument, so run it from the root or pass the test files explicitly, and run the API contract check as well.',
    'The project needs express installed for the routes to load. If express is missing, report that as a missing dependency, not as a failing test.',
    'Read every generated handler and confirm each path sends a response. A handler whose success path never calls res.status and res.json is a failure even when every test passes.',
    'Route files written this run:',
    routeFiles.length > 0 ? routeFiles.join('\n') : '(no route file was written)',
    'Every endpoint must produce four files, and the mount must produce the index. Confirm each of these paths exists on disk and report every missing one as a failure:',
    expectedFiles.join('\n'),
    baseDir + 'routes/index.js',
    'A file this run was supposed to write but did not is a failure, even when the tests that do exist all pass.',
    'The mount index is ' + baseDir + 'routes/index.js: check it requires each descriptor file and wires each one onto one shared Express router with router[descriptor.method](descriptor.path, descriptor.handle).',
    'A failure in one generated endpoint must not hide the others: report every failing check.',
    'Do not edit any file.',
  ].join('\n'),
  {
    label: 'verify',
    schema: {
      type: 'object',
      required: ['ok', 'issues'],
      properties: {
        ok: { type: 'boolean', description: 'True when the tests and the contract check both pass' },
        issues: { type: 'array', items: { type: 'string' }, description: 'One entry per failing check' },
      },
    },
  },
)

return { specFile, endpointCount: endpoints.length, generated: files, mounted, verification }
