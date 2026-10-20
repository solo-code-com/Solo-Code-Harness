# Solo-Code Harness — Makefile
# ==============================
# All tooling runs through `python`. No `uv`, no `pip` required.
# The harness adapter has zero dependencies beyond Python stdlib.

# Resolve Python: try python3, then python, then py (Windows launcher)
PY := $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null || echo py)

.PHONY: help generate generate-claude validate garden lint-budget stdlib-only text-hygiene prose lint-pin test test-integration eval check security-scan gitleaks

help:
	@echo "Solo-Code Harness — Quality Gates"
	@echo "=================================="
	@echo ""
	@echo "Development:"
	@echo "  make generate           Regenerate the Claude Code engine from .kilo/ source"
	@echo "  make generate-claude    Same as 'make generate' (alias)"
	@echo "  make validate           Validate agent/skill frontmatter schemas (.kilo/)"
	@echo "  make garden             Drift detection (.kilo/ <-> .claude/ generated, .copilot/ parity)"
	@echo "  make lint-budget        Ratchet for S/BLE rules absent from .ruff.toml"
	@echo "  make stdlib-only        Enforce SPEC section 4: tools/ and .github/scripts/ are stdlib-only"
	@echo "  make text-hygiene      Enforce LF endings, no trailing space, final newline"
	@echo "  make prose              Prose budget gate (long sentences, semicolons, em-dashes)"
	@echo "  make lint-pin           Run the pinned ruff (the version the commit hook uses)"
	@echo "  make test               Run full test suite (auto-discovers tools/test_*.py)"
	@echo "  make test-integration   Copilot structure + shared-state schema checks"
	@echo "  make check              Full CI gate: lint + validate + garden + test + security"
	@echo ""
	@echo "Security:"
	@echo "  make security-scan      Scan for hardcoded secrets"
	@echo "  make gitleaks           Scan with gitleaks (.gitleaks.toml config)"
	@echo ""

generate:
	$(PY) tools/generate_harness.py --harness claude

generate-claude:
	$(PY) tools/generate_harness.py --harness claude

validate:
	$(PY) tools/validate_schemas.py

garden:
	$(PY) tools/garden.py

lint-budget:
	$(PY) tools/check_lint_budget.py

stdlib-only:
	$(PY) tools/check_stdlib_only.py

text-hygiene:
	$(PY) tools/check_text_hygiene.py

prose:
	$(PY) tools/prose_lint.py

# The commit hook pins ruff 0.9.1, which still raises UP038; the ruff on PATH does not.
lint-pin:
	$(PY) -m pre_commit run ruff --all-files

test:
	$(PY) -m pytest tools/ -v

test-integration:
	$(PY) tools/test_integration.py

check:
	@echo "=== Lint (ruff) ==="
	ruff check . --exclude "Solo-Code-Harness" || exit 1
	@echo ""
	@echo "=== Lint Budget (S/BLE families absent from .ruff.toml) ==="
	$(PY) tools/check_lint_budget.py || exit 1
	@echo ""
	@echo "=== Stdlib-Only Gate (SPEC section 4) ==="
	$(PY) tools/check_stdlib_only.py || exit 1
	@echo ""
	@echo "=== Text Hygiene (LF, no trailing space, final newline) ==="
	$(PY) tools/check_text_hygiene.py || exit 1
	@echo ""
	@echo "=== Prose Budget ==="
	$(PY) tools/prose_lint.py || exit 1
	@echo ""
	@echo "=== Schema Validation ==="
	$(PY) tools/validate_schemas.py || exit 1
	@echo ""
	@echo "=== Garden (drift detection) ==="
	$(PY) tools/garden.py || exit 1
	@echo ""
	@echo "=== Harness Tests (auto-discovers all tools/test_*.py) ==="
	$(PY) -m pytest tools/ -q || exit 1
	@echo ""
	@echo "=== Security Scan ==="
	$(PY) .github/scripts/security_scan.py . || exit 1
	@echo ""
	@echo "  All gates passed."

security-scan:
	$(PY) .github/scripts/security_scan.py .

gitleaks:
	gitleaks dir . --no-banner -c .gitleaks.toml
