#!/usr/bin/env bash
# verify.sh — Solo-Code-Harness Verification (Claude Code)
set -u

pass=0
fail=0
skip=0
ok()   { echo "  PASS  $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL  $1"; fail=$((fail+1)); }
warn() { echo "  SKIP  $1 (not available)"; skip=$((skip+1)); }

check_kw() {
  if [ -f "$1" ] && grep -qi "$2" "$1"; then ok "$3"; else bad "$3 (thieu '$2' trong $1)"; fi
}

is_wsl() { grep -qi microsoft /proc/version 2>/dev/null; }

TEMP_DIR="${TMPDIR:-${TEMP:-/tmp}}"
mkdir -p "$TEMP_DIR" 2>/dev/null || true

echo "=== Solo-Code-Harness Verification (Claude Code) ==="
if is_wsl; then echo "  (WSL detected — .exe-native tests will SKIP)" ; fi
echo

# ---------- STRUCTURE ----------
echo "[STRUCTURE]"
for d in .claude .github .vscode; do
  [ -d "$d" ] && ok "thu muc $d/ ton tai" || bad "thieu thu muc $d/"
done
for f in .claude/settings.json CLAUDE.md .mcp.json; do
  [ -f "$f" ] && ok "$f ton tai" || bad "thieu file $f"
done
echo

# ---------- TESTS ----------
echo "[TESTS]"
if is_wsl; then
  warn "guard tests (chay 'pytest tools/' tu PowerShell)"
  warn "ruff check (chay 'ruff check .' tu PowerShell)"
else
  GUARD_LOG="$TEMP_DIR/guard.log"
  if python -m pytest tools/test_claude_guard.py -q >"$GUARD_LOG" 2>&1; then
    ok "guard hook tests  pass"
  else
    bad "guard hook tests fail"
  fi
  # Lint only harness-owned Python; vendored SDKs live at the repo root and
  # are not ours to lint (ruff's own exclude list can't know about them).
  if ruff check tools/ .claude/hooks/ .github/scripts/ >"$TEMP_DIR/ruff.log" 2>&1; then
    ok "ruff check  no errors"
  else
    bad "ruff check co loi"
  fi
  if python tools/check_lint_budget.py >"$TEMP_DIR/lint-budget.log" 2>&1; then
    ok "check_lint_budget  pass"
  else
    bad "check_lint_budget fail"
  fi
  if python tools/check_stdlib_only.py >"$TEMP_DIR/stdlib-only.log" 2>&1; then
    ok "check_stdlib_only  pass"
  else
    bad "check_stdlib_only fail"
  fi
  if python tools/check_text_hygiene.py >"$TEMP_DIR/text-hygiene.log" 2>&1; then
    ok "check_text_hygiene  pass"
  else
    bad "check_text_hygiene fail"
  fi
  if python tools/check_bypass_claims.py >"$TEMP_DIR/bypass-claims.log" 2>&1; then
    ok "check_bypass_claims  pass"
  else
    bad "check_bypass_claims fail"
  fi
  if python tools/prose_lint.py >"$TEMP_DIR/prose.log" 2>&1; then
    ok "prose_lint  pass"
  else
    bad "prose_lint fail"
  fi
  if python tools/validate_schemas.py >"$TEMP_DIR/schemas.log" 2>&1; then
    ok "validate_schemas  pass"
  else
    bad "validate_schemas fail"
  fi
fi
echo

# ---------- SECURITY ----------
echo "[SECURITY]"
if is_wsl; then
  warn "security_scan (chay tu PowerShell)"
  warn "gitleaks (chay tu PowerShell)"
else
  if python .github/scripts/security_scan.py . >"$TEMP_DIR/sec.log" 2>&1; then
    ok "security_scan  clean"
  else
    bad "security_scan co van de"
  fi
  if command -v gitleaks >/dev/null 2>&1; then
    if gitleaks dir . --no-banner -c .gitleaks.toml >"$TEMP_DIR/gl.log" 2>&1; then
      ok "gitleaks  no leaks"
    else
      bad "gitleaks tim thay leaks"
    fi
  else
    warn "gitleaks (khong co trong PATH)"
  fi
fi
echo

# ---------- RULEBOOK ----------
echo "[RULEBOOK]"
CL=CLAUDE.md
[ -f "$CL" ] && ok "CLAUDE.md ton tai" || bad "thieu CLAUDE.md"
check_kw "$CL" "destructive" "CLAUDE.md: destructive guard"
check_kw "$CL" "socratic"    "CLAUDE.md: Socratic Gate"
check_kw "$CL" "read"        "CLAUDE.md: read before edit"
check_kw "$CL" "plan"        "CLAUDE.md: plan before implement"
check_kw "$CL" "commit"      "CLAUDE.md: commit convention"
check_kw "$CL" "DeepSeek"    "CLAUDE.md: Model Selection"
echo

# ---------- SKILLS ----------
echo "[SKILLS]"
for sk in code-review-expert brainstorming systematic-debugging testing-patterns file-editor-pro git-workflow-master api-patterns permission-guard solo-code-harness block-no-verify; do
  f=".claude/skills/$sk/SKILL.md"
  [ -f "$f" ] && ok "skill: $sk" || bad "thieu skill: $sk"
done
echo

# ---------- GARDEN ----------
echo "[GARDEN]"
# Probe by running the interpreter, not just `command -v`: on Windows,
# python3 is a Microsoft Store stub that resolves on PATH but exits with
# an "install from the Store" message, so every gated check below failed.
PY=""
for cand in python3 python py; do
  if "$cand" -c "import sys" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -n "$PY" ] && ! is_wsl; then
  if $PY tools/garden.py >"$TEMP_DIR/garden.log" 2>&1; then ok "garden  clean"; else bad "garden co drift"; fi
  if $PY -m pytest tools/ -q >"$TEMP_DIR/harness.log" 2>&1; then ok "harness tests  pass"; else bad "harness tests fail"; fi
  if $PY tools/test_integration.py >"$TEMP_DIR/integration.log" 2>&1; then ok "integration tests  pass"; else bad "integration tests fail"; fi
else
  warn "garden + tests (chay tu PowerShell)"
fi
echo

# ---------- BOUNDARY ----------
echo "[BOUNDARY]"
VIOLATIONS=0
for f in Dockerfile docker-compose.yml docker-compose.yaml requirements.txt package.json Cargo.toml go.mod; do
  [ -f "$f" ] && { bad "Root chua $f (infrastructure)"; VIOLATIONS=$((VIOLATIONS+1)); }
done
if [ "$VIOLATIONS" -eq 0 ]; then
  ok "boundary  clean"
else
  bad "boundary  $VIOLATIONS vi pham"
fi
echo

# ---------- TOTAL ----------
echo "--------------------------------------"
echo "KET QUA: $pass PASS / $((pass+fail)) checked ($skip skipped)"
[ "$fail" -eq 0 ] && exit 0 || exit 1
