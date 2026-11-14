# Phase 3 Brief: Native Workflow Tool Full Adoption

## Who you are and what this is

You are DeepSeek, executing Phase 3 of the Claude Code vanilla adoption plan.
**Phase 1 and 2 are complete** (path-scoped rules, static permissions, workflow
routing documentation). This phase adds **full native Workflow tool leverage**
with templates, examples, and expanded routing guidance.

---

## Critical constraints (READ BEFORE EDITING)

1. **Harness boundary.** Every file you touch is harness infrastructure. Allowed
   paths are listed per-task. Do not edit outside those lists.

2. **Cross-engine parity is mandatory.** `.kilo/` is source of truth. Edit
   `.kilo/` first, then mirror to `.claude/`, then run `python tools/garden.py`.
   A `lock_stale` drift after edits is EXPECTED — fix with
   `python tools/deploy.py --root-lock` at the very end.

3. **Verify every claim with a command.** Use this table in your final report:

   | Claim | Command run | Output |
   |-------|-------------|--------|

4. **No protected config edits** (`.ruff.toml`, `eslint.config.js`). None of
   the tasks below require it.

5. **Workflow script syntax** — valid JavaScript/TypeScript, executable by
   Claude Code's Workflow tool. Must start with `export const meta = {...}`.

6. **Schema format** — JSON Schema draft-07. Required properties must list in
   `required: []` array.

---

## Ground truth (do not re-derive)

**Current skill count:** 54 (after Phase 2). This phase does NOT add new skills
(templates go in `.claude/workflows/templates/`, not `.claude/skills/`). Skill
count stays 54.

**Workflow tool primitives** (from Claude Code vanilla v2.1.318+):
- `agent(prompt, {schema, label, phase, model, effort, isolation, agentType})`
  — spawn subagent, return structured output if schema provided
- `parallel(thunks)` — run array of `() => Promise` concurrently, barrier
- `pipeline(items, ...stages)` — run items through stages with NO barrier
  (item A stage 3 while item B stage 1)
- `phase(title)` — start new phase, subsequent agents group under this title
- `log(message)` — emit progress message
- `budget.total` — token target from user's `+500k` directive (null if not set)
- `budget.spent()` — output tokens used this turn
- `budget.remaining()` — max(0, total - spent()) or Infinity
- `workflow(nameOrRef, args)` — invoke another workflow inline
- `args` — value passed as `args` parameter to Workflow tool

**User profile:** AI architect — designs systems, writes specs, AI implements
100%. Workflow = high-level orchestration language for delegating to AI workers.
Patterns repeat across 20+ business projects/year.

---

## Task 3.1 — Create workflow template library

### Files allowed to create

- `.claude/workflows/templates/crud-module.js`
- `.claude/workflows/templates/api-from-spec.js`
- `.claude/workflows/templates/data-migration.js`
- `.claude/workflows/templates/compliance-audit.js`
- `.claude/workflows/templates/adversarial-implementation.js`
- Mirror all 5 to `.kilo/workflows/templates/` (source of truth)

### Spec: 5 templates

Each template is a **reusable workflow script** parameterized via `args`. The
user (or orchestrator Claude) will invoke with:
```javascript
await workflow({scriptPath: '.claude/workflows/templates/crud-module.js'}, {
  entityName: 'User',
  entitySchema: {...}
})
```

**Template structure (all 5 follow this):**

```javascript
export const meta = {
  name: '<template-name>',
  description: '<one-line summary>',
  phases: [
    { title: '<Phase1>', detail: '<what happens>' },
    { title: '<Phase2>', detail: '<what happens>' }
  ]
}

// Validate args
if (!args.requiredParam) {
  throw new Error('Missing required args.requiredParam')
}

// Extract args
const { param1, param2 } = args

// Budget-aware scaling (if applicable)
let itemsToProcess = allItems
if (budget.total) {
  const costPerItem = 10_000 // rough tokens/item
  const affordable = Math.floor(budget.remaining() / costPerItem)
  if (affordable < allItems.length) {
    log(`Budget allows ${affordable} of ${allItems.length} items, trimming`)
    itemsToProcess = allItems.slice(0, affordable)
  }
}

// Phase 1
phase('Phase1')
const result1 = await agent('do something', {
  schema: { /* JSON Schema */ },
  effort: 'medium'
})

// Phase 2 (use result1)
phase('Phase2')
const result2 = await parallel(result1.items.map(item => 
  () => agent(`process ${item}`, {
    schema: { /* ... */ },
    phase: 'Phase2' // explicit phase for parallel calls
  })
))

// Return value (optional)
return { summary: '...', details: result2 }
```

---

### Template 1: `crud-module.js`

**Purpose:** Generate complete CRUD module (model, repository, service, API
controller, tests) from entity schema.

**Args:**
- `entityName` (string, required) — e.g., "User", "Product"
- `entitySchema` (object, required) — `{fields: [...], relationships: [...]}`
- `outputDir` (string, optional, default `src/`) — base directory

**Phases:**
1. **Generate** — 5 parallel agents (model, repo, service, API, tests)
2. **Verify** — run linter + type check on generated files

**Schemas to define:**
- `MODEL_SCHEMA` — model class structure
- `REPO_SCHEMA` — repository interface
- `SERVICE_SCHEMA` — service methods
- `API_SCHEMA` — API routes + handlers
- `TEST_SCHEMA` — test cases

**Budget:** Yes — if >10 entities in batch, scale down

**Key code:**
```javascript
const components = ['model', 'repository', 'service', 'api', 'tests']

phase('Generate')
const generated = await parallel(components.map(comp =>
  () => agent(`Generate ${entityName} ${comp} from schema`, {
    schema: COMPONENT_SCHEMAS[comp],
    phase: 'Generate'
  })
))

phase('Verify')
await agent('Run linter and type check on generated CRUD module', {
  effort: 'low'
})
```

---

### Template 2: `api-from-spec.js`

**Purpose:** Generate API implementation (routes, handlers, validation, tests)
from OpenAPI/Swagger spec.

**Args:**
- `specFile` (string, required) — path to OpenAPI YAML/JSON
- `outputDir` (string, optional, default `src/api/`)

**Phases:**
1. **Parse** — extract endpoints from spec
2. **Generate** — pipeline endpoints through route → handler → validation → test
3. **Verify** — run tests + API contract validation

**Budget:** Yes — if >50 endpoints, trim to affordable subset

**Key code:**
```javascript
phase('Parse')
const spec = await agent(`Parse OpenAPI spec from ${specFile}`, {
  schema: {
    type: 'object',
    properties: {
      endpoints: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            path: { type: 'string' },
            method: { type: 'string' },
            operationId: { type: 'string' },
            parameters: { type: 'array' },
            requestBody: { type: 'object' },
            responses: { type: 'object' }
          },
          required: ['path', 'method']
        }
      }
    },
    required: ['endpoints']
  }
})

phase('Generate')
await pipeline(
  spec.endpoints,
  (ep) => agent(`Generate route for ${ep.method} ${ep.path}`, {
    schema: ROUTE_SCHEMA,
    phase: 'Generate'
  }),
  (route, ep) => agent(`Generate handler for ${ep.operationId}`, {
    schema: HANDLER_SCHEMA,
    phase: 'Generate'
  }),
  (handler, ep) => agent(`Generate validation for ${ep.path}`, {
    schema: VALIDATION_SCHEMA,
    phase: 'Generate'
  }),
  (validation, ep) => agent(`Generate tests for ${ep.operationId}`, {
    schema: TEST_SCHEMA,
    phase: 'Generate',
    effort: 'low'
  })
)
```

---

### Template 3: `data-migration.js`

**Purpose:** Migrate N data files (CSV, JSON, XML) to new schema with validation.

**Args:**
- `sourcePattern` (string, required) — glob pattern, e.g., `data/*.csv`
- `targetSchema` (object, required) — new schema definition
- `outputDir` (string, required) — where to write transformed files

**Phases:**
1. **Discover** — find all matching files
2. **Validate** — check each file against old schema
3. **Transform** — pipeline: validate → transform → write
4. **Verify** — validate all output files against new schema

**Budget:** Yes — if >100 files, trim

**Isolation:** Yes — each transform in separate worktree to avoid conflicts

**Key code:**
```javascript
phase('Discover')
const files = await agent(`Find all files matching ${sourcePattern}`, {
  schema: {
    type: 'object',
    properties: {
      files: { type: 'array', items: { type: 'string' } }
    },
    required: ['files']
  }
})

phase('Transform')
await pipeline(
  files.files,
  (file) => agent(`Validate ${file} against old schema`, {
    schema: VALIDATION_SCHEMA,
    phase: 'Transform'
  }),
  (validation, file) => agent(`Transform ${file} to new schema`, {
    schema: TRANSFORM_SCHEMA,
    isolation: 'worktree', // isolated transform
    phase: 'Transform'
  }),
  (transformed, file) => agent(`Write transformed ${file} to ${outputDir}`, {
    effort: 'low',
    phase: 'Transform'
  })
)

phase('Verify')
await agent(`Validate all files in ${outputDir} against new schema`, {
  schema: VERIFY_SCHEMA
})
```

---

### Template 4: `compliance-audit.js`

**Purpose:** Audit code/docs/configs against business rules, return structured
findings.

**Args:**
- `rulesFile` (string, required) — path to business rules doc
- `targetDirs` (array of string, required) — directories to audit
- `auditTypes` (array of string, optional) — e.g., ['security', 'data-privacy', 'accessibility']

**Phases:**
1. **Load** — parse business rules
2. **Audit** — parallel audit per directory
3. **Synthesize** — merge findings, rank by severity

**Budget:** Yes — if >50 directories, trim

**Key code:**
```javascript
phase('Load')
const rules = await agent(`Parse business rules from ${rulesFile}`, {
  schema: {
    type: 'object',
    properties: {
      rules: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            id: { type: 'string' },
            category: { type: 'string' },
            description: { type: 'string' },
            severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] }
          },
          required: ['id', 'category', 'description']
        }
      }
    },
    required: ['rules']
  }
})

phase('Audit')
const findings = await parallel(targetDirs.map(dir =>
  () => agent(`Audit ${dir} against ${rules.rules.length} business rules`, {
    schema: {
      type: 'object',
      properties: {
        findings: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              ruleId: { type: 'string' },
              file: { type: 'string' },
              line: { type: 'number' },
              severity: { type: 'string' },
              message: { type: 'string' }
            },
            required: ['ruleId', 'file', 'message']
          }
        }
      },
      required: ['findings']
    },
    phase: 'Audit'
  })
))

phase('Synthesize')
const allFindings = findings.flatMap(f => f.findings)
const ranked = allFindings.sort((a, b) => {
  const severityOrder = {critical: 0, high: 1, medium: 2, low: 3}
  return severityOrder[a.severity] - severityOrder[b.severity]
})

log(`Found ${ranked.length} compliance issues`)
return { findings: ranked }
```

---

### Template 5: `adversarial-implementation.js`

**Purpose:** Implement feature → security review → business logic review → fix
issues (adversarial quality loop).

**Args:**
- `specFile` (string, required) — feature spec
- `targetDir` (string, required) — where to implement
- `businessRulesFile` (string, optional) — business rules to verify against

**Phases:**
1. **Implement** — generate implementation from spec
2. **Security Review** — independent security audit
3. **Logic Review** — verify against business rules
4. **Fix** — parallel fix all issues found

**Budget:** Yes — adjust review depth based on budget

**Isolation:** Yes — each fix in separate worktree

**Key code:**
```javascript
phase('Implement')
const impl = await agent(`Implement feature from ${specFile} in ${targetDir}`, {
  schema: {
    type: 'object',
    properties: {
      filesWritten: { type: 'array', items: { type: 'string' } },
      summary: { type: 'string' }
    },
    required: ['filesWritten', 'summary']
  }
})

phase('Security Review')
const securityIssues = await agent(
  `Security audit of implementation in ${targetDir}: check auth, injection, secrets, crypto`,
  {
    schema: {
      type: 'object',
      properties: {
        issues: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              file: { type: 'string' },
              line: { type: 'number' },
              category: { type: 'string' },
              severity: { type: 'string' },
              description: { type: 'string' },
              recommendation: { type: 'string' }
            },
            required: ['file', 'category', 'severity', 'description']
          }
        }
      },
      required: ['issues']
    },
    effort: 'high'
  }
)

phase('Logic Review')
const logicIssues = await agent(
  `Verify implementation matches business rules in ${businessRulesFile || specFile}`,
  {
    schema: {
      type: 'object',
      properties: {
        issues: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              file: { type: 'string' },
              ruleViolated: { type: 'string' },
              description: { type: 'string' }
            },
            required: ['file', 'description']
          }
        }
      },
      required: ['issues']
    },
    effort: 'high'
  }
)

const allIssues = [...securityIssues.issues, ...logicIssues.issues]

if (allIssues.length === 0) {
  log('No issues found, implementation passes adversarial review')
  return { status: 'passed' }
}

log(`Found ${allIssues.length} issues, fixing...`)

phase('Fix')
await parallel(allIssues.map(issue =>
  () => agent(`Fix issue in ${issue.file}: ${issue.description}`, {
    isolation: 'worktree',
    effort: 'medium',
    phase: 'Fix'
  })
))

return { status: 'fixed', issuesFixed: allIssues.length }
```

---

### Verify (Task 3.1)

```bash
ls .claude/workflows/templates/
# Expect: 5 .js files

ls .kilo/workflows/templates/
# Expect: 5 .js files (mirrors)

# Syntax check (each file must be valid JS with export const meta)
node -e "require('./.claude/workflows/templates/crud-module.js')" 2>&1
# Repeat for all 5

python tools/garden.py 2>&1 | grep -E "Total drift|lock_stale"
# Expect: lock_stale drift only (fixed at end)
```

---

## Task 3.2 — Expand skill with template examples

### Files allowed to edit

- `.claude/skills/dynamic-workflow-orchestration/SKILL.md`
- `.kilo/skill/dynamic-workflow-orchestration/SKILL.md` (mirror, source of truth)
- `.copilot/skill/dynamic-workflow-orchestration/SKILL.md` (mirror)
- `.gemini/antigravity/skills/dynamic-workflow-orchestration/SKILL.md` (mirror)

### Spec

Replace the **"Three patterns"** section in the skill with **"Five template
patterns"**. Each pattern shows:
1. When to use
2. How to invoke
3. Key primitives used
4. Expected outcome

**New section content:**

```markdown
## Five template patterns

All templates live in `.claude/workflows/templates/` and are invoked with
`workflow({scriptPath: '...'}, args)`.

### 1. CRUD module generation

**When:** Implementing entity with full CRUD operations (model, repo, service,
API, tests).

**Invoke:**
```javascript
await workflow(
  {scriptPath: '.claude/workflows/templates/crud-module.js'},
  {
    entityName: 'User',
    entitySchema: {
      fields: [
        {name: 'id', type: 'uuid', required: true},
        {name: 'email', type: 'string', unique: true},
        {name: 'role', type: 'enum', values: ['admin', 'user']}
      ],
      relationships: [
        {type: 'hasMany', target: 'Order'}
      ]
    }
  }
)
```

**Primitives:** `parallel()` (5 components at once), `phase()`, schema enforcement

**Outcome:** 5 files written (model, repo, service, API, tests), linter-verified

---

### 2. API from OpenAPI spec

**When:** Implementing REST API from existing Swagger/OpenAPI definition.

**Invoke:**
```javascript
await workflow(
  {scriptPath: '.claude/workflows/templates/api-from-spec.js'},
  {
    specFile: 'docs/api-spec.yaml',
    outputDir: 'src/api/v2/'
  }
)
```

**Primitives:** `pipeline()` (endpoint → route → handler → validation → test,
no barrier), budget-aware trimming

**Outcome:** N endpoints implemented with tests, contract-validated

---

### 3. Data migration

**When:** Transform 30-200 data files (CSV, JSON) to new schema.

**Invoke:**
```javascript
await workflow(
  {scriptPath: '.claude/workflows/templates/data-migration.js'},
  {
    sourcePattern: 'data/legacy/*.csv',
    targetSchema: {type: 'object', properties: {...}},
    outputDir: 'data/v2/'
  }
)
```

**Primitives:** `pipeline()` + `isolation: 'worktree'` (each file transforms
independently), schema validation

**Outcome:** N files transformed, validated against new schema

---

### 4. Compliance audit

**When:** Audit codebase/docs/configs against business rules, generate findings
report.

**Invoke:**
```javascript
const result = await workflow(
  {scriptPath: '.claude/workflows/templates/compliance-audit.js'},
  {
    rulesFile: 'docs/compliance-rules.md',
    targetDirs: ['src/', 'config/', 'docs/'],
    auditTypes: ['security', 'data-privacy']
  }
)
// result.findings = [{ruleId, file, line, severity, message}, ...]
```

**Primitives:** `parallel()` (audit directories concurrently), structured
findings schema

**Outcome:** Ranked list of compliance violations

---

### 5. Adversarial implementation

**When:** Implement feature with built-in quality loop (security + logic review
+ auto-fix).

**Invoke:**
```javascript
const result = await workflow(
  {scriptPath: '.claude/workflows/templates/adversarial-implementation.js'},
  {
    specFile: 'specs/payment-flow.md',
    targetDir: 'src/payments/',
    businessRulesFile: 'docs/payment-rules.md'
  }
)
// result.status = 'passed' | 'fixed'
// result.issuesFixed = number
```

**Primitives:** `parallel()` (concurrent security + logic review),
`isolation: 'worktree'` (parallel fixes), `effort: 'high'` (deep review)

**Outcome:** Implementation verified by 2 independent reviewers, issues
auto-fixed

---

## When to use templates vs inline scripts

**Use templates:**
- Pattern repeats >3 times across projects
- Need budget control, adversarial review, or isolation
- Structured output critical (API contracts, compliance reports)

**Use inline scripts:**
- One-off task specific to current codebase
- Quick prototype (later extract to template if useful)

**Use CLI delegation instead:**
- Task needs different model (Antigravity/Gemini for second opinion)
- Context exceeds Claude window (>200 files, large docs)
- UI verification (rendered output, screenshots)
```

**Mirror this new section** to all 4 skill files (`.claude/`, `.kilo/`,
`.copilot/`, `.gemini/`).

### Verify (Task 3.2)

```bash
grep -n "Five template patterns" \
  .claude/skills/dynamic-workflow-orchestration/SKILL.md \
  .kilo/skill/dynamic-workflow-orchestration/SKILL.md
# Expect: line numbers in both files

wc -l .claude/skills/dynamic-workflow-orchestration/SKILL.md
# Expect: ~150-200 lines (expanded from ~80)

python tools/validate_schemas.py 2>&1 | tail -3
# Expect: Files checked: 70 / Errors: 0

python tools/garden.py 2>&1 | grep -E "Skill content drift"
# Expect: [OK] Skill content drift (.copilot, .gemini)
```

---

## Task 3.3 — Expand routing table with concrete examples

### Files allowed to edit

- `CLAUDE.md` (generated — edit template in `tools/claude_engine.py`)
- `AGENTS.md` (manual)
- `.kilo/instruction/custom-framework-rules.md` (if it has routing table)

### Spec

The routing table currently has 1 generic row for "Broad audit/research":

```
| Broad audit/research fitting Claude's context | **Native Workflow tool** | In-process subagents, structured output, no CLI round-trip |
```

**Replace** this row with **5 concrete rows** (one per template):

```markdown
| Generate CRUD for 10-30 entities | **Native Workflow** (`crud-module.js`) | Parallel generation, schema-enforced. Batch if >10 entities. CLI if different models needed. |
| Implement API from OpenAPI spec (20-50 endpoints) | **Native Workflow** (`api-from-spec.js`) | Pipeline per endpoint, budget-aware. CLI if >100 endpoints or context exceeds Claude window. |
| Migrate 30-200 data files to new schema | **Native Workflow** (`data-migration.js`) | Pipeline with worktree isolation, validation. CLI if >500 files. |
| Audit code/docs for compliance (5-50 directories) | **Native Workflow** (`compliance-audit.js`) | Parallel audit, structured findings. CLI (Antigravity) if >100 directories or multi-repo. |
| Implement feature with adversarial review | **Native Workflow** (`adversarial-implementation.js`) | Security + logic review loops, auto-fix. CLI if need different model perspective. |
```

**Add threshold guidance paragraph** after the table:

```markdown
**Workflow vs CLI thresholds:**
- **Workflow:** <200 files/endpoints, fits Claude context (~1M tokens), need
  structured output or adversarial loops
- **CLI delegation:** >200 items, exceeds context, need cross-engine perspective
  (Gemini breadth, DeepSeek free for mechanical), or UI verification
- **Both:** Start Workflow for core work, delegate verification to CLI with
  different model
```

**In CLAUDE.md:** Edit the `_CLAUDE_MD_TEMPLATE` in `tools/claude_engine.py`
around line 450-500 (search for `## Choosing a worker engine`). Update the
routing table, then regenerate with
`python tools/generate_harness.py --harness claude`.

**In AGENTS.md:** Edit directly (manual file, not generated). Find the routing
table around line 262-271, replace the generic row with 5 concrete rows + add
threshold paragraph.

**In `.kilo/instruction/custom-framework-rules.md`:** Check if file has a
routing table. If yes, update it. If no, skip (already confirmed in Phase 2
brief that this file has no table).

### Verify (Task 3.3)

```bash
grep -n "crud-module.js\|api-from-spec.js" CLAUDE.md AGENTS.md
# Expect: line numbers in both files (5 template names)

grep -n "Workflow vs CLI thresholds" CLAUDE.md AGENTS.md
# Expect: line numbers in both files

python tools/validate_schemas.py 2>&1 | tail -3
# Expect: Files checked: 70 / Errors: 0

python tools/garden.py 2>&1 | tail -3
# Expect: lock_stale drift only
```

---

## Task 3.4 — Add workflow invocation examples to CLAUDE.md

### Files allowed to edit

- `CLAUDE.md` (via template in `tools/claude_engine.py`)
- `AGENTS.md` (manual)

### Spec

Add a new section **"Using workflow templates"** after the routing table (around
line 180 in CLAUDE.md, line 280 in AGENTS.md).

**Content:**

```markdown
## Using workflow templates

Workflow templates are reusable orchestration scripts in
`.claude/workflows/templates/`. Invoke with the `Workflow` tool:

```javascript
await workflow(
  {scriptPath: '.claude/workflows/templates/<name>.js'},
  {
    // args object, specific to each template
  }
)
```

**Example 1: Generate CRUD module**
```javascript
await workflow(
  {scriptPath: '.claude/workflows/templates/crud-module.js'},
  {
    entityName: 'Product',
    entitySchema: {
      fields: [
        {name: 'id', type: 'uuid', required: true},
        {name: 'name', type: 'string', maxLength: 255},
        {name: 'price', type: 'decimal', precision: [10, 2]},
        {name: 'stock', type: 'integer', default: 0}
      ],
      relationships: [
        {type: 'belongsTo', target: 'Category'},
        {type: 'hasMany', target: 'OrderItem'}
      ]
    },
    outputDir: 'src/modules/product/'
  }
)
```

**Example 2: Compliance audit with budget**
```javascript
// User prompt: "+200k audit payment module for PCI-DSS compliance"
// budget.total = 200_000

const result = await workflow(
  {scriptPath: '.claude/workflows/templates/compliance-audit.js'},
  {
    rulesFile: 'docs/pci-dss-requirements.md',
    targetDirs: ['src/payments/', 'config/payment-gateways/', 'docs/payments/'],
    auditTypes: ['security', 'data-privacy', 'logging']
  }
)

log(`Found ${result.findings.length} PCI-DSS violations`)
// Template auto-trims scope if budget insufficient
```

**Example 3: Adversarial implementation**
```javascript
await workflow(
  {scriptPath: '.claude/workflows/templates/adversarial-implementation.js'},
  {
    specFile: 'specs/user-authentication.md',
    targetDir: 'src/auth/',
    businessRulesFile: 'docs/security-policy.md'
  }
)
// Returns {status: 'passed'} or {status: 'fixed', issuesFixed: N}
```

**Budget control:** Templates respect `budget.total` from `+500k` directive.
They auto-scale (trim items, reduce depth) when budget insufficient. Track
spending with `budget.spent()` after workflow completes.

**Template library:** See
`.claude/skills/dynamic-workflow-orchestration/SKILL.md` for all 5 templates
and their args.
```

**In CLAUDE.md:** Add to `_CLAUDE_MD_TEMPLATE` in `tools/claude_engine.py`,
then regenerate.

**In AGENTS.md:** Add directly (manual file).

### Verify (Task 3.4)

```bash
grep -n "Using workflow templates" CLAUDE.md AGENTS.md
# Expect: line numbers in both files

grep -n "budget.total\|budget.spent" CLAUDE.md AGENTS.md
# Expect: multiple matches (examples reference budget)

wc -l CLAUDE.md
# Expect: ~50-70 lines more than before (examples added)
```

---

## End-of-phase cleanup

After all 4 tasks done:

```bash
# Regenerate .harness.lock
python tools/deploy.py --root-lock

# Run all 6 gates
ruff check .
python tools/check_lint_budget.py
python tools/validate_schemas.py
python tools/garden.py
python -m pytest tools/test_deploy.py tools/test_garden.py -q
python .github/scripts/security_scan.py .
```

**Expected after `--root-lock`:**
- `garden.py`: `Total drift issues: 0`
- All tests pass

**Red gates that should NOT appear:**
- Skill parity drift (all 4 engines mirrored)
- Instruction drift
- Any garden drift other than `lock_stale` pre-`--root-lock`

---

## Final report format

Return a markdown report with:

1. **Per-task status table**
   | Task | Status | Files created/edited |
   |------|--------|---------------------|

2. **Claim / Command / Output table**
   | Claim | Command run | Output |
   |-------|-------------|--------|

3. **Template verification**
   - Each of 5 templates: syntax valid (node check), meta block present
   - Mirrored to .kilo/ (file hash match or diff stat)

4. **Skill expansion verification**
   - Line count before/after
   - "Five template patterns" section present in all 4 engines

5. **Routing table verification**
   - 5 template names present in CLAUDE.md + AGENTS.md
   - Threshold guidance paragraph present

6. **Gate results**
   - All 6 commands + output tails

7. **Red gates hit and resolution**
   - `lock_stale` — fixed with `--root-lock`
   - Any others — how resolved

8. **Unexpected findings**
   - Line number shifts, file missing, generator issues

---

## Done when

- 5 workflow templates created (.claude/ + .kilo/ mirrors)
- Skill expanded with template examples (all 4 engines)
- Routing table has 5 concrete rows (CLAUDE.md + AGENTS.md)
- New section "Using workflow templates" added (CLAUDE.md + AGENTS.md)
- All 6 gates green
- `garden.py` drift = 0
- Skill count still 54 (no new skills, only templates)
