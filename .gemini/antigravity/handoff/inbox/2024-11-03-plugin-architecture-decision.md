# Handoff: DeepSeek Multi-Engine Integration - Architecture Decision

**From:** Claude Sonnet 5 (Architect)
**To:** Gemini (Executor)
**Date:** 2024-11-03
**Priority:** HIGH
**Type:** Architecture Change + Implementation Direction

---

## Executive Summary

**DECISION: Switch from Integration approach to Plugin architecture.**

- **Old approach:** Integration (merge deepseek-harness into monorepo)
- **New approach:** Plugin (build Solo-Code as Cordis plugin)
- **Rationale:** 40-50% less effort, better architecture, easier maintenance
- **Impact on current work:** Preserve tools/services logic, change structure
- **Action required:** Stop integration work, start plugin implementation

---

## Context

### What We Analyzed

Analyzed 3 sources for building DeepSeek Multi-Engine Harness:
1. **deepseek-harness-master** - TypeScript monorepo with Cordis architecture (40+ packages)
2. **project_tools/g-harness** - Solo-Code multi-engine orchestration (70+ tools)
3. **F:/Project/g-harness/g-harness** - Target integration directory

### Original Plan (Integration Approach)

Created 67KB specification (docs/INTEGRATION_DESIGN_SPEC.md):
- 7 phases, 25-35 hours
- Copy deepseek-harness as base
- Port 70+ tools to tools/
- Create packages/orchestration/ (NEW TypeScript package)
- Merge configs into .claude/, .kilo/, etc.
- **Problem:** Forks DeepSeek core, hard to maintain, violates Cordis design

### Analysis Result

Compared Integration vs Plugin in docs/ARCHITECTURE_COMPARISON.md:
- Plugin saves 40-50% effort (11-16h vs 25-35h)
- Plugin follows Cordis design (DeepSeek built for plugins)
- Plugin easier maintenance (no fork tracking)
- Plugin has zero blocking limitations (docs/PLUGIN_LIMITATIONS.md)

---

## Architecture Decision

### ✅ CHOSEN: Plugin Architecture

**Structure:**
```
deepseek-harness-master/ (UNCHANGED)
  packages/
    core/              # DeepSeek core - untouched
    llm/               # Model providers - untouched
    ...
    solo-code/         # NEW PLUGIN PACKAGE
      package.json
      src/
        index.ts                    # Plugin entry point
        executor-registry.ts        # Multi-engine routing
        delegation-service.ts       # CLI delegation
        guard-service.ts            # Write gating
        usage-tracking.ts           # Cost tracking
      tools/                        # Python tools
        opencode_delegate.py
        antigravity_delegate.py
        kilo_cli_delegate.py
        benchmark_executors.py
        ...
      tests/
        *.test.ts
```

**Why Plugin:**
1. Tuân thủ Cordis architecture (DeepSeek designed for this)
2. 11-16h thay vì 25-35h (tiết kiệm 40-50%)
3. No fork to maintain - DeepSeek upgrades via npm update
4. Reversible - enable/disable in config
5. Clear separation of concerns

---

## What to Preserve from Current Work

### ✅ Reuse These (if already implemented)

**Python Tools:**
- All delegation scripts (opencode_delegate.py, etc.)
- Benchmark tools
- Verification gates
- Security scanners
→ Move to: packages/solo-code/tools/

**TypeScript Service Logic:**
- Executor registry implementation
- Task routing logic
- CLI delegation code
- Usage tracking
→ Adapt to: Cordis Service classes

**Tests:**
- Unit tests for tools
- Integration test scenarios
- Benchmark test cases
→ Adapt to: packages/solo-code/tests/

**Documentation:**
- Tool usage docs
- API documentation
→ Keep and reference

### ❌ Discard These

**Integration-specific code:**
- packages/orchestration/ structure (wrong location)
- Integration bridge code (not needed)
- Makefile verify-harness (plugin has own build)
- Any modifications to DeepSeek core packages

**Copied configs:**
- Don't copy .claude/, .kilo/ to root
- Plugin owns its configs internally

---

## New Implementation Plan

### Phase 1: Plugin Skeleton (3-4h)

**Owner:** You (Gemini) or OpenCode

**Tasks:**
1. Create packages/solo-code/ directory structure
2. Setup package.json with Cordis dependencies
3. Create tsconfig.json
4. Implement plugin entry point (src/index.ts)
5. Register with Cordis system

**Files to create:**
```
packages/solo-code/
  package.json
  tsconfig.json
  README.md
  src/
    index.ts
  tests/
    index.test.ts
```

**Entry point pattern:**
```typescript
import { Context, Service } from 'cordis'

export interface SoloCodeConfig {
  executors: Record<string, ExecutorConfig>
  guards: { executor_mode: 'on' | 'off' }
  tracking: { enabled: boolean }
}

export class SoloCodePlugin extends Service {
  static inject = ['agents', 'tools']

  constructor(ctx: Context, config: SoloCodeConfig) {
    super(ctx, 'solo-code', true)

    // Register sub-services
    ctx.plugin(ExecutorRegistry, config.executors)
    ctx.plugin(DelegationService, config)
    ctx.plugin(GuardService, config.guards)
    ctx.plugin(UsageTracker, config.tracking)

    ctx.on('ready', () => {
      this.logger.info('Solo-Code plugin loaded')
    })
  }
}

export default SoloCodePlugin
```

**Verification:**
```bash
cd packages/solo-code
pnpm install
pnpm run build
```

---

### Phase 2: Core Services (4-6h)

**Owner:** You (Gemini) with DSH Reasoning profile

**Implement 4 Cordis Services:**

1. **ExecutorRegistry Service** (src/executor-registry.ts)
   - Load .solocode/executor-config.json
   - Manage executor configs
   - Route tasks to executors
   - Pattern: extends Service

2. **DelegationService** (src/delegation-service.ts)
   - Execute Python CLI tools
   - Parse JSON/text results
   - Handle timeouts/errors
   - Extract file changes from git diff

3. **GuardService** (src/guard-service.ts)
   - Check executor mode state
   - Intercept tools/pre-execute event
   - Block/allow write operations
   - Emit delegation instructions

4. **UsageTracker Service** (src/usage-tracking.ts)
   - Log to .solocode/*.jsonl
   - Track tokens, costs, success rates
   - Emit usage events
   - Generate reports

**Key Pattern:**
```typescript
export class ExecutorRegistry extends Service {
  static inject = [] // No dependencies

  private executors: Map<string, ExecutorConfig>

  constructor(ctx: Context, config: Record<string, ExecutorConfig>) {
    super(ctx, 'executor-registry')

    // Load executor configs
    this.executors = new Map(Object.entries(config))
  }

  getExecutor(name: string): ExecutorConfig | undefined {
    return this.executors.get(name)
  }

  routeTask(taskType: string): ExecutorConfig {
    // Route based on task type
  }
}
```

**Verification:**
```bash
pnpm run test packages/solo-code
pnpm run test:coverage
```

---

### Phase 3: Tools Integration (2-3h)

**Owner:** You (Gemini) or OpenCode

**Tasks:**
1. Copy Python tools from project_tools/g-harness/tools/
2. Place in packages/solo-code/tools/
3. Update DelegationService to call these tools
4. Test CLI integration

**Files to copy:**
```
packages/solo-code/tools/
  opencode_delegate.py
  kilo_cli_delegate.py
  antigravity_delegate.py
  benchmark_executors.py
  coverage_gate.py
  security_scan.py
  agent_scope.py
```

**Wire up in DelegationService:**
```typescript
async delegate(executor: ExecutorConfig, task: string) {
  const toolPath = resolve(__dirname, '../tools', executor.command)

  const result = await execFile('python', [toolPath, task])

  return this.parseResult(result)
}
```

**Verification:**
```bash
python packages/solo-code/tools/opencode_delegate.py --help
python packages/solo-code/tools/opencode_delegate.py "list files" --dry-run
```

---

### Phase 4: Testing & Documentation (2-3h)

**Owner:** You (Gemini)

**Tasks:**
1. Write unit tests for each service
2. Write integration tests
3. Update README.md
4. Create usage examples

**Test coverage required:** 100% (DeepSeek policy)

**Verification:**
```bash
pnpm run test:coverage packages/solo-code
# Must show 100% coverage on src/**
```

---

## Success Criteria

### Must Have
- [ ] Plugin builds successfully (pnpm run build)
- [ ] All tests pass with 100% coverage
- [ ] Plugin loads in DeepSeek profile
- [ ] Can delegate task to OpenCode
- [ ] Guard service blocks writes when executor mode ON
- [ ] Usage tracking logs to JSONL

### Should Have
- [ ] Documentation complete
- [ ] Examples working
- [ ] Benchmark tests pass
- [ ] Integration with existing DeepSeek features verified

---

## Reference Documents

**Read these for context:**

1. **docs/ARCHITECTURE_COMPARISON.md** - Why Plugin > Integration
2. **docs/PLUGIN_LIMITATIONS.md** - Plugin capabilities analysis
3. **docs/INTEGRATION_DESIGN_SPEC.md** - Original plan (reference only)

**DeepSeek Documentation:**

1. **docs/architecture.md** - Cordis architecture guide
2. **docs/cordis-primer.md** - Cordis basics
3. **packages/*/src/index.ts** - 40+ plugin examples

**Study these plugin examples:**
- packages/api/gateway/src/index.ts - Service pattern
- packages/core/tools/src/index.ts - Tool registration
- packages/subagent/*/src/index.ts - Subagent patterns

---

## Current State Check

### If You Haven't Started Yet
→ Perfect! Start with Phase 1 (plugin skeleton)

### If You Started Integration Approach
→ Stop. Assess what's reusable:
- Python tools → Move to packages/solo-code/tools/
- TypeScript logic → Adapt to Cordis Services
- Tests → Adapt to new structure
- Discard: integration bridge code, copied configs

### If You're Deep in Integration
→ Estimate reuse %:
- Tools: 100% reusable
- TypeScript services: 80% reusable (change structure)
- Tests: 70% reusable (adapt to plugin)
- Integration code: 0% reusable (discard)

Migration effort: 2-3h to refactor existing work into plugin structure

---

## Questions to Answer Before Starting

1. **Current work status?**
   - Not started yet?
   - Started Integration approach?
   - How much is done?

2. **What's already implemented?**
   - Python tools ported?
   - TypeScript services written?
   - Tests created?

3. **Time estimate for migration?**
   - If switching from Integration
   - How much to preserve vs rewrite?

4. **Clarifications needed?**
   - Cordis patterns unclear?
   - Plugin structure questions?
   - Service implementation details?

---

## Next Actions for You (Gemini)

### Immediate (before coding)
1. **Reply to this handoff** with current status
2. **Assess existing work** - what's reusable?
3. **Read Cordis primer** - docs/cordis-primer.md (30 min)
4. **Study 2-3 plugin examples** - packages/*/src/index.ts (30 min)

### Then Start Phase 1
1. Create packages/solo-code/ structure
2. Setup package.json, tsconfig.json
3. Implement plugin entry point
4. Verify build works

### Communication Protocol
- **Report progress** after each phase
- **Ask questions** if Cordis patterns unclear
- **Share blockers** immediately
- **Verify** at each phase boundary

---

## Support Available

**From Claude (me):**
- Architecture decisions
- Cordis pattern clarification
- Review plugin structure
- Verify design decisions

**From DeepSeek Docs:**
- Cordis primer: docs/cordis-primer.md
- Architecture guide: docs/architecture.md
- 40+ plugin examples in packages/

**From OpenCode:**
- Mechanical file operations
- Python tool testing
- Build/test execution

---

## Timeline

**Original Integration:** 25-35h
**New Plugin:** 11-16h
**Savings:** 14-19h (40-50%)

**Breakdown:**
- Phase 1: 3-4h
- Phase 2: 4-6h
- Phase 3: 2-3h
- Phase 4: 2-3h

**Your estimate:** ___h (fill after assessing current work)

---

## Status Updates

Please reply with:
1. Current work status
2. What's already done (if any)
3. Questions/clarifications needed
4. Estimated timeline for Phase 1

---

**This handoff is READ-ONLY. Do not modify this file.**

Reply by creating: `.gemini/antigravity/handoff/outbox/2024-11-03-plugin-response.md`

Or ask Claude directly in conversation.

---

**END OF HANDOFF**
