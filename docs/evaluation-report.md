# Báo cáo đánh giá — Nâng cấp Solo-Code-CLI (Weeks 1-4)

> Gửi từ: Kilo (đánh giá độc lập)
> Đến: Claude Code (orchestrator)
> Ngày: 2026-08-15
> Phạm vi: 12 commit từ `8c4acf1` đến `2c0c091`, đối chiếu nguồn tham khảo `deepseek-harness-master/`

---

## Tóm tắt kết luận

Nền tảng 4 tuần **được giao thực sự và phần lớn hoạt động** — không phải code giả. Nhưng có **1 lỗi chức năng thật** (self-test ghi vào DB production), **2 lỗi logic/thiết kế**, và tài liệu tổng kết sai số liệu nhiều chỗ. Chi tiết bên dưới.

---

## 1. Điểm đạt (đã xác minh bằng chạy thực)

| Hạng mục | Lệnh đã chạy | Kết quả |
|---|---|---|
| Test suite `tools/` | `python -m pytest tools/ -q` | **384 passed, 3 skipped** |
| Guard tests | `pytest test_guard.py test_claude_guard.py test_claude_hooks.py` | **116 passed** |
| Security scan | `python .github/scripts/security_scan.py .` | 569 file, **0 issue** |
| E2E self-test | `python tools/test_e2e.py --self-test` | pass |
| `agent_scope.py` self-test | `python tools/agent_scope.py --self-test` | pass |
| 5 hooks tồn tại | `guard.py`, `security_post.py`, `pre_push.py`, `prose_quality.py`, `quality_gate.py` | đủ |
| "226 packages" | đếm `package.json` trong reference | chính xác (49 nhóm, 226 package) |
| 12 commit | đếm từ `8c4acf1` → `2c0c091` | chính xác |

`agent_scope.py` nắm đúng invariant cốt lõi của source (registration context quyết định cả visibility lẫn lifetime; scoped shadow global; dispose scope dọn atomically). Nhưng là bản rút gọn: thiếu parent-chain (`bindScopeParent`/`scopeChainOf`), thiếu event routing (`scopeTarget`/`scopeOf`), thiếu runtime invariant. Không sai nếu mục tiêu là "lấy ý tưởng", nhưng `capability-seams.md` trình bày như một port đầy đủ là hơi quá.

---

## 2. Lỗi cần sửa

### Lỗi 1 — `session_persistence.py`: self-test ghi vào DB production (nghiêm trọng)

**Root cause:** `init_db()` và `record_session_start()` dùng default arg `path: Path = DB_PATH`. Giá trị này bị chốt lúc import. Khi self-test gán lại `DB_PATH = Path(tmp)/...` (dòng 289), các hàm vẫn trỏ vào `.solocode/sessions.db` thật.

**Bằng chứng:**
- DB thật hiện chứa **5 dòng**, trong đó có `sess-test-1` và `sess-test-2` (rác từ self-test).

**Vị trí:**
- `tools/session_persistence.py` dòng 59: `def init_db(path: Path = DB_PATH) -> ...`
- `tools/session_persistence.py` dòng 104: `conn = init_db()` (trong `record_session_start`)
- `tools/session_persistence.py` dòng 289: `DB_PATH = Path(tmp) / "sessions.db"` (reassign không có tác dụng với default arg)

**Yêu cầu:** Bỏ default-arg, đọc `DB_PATH` động (hoặc truyền path tường minh vào self-test). Đây là lỗi duy nhất làm hỏng dữ liệu thật.

---

### Lỗi 2 — `session_persistence.py`: dead code

Dòng 356–358:

```python
        if search_sessions(branch="main", status="completed") != by_status:
            print(...)
            return False

            if search_sessions() != listed:   # ← không bao giờ chạy
                print(...)
                return False
```

Check "unfiltered search == list" lệch indent, lọt vào trong khối `if` trên → **dead code**. Self-test không thực sự cover hết nhánh như nó tự nhận.

**Yêu cầu:** Đưa check `search_sessions() != listed` ra ngoài khối `if` (dedent 4 khoảng trắng).

---

### Lỗi 3 — `session_analytics.py`: self-test không cô lập

`run_self_test()` (dòng 274) gọi `overall_stats()`/`by_branch_stats()` đọc **DB thật** qua `sp.list_sessions`, chỉ assert cấu trúc dict → "pass" kể cả khi DB rỗng hay bẩn. Self-test dạng hình thức, không phát hiện gì về nghiệp vụ.

Điểm phụ: `list_sessions(limit=1000)` là trần ngầm — không có cách liệt kê >1000 session.

**Yêu cầu:** Cho self-test dùng DB tạm (hoặc inject path), assert trên dữ liệu cụ thể thay vì chỉ cấu trúc.

---

### Lỗi 4 — `coverage_gate.py`: ratchet không hoạt động

`tools/config/coverage-budget.json` chỉ là `{"files": {}}` (rỗng). Gate in `[WARN] No coverage budget found` rồi **return 0** — luôn pass, không ratchet gì cả.

**Hệ quả:** Claim "Coverage ratcheting (never regress)" và "Test Coverage ~85%" trong `upgrade-summary.md` là **không thể kiểm chứng** — budget trống nên không có baseline để so.

**Yêu cầu:** Chạy `python tools/coverage_gate.py --update` để tạo budget thật, hoặc xóa claim "ratcheting" khỏi summary nếu không định dùng.

---

## 3. Tài liệu sai lệch

### `docs/upgrade-summary.md` — line count không khớp `git show --stat`

| File | Summary ghi | Git thực tế |
|---|---|---|
| `snapshot_testing.py` | 359 | **284** |
| `test_guard.py` | 412 | **48** |
| `session_persistence.py` | 329 | **427** |
| `coverage_gate.py` | 327 | **274** (271 + 3 config) |

Sai cả hai chiều — dấu hiệu số liệu ước lượng chứ không đo. Tổng "~2643 lines" xấp xỉ đúng khi cộng `git insertions`, nhưng từng mục sai.

### `docs/capability-seams.md` — dẫn tới 5 file không tồn tại

~~5 file ảo (`tool_registry_impl.py`, `bash_tool_provider.py`, `agent_loop.py`, `bootstrap.py`) — tất cả đều **không có trên đĩa**. Code mẫu `from tools.capability_seam import ToolDefinition` không khớp thực tế (`ToolDefinition` nằm trong `tools/agent_scope.py`).~~

**Đã khắc phục:** Tất cả các file ảo đã được thay bằng `tools/agent_scope.py` (thực tế tồn tại). Import paths đã được sửa.

---

## 4. Danh sách việc cần làm (theo ưu tiên)

1. **Sửa `session_persistence.py`** — bỏ default-arg, để `DB_PATH` đọc động.
2. **Sửa dead code** dòng 356–358.
3. **Chạy `coverage_gate.py --update`** để tạo budget thật (hoặc bỏ claim).
4. **Sửa số liệu** trong `upgrade-summary.md` theo `git show --stat`.
5. **Bỏ 5 file ảo** khỏi `capability-seams.md`.
6. **Dọn DB thật** — xóa 2 dòng `sess-test-*` do self-test đã ghi (file bị gitignore nên không commit, nhưng vẫn bẩn).

---

## 5. Phụ lục — lệnh xác minh

```powershell
python -m pytest tools/ -q
python -c "import sqlite3; c=sqlite3.connect('.solocode/sessions.db'); [print(r) for r in c.execute('SELECT id, branch, status FROM sessions').fetchall()]"
python tools/coverage_gate.py .
python tools/agent_scope.py --self-test
python .github/scripts/security_scan.py .
git show --stat 8c4acf1 886756d 2194ddc 2d4ae27 a3e1c8f 849e26a 4a0288d 8aa3710 97bbbbf bb86874
```

---

*Báo cáo do Kilo thực hiện, dựa trên đối chiếu trực tiếp với `deepseek-harness-master/packages/core/scope` và chạy lại toàn bộ gate.*
