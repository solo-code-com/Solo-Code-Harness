# DeepSeek Multi-Engine Harness - Integration Plan

**Version:** 1.0
**Architect:** Claude Sonnet 5
**Date:** 2024-11-03

---

## Executive Summary

**Goal:** Integrate deepseek-harness-master (robust TypeScript monorepo) with Solo-Code multi-engine orchestration capabilities.

**Sources:**
1. Base: f:/Project/g-harness/deepseek-harness-master (40+ packages, Cordis architecture)
2. Integration source: F:/Project/project_tools/g_harness/g-harness (multi-engine tools, delegation)
3. Target: F:/Project/g-harness/g-harness (current directory)

**Result:** Production AI agent harness supporting 6+ engines with disciplined delegation.

---

## Architecture Strategy

### Principle
Preserve DeepSeek core, add orchestration wrapper layer.

### Flow


### 5 Integration Layers
1. **Foundation** - DeepSeek core (UNCHANGED)
2. **Adapters** - packages/orchestration/ (NEW)
3. **Tools** - tools/ directory (PORTED)
4. **Configs** - .claude/, .kilo/, etc. (PORTED)
5. **Discipline** - AGENTS.md, hooks (INTEGRATED)

---

## 7-Phase Implementation

### Phase 0: Environment Setup
- **Duration:** 1-2 hours
- **Risk:** Low
- **Owner:** OpenCode
- **Tasks:**
  - Copy deepseek-harness-master as base
  - Verify builds and tests pass
  - Create integration directories

### Phase 1: Tool Infrastructure
- **Duration:** 2-4 hours
- **Risk:** Low
- **Owner:** OpenCode
- **Tasks:**
  - Port 70+ tools from project_tools
  - Adapt delegation tools
  - Add verification gates
  - Update Makefile

### Phase 2: Executor Mode
- **Duration:** 2-3 hours
- **Risk:** Medium
- **Owner:** OpenCode
- **Tasks:**
  - Create .solocode/ directory
  - Copy guard hooks
  - Test write gating

### Phase 3: Multi-Engine Config
- **Duration:** 3-4 hours
- **Risk:** Low
- **Owner:** OpenCode
- **Tasks:**
  - Copy engine directories
  - Copy launchers
  - Configure models

### Phase 4: Orchestration Package
- **Duration:** 6-8 hours
- **Risk:** HIGH
- **Owner:** DSH Reasoning
- **Tasks:**
  - Create packages/orchestration/
  - Implement TypeScript classes
  - Write tests (100% coverage)
  - Bridge to DeepSeek subagent system

### Phase 5: Documentation
- **Duration:** 3-4 hours
- **Risk:** Low
- **Owner:** Claude → OpenCode
- **Tasks:**
  - Merge AGENTS.md
  - Create architecture guide
  - Create usage guide

### Phase 6: Testing
- **Duration:** 4-6 hours
- **Risk:** Medium
- **Owner:** OpenCode + DSH
- **Tasks:**
  - Unit tests
  - Integration tests
  - Benchmarks
  - Full validation

### Phase 7: Deployment
- **Duration:** 2-3 hours
- **Risk:** Low
- **Owner:** OpenCode
- **Tasks:**
  - CHANGELOG
  - Final verification

---

## Key Components

### From project_tools (to port):

**Tools:**
- antigravity_delegate.py
- opencode_delegate.py
- kilo_cli_delegate.py
- benchmark_executors.py
- coverage_gate.py
- security_scan.py
- garden.py
- validate_schemas.py

**Hooks:**
- .claude/hooks/guard.py
- .claude/hooks/quality_gate.py
- .claude/hooks/session_start.py

**Configs:**
- .solocode/executor-mode
- .solocode/executor-config.json

### New Component: packages/orchestration/

**TypeScript classes:**
- ExecutorRegistry - manage executors
- TaskRouter - classify and route
- Delegator - execute via CLI
- UsageTracker - log costs
- ExternalExecutor - Cordis bridge

---

## Executor Routing

| Task Type | Executor | Model | Cost |
|-----------|----------|-------|------|
| Simple edits | OpenCode | deepseek-v4-pro | Low |
| Boilerplate | OpenCode | deepseek-v4-pro | Low |
| Complex logic | DSH | deepseek-reasoner | High |
| Reasoning | DSH | deepseek-reasoner | High |
| Fallback | Kilo | deepseek-v4-pro | Low |

---

## Success Criteria

**Functional:**
- All 40+ DeepSeek packages work
- Multi-engine delegation works
- Executor mode enforces discipline
- All gates pass

**Quality:**
- 100% test coverage maintained
- No security issues
- Complete documentation

**Performance:**
- Build < 5 min
- Tests < 10 min

---

## Timeline

**Total: 25-35 hours over 1-2 weeks**

Phase 4 (Orchestration Package) is the most complex and critical.

---

## Next Steps

1. Review this plan with user
2. Begin Phase 0 (environment setup)
3. Progress through phases incrementally
4. Test at each phase boundary
5. Report results

---

For detailed implementation specifications, refer to the full design document with code interfaces and detailed task breakdowns.
