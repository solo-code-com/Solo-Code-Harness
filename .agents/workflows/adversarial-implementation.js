export const meta = {
  name: 'adversarial-implementation',
  description: 'Implement a spec, review it with two independent reviewers, then fix every issue in one pass',
  phases: ['Implement', 'Review', 'Fix'],
}

const input = args || {}

if (typeof input.specFile !== 'string' || input.specFile.trim() === '') {
  throw new Error('adversarial-implementation: missing required arg "specFile" (path to the spec to implement)')
}

if (typeof input.targetDir !== 'string' || input.targetDir.trim() === '') {
  throw new Error('adversarial-implementation: missing required arg "targetDir" (the directory to implement into)')
}

const specFile = input.specFile.trim()
const targetDir = input.targetDir.trim()
const businessRulesFile =
  typeof input.businessRulesFile === 'string' && input.businessRulesFile.trim() !== '' ? input.businessRulesFile.trim() : null

phase('Implement')

const implemented = await agent(
  [
    'Implement the spec at ' + specFile + ' inside ' + targetDir + '.',
    'Write the files the spec calls for and touch nothing outside ' + targetDir + '.',
    'Return the list of files you wrote and a summary of the implementation.',
  ].join('\n'),
  {
    label: 'implement',
    schema: {
      type: 'object',
      required: ['filesWritten', 'summary'],
      properties: {
        filesWritten: { type: 'array', items: { type: 'string' }, description: 'Paths written, relative to the repository root' },
        summary: { type: 'string', description: 'What was implemented, and anything the spec left open' },
      },
    },
  },
)

if (!implemented) {
  throw new Error('adversarial-implementation: the implement agent returned no result for ' + specFile)
}

const filesWritten = Array.isArray(implemented.filesWritten) ? implemented.filesWritten : []

phase('Review')

const reviews = await parallel([
  () =>
    agent(
      [
        'Review the implementation of ' + specFile + ' for security defects only.',
        'Files: ' + filesWritten.join(', '),
        'Look for unvalidated input, injection, missing authorization, secret handling and unsafe defaults.',
        'Do not edit any file. Report one issue per defect found.',
      ].join('\n'),
      {
        label: 'review:security',
        schema: {
          type: 'object',
          required: ['issues'],
          properties: {
            issues: {
              type: 'array',
              items: {
                type: 'object',
                required: ['file', 'category', 'severity', 'description'],
                properties: {
                  file: { type: 'string', description: 'The offending file' },
                  category: { type: 'string', description: 'Defect category, such as injection or authorization' },
                  severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
                  description: { type: 'string', description: 'What is wrong and why it matters' },
                },
              },
            },
          },
        },
      },
    ),
  () =>
    agent(
      [
        'Review the implementation of ' + specFile + ' against the business rules only.',
        businessRulesFile === null ? 'No business rules file was supplied: derive the rules from ' + specFile + '.' : 'Business rules: ' + businessRulesFile + '.',
        'Files: ' + filesWritten.join(', '),
        'Do not edit any file. Report one issue per mismatch between the rules and the code.',
      ].join('\n'),
      {
        label: 'review:business-rules',
        schema: {
          type: 'object',
          required: ['issues'],
          properties: {
            issues: {
              type: 'array',
              items: {
                type: 'object',
                required: ['file', 'category', 'severity', 'description'],
                properties: {
                  file: { type: 'string', description: 'The offending file' },
                  category: { type: 'string', description: 'Rule category the code violates' },
                  severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
                  description: { type: 'string', description: 'The rule and how the code departs from it' },
                },
              },
            },
          },
        },
      },
    ),
])

const issues = reviews
  .filter(Boolean)
  .flatMap((review) => (Array.isArray(review.issues) ? review.issues : []))

phase('Fix')

if (issues.length === 0) {
  log('both reviewers returned no issue for ' + specFile)
  return { status: 'passed' }
}

const fixed = await agent(
  [
    'Fix every issue below in the implementation of ' + specFile + '.',
    'Issues: ' + JSON.stringify(issues),
    'Edit only the files named in the issues. Fix all of them in this one pass.',
  ].join('\n'),
  {
    label: 'fix',
    schema: {
      type: 'object',
      required: ['filesChanged', 'summary'],
      properties: {
        filesChanged: { type: 'array', items: { type: 'string' }, description: 'Paths edited' },
        summary: { type: 'string', description: 'How each issue was resolved' },
      },
    },
  },
)

log(fixed === null ? 'the fix agent returned no result' : fixed.summary)

return { status: 'fixed', issuesFixed: issues.length }
