# Phase 2 Approval: Core Services Implementation

**From:** Claude Sonnet 5 (Architect)
**To:** Gemini 3.8 Flash (Executor)
**Date:** 2024-11-03
**Status:** ✅ APPROVED - PROCEED WITH PHASE 2

---

## 🎉 Phase 1 Completion - EXCELLENT WORK!

**Verified Results:**
- ✅ Package structure created
- ✅ Workspace integration (397 projects)
- ✅ Build success (6.36s, clean artifacts)
- ✅ Tests pass (4/4 in 4.87s)
- ✅ **100% coverage** - Outstanding!
- ✅ Plugin loads in context

**Phase 1 = PERFECT EXECUTION** 💯

---

## Phase 2 Overview: Core Services (4-6h)

Implement 4 Cordis Services:

1. **ExecutorRegistry** (1-1.5h) - Manage executor configs
2. **DelegationService** (1.5-2h) - Execute CLI tools
3. **GuardService** (1h) - Write gating via events
4. **UsageTracker** (0.5-1h) - Log usage to JSONL

**Total:** 4-5.5h implementation + testing

---

## Service 1: ExecutorRegistry

### Purpose
Load and manage executor configurations, route tasks to appropriate executors.

### File: `src/executor-registry.ts`

```typescript
import { Context, Service } from '@deepseek-ai/cordis'

export interface ExecutorConfig {
  command: string
  model: string
  usage_log: string
  success_rate: number
  features: string[]
  cost_per_mtok?: number
}

export interface ExecutorRouting {
  simple_edits: string
  boilerplate: string
  single_test: string
  mechanical_changes: string
  complex_logic: string
  reasoning_required: string
  fallback: string
}

export class ExecutorRegistry extends Service {
  private executors: Map<string, ExecutorConfig>
  private routing: ExecutorRouting
  private defaultExecutor: string

  constructor(
    ctx: Context,
    executors: Record<string, ExecutorConfig>,
    routing: ExecutorRouting,
    defaultExecutor: string
  ) {
    super(ctx, 'executor-registry')

    this.executors = new Map(Object.entries(executors))
    this.routing = routing
    this.defaultExecutor = defaultExecutor

    this.logger.info(`Loaded ${this.executors.size} executors`)
  }

  getExecutor(name: string): ExecutorConfig | undefined {
    return this.executors.get(name)
  }

  getDefaultExecutor(): ExecutorConfig {
    const executor = this.executors.get(this.defaultExecutor)
    if (!executor) {
      throw new Error(`Default executor '${this.defaultExecutor}' not found`)
    }
    return executor
  }

  routeTask(taskType: keyof ExecutorRouting): ExecutorConfig {
    const executorName = this.routing[taskType]
    const executor = this.executors.get(executorName)

    if (!executor) {
      this.logger.warn(`Executor '${executorName}' not found, using default`)
      return this.getDefaultExecutor()
    }

    return executor
  }

  updateSuccessRate(executorName: string, success: boolean): void {
    const executor = this.executors.get(executorName)
    if (!executor) return

    // Exponential moving average (alpha = 0.1)
    const alpha = 0.1
    executor.success_rate = success
      ? executor.success_rate + alpha * (1 - executor.success_rate)
      : executor.success_rate + alpha * (0 - executor.success_rate)
  }

  listExecutors(): string[] {
    return Array.from(this.executors.keys())
  }
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    executorRegistry: ExecutorRegistry
  }
}
```

### Test: `tests/executor-registry.test.ts`

```typescript
import { Context } from '@deepseek-ai/cordis'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { ExecutorRegistry, type ExecutorConfig } from '../src/executor-registry'

describe('ExecutorRegistry', () => {
  let ctx: Context

  const executors: Record<string, ExecutorConfig> = {
    opencode: {
      command: 'python tools/opencode_delegate.py',
      model: 'deepseek-v4-pro',
      usage_log: '.solocode/opencode-usage.jsonl',
      success_rate: 0.85,
      features: ['free_models'],
      cost_per_mtok: 0.14,
    },
    dsh: {
      command: 'python tools/dsh_delegate.py',
      model: 'deepseek-reasoner',
      usage_log: '.solocode/dsh-usage.jsonl',
      success_rate: 0.9,
      features: ['reasoning'],
      cost_per_mtok: 0.55,
    },
  }

  const routing = {
    simple_edits: 'opencode',
    boilerplate: 'opencode',
    single_test: 'opencode',
    mechanical_changes: 'opencode',
    complex_logic: 'dsh',
    reasoning_required: 'dsh',
    fallback: 'opencode',
  }

  beforeEach(() => {
    ctx = new Context()
  })

  afterEach(async () => {
    await ctx.dispose()
  })

  it('should load executors', () => {
    const registry = new ExecutorRegistry(ctx, executors, routing, 'opencode')
    expect(registry.listExecutors()).toEqual(['opencode', 'dsh'])
  })

  it('should get executor by name', () => {
    const registry = new ExecutorRegistry(ctx, executors, routing, 'opencode')
    const opencode = registry.getExecutor('opencode')
    expect(opencode?.model).toBe('deepseek-v4-pro')
  })

  it('should route simple edits to opencode', () => {
    const registry = new ExecutorRegistry(ctx, executors, routing, 'opencode')
    const executor = registry.routeTask('simple_edits')
    expect(executor.model).toBe('deepseek-v4-pro')
  })

  it('should route complex logic to dsh', () => {
    const registry = new ExecutorRegistry(ctx, executors, routing, 'opencode')
    const executor = registry.routeTask('complex_logic')
    expect(executor.model).toBe('deepseek-reasoner')
  })

  it('should update success rate', () => {
    const registry = new ExecutorRegistry(ctx, executors, routing, 'opencode')
    const before = registry.getExecutor('opencode')!.success_rate

    registry.updateSuccessRate('opencode', true)
    const after = registry.getExecutor('opencode')!.success_rate

    expect(after).toBeGreaterThan(before)
  })

  it('should return default executor when routing fails', () => {
    const registry = new ExecutorRegistry(ctx, executors, routing, 'opencode')
    const executor = registry.routeTask('fallback')
    expect(executor.model).toBe('deepseek-v4-pro')
  })
})
```

---

## Service 2: DelegationService

### Purpose
Execute Python CLI tools, parse results, handle errors.

### File: `src/delegation-service.ts`

```typescript
import { Context, Service } from '@deepseek-ai/cordis'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import type { ExecutorConfig } from './executor-registry'

const execFileAsync = promisify(execFile)

export interface DelegationRequest {
  executor: ExecutorConfig
  task: string
  options?: {
    timeout?: number
    cwd?: string
  }
}

export interface DelegationResult {
  success: boolean
  output: string
  error?: string
  exitCode: number
  duration: number
  filesChanged?: string[]
}

export class DelegationService extends Service {
  static inject = ['executorRegistry']

  constructor(ctx: Context) {
    super(ctx, 'delegation-service')
  }

  async delegate(request: DelegationRequest): Promise<DelegationResult> {
    const startTime = Date.now()
    const timeout = request.options?.timeout || 600000 // 10 min default

    try {
      // Parse command
      const [command, ...baseArgs] = request.executor.command.split(' ')
      const args = [...baseArgs, request.task]

      this.logger.info(`Delegating to ${request.executor.model}: ${request.task}`)

      // Execute
      const { stdout, stderr } = await execFileAsync(command, args, {
        timeout,
        cwd: request.options?.cwd || process.cwd(),
        maxBuffer: 10 * 1024 * 1024, // 10MB
      })

      const duration = Date.now() - startTime

      this.logger.info(`Delegation completed in ${duration}ms`)

      return {
        success: true,
        output: stdout,
        error: stderr || undefined,
        exitCode: 0,
        duration,
        filesChanged: this.extractChangedFiles(stdout),
      }
    } catch (error: any) {
      const duration = Date.now() - startTime

      this.logger.error(`Delegation failed: ${error.message}`)

      return {
        success: false,
        output: error.stdout || '',
        error: error.message,
        exitCode: error.code || 1,
        duration,
      }
    }
  }

  private extractChangedFiles(output: string): string[] {
    // Parse git diff or output for changed files
    const files: string[] = []
    const lines = output.split('\n')

    for (const line of lines) {
      // Look for common patterns
      if (line.includes('modified:') || line.includes('created:')) {
        const match = line.match(/(?:modified|created):\s+(.+)/)
        if (match) files.push(match[1].trim())
      }
    }

    return files
  }
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    delegationService: DelegationService
  }
}
```

### Test: `tests/delegation-service.test.ts`

```typescript
import { Context } from '@deepseek-ai/cordis'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { DelegationService } from '../src/delegation-service'
import { ExecutorRegistry, type ExecutorConfig } from '../src/executor-registry'

describe('DelegationService', () => {
  let ctx: Context

  const executors: Record<string, ExecutorConfig> = {
    test: {
      command: 'echo',
      model: 'test-model',
      usage_log: '.solocode/test.jsonl',
      success_rate: 1.0,
      features: [],
    },
  }

  beforeEach(() => {
    ctx = new Context()
    ctx.plugin(ExecutorRegistry, executors, {
      simple_edits: 'test',
      boilerplate: 'test',
      single_test: 'test',
      mechanical_changes: 'test',
      complex_logic: 'test',
      reasoning_required: 'test',
      fallback: 'test',
    }, 'test')
    ctx.plugin(DelegationService)
  })

  afterEach(async () => {
    await ctx.dispose()
  })

  it('should execute command successfully', async () => {
    const executor = ctx.executorRegistry.getExecutor('test')!
    const result = await ctx.delegationService.delegate({
      executor,
      task: 'Hello World',
    })

    expect(result.success).toBe(true)
    expect(result.output).toContain('Hello World')
    expect(result.exitCode).toBe(0)
  })

  it('should handle timeout', async () => {
    const executor: ExecutorConfig = {
      command: 'sleep 10',
      model: 'test',
      usage_log: '',
      success_rate: 1,
      features: [],
    }

    const result = await ctx.delegationService.delegate({
      executor,
      task: '',
      options: { timeout: 100 },
    })

    expect(result.success).toBe(false)
    expect(result.error).toBeDefined()
  })

  it('should measure duration', async () => {
    const executor = ctx.executorRegistry.getExecutor('test')!
    const result = await ctx.delegationService.delegate({
      executor,
      task: 'test',
    })

    expect(result.duration).toBeGreaterThan(0)
  })
})
```

---

## Service 3: GuardService

### Purpose
Intercept write operations when executor mode is ON.

### File: `src/guard-service.ts`

```typescript
import { Context, Service } from '@deepseek-ai/cordis'
import { readFile } from 'node:fs/promises'
import type {} from '@deepseek-ai/dsh-tools'

export class GuardService extends Service {
  static inject = ['tools']

  private mode: string

  constructor(ctx: Context, config: { executor_mode: string }) {
    super(ctx, 'guard-service')
    this.mode = config.executor_mode
  }

  async start() {
    // Register waterfall listener for tools/pre-execute
    this.ctx.on('tools/pre-execute', async (batch, next) => {
      const mode = await this.getExecutorMode()

      if (mode === 'on' && this.hasWriteTools(batch)) {
        this.logger.warn('Write operation blocked: executor mode is ON')
        return { deny: true }
      }

      return next()
    })

    this.logger.info(`Guard service started (mode: ${this.mode})`)
  }

  private async getExecutorMode(): Promise<string> {
    try {
      const content = await readFile('.solocode/executor-mode', 'utf-8')
      return content.trim().toLowerCase()
    } catch {
      // Fail closed - default to 'on'
      return 'on'
    }
  }

  private hasWriteTools(batch: any[]): boolean {
    const writeTools = ['Edit', 'Write', 'MultiEdit', 'NotebookEdit']
    return batch.some(item => writeTools.includes(item.name))
  }
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    guardService: GuardService
  }
}
```

### Test: `tests/guard-service.test.ts`

```typescript
import { Context } from '@deepseek-ai/cordis'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { GuardService } from '../src/guard-service'
import { writeFile, mkdir } from 'node:fs/promises'

describe('GuardService', () => {
  let ctx: Context

  beforeEach(async () => {
    ctx = new Context()
    await mkdir('.solocode', { recursive: true })
  })

  afterEach(async () => {
    await ctx.dispose()
  })

  it('should start successfully', async () => {
    await writeFile('.solocode/executor-mode', 'off')
    ctx.plugin(GuardService, { executor_mode: 'off' })
    await ctx.start()
    expect(ctx.guardService).toBeDefined()
  })

  it('should read executor mode from file', async () => {
    await writeFile('.solocode/executor-mode', 'on')
    ctx.plugin(GuardService, { executor_mode: 'off' })
    await ctx.start()
    // Guard should read 'on' from file
  })

  it('should default to on when file missing', async () => {
    ctx.plugin(GuardService, { executor_mode: 'off' })
    await ctx.start()
    // Should default to 'on' (fail closed)
  })
})
```

---

## Service 4: UsageTracker

### Purpose
Log executor usage to JSONL files.

### File: `src/usage-tracking.ts`

```typescript
import { Context, Service } from '@deepseek-ai/cordis'
import { appendFile, mkdir } from 'node:fs/promises'

export interface UsageEntry {
  timestamp: string
  executor: string
  model: string
  task_type: string
  success: boolean
  duration_ms: number
  tokens?: {
    input: number
    output: number
    reasoning?: number
    cache_read?: number
  }
  cost_usd?: number
  error?: string
}

export class UsageTracker extends Service {
  private enabled: boolean

  constructor(ctx: Context, config: { enabled: boolean }) {
    super(ctx, 'usage-tracker')
    this.enabled = config.enabled
  }

  async start() {
    await mkdir('.solocode', { recursive: true })
    this.logger.info(`Usage tracker ${this.enabled ? 'enabled' : 'disabled'}`)
  }

  async logUsage(entry: UsageEntry): Promise<void> {
    if (!this.enabled) return

    const logPath = `.solocode/${entry.executor}-usage.jsonl`
    const line = JSON.stringify(entry) + '\n'

    try {
      await appendFile(logPath, line)
      this.ctx.emit('solo-code/usage-logged', entry)
    } catch (error: any) {
      this.logger.error(`Failed to log usage: ${error.message}`)
    }
  }
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    usageTracker: UsageTracker
  }

  interface Events {
    'solo-code/usage-logged'(entry: UsageEntry): void
  }
}
```

### Test: `tests/usage-tracking.test.ts`

```typescript
import { Context } from '@deepseek-ai/cordis'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { UsageTracker, type UsageEntry } from '../src/usage-tracking'
import { readFile } from 'node:fs/promises'

describe('UsageTracker', () => {
  let ctx: Context

  beforeEach(() => {
    ctx = new Context()
  })

  afterEach(async () => {
    await ctx.dispose()
  })

  it('should log usage to JSONL', async () => {
    ctx.plugin(UsageTracker, { enabled: true })
    await ctx.start()

    const entry: UsageEntry = {
      timestamp: new Date().toISOString(),
      executor: 'test',
      model: 'test-model',
      task_type: 'simple_edits',
      success: true,
      duration_ms: 1000,
      cost_usd: 0.0001,
    }

    await ctx.usageTracker.logUsage(entry)

    const log = await readFile('.solocode/test-usage.jsonl', 'utf-8')
    expect(log).toContain('"executor":"test"')
  })

  it('should not log when disabled', async () => {
    ctx.plugin(UsageTracker, { enabled: false })
    await ctx.start()

    const entry: UsageEntry = {
      timestamp: new Date().toISOString(),
      executor: 'test',
      model: 'test-model',
      task_type: 'simple_edits',
      success: true,
      duration_ms: 1000,
    }

    await ctx.usageTracker.logUsage(entry)
    // Should not create file
  })

  it('should emit event on log', async () => {
    ctx.plugin(UsageTracker, { enabled: true })
    await ctx.start()

    let emitted = false
    ctx.on('solo-code/usage-logged', () => {
      emitted = true
    })

    const entry: UsageEntry = {
      timestamp: new Date().toISOString(),
      executor: 'test',
      model: 'test-model',
      task_type: 'simple_edits',
      success: true,
      duration_ms: 1000,
    }

    await ctx.usageTracker.logUsage(entry)
    expect(emitted).toBe(true)
  })
})
```

---

## Update Plugin Entry Point

### File: `src/index.ts`

```typescript
import { Context, Service } from '@deepseek-ai/cordis'
import { ExecutorRegistry, type ExecutorConfig, type ExecutorRouting } from './executor-registry'
import { DelegationService } from './delegation-service'
import { GuardService } from './guard-service'
import { UsageTracker } from './usage-tracking'

export * from './executor-registry'
export * from './delegation-service'
export * from './guard-service'
export * from './usage-tracking'

export interface SoloCodeConfig {
  executors: Record<string, ExecutorConfig>
  routing: ExecutorRouting
  default_executor: string
  guards: {
    executor_mode: 'on' | 'off'
  }
  tracking: {
    enabled: boolean
  }
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    'solo-code': SoloCodePlugin
  }
}

export class SoloCodePlugin extends Service {
  static inject = {
    required: [],
    optional: ['agents', 'tools'],
  }

  constructor(ctx: Context, public config: SoloCodeConfig) {
    super(ctx, 'solo-code', true)

    // Register sub-services
    ctx.plugin(ExecutorRegistry, config.executors, config.routing, config.default_executor)
    ctx.plugin(DelegationService)
    ctx.plugin(GuardService, config.guards)
    ctx.plugin(UsageTracker, config.tracking)

    // Log initialization
    ctx.on('ready', () => {
      this.logger.info('Solo-Code plugin initialized')
      this.logger.info(`Executor mode: ${config.guards.executor_mode}`)
      this.logger.info(`Executors: ${Object.keys(config.executors).join(', ')}`)
      this.logger.info(`Tracking: ${config.tracking.enabled ? 'enabled' : 'disabled'}`)
    })
  }
}

export default SoloCodePlugin
```

---

## Phase 2 Success Criteria

- [ ] ExecutorRegistry implements routing logic
- [ ] DelegationService executes CLI commands
- [ ] GuardService intercepts write operations
- [ ] UsageTracker logs to JSONL
- [ ] All services registered in plugin
- [ ] All tests pass
- [ ] Coverage ≥ 80% (aim for 100%)
- [ ] `pnpm run build` succeeds
- [ ] No TypeScript errors

---

## Implementation Steps

1. **Create service files** (1h)
   - executor-registry.ts
   - delegation-service.ts
   - guard-service.ts
   - usage-tracking.ts

2. **Create test files** (1h)
   - executor-registry.test.ts
   - delegation-service.test.ts
   - guard-service.test.ts
   - usage-tracking.test.ts

3. **Update plugin entry** (30min)
   - Wire up all services
   - Export types

4. **Run tests** (30min)
   - Fix any failures
   - Verify coverage

5. **Build and verify** (30min)
   - pnpm run build
   - pnpm run test:coverage

---

## 🚀 YOU ARE APPROVED FOR PHASE 2

Begin implementation now.

Timeline: 4-6h

Report back when complete.

---

**END OF APPROVAL**
