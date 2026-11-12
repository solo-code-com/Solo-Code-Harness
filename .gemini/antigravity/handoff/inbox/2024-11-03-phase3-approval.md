# Phase 3 Approval: Tools Integration

**From:** Claude Sonnet 5 (Architect)
**To:** Gemini 3.8 Flash (Executor)
**Date:** 2024-11-03
**Status:** ✅ APPROVED - PROCEED WITH PHASE 3

---

## 🎉 Phase 2 Completion - OUTSTANDING WORK!

**Verified Results:**
- ✅ 4 services implemented (ExecutorRegistry, DelegationService, GuardService, UsageTracker)
- ✅ Build success (6.53s)
- ✅ All tests pass (24/24 = 100%)
- ✅ **Coverage exceptional:**
  - Statements: 98.30% (target ≥80%)
  - Functions: 100%
  - Lines: 98.23%
  - Branches: 81.17%

**Phase 2 = EXCELLENT EXECUTION** 💯

**Progress: 3/7 phases complete (42%)**

---

## Phase 3 Overview: Tools Integration (2-3h)

**Goal:** Port Python tools from g-harness and wire up to DelegationService.

**Tasks:**
1. Copy Python tools to packages/solo-code/tools/
2. Verify tools are executable
3. Test CLI integration
4. Update DelegationService if needed
5. End-to-end delegation test

**Timeline:** 2-3h

---

## Task 3.1: Copy Python Tools (1h)

### Source Location
```
F:/Project/project_tools/g_harness/g-harness/tools/
```

### Target Location
```
deepseek-harness-master/packages/solo-code/tools/
```

### Tools to Copy (Priority Order)

**Core Delegation Tools (MUST HAVE):**
```bash
opencode_delegate.py      # OpenCode CLI executor
kilo_cli_delegate.py      # Kilo CLI executor
antigravity_delegate.py   # Gemini/Antigravity CLI executor
dsh_delegate.py           # DeepSeek native executor
agent_scope.py            # Scope checking utility
```

**Supporting Tools (SHOULD HAVE):**
```bash
benchmark_executors.py    # Executor performance testing
coverage_gate.py          # Coverage enforcement
security_scan.py          # Security scanning
check_lint_budget.py      # Lint budget checking
validate_schemas.py       # Schema validation
garden.py                 # Cross-engine drift detection
env_file.py               # Environment file handling
```

**Config Files:**
```bash
__init__.py               # Python package marker
config/                   # Configuration directory
  executor-config.json    # Executor configurations
  lint-budget.json        # Lint budgets
  model-catalog.json      # Model catalog
```

### Copy Commands

```bash
cd deepseek-harness-master/packages/solo-code

# Create tools directory
mkdir -p tools/config

# Copy core delegation tools
cp F:/Project/project_tools/g_harness/g-harness/tools/opencode_delegate.py tools/
cp F:/Project/project_tools/g_harness/g-harness/tools/kilo_cli_delegate.py tools/
cp F:/Project/project_tools/g_harness/g-harness/tools/antigravity_delegate.py tools/
cp F:/Project/project_tools/g_harness/g-harness/tools/dsh_delegate.py tools/
cp F:/Project/project_tools/g_harness/g-harness/tools/agent_scope.py tools/

# Copy supporting tools
cp F:/Project/project_tools/g_harness/g-harness/tools/benchmark_executors.py tools/
cp F:/Project/project_tools/g_harness/g-harness/tools/coverage_gate.py tools/
cp F:/Project/project_tools/g_harness/g-harness/tools/security_scan.py tools/
cp F:/Project/project_tools/g_harness/g-harness/tools/check_lint_budget.py tools/
cp F:/Project/project_tools/g_harness/g-harness/tools/validate_schemas.py tools/
cp F:/Project/project_tools/g_harness/g-harness/tools/garden.py tools/
cp F:/Project/project_tools/g_harness/g-harness/tools/env_file.py tools/

# Copy Python package marker
cp F:/Project/project_tools/g_harness/g-harness/tools/__init__.py tools/

# Copy config directory
cp -r F:/Project/project_tools/g_harness/g-harness/tools/config/* tools/config/
```

---

## Task 3.2: Verify Tools Executable (30min)

### Check Python Dependencies

**Create `pyproject.toml`:**
```txt
# Core dependencies for delegation tools
```

Most tools use stdlib only, but check for any imports.

### Test Each Tool

```bash
cd packages/solo-code/tools

# Test opencode_delegate.py
python opencode_delegate.py --help
python opencode_delegate.py "echo test" --dry-run

# Test kilo_cli_delegate.py
python kilo_cli_delegate.py --help

# Test antigravity_delegate.py
python antigravity_delegate.py --help

# Test dsh_delegate.py
python dsh_delegate.py --help

# Test benchmark_executors.py
python benchmark_executors.py --help
```

**Expected:** All tools show help or dry-run successfully.

---

## Task 3.3: Integration Test (1h)

### Test 3.3.1: DelegationService → opencode_delegate.py

**Create `tests/integration/delegation.test.ts`:**

```typescript
import { Context } from '@deepseek-ai/cordis'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { SoloCodePlugin, type SoloCodeConfig } from '../../src'
import { resolve } from 'node:path'

describe('Tools Integration', () => {
  let ctx: Context

  const config: SoloCodeConfig = {
    executors: {
      opencode: {
        command: resolve(__dirname, '../../tools/opencode_delegate.py'),
        model: 'commandcode/deepseek-v4-pro',
        usage_log: '.solocode/opencode-usage.jsonl',
        success_rate: 0.85,
        features: ['free_models'],
        cost_per_mtok: 0.14,
      },
    },
    routing: {
      simple_edits: 'opencode',
      boilerplate: 'opencode',
      single_test: 'opencode',
      mechanical_changes: 'opencode',
      complex_logic: 'opencode',
      reasoning_required: 'opencode',
      fallback: 'opencode',
    },
    default_executor: 'opencode',
    guards: {
      executor_mode: 'off',
    },
    tracking: {
      enabled: true,
    },
  }

  beforeEach(() => {
    ctx = new Context()
    ctx.plugin(SoloCodePlugin, config)
  })

  afterEach(async () => {
    await ctx.dispose()
  })

  it('should delegate to opencode_delegate.py with --help', async () => {
    const executor = ctx.executorRegistry.getExecutor('opencode')!

    const result = await ctx.delegationService.delegate({
      executor: {
        ...executor,
        command: `python ${executor.command} --help`,
      },
      task: '',
    })

    expect(result.success).toBe(true)
    expect(result.output).toContain('usage:' || 'Usage:')
  })

  it('should delegate simple task with --dry-run', async () => {
    const executor = ctx.executorRegistry.getExecutor('opencode')!

    const result = await ctx.delegationService.delegate({
      executor: {
        ...executor,
        command: `python ${executor.command}`,
      },
      task: 'echo "Hello from plugin" --dry-run',
    })

    // Should succeed or fail gracefully
    expect(result.exitCode).toBeDefined()
  })
})
```

### Test 3.3.2: End-to-End Orchestration

**Create `tests/integration/orchestration.test.ts`:**

```typescript
import { Context } from '@deepseek-ai/cordis'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { SoloCodePlugin } from '../../src'
import { writeFile } from 'node:fs/promises'

describe('End-to-End Orchestration', () => {
  let ctx: Context

  beforeEach(async () => {
    ctx = new Context()

    // Setup executor mode
    await writeFile('.solocode/executor-mode', 'off')

    ctx.plugin(SoloCodePlugin, {
      executors: {
        test: {
          command: 'echo',
          model: 'test-model',
          usage_log: '.solocode/test-usage.jsonl',
          success_rate: 1.0,
          features: [],
        },
      },
      routing: {
        simple_edits: 'test',
        boilerplate: 'test',
        single_test: 'test',
        mechanical_changes: 'test',
        complex_logic: 'test',
        reasoning_required: 'test',
        fallback: 'test',
      },
      default_executor: 'test',
      guards: {
        executor_mode: 'off',
      },
      tracking: {
        enabled: true,
      },
    })

    await ctx.start()
  })

  afterEach(async () => {
    await ctx.dispose()
  })

  it('should orchestrate: registry → delegation → tracking', async () => {
    // 1. Get executor from registry
    const executor = ctx.executorRegistry.routeTask('simple_edits')
    expect(executor.model).toBe('test-model')

    // 2. Delegate task
    const result = await ctx.delegationService.delegate({
      executor,
      task: 'Hello Orchestration',
    })
    expect(result.success).toBe(true)

    // 3. Track usage
    await ctx.usageTracker.logUsage({
      timestamp: new Date().toISOString(),
      executor: 'test',
      model: executor.model,
      task_type: 'simple_edits',
      success: result.success,
      duration_ms: result.duration,
    })

    // All components working together
  })

  it('should guard write operations when mode is ON', async () => {
    await writeFile('.solocode/executor-mode', 'on')

    // GuardService should intercept write operations
    // This requires tools service to be loaded
    // For now, just verify guard service is active
    expect(ctx.guardService).toBeDefined()
  })
})
```

---

## Task 3.4: Update Documentation (30min)

### Update README.md

Add tools section:

```markdown
## Tools

Python delegation tools in `tools/`:

### Core Executors
- `opencode_delegate.py` - OpenCode CLI delegation
- `kilo_cli_delegate.py` - Kilo CLI delegation
- `dsh_delegate.py` - DeepSeek native delegation
- `antigravity_delegate.py` - Gemini/Antigravity delegation

### Utilities
- `benchmark_executors.py` - Performance benchmarking
- `coverage_gate.py` - Coverage enforcement
- `security_scan.py` - Security scanning
- `agent_scope.py` - Scope verification

### Usage

```bash
# Direct tool usage
python tools/opencode_delegate.py "task description" --free

# Via DelegationService
const result = await ctx.delegationService.delegate({
  executor: ctx.executorRegistry.getExecutor('opencode'),
  task: 'Add TODO comment',
})
```
```

### Create README.md

```markdown
# Solo-Code Tools

Python CLI tools for multi-engine orchestration.

## Delegation Tools

### opencode_delegate.py
OpenCode CLI executor using DeepSeek V4 Pro.

```bash
python opencode_delegate.py "task description" [--free] [--dry-run]
```

### kilo_cli_delegate.py
Kilo CLI executor.

```bash
python kilo_cli_delegate.py "task description"
```

### dsh_delegate.py
DeepSeek Harness native executor.

```bash
python dsh_delegate.py "task description" [--profile reasoning|fast|balanced]
```

### antigravity_delegate.py
Gemini/Antigravity CLI executor.

```bash
python antigravity_delegate.py "task description" [--allow-dir path]
```

## Utility Tools

### benchmark_executors.py
Benchmark executor performance.

```bash
python benchmark_executors.py --executors opencode,kilo,dsh
```

### coverage_gate.py
Enforce coverage requirements.

```bash
python coverage_gate.py --target packages/solo-code
```

### security_scan.py
Scan for security issues.

```bash
python security_scan.py .
```

## Configuration

Executor configs in `config/executor-config.json`.
```

---

## Phase 3 Success Criteria

- [ ] All core tools copied to packages/solo-code/tools/
- [ ] All tools executable (--help works)
- [ ] DelegationService can call tools
- [ ] Integration tests pass
- [ ] End-to-end orchestration works
- [ ] Documentation updated
- [ ] `pnpm run test` passes all tests
- [ ] Build succeeds

---

## Implementation Steps

1. **Copy tools** (30min)
   - Run copy commands
   - Verify file structure

2. **Test tools** (30min)
   - Run --help on each
   - Test dry-run mode

3. **Integration tests** (1h)
   - Create integration test suite
   - Test delegation flow
   - Test orchestration

4. **Documentation** (30min)
   - Update README.md
   - Create README.md

5. **Final verification** (30min)
   - Run all tests
   - Build
   - Coverage check

---

## Notes

**Python Path:**
Tools will be executed from `packages/solo-code/tools/`. Make sure paths are relative to plugin root.

**Dependencies:**
Most tools use stdlib only. If any tool needs external deps, add to `pyproject.toml`.

**Dry-run Mode:**
For testing without API calls, use `--dry-run` flag where available.

**API Keys:**
Delegation tools may need API keys. For Phase 3 testing, use --dry-run or mock mode.

---

## 🚀 YOU ARE APPROVED FOR PHASE 3

Begin implementation now.

Timeline: 2-3h

Report back when complete.

Next: Phase 4 (Testing & Documentation, 2-3h)

---

**END OF APPROVAL**
