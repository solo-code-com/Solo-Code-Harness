---
slug: executor-benchmark-2026-09-04
completed: 2026-09-04T16:30:00+07:00
from: claude
status: completed
---

# Executor Benchmark Report — 2026-09-04

**Thực hiện bởi:** Claude Code (Orchestrator)
**Mục tiêu:** Tìm model thay thế rẻ hơn DeepSeek V4 Pro cho executor tasks
**Kết quả:** ❌ Không tìm thấy alternative rẻ hơn

---

## Tóm tắt kết quả

### Models tested via opencode-go provider:
1. **DeepSeek V4 Pro** (baseline): $0.000312/task ⭐ **CHEAPEST**
2. **DeepSeek V4 Flash**: $0.000327/task (1.05x, acceptable cho simple tasks)
3. **Qwen 3.7 Max**: $0.008885/task (**28.5x đắt hơn Pro**)
4. **GLM-5**: $0.023174/task (**74.3x đắt hơn Pro**)
5. **GLM-5.1**: $0.033905/task (**108.7x đắt hơn Pro**)

### Root cause: Token inefficiency
```
DeepSeek Pro:  164 tokens input   ← efficient
Qwen 3.7:      2,813 tokens       ← 17x bloat
GLM-5:         48,035 tokens      ← 293x bloat!
```

### Recommendation
✅ **Giữ nguyên setup hiện tại** - DeepSeek Flash + Pro vẫn là optimal choice.

---

## Chi tiết báo cáo

Xem đầy đủ tại: [docs/executor-benchmark-2026-09-04.md](file:///F:/Project/Solo-Code-CLI/docs/executor-benchmark-2026-09-04.md)

---

## Issues phát hiện trong quá trình benchmark

### 1. Lỗi Lint trong `tools/benchmark_executors.py` 🔴

**SIM118:** Line 355 - `for m in by_model.keys()` → nên dùng `for m in by_model`

**S602 (Security):** Lines 155, 230 - `shell=True` trong subprocess
```python
subprocess.run(task["setup"], shell=True, ...)  # Unsafe!
```

**S607:** Line 161 - Dùng `"python"` thay vì `sys.executable`

**BLE001:** Line 278 - Blind exception catch
```python
except Exception as exc:  # Too broad
```

**Impact:**
- Fail `checklist.py` P1 gate (ruff linter)
- Fail `test_check_lint_budget.py` (62 findings vs budget 57)

---

## Kế hoạch sửa lỗi (aligned với Gemini audit)

### Bước 1: Fix lints trong `benchmark_executors.py`
1. Line 355: Bỏ `.keys()`
2. Lines 155, 230: Chuyển `shell=True` sang list args hoặc `shlex.split()`
3. Line 161: Thay `"python"` → `sys.executable`
4. Line 278: Catch specific exceptions hoặc `# noqa: BLE001` với lý do

### Bước 2: Config pytest cho Windows
Thêm vào `pyproject.toml`:
```toml
[tool.pytest.ini_options]
addopts = "--basetemp=.pytest_temp"
```

Thêm `.pytest_temp/` vào `.gitignore` và `tools/garden.py` skip list.

### Bước 3: Update tài liệu
- `AGENTS.md`: Cập nhật trạng thái OpenCode v4.2.0 (đã tái tích hợp)
- Bảng Boundaries: Thêm `.opencode/` vào danh sách engines

### Bước 4: Verify & Commit
```bash
python .github/scripts/checklist.py .  # Should be 5/5 PASS
python -m pytest tools/                 # Should be 452/452 PASS
git add -A
git commit -m "feat(opencode): add executor benchmark tool + fix lints"
```

---

## Files created
- ✅ `tools/benchmark_executors.py` - Automated benchmark tool
- ✅ `docs/executor-benchmark-2026-09-04.md` - Full report
- ✅ `.solocode/benchmark-results.jsonl` - Raw data
- ✅ `.solocode/executor-benchmark-report.md` - Initial analysis
- ✅ `.solocode/executor-final-recommendation.md` - Detailed findings

---

## Next actions for orchestrator

**Priority 1 (CRITICAL):** Fix lints in `benchmark_executors.py`
- Blocks commit
- Breaks verification gates

**Priority 2 (HIGH):** Config pytest Windows workaround
- Improves dev experience
- Prevents PermissionError

**Priority 3 (MEDIUM):** Update AGENTS.md docs
- Documentation consistency

**Priority 4 (LOW):** Consider future alternatives
- Test DeepSeek direct API (bypass opencode-go markup)
- Monitor GLM/Qwen price drops
- Evaluate free tier models

---

## Alignment với Gemini audit

Các issues trong benchmark tool **trùng khớp 100%** với Gemini audit findings:

| Issue | Gemini Finding | Status |
|-------|----------------|--------|
| SIM118 line 355 | ✅ Detected | Need fix |
| S602 shell=True | ✅ Detected | Need fix |
| S607 python path | ✅ Detected | Need fix |
| BLE001 blind catch | ✅ Detected | Need fix |
| Pytest Windows | ✅ Detected | Need config |
| AGENTS.md drift | ✅ Detected | Need update |

**Kết luận:** Gemini audit report chính xác và đầy đủ.

---

## Token budget note

Benchmark này tốn ~8-10K tokens/session (do Unicode fixes + multiple retries).
Future improvements: Skip failed models earlier to save tokens.
