export const meta = {
  name: 'compliance-audit',
  description: 'Load a rules file, audit every target directory against it in parallel, then rank the findings by severity',
  phases: ['Load', 'Audit', 'Synthesize'],
}

const input = args || {}

if (typeof input.rulesFile !== 'string' || input.rulesFile.trim() === '') {
  throw new Error('compliance-audit: missing required arg "rulesFile" (path to the rules file)')
}

if (!Array.isArray(input.targetDirs) || input.targetDirs.length === 0) {
  throw new Error('compliance-audit: missing required arg "targetDirs" (a non-empty array of directories)')
}

const rulesFile = input.rulesFile.trim()
const targetDirs = input.targetDirs
const auditTypes = Array.isArray(input.auditTypes) && input.auditTypes.length > 0 ? input.auditTypes : ['all']
const cap = 4096

if (targetDirs.length > cap) {
  throw new Error(
    'compliance-audit: ' +
      targetDirs.length +
      ' target directories, above the ' +
      cap +
      '-item cap of one parallel call. Audit them in batches.',
  )
}

phase('Load')

const loaded = await agent(
  [
    'Parse the compliance rules in ' + rulesFile + '.',
    'Return one entry per rule, with its id, category, description and severity.',
    'Do not write any file.',
  ].join('\n'),
  {
    label: 'load-rules',
    schema: {
      type: 'object',
      required: ['rules'],
      properties: {
        rules: {
          type: 'array',
          items: {
            type: 'object',
            required: ['id', 'category', 'description', 'severity'],
            properties: {
              id: { type: 'string', description: 'Stable rule identifier' },
              category: { type: 'string', description: 'Rule category, such as secrets or access-control' },
              description: { type: 'string', description: 'What the rule requires' },
              severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
            },
          },
        },
      },
    },
  },
)

if (!loaded) {
  throw new Error('compliance-audit: the load agent returned no result for ' + rulesFile)
}

const rules = Array.isArray(loaded.rules) ? loaded.rules : []

phase('Audit')

const audits = await parallel(
  targetDirs.map((dir) => () =>
    agent(
      [
        'Audit every file under ' + dir + ' against the rules loaded from ' + rulesFile + '.',
        'Audit types requested: ' + auditTypes.join(', ') + '.',
        'Rules: ' + JSON.stringify(rules),
        'Return one finding per violation, with the rule id, the file, the line and a message. Do not edit any file.',
      ].join('\n'),
      {
        label: 'audit:' + dir,
        schema: {
          type: 'object',
          required: ['findings'],
          properties: {
            findings: {
              type: 'array',
              items: {
                type: 'object',
                required: ['ruleId', 'file', 'line', 'severity', 'message'],
                properties: {
                  ruleId: { type: 'string', description: 'The rule violated' },
                  file: { type: 'string', description: 'Repository-relative path of the offending file' },
                  line: { type: 'number', description: 'Line number, or 0 when the rule applies to the whole file' },
                  severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
                  message: { type: 'string', description: 'What violates the rule' },
                },
              },
            },
          },
        },
      },
    ),
  ),
)

phase('Synthesize')

const ranking = { critical: 0, high: 1, medium: 2, low: 3 }

const findings = audits
  .filter(Boolean)
  .flatMap((result) => (Array.isArray(result.findings) ? result.findings : []))
  .sort((a, b) => (ranking[a.severity] === undefined ? 4 : ranking[a.severity]) - (ranking[b.severity] === undefined ? 4 : ranking[b.severity]))

const answered = audits.filter(Boolean).length
log(findings.length + ' finding(s) across ' + answered + ' of ' + targetDirs.length + ' directory(ies)')

return { findings }
