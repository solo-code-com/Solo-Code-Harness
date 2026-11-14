# Phase 3 Post-Review Fixes

## Who you are and what this is

You are DeepSeek, fixing 3 issues you found during Phase 2 verification. These
are quick fixes (2-10 minutes total). Phase 2 commit is already done; these
fixes will be a separate commit.

---

## Critical constraints (same as before)

1. **Harness boundary** — allowed paths listed per-task
2. **Cross-engine parity** — edit `.kilo/` first, mirror to others
3. **Verify every claim** — table with command + output
4. **No protected config edits**

---

## Fix 1: Add engine scope to dynamic-workflow-orchestration skill

### Issue

The skill describes `phase()`, `agent()`, `parallel()`, `pipeline()` primitives
from Claude Code's Workflow tool. But the skill file is mirrored to all 4
engines (`.claude/`, `.kilo/`, `.copilot/`, `.gemini/`), and **OpenCode, Kilo,
Copilot do NOT have the Workflow tool**.

When an OpenCode or Kilo agent reads this skill, they get instructions to use
tools they don't have → confusion.

### Fix

Add **1 sentence at the top** of the skill body (after frontmatter, before the
`# Dynamic Workflow Orchestration` heading):

```markdown
---
name: dynamic-workflow-orchestration
description: "..."
license: MIT
---

> **Claude Code only.** Kilo, OpenCode, and Copilot do not have the Workflow tool — use CLI delegation (`tools/antigravity_delegate.py`, `tools/opencode_delegate.py`) instead.

# Dynamic Workflow Orchestration
...
```

### Files to edit

- `.kilo/skill/dynamic-workflow-orchestration/SKILL.md` (source of truth)
- `.claude/skills/dynamic-workflow-orchestration/SKILL.md` (mirror)
- `.copilot/skill/dynamic-workflow-orchestration/SKILL.md` (mirror)
- `.gemini/antigravity/skills/dynamic-workflow-orchestration/SKILL.md` (mirror)

Add the **exact same line** to all 4 files (after frontmatter, before heading).

### Verify

```bash
grep -n "Claude Code only" \
  .kilo/skill/dynamic-workflow-orchestration/SKILL.md \
  .claude/skills/dynamic-workflow-orchestration/SKILL.md \
  .copilot/skill/dynamic-workflow-orchestration/SKILL.md \
  .gemini/antigravity/skills/dynamic-workflow-orchestration/SKILL.md

# Expect: line 6 in all 4 files (after frontmatter ends at line 5)

python tools/garden.py 2>&1 | grep "Skill content drift"
# Expect: [OK] Skill content drift
```

---

## Fix 2: Change `rm -rf *` from deny to ask

### Issue

`permissions.deny` includes `"Bash(rm -rf *)"` — this blocks **legitimate
cleanup** like `rm -rf node_modules`, `rm -rf dist`, `rm -rf build`.

The old `guard.py` hook used context (`rm -rf build /` → block, `rm -rf dist`
→ allow). The new static deny list has no context → blocks everything.

### Fix

In `.claude/settings.json`:
1. **Remove** `"Bash(rm -rf *)"` from `permissions.deny` array
2. **Add** `"Bash(rm -rf *)"` to `permissions.ask` array (create the array if
   it doesn't exist)

**Before:**
```json
{
  "permissions": {
    "deny": [
      "Read(./.env)",
      "Read(./.env.*)",
      "Read(./secrets/**)",
      "Bash(rm -rf *)",
      "Bash(rm -rf /*)",
      "Bash(git push --force *)",
      "Bash(git push -f origin main*)",
      "Bash(git push -f origin master*)",
      "Bash(git reset --hard*)",
      "Bash(DROP TABLE*)"
    ]
  }
}
```

**After:**
```json
{
  "permissions": {
    "deny": [
      "Read(./.env)",
      "Read(./.env.*)",
      "Read(./secrets/**)",
      "Bash(rm -rf /*)",
      "Bash(git push --force *)",
      "Bash(git push -f origin main*)",
      "Bash(git push -f origin master*)",
      "Bash(git reset --hard*)",
      "Bash(DROP TABLE*)"
    ],
    "ask": [
      "Bash(rm -rf *)"
    ]
  }
}
```

**Rationale:** `rm -rf /*` (root) still denied. `rm -rf *` (current dir)
prompts user → allows legitimate cleanup with human approval.

### Files to edit

- `.claude/settings.json`

**Do NOT edit** `.kilo/kilo.jsonc` or other engines — `permissions.deny` is
Claude Code-specific (other engines don't read it).

### Verify

```bash
jq '.permissions.deny | length' .claude/settings.json
# Expect: 9 (was 10, removed 1)

jq '.permissions.ask | length' .claude/settings.json
# Expect: 1 (new array)

jq '.permissions.ask[0]' .claude/settings.json
# Expect: "Bash(rm -rf *)"

jq '.permissions.deny | map(select(. == "Bash(rm -rf *)"))' .claude/settings.json
# Expect: [] (empty — pattern removed)

python tools/validate_schemas.py 2>&1 | tail -3
# Expect: Files checked: 70 / Errors: 0
```

---

## Fix 3: Wrap PATH_SCOPED_RULES import in try/except

### Issue

`tools/garden.py` imports `PATH_SCOPED_RULES` from `tools/claude_engine.py`:

```python
from claude_engine import PATH_SCOPED_RULES
```

If `claude_engine.py` has a syntax error, this import crashes garden.py with
unhelpful traceback instead of reporting a drift issue.

### Fix

Wrap the import in `try/except` at the top of `tools/garden.py`. If import
fails, set `PATH_SCOPED_RULES = None` and let the check that uses it report
drift gracefully.

**Current code (around line 15-20):**
```python
from claude_engine import PATH_SCOPED_RULES
```

**New code:**
```python
try:
    from claude_engine import PATH_SCOPED_RULES
except Exception as e:
    # If claude_engine.py has syntax errors, don't crash garden —
    # let the check below report it as drift
    PATH_SCOPED_RULES = None
    import sys
    print(f"WARNING: Failed to import PATH_SCOPED_RULES from claude_engine.py: {e}", file=sys.stderr)
```

**Then find the check that uses `PATH_SCOPED_RULES`** (search for
`PATH_SCOPED_RULES` in garden.py, likely in a function checking rules drift).
Add a guard:

```python
if PATH_SCOPED_RULES is None:
    issues.append({
        'check': 'path_scoped_rules_import',
        'severity': 'error',
        'message': 'Failed to import PATH_SCOPED_RULES from claude_engine.py — check for syntax errors'
    })
    return issues
```

### Files to edit

- `tools/garden.py`

### Verify

```bash
# Test: introduce syntax error in claude_engine.py temporarily
echo "SYNTAX ERROR" >> tools/claude_engine.py

python tools/garden.py 2>&1 | grep -i "PATH_SCOPED_RULES\|syntax"
# Expect: WARNING about import failure, or drift issue reported

# Revert
git checkout tools/claude_engine.py

python tools/garden.py 2>&1 | tail -3
# Expect: Total drift issues: 0 (or 1 if lock_stale)
```

---

## End-of-fixes cleanup

After all 3 fixes:

```bash
python tools/deploy.py --root-lock
ruff check .
python tools/check_lint_budget.py
python tools/validate_schemas.py
python tools/garden.py
python -m pytest tools/test_garden.py -q
python .github/scripts/security_scan.py .
```

**Expected:** All gates green, garden drift = 0

---

## Final report format

Return markdown with:

1. **Per-fix status**
   | Fix | Status | Files edited |
   |-----|--------|-------------|

2. **Claim / Command / Output**
   | Claim | Command | Output |
   |-------|---------|--------|

3. **Verification results**
   - Fix 1: 4 files have "Claude Code only" line at same position
   - Fix 2: `deny` array length 9, `ask` array length 1
   - Fix 3: Syntax error test passed (garden reports issue, doesn't crash)

4. **Gate results** (all 6 commands + output)

5. **Unexpected findings** (if any)

---

## Done when

- Skill has engine scope warning (all 4 engines)
- `permissions.deny` → 9 items, `permissions.ask` → 1 item
- `garden.py` import wrapped, syntax error handled gracefully
- All 6 gates green
- Garden drift = 0
