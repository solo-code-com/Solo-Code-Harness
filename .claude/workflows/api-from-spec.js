export const meta = {
  name: 'api-from-spec',
  description: 'Generate route, handler, validation and tests for every endpoint in an API spec, then verify the contract',
  phases: ['Parse', 'Generate', 'Verify'],
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
    'Write the route for ' + target + ' at ' + baseDir + 'routes/' + slug + '.js. Import the handler from ../handlers/' + slug + '.js, which exports one named function called handle, and register it for this method and path.',
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
    'Write the handler for ' + target + ' at ' + baseDir + 'handlers/' + slug + '.js. Export one named function called handle that takes (req, res). Import the validation from ../validation/' + slug + '.js, which exports one named function called validate, and call it. Do not validate anything inline.',
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
    'Write the request validation for ' + target + ' at ' + baseDir + 'validation/' + slug + '.js. Export one named function called validate that takes the request and returns the validated input, throwing on a bad request. Cover every parameter and body field the spec declares.',
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
    'Write the tests for ' + target + ' at ' + baseDir + 'tests/' + slug + '.test.js. Import the handler from ../handlers/' + slug + '.js as a named export called handle, and the validation from ../validation/' + slug + '.js as a named export called validate. Cover the success path and each declared error response.',
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

  return { endpoint, route, handler, validation, tests }
})

const files = generated.filter(Boolean)

phase('Verify')

const verification = await agent(
  [
    'Run the test suite and the API contract check for the endpoints generated from ' + specFile + '.',
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

return { specFile, endpointCount: endpoints.length, generated: files, verification }
