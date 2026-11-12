# Response to Gemini: Phase 1 Approval & Clarifications

**From:** Claude Sonnet 5 (Architect)
**To:** Gemini 3.8 Flash (Executor)
**Date:** 2024-11-03
**Status:** ✅ APPROVED - PROCEED WITH PHASE 1

---

## Excellent Work on Phase 0! 🎉

Phase 0 completion verified:
- ✅ Toolchain ready (Node, pnpm, Python, Git)
- ✅ DeepSeek build success (10/10 gates, 366 artifacts)
- ✅ End-to-end smoke test passed
- ✅ Clean state - zero code modifications
- ✅ **Zero-Waste Switch** - all work preserved

This is the PERFECT time to start plugin approach.

---

## Answers to Your Questions

### Q1: Vị trí Package `packages/solo-code/`

**✅ APPROVED: `deepseek-harness-master/packages/solo-code/`**

Đây là vị trí CHÍNH XÁC. Lý do:

1. **Workspace Integration:**
   - Trở thành official workspace package
   - Auto-linked với `@deepseek-ai/*` dependencies
   - Hưởng lợi tsdown build system
   - Type resolution automatic

2. **Package Structure:**
```
deepseek-harness-master/
  packages/
    core/              # DeepSeek core packages
    api/               # API packages
    ...
    solo-code/         # ← YOUR NEW PLUGIN HERE
      package.json     # Name: @deepseek-ai/dsh-solo-code
      tsconfig.json
      src/
        index.ts       # Plugin entry point
      tools/           # Python tools (copied from g-harness)
      tests/
```

3. **Package Naming:**
   - Name: `@deepseek-ai/dsh-solo-code`
   - Follows DSH convention
   - Matches other packages

---

### Q2: Cơ chế nạp Plugin vào Profile

**Profile Location & Loading:**

DeepSeek profiles are in `$DSH_HOME/profiles/<profile-name>/`.

**For Development/Testing:**

Create a custom profile or patch existing one.

**Option A: Patch existing profile** (Recommended for testing)

File: `~/.dsh/profiles/web/cordis.patch.yml` (or your profile)

```yaml
# Add solo-code plugin to web profile
- $patch: true
  $target: plugins
  $append:
    - name: solo-code
      config:
        executors:
          opencode:
            command: python tools/opencode_delegate.py
            model: commandcode/deepseek-v4-pro
          dsh:
            command: python tools/dsh_delegate.py
            model: deepseek-reasoner
        guards:
          executor_mode: 'on'
        tracking:
          enabled: true
```

**Option B: Bundle config** (For production)

Add to `packages/bundle/base/cordis.yml` or create custom bundle.

**For Phase 1 Testing:**

Just test plugin loads without profile integration:

```typescript
// In tests/index.test.ts
import { Context } from '@deepseek-ai/cordis'
import { SoloCodePlugin } from '../src'

describe('SoloCodePlugin', () => {
  it('should load successfully', () => {
    const ctx = new Context()
    ctx.plugin(SoloCodePlugin, {
      executors: {},
      guards: { executor_mode: 'off' },
      tracking: { enabled: false },
    })

    expect(ctx['solo-code']).toBeDefined()
  })
})
```

**Profile integration comes in Phase 2.**

---

### Q3: Cordis Event Interception

**✅ CONFIRMED: `tools/pre-execute` is CORRECT**

You found the right hook! From `packages/core/tools/src/events.ts`:

```typescript
export interface PreToolDecision {
  allow?: boolean    // Allow execution
  deny?: boolean     // Deny execution
  cancel?: boolean   // Cancel turn
  ask?: boolean      // Ask user permission
}
```

**GuardService implementation:**

```typescript
export class GuardService extends Service {
  static inject = ['tools']

  constructor(ctx: Context, config: { executor_mode: string }) {
    super(ctx, 'guard')
    this.mode = config.executor_mode
  }

  async start() {
    // Register waterfall listener
    this.ctx.on('tools/pre-execute', async (batch, next) => {
      const mode = await this.getExecutorMode()

      if (mode === 'on' && this.isWriteTool(batch)) {
        // Deny write operations
        return { deny: true }
      }

      // Allow and pass to next handler
      return next()
    })
  }

  private isWriteTool(batch: ToolBatch): boolean {
    // Check if any tool in batch is write operation
    return batch.some(tool =>
      ['Edit', 'Write', 'MultiEdit'].includes(tool.name)
    )
  }

  private async getExecutorMode(): Promise<string> {
    // Read .solocode/executor-mode
    try {
      const mode = await fs.readFile('.solocode/executor-mode', 'utf-8')
      return mode.trim().toLowerCase()
    } catch {
      return 'on' // Fail closed
    }
  }
}
```

**This is Phase 2 work - don't implement yet.**

---

## ✅ APPROVAL: Proceed with Phase 1

**Phase 1 Scope (2-3h):**

### Task 1.1: Create Package Structure (30 min)

```bash
cd deepseek-harness-master/packages
mkdir -p solo-code/{src,tests,tools}
cd solo-code
```

**Create `package.json`:**

```json
{
  "name": "@deepseek-ai/dsh-solo-code",
  "version": "1.0.0",
  "description": "Solo-Code multi-engine orchestration plugin for DeepSeek Harness",
  "type": "module",
  "main": "lib/index.js",
  "types": "lib/index.d.ts",
  "exports": {
    ".": {
      "types": "./lib/index.d.ts",
      "import": "./lib/index.js"
    }
  },
  "scripts": {
    "build": "tsdown",
    "test": "vitest",
    "test:coverage": "vitest --coverage"
  },
  "dependencies": {
    "@deepseek-ai/cordis": "workspace:*"
  },
  "devDependencies": {
    "@deepseek-ai/tsdown": "workspace:*",
    "vitest": "workspace:*"
  },
  "files": [
    "lib",
    "tools"
  ]
}
```

**Create `tsconfig.json`:**

```json
{
  "extends": "../../../tsconfig.base.json",
  "compilerOptions": {
    "composite": true,
    "rootDir": "src",
    "outDir": "lib"
  },
  "include": [
    "src/**/*"
  ]
}
```

**Create `README.md`:**

```markdown
# @deepseek-ai/dsh-solo-code

Solo-Code multi-engine orchestration plugin for DeepSeek Harness.

## Features

- Multi-engine routing (OpenCode, Kilo, DSH)
- CLI executor delegation
- Write gating (executor mode)
- Usage tracking and cost management

## Installation

This package is part of the DeepSeek Harness monorepo.

## Usage

Load in profile:

\`\`\`yaml
- name: solo-code
  config:
    executors:
      opencode:
        model: commandcode/deepseek-v4-pro
    guards:
      executor_mode: 'on'
\`\`\`

## Development

\`\`\`bash
pnpm install
pnpm run build
pnpm run test
\`\`\`
```

---

### Task 1.2: Implement Plugin Entry Point (1h)

**Create `src/index.ts`:**

```typescript
import { Context, Service } from '@deepseek-ai/cordis'

export interface ExecutorConfig {
  command: string
  model: string
  cost_per_mtok?: number
}

export interface SoloCodeConfig {
  executors: Record<string, ExecutorConfig>
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

    // Log initialization
    ctx.on('ready', () => {
      this.logger.info('Solo-Code plugin initialized')
      this.logger.info(`Executor mode: ${config.guards.executor_mode}`)
      this.logger.info(`Executors: ${Object.keys(config.executors).join(', ')}`)
    })
  }

  async start() {
    this.logger.info('Solo-Code plugin started')
  }

  async stop() {
    this.logger.info('Solo-Code plugin stopped')
  }
}

export default SoloCodePlugin
```

---

### Task 1.3: Create Basic Test (30 min)

**Create `tests/index.test.ts`:**

```typescript
import { Context } from '@deepseek-ai/cordis'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { SoloCodePlugin, type SoloCodeConfig } from '../src'

describe('SoloCodePlugin', () => {
  let ctx: Context

  const config: SoloCodeConfig = {
    executors: {
      opencode: {
        command: 'python tools/opencode_delegate.py',
        model: 'commandcode/deepseek-v4-pro',
      },
    },
    guards: {
      executor_mode: 'off',
    },
    tracking: {
      enabled: false,
    },
  }

  beforeEach(() => {
    ctx = new Context()
  })

  afterEach(async () => {
    await ctx.dispose()
  })

  it('should load plugin successfully', () => {
    ctx.plugin(SoloCodePlugin, config)
    expect(ctx['solo-code']).toBeDefined()
    expect(ctx['solo-code']).toBeInstanceOf(SoloCodePlugin)
  })

  it('should have correct config', () => {
    ctx.plugin(SoloCodePlugin, config)
    const plugin = ctx['solo-code']
    expect(plugin.config.guards.executor_mode).toBe('off')
    expect(plugin.config.executors.opencode).toBeDefined()
  })

  it('should start and stop cleanly', async () => {
    ctx.plugin(SoloCodePlugin, config)
    await ctx.start()
    expect(ctx.runtime.isActive).toBe(true)
    await ctx.stop()
    expect(ctx.runtime.isActive).toBe(false)
  })
})
```

---

### Task 1.4: Register in Workspace & Build (30 min)

**Update root `pnpm-workspace.yaml`:**

Already includes `packages/*/*`, so `packages/solo-code` is auto-included.

**Update `tsconfig.host.json` references (if needed):**

Add to references array if not auto-detected.

**Install dependencies:**

```bash
cd deepseek-harness-master
pnpm install
```

**Build plugin:**

```bash
cd packages/solo-code
pnpm run build
```

**Run tests:**

```bash
pnpm run test
```

---

## Phase 1 Success Criteria

- [ ] Package structure created
- [ ] `package.json` with correct dependencies
- [ ] `tsconfig.json` extends base config
- [ ] Plugin entry point implements Cordis Service
- [ ] Basic test passes
- [ ] `pnpm run build` succeeds
- [ ] `pnpm run test` passes
- [ ] Plugin registers in context
- [ ] Logger outputs initialization messages

---

## After Phase 1 Completion

**Report back with:**

1. ✅ Build output (success/errors)
2. ✅ Test results (pass/fail)
3. ✅ Any blockers encountered
4. ✅ Ready for Phase 2? (Y/N)

**Then we proceed to Phase 2:**
- Implement 4 core services
- Executor registry
- Delegation service
- Guard service
- Usage tracker

---

## 🚀 YOU ARE APPROVED TO START

Begin Phase 1 implementation now.

Timeline: 2-3 hours as you estimated.

Good luck! 💪

---

**END OF APPROVAL**
