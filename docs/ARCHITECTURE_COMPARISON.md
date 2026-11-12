# So sánh Kiến trúc: Integration vs Plugin

**Ngày:** 2024-11-03
**Phân tích:** Claude Sonnet 5

---

## Kết luận Executive

**KHUYẾN NGHỊ: Plugin Architecture** - Vượt trội về mọi mặt

| Tiêu chí | Integration | Plugin | Winner |
|----------|------------|--------|---------|
| Phù hợp kiến trúc | Fork DeepSeek | Leverage Cordis | **Plugin** |
| Độ phức tạp | 7 phases, 25-35h | 4 phases, 11-16h | **Plugin** |
| Maintainability | Khó (track upstream) | Dễ (independent) | **Plugin** |
| Reversibility | Khó rollback | Enable/disable | **Plugin** |
| Upgrade path | Re-integration | npm update | **Plugin** |
| Risk level | HIGH (Phase 4) | Medium-Low | **Plugin** |

---

## Phương án 1: Integration (Current Plan)

### Mô tả
Copy deepseek-harness-master, tích hợp toàn bộ Solo-Code vào monorepo.

### Ưu điểm
- ✅ Tích hợp chặt chẽ, tất cả trong một repo
- ✅ Control toàn bộ stack

### Nhược điểm
- ❌ **Fork DeepSeek core** - phải track upstream changes
- ❌ **Độ phức tạp cao** - 7 phases, 25-35 giờ
- ❌ **Phase 4 HIGH RISK** - packages/orchestration/ integration
- ❌ **Khó maintain** - merge conflicts mỗi DeepSeek update
- ❌ **Vi phạm kiến trúc** - DeepSeek designed for plugins
- ❌ **Không reversible** - rollback = restore entire codebase
- ❌ **Technical debt** - mixed boundaries, hard to test isolated

### Timeline
- Phase 0: 1-2h (setup)
- Phase 1: 2-4h (tools)
- Phase 2: 2-3h (executor mode)
- Phase 3: 3-4h (configs)
- **Phase 4: 6-8h (orchestration) ⚠️ HIGH RISK**
- Phase 5: 3-4h (docs)
- Phase 6: 4-6h (testing)
- Phase 7: 2-3h (deployment)

**Total: 25-35 giờ**

---

## Phương án 2: Plugin Architecture (RECOMMENDED)

### Mô tả
Giữ nguyên deepseek-harness-master, xây dựng g-harness như **Cordis plugin**.

### Kiến trúc

```
deepseek-harness-master/ (UNCHANGED)
  packages/
    core/              # DeepSeek core
    llm/               # Model providers
    subagent/          # Subagent system
    ...
    solo-code/         # NEW PLUGIN
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
```

### Plugin Registration (Cordis pattern)

```typescript
// packages/solo-code/src/index.ts
import { Context, Service } from 'cordis'

export class SoloCodePlugin extends Service {
  constructor(ctx: Context, config: SoloCodeConfig) {
    super(ctx, 'solo-code', true)

    // Register services
    ctx.plugin(ExecutorRegistry, config.executors)
    ctx.plugin(DelegationService, config.delegation)
    ctx.plugin(GuardService, config.guards)
    ctx.plugin(UsageTracker, config.tracking)
  }
}

export default SoloCodePlugin
```

### Enable trong Profile

```typescript
// balanced.ts profile
export default {
  plugins: [
    ['solo-code', {
      executors: {
        opencode: { model: 'deepseek-v4-pro', cost: 0.14 },
        kilo: { model: 'deepseek-v4-pro', cost: 0.14 },
        dsh: { model: 'deepseek-reasoner', cost: 0.55 },
      },
      guards: { executor_mode: 'on' },
      tracking: { enabled: true },
    }],
  ],
}
```

### Sử dụng từ DeepSeek code

```typescript
// Bất kỳ package nào có thể dùng solo-code services
const soloCode = ctx['solo-code']
const result = await soloCode.delegate('opencode', 'Add TODO comment')
```

---

## Ưu điểm Plugin Architecture

### 1. Tuân thủ kiến trúc DeepSeek
- ✅ Cordis plugin là extension pattern chính thống
- ✅ DeepSeek designed cho điều này
- ✅ Leverage infrastructure có sẵn
- ✅ Community best practice

### 2. Separation of concerns hoàn hảo
- ✅ DeepSeek core = untouched
- ✅ Solo-Code = isolated plugin
- ✅ Clear boundaries
- ✅ Test plugin độc lập

### 3. Upgrade path dễ dàng
- ✅ DeepSeek upgrade = npm update (no re-integration)
- ✅ Plugin upgrade = independent versioning
- ✅ Không merge conflicts
- ✅ Luôn sync với DeepSeek latest

### 4. Reversibility cao
- ✅ Enable/disable trong config
- ✅ Remove plugin = xóa 1 package
- ✅ Zero impact on DeepSeek core
- ✅ Easy rollback

### 5. Đơn giản hơn nhiều
- ✅ 4 phases thay vì 7
- ✅ 11-16 giờ thay vì 25-35 giờ (tiết kiệm 40-50%)
- ✅ Ít integration points
- ✅ Lower risk overall

### 6. Maintainability tốt
- ✅ Plugin có version riêng
- ✅ npm dependencies management
- ✅ Clear ownership
- ✅ Technical debt isolated

### 7. Distribution dễ
- ✅ Publish lên npm: `@solo-code/dsh-plugin`
- ✅ Users: `pnpm add @solo-code/dsh-plugin`
- ✅ Enable trong profile
- ✅ No fork to maintain

### 8. Testing tốt hơn
- ✅ Unit test plugin riêng
- ✅ Mock Cordis context dễ
- ✅ Integration test isolated
- ✅ CI/CD riêng cho plugin

---

## Implementation Plan (Plugin Approach)

### Phase 1: Plugin Skeleton (3-4h, Low Risk)

**Owner:** OpenCode

**Tasks:**
1. Create `packages/solo-code/` structure
2. Setup `package.json`, `tsconfig.json`
3. Implement plugin entry point (index.ts)
4. Register với Cordis
5. Add to workspace

**Verification:**
```bash
pnpm run build packages/solo-code
```

---

### Phase 2: Core Services (4-6h, Medium Risk)

**Owner:** DSH Reasoning

**Implement 4 Cordis Services:**

1. **ExecutorRegistry Service**
   - Load `.solocode/executor-config.json`
   - Manage executor configs
   - Route tasks to executors

2. **DelegationService**
   - Execute Python CLI tools
   - Parse results
   - Handle timeouts/errors

3. **GuardService**
   - Implement write gating
   - Check executor mode
   - Block/allow operations

4. **UsageTracker Service**
   - Log to `.solocode/*.jsonl`
   - Track costs, tokens, success rates
   - Generate reports

**Verification:**
```bash
pnpm run test packages/solo-code
```

---

### Phase 3: Tools Integration (2-3h, Low Risk)

**Owner:** OpenCode

**Tasks:**
1. Copy Python tools từ project_tools
2. Place trong `packages/solo-code/tools/`
3. Wire up CLI calls từ DelegationService
4. Test delegation flow

**Files to copy:**
- opencode_delegate.py
- kilo_cli_delegate.py
- antigravity_delegate.py
- benchmark_executors.py
- coverage_gate.py
- security_scan.py

**Verification:**
```bash
python packages/solo-code/tools/opencode_delegate.py --help
```

---

### Phase 4: Testing & Docs (2-3h, Low Risk)

**Owner:** OpenCode

**Tasks:**
1. Unit tests cho mỗi service
2. Integration test: enable plugin in test profile
3. Write `packages/solo-code/README.md`
4. Usage examples
5. API documentation

**Verification:**
```bash
pnpm run test:coverage packages/solo-code
```

---

**Total Timeline: 11-16 giờ** (so với 25-35 giờ của Integration)

---

## So sánh Chi tiết

### Architectural Alignment

**Integration:**
- ❌ Bypasses Cordis plugin system
- ❌ Modifies DeepSeek structure
- ❌ Mixes harness/project concerns

**Plugin:**
- ✅ Uses Cordis (as designed)
- ✅ Extends without modification
- ✅ Clear separation

**Winner: Plugin**

---

### Complexity & Risk

**Integration:**
- 7 phases, 25-35 giờ
- Phase 4 = HIGH RISK
- 67KB specification
- Many integration points

**Plugin:**
- 4 phases, 11-16 giờ
- All phases = Low-Medium risk
- Simpler spec
- Few integration points

**Winner: Plugin** (40-50% less effort)

---

### Maintainability

**Integration:**
- Fork dsh-harness
- Track upstream manually
- Merge conflicts
- Re-integration work

**Plugin:**
- No fork
- npm update
- Zero conflicts
- Independent versioning

**Winner: Plugin** (significantly easier)

---

### Reversibility

**Integration:**
- Rollback = restore codebase
- All-or-nothing
- High cost to undo

**Plugin:**
- Disable in config
- Remove package
- Low cost to undo

**Winner: Plugin**

---

### Testing

**Integration:**
- Hard to test isolated
- DeepSeek changes break tests
- Difficult to mock

**Plugin:**
- Easy isolated testing
- Mock Cordis context
- Clear test boundaries

**Winner: Plugin**

---

### Distribution

**Integration:**
- Fork entire repo
- Users get monolith
- Hard to share

**Plugin:**
- Publish to npm
- Users add package
- Easy to share

**Winner: Plugin**

---

## Nhược điểm Plugin (minor)

⚠️ **Plugin API constraints**
- Phải follow Cordis patterns
- Không modify DeepSeek internals
- Nhưng: Không cần modify internals

⚠️ **Learning curve**
- Cần hiểu Cordis plugin system
- Nhưng: Documentation tốt, patterns clear

---

## Migration từ Current Plan

Nếu đã bắt đầu Integration approach, chuyển sang Plugin:

**Reuse được:**
- ✅ Tool implementations (Python scripts)
- ✅ TypeScript service logic
- ✅ Test cases
- ✅ Documentation

**Discard:**
- ❌ packages/orchestration/ structure (move vào plugin)
- ❌ Integration bridge code
- ❌ Makefile verify-harness target

**Effort:** ~2-3 giờ refactoring

---

## Kết luận & Khuyến nghị

### ✅ KHUYẾN NGHỊ MẠNH: Plugin Architecture

**Lý do chính:**

1. **Tuân thủ kiến trúc** - DeepSeek built for plugins
2. **Đơn giản hơn 40-50%** - 11-16h vs 25-35h
3. **Risk thấp hơn** - No high-risk Phase 4
4. **Maintain dễ hơn nhiều** - No fork, no conflicts
5. **Reversible** - Enable/disable dễ dàng
6. **Professional** - Standard Cordis pattern
7. **Ecosystem** - Stay in DeepSeek community

### Hành động được đề xuất

1. **Stop** current integration plan
2. **Adopt** plugin architecture
3. **Start** Phase 1 (plugin skeleton)
4. **Complete** trong 11-16 giờ

### So sánh tổng thể

| Aspect | Integration | Plugin | Savings |
|--------|------------|--------|---------|
| Effort | 25-35h | 11-16h | **40-50%** |
| Risk | HIGH | Low-Medium | **Lower** |
| Maintainability | Khó | Dễ | **Much better** |
| Reversibility | Thấp | Cao | **Much better** |
| Architecture fit | Poor | Excellent | **Much better** |

---

**Plugin architecture là lựa chọn đúng đắn về mọi mặt.**

---

## Bước tiếp theo

**Nếu chọn Plugin (recommended):**

1. Review Cordis plugin documentation
2. Create packages/solo-code/ skeleton
3. Implement Phase 1 (3-4h)
4. Progress qua Phases 2-4
5. Timeline: ~2 tuần part-time

**Nếu tiếp tục Integration:**

1. Accept 40-50% more effort
2. Accept maintenance burden
3. Accept fork management overhead
4. Proceed with current 67KB spec

**Câu hỏi cho bạn:**
- Bạn có muốn chuyển sang Plugin approach?
- Cần tôi tạo detailed plugin implementation spec?
- Bắt đầu Phase 1 plugin skeleton?

---

**END OF COMPARISON**
