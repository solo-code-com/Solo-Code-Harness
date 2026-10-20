# Code Review Checklist

Systematic checklist for thorough code reviews. Mark items with `[x]` as you verify them.

---

## 1. Security

**Input Validation & Sanitization**
- [ ] All external input is validated (type, range, format)
- [ ] SQL queries use parameterized statements (no string concatenation)
- [ ] No XSS vulnerabilities (HTML escaped in templates)
- [ ] No command injection (subprocess shell=False, fixed argv)
- [ ] Path traversal protected (Path.resolve(), relative_to() checks)
- [ ] File uploads restricted (extension whitelist, size limits)

**Authentication & Authorization**
- [ ] Authentication required for protected endpoints
- [ ] Authorization checks before sensitive operations
- [ ] Session tokens stored securely (httpOnly cookies, not localStorage)
- [ ] Password hashing uses modern algorithm (bcrypt, argon2)

**Secrets & Credentials**
- [ ] No hardcoded secrets (API keys, passwords, tokens)
- [ ] Credentials loaded from environment variables
- [ ] Secrets not logged or exposed in error messages
- [ ] No secrets committed to git (check git diff)

---

## 2. Code Quality

**Type Safety & Documentation**
- [ ] 100% type hint coverage on function signatures
- [ ] Modern Python 3.10+ syntax (dict[str, int] not Dict[str, int])
- [ ] Docstrings present with Args/Returns/Raises sections
- [ ] Complex logic has explanatory comments

**Error Handling**
- [ ] Specific exceptions caught (not bare except:)
- [ ] Error messages are actionable
- [ ] Resources cleaned up (files closed, connections released)
- [ ] No silent failures (empty except: pass without logging)

**Code Structure**
- [ ] Functions under 50 lines
- [ ] Single Responsibility Principle followed
- [ ] No code duplication (DRY principle)
- [ ] Clear, descriptive variable/function names
- [ ] Constants in UPPER_CASE at module level

---

## 3. Testing

**Test Coverage**
- [ ] Unit tests for new functions
- [ ] Integration tests for API endpoints
- [ ] Edge cases covered (empty input, null, boundary values)
- [ ] Error paths tested (invalid input, network failures)

**Test Quality**
- [ ] Tests are deterministic (no random data, no time dependencies)
- [ ] Self-test mode available for framework validation
- [ ] pytest integration (pytest-compatible test discovery)
- [ ] Test fixtures isolated (temp directories, mock external services)

**Verification**
- [ ] All tests pass locally
- [ ] Coverage maintained or improved (coverage gate)
- [ ] No flaky tests (run 3x to verify stability)

---

## 4. Performance

**Database & Queries**
- [ ] No N+1 query problems (use eager loading)
- [ ] Indexes present on frequently queried columns
- [ ] Query results paginated (limit/offset)
- [ ] Database connections properly pooled

**Async & Concurrency**
- [ ] Async functions use await (not blocking sync calls)
- [ ] Race conditions prevented (locks, atomic operations)
- [ ] Timeout guards on external calls (subprocess, HTTP requests)
- [ ] Background tasks have cancellation support

**Resource Management**
- [ ] Large files streamed (not loaded entirely into memory)
- [ ] Temp files cleaned up in finally blocks
- [ ] Memory leaks prevented (no circular references, weak refs where needed)
- [ ] Caching used appropriately (memoization, Redis)

---

## 5. Architecture

**Design Principles**
- [ ] SOLID principles followed (Single Responsibility, Open/Closed, etc.)
- [ ] Separation of concerns (business logic separate from I/O)
- [ ] Dependency injection used (not hardcoded dependencies)
- [ ] Interfaces/protocols defined for extensibility

**Dependencies**
- [ ] New dependencies justified (not reinventing stdlib)
- [ ] Dependencies pinned to specific versions
- [ ] No circular dependencies between modules
- [ ] Import paths relative to project root (not brittle relative imports)

**Maintainability**
- [ ] Configuration externalized (not hardcoded)
- [ ] Feature flags used for risky changes
- [ ] Backward compatibility maintained (or migration path provided)
- [ ] API changes documented in CHANGELOG

---

## 6. Git & Version Control

**Commit Quality**
- [ ] Commit message follows Conventional Commits (feat:, fix:, docs:, etc.)
- [ ] Commits are atomic (one logical change per commit)
- [ ] No WIP/temp commits in final PR
- [ ] Co-authored-by trailer present for AI-assisted code

**Branch Strategy**
- [ ] Feature branch created from main/master
- [ ] Branch name descriptive (feature/xyz, fix/abc)
- [ ] No commits directly to main/master
- [ ] Conflicts resolved (git status clean)

**Pre-Merge Checks**
- [ ] All verification gates pass (ruff, pytest, security scan)
- [ ] No force-push to shared branches
- [ ] PR description explains changes and testing approach
- [ ] Reviewer feedback addressed

---

## Review Completion

After marking all applicable items:

1. **Pass**: All critical items checked → Approve PR
2. **Conditional**: Minor issues → Request changes with clear action items
3. **Block**: Security/correctness issues → Block until fixed

**Critical items** (must pass):
- Security: No secrets, no injection vulnerabilities
- Testing: Tests pass, coverage maintained
- Git: Conventional commit format, no force-push to main

---

**Template version**: 1.0 (2026-08-14)
**Pattern source**: `.solocode/review-report.md`, DeepSeek harness defensive patterns
