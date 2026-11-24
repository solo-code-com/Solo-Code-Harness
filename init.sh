#!/usr/bin/env bash
# init.sh — Solo-Code Harness cross-platform bootstrap.
#
# Detects which harness engines are present (Kilo, OpenCode, Claude Code,
# OpenCode, Gemini), reports their rulebooks, and prints the shared-state
# summary. Works from any harness platform directory — no engine is hardcoded.
#
# Usage:
#   ./init.sh            # human-readable report
#   ./init.sh --quiet    # only warnings/errors
#
# Portable: pure POSIX sh + coreutils; Python is optional (used only for the
# shared-state summary). Falls back py -> python3 -> python for the interpreter.
set -u

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

say() { [ "$QUIET" -eq 1 ] || echo "$@"; }

# ── Resolve a Python interpreter (optional) ─────────────────────────────
PYTHON=""
for cand in py python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    # `py` needs a version probe on Windows; treat any success as usable.
    if "$cand" -c "import sys" >/dev/null 2>&1; then
      PYTHON="$cand"
      break
    fi
  fi
done

say "=== Solo-Code Harness — Bootstrap ==="
say "CWD: $(pwd)"
say ""

# ── Engine rulebooks ────────────────────────────────────────────────────
say "[RULEBOOKS]"
engines_found=0
check_rulebook() {
  # $1 = path, $2 = engine label
  if [ -e "$1" ]; then
    say "  [x] $2 -> $1"
    engines_found=$((engines_found + 1))
  fi
}
check_rulebook "CLAUDE.md"                          "Claude Code"
check_rulebook "AGENTS.md"                          "Kilo / OpenCode (AGENTS.md)"
check_rulebook ".gemini"                            "Gemini / Antigravity"
[ "$engines_found" -eq 0 ] && say "  (no rulebooks detected — is this a harness project?)"
say ""

# ── Engine directories ──────────────────────────────────────────────────
say "[ENGINE DIRS]"
for pair in ".kilo:Kilo" ".opencode:OpenCode" ".gemini:Gemini"; do
  dir="${pair%%:*}"; label="${pair#*:}"
  [ -d "$dir" ] && say "  [x] $label ($dir/)"
done
say ""

# ── Shared state summary (optional) ─────────────────────────────────────
say "[SHARED STATE]"
if [ -n "$PYTHON" ] && [ -f "tools/shared_state.py" ]; then
  if [ -f ".solocode/shared-state.db" ]; then
    "$PYTHON" tools/shared_state.py show 2>/dev/null \
      | sed 's/^/  /' \
      || say "  (shared-state read failed — non-fatal)"
  else
    say "  (no .solocode/shared-state.db yet — created on first engine run)"
  fi
else
  say "  (Python or tools/shared_state.py unavailable — skipping)"
fi
say ""

# ── Result ──────────────────────────────────────────────────────────────
if [ "$engines_found" -eq 0 ] && [ ! -d ".kilo" ]; then
  echo "[init] No harness engines detected." >&2
  exit 1
fi
say "[init] Bootstrap OK — $engines_found rulebook(s) active."
exit 0
