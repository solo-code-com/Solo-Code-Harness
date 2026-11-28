#!/usr/bin/env python3
"""
Secret Pattern Coverage Tests (cross-scanner)
=============================================
This harness has THREE independent secret detectors, each a separate
hand-maintained copy of the same pattern list:

  1. tools/guard_hook.py               -- engine-neutral gate, blocks the write (exit 2)
  2. .kilo/hooks/pre-tool-use/secret-scan.js -- Kilo PreToolUse equivalent
  3. .github/scripts/security_scan.py  -- CI gate, blocks the merge (exit 1)

Nothing structurally forced them to agree, and they drifted: a token format
added to one was not added to the others. Worse, all three shared the same
blind spots -- every modern prefixed-token format (sk-ant-, sk-proj-, npm_,
glpat-, dop_v1_) and the Authorization: Bearer header form passed all three
untouched, including this project's own Anthropic API key format.

These tests pin ONE corpus against ALL THREE scanners, so a format can never
again be covered by one detector and missed by the others. Every MUST_DETECT
entry was verified failing on all three before the patterns were fixed.

Usage:
    python -m pytest tools/test_secret_patterns.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "tools" / "guard_hook.py"
SECRET_SCAN_JS = ROOT / ".kilo" / "hooks" / "pre-tool-use" / "secret-scan.js"

sys.path.insert(0, str(ROOT / ".github" / "scripts"))
from security_scan import SECRET_PATTERNS as CI_PATTERNS  # noqa: E402
from security_scan import UNSAFE_PATTERNS as CI_UNSAFE_PATTERNS  # noqa: E402
from security_scan import scan_file as ci_scan_file  # noqa: E402

# ── Shared corpus ────────────────────────────────────────────────────────
# Synthetic values: correct SHAPE, never a real credential. Each entry is
# (id, sample_line). The `id` doubles as the pytest test id.
MUST_DETECT: list[tuple[str, str]] = [
    # The format this project itself uses -- missed by all 3 scanners because
    # `sk-[a-zA-Z0-9]{20,}` does not cross the "-" in "sk-ant-".
    (
        "anthropic_sk_ant",
        "ANTHROPIC_API_KEY=sk-ant-api03-AbCdEf1234567890AbCdEf1234567890AbCdEfGh",
    ),
    ("openai_sk_proj", "OPENAI_API_KEY=sk-proj-AbCdEf1234567890AbCdEfGhIjKl"),
    # Bearer in an HTTP header has no quotes and no "=", so the quoted
    # `hardcoded_token` pattern never matched it.
    (
        "bearer_header",
        'curl -H "Authorization: Bearer AbCdEf1234567890AbCdEfGhIjKl" https://api.example.com',
    ),
    ("npm_token", "NPM_TOKEN=npm_AbCdEf1234567890AbCdEf1234567890abcd"),
    ("gitlab_pat", "CI_TOKEN=glpat-AbCdEf1234567890AbCd"),
    (
        "github_fine_grained",
        "GITHUB_TOKEN=github_pat_11ABCDEFG0AbCdEf1234567890AbCdEf1234567890",
    ),
    ("digitalocean", "DO_TOKEN=dop_v1_" + "a" * 64),
    # Regression guards -- these already worked; they must keep working.
    ("aws_access_key", "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"),
    ("google_api_key", "GOOGLE_KEY=AIzaSyA1234567890abcdefghijklmnopqrstuv"),
    ("slack_token", "SLACK=xoxb-1234567890-AbCdEfGhIjKl"),
    ("private_key_pem", "-----BEGIN RSA PRIVATE KEY-----"),
]

# Lines that must NOT trip any scanner. A detector that cries wolf gets
# switched off, so false positives cost more than misses (SPEC 7.2.1).
MUST_NOT_DETECT: list[tuple[str, str]] = [
    (
        "prose_about_keys",
        "Store the API key in an environment variable, never in source.",
    ),
    ("short_placeholder", 'api_key = "TODO"'),
    ("env_var_reference", 'api_key = os.environ["ANTHROPIC_API_KEY"]'),
    ("sk_prefixed_word", "The sketch-utils module handles rendering."),
    ("bearer_prose", "Send the token as a Bearer credential in the header."),
    ("npm_install", "npm_config_registry is set by the CI runner."),
    ("markdown_placeholder", "ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx"),
]


def _ci_scan(line: str) -> list[str]:
    """Run the CI scanner's patterns exactly as scan_file() does."""
    return [
        desc
        for _rule_id, pattern, desc in CI_PATTERNS
        if re.search(pattern, line, re.IGNORECASE)
    ]


# These tests assert the guard's SECRET behavior, including negative cases
# ("this benign line must NOT block"). Executor mode is default-ON and blocks
# every Edit/Write regardless of content, which would turn each negative case
# into a false pass/fail unrelated to secret detection. Point the guard at a
# throwaway root with executor mode off so this file tests one thing only.
_EXECUTOR_OFF_ROOT = Path(tempfile.mkdtemp(prefix="secretpat-exec-off-"))
(_EXECUTOR_OFF_ROOT / ".solocode").mkdir(parents=True, exist_ok=True)
(_EXECUTOR_OFF_ROOT / ".solocode" / "executor-mode").write_text("off", encoding="utf-8")


def _run_guard(payload: dict) -> int:
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(_EXECUTOR_OFF_ROOT)},
    )
    return proc.returncode


def _run_secret_scan_js_proc(
    content: str, file_path: str = "app.py"
) -> subprocess.CompletedProcess:
    """Invoke the Kilo secret-scan hook on a code file by default.

    The default used to be config.yml, where every finding escalates to a block,
    so the code-file path -- the one that only warned -- was never exercised.
    """
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": file_path, "content": content},
    }
    return subprocess.run(
        ["node", str(SECRET_SCAN_JS)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def _run_secret_scan_js(content: str, file_path: str = "app.py") -> int:
    return _run_secret_scan_js_proc(content, file_path).returncode


# ── 1. CI gate: .github/scripts/security_scan.py ─────────────────────────


@pytest.mark.parametrize("sample_id,line", MUST_DETECT, ids=[s[0] for s in MUST_DETECT])
def test_ci_scanner_detects(sample_id, line):
    assert _ci_scan(line), f"security_scan.py missed {sample_id}: {line[:60]}"


@pytest.mark.parametrize(
    "sample_id,line", MUST_NOT_DETECT, ids=[s[0] for s in MUST_NOT_DETECT]
)
def test_ci_scanner_no_false_positive(sample_id, line):
    hits = _ci_scan(line)
    assert not hits, f"security_scan.py false-positive on {sample_id}: {hits}"


# ── 2. Claude PreToolUse gate: .claude/hooks/guard.py ────────────────────


@pytest.mark.parametrize("sample_id,line", MUST_DETECT, ids=[s[0] for s in MUST_DETECT])
def test_guard_blocks_secret_in_content(sample_id, line):
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "config.py", "content": line},
    }
    assert _run_guard(payload) == 2, f"guard.py allowed a write containing {sample_id}"


@pytest.mark.parametrize(
    "sample_id,line", MUST_NOT_DETECT, ids=[s[0] for s in MUST_NOT_DETECT]
)
def test_guard_allows_non_secret(sample_id, line):
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "notes.md", "content": line},
    }
    assert _run_guard(payload) == 0, f"guard.py false-blocked {sample_id}"


# ── 3. Kilo PreToolUse gate: .kilo/hooks/pre-tool-use/secret-scan.js ─────


@pytest.mark.parametrize("sample_id,line", MUST_DETECT, ids=[s[0] for s in MUST_DETECT])
def test_kilo_secret_scan_blocks(sample_id, line):
    assert _run_secret_scan_js(line) == 2, f"secret-scan.js allowed {sample_id}"


@pytest.mark.parametrize(
    "sample_id,line", MUST_NOT_DETECT, ids=[s[0] for s in MUST_NOT_DETECT]
)
def test_kilo_secret_scan_allows(sample_id, line):
    assert _run_secret_scan_js(line) == 0, f"secret-scan.js false-blocked {sample_id}"


# A format pattern identifies a credential by shape, so it blocks in code.
# Before 2026-11-28 only config extensions blocked; .py/.js/.sh findings warned.
CODE_EXTENSIONS = ["app.py", "src/index.ts", "src/App.tsx", "deploy.sh", "main.go"]


@pytest.mark.parametrize("file_path", CODE_EXTENSIONS)
def test_kilo_secret_scan_blocks_a_format_secret_in_code_files(file_path):
    assert _run_secret_scan_js(MUST_DETECT[0][1], file_path) == 2, (
        f"{file_path} let a format-identifiable secret through as a warning"
    )


# A keyword pattern blocks in a config file, where a literal value belongs.
# Each entry was measured passing before the fix named in its comment.
CONFIG_SECRETS = [
    # An unbounded `secret[-_ ].*` allowlist waved the real Notion token shape
    # (`secret_` + 43 chars) through.
    (
        "notion_token",
        # Split so the fixture is not itself a token-shaped string: GitHub push
        # protection blocks a contiguous `secret_` + 43 chars, and this file is
        # pushed to a public remote.
        'api_key = "secret_' + "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5zA7bC9d" + '"',
    ),
    # The value test took the longest run of the whole match, so the key NAME
    # won and the punctuated value was never tested.
    ("punctuated_password", 'password = "P@ss.w0rd!99"'),
    ("api_key_value", 'api_key = "Xk9mQ2pL7vR4tN8w"'),
    # A placeholder prefix is not a placeholder value.
    ("placeholder_suffix", 'password = "test1234!A9$zQ"'),
    # `your[-_ ].*` allowed any 40-char credential starting with `your_`.
    ("your_prefixed", 'api_key = "your_aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU"'),
    # `Authorization: Basic <b64>` has a space but is still a credential.
    ("basic_scheme_value", 'api_key = "Basic dXNlcjpzM2NyM3RwYXNz"'),
    # Unquoted assignment whose value is a bare literal.
    ("unquoted_bare", "password = Xk9mQ2pL7vR4tN8w"),
    # A synthetic first match must not hide a real second match on the line.
    (
        "two_secrets_one_line",
        'cfg = {password: "test1234", secret: "Xk9mQ2pL7vR4tN8w"}',
    ),
    # Django's settings shape; the keyword list stopped at `secret`.
    ("django_secret_key", 'SECRET_KEY = "a9F2kL8mP3qR7tV1wX5yZ0bC4dE6gH8j"'),
    # The allowlist read the FIRST quoted value in the match, so an earlier
    # placeholder hid the real key that follows it.
    (
        "placeholder_then_real",
        'aws_env = "test", aws_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"',
    ),
]


@pytest.mark.parametrize(
    "sample_id,line", CONFIG_SECRETS, ids=[s[0] for s in CONFIG_SECRETS]
)
def test_kilo_secret_scan_blocks_heuristic_secrets_in_config(sample_id, line):
    assert _run_secret_scan_js(line, ".env") == 2, f"hook allowed {sample_id}"


# The same finding is a WARNING in code: `password = current_password` is
# ordinary code, and a hook that blocks it is a hook people disable. The CI gate
# catches `password = "new_password"` but not `password = current_password`; an
# unprefixed value in code is a known gap.
HEURISTIC_IN_CODE = [
    ("unquoted_identifier", "password = current_password"),
    ("js_identifier", "const token = defaultAccessToken;"),
    ("header_name", 'api_key = "x-api-key"'),
    ("field_constant", 'password = "new_password"'),
    ("django_secret_key", 'SECRET_KEY = "a9F2kL8mP3qR7tV1wX5yZ0bC4dE6gH8j"'),
    ("unprefixed_key", 'api_key = "Xk9mQ2pL7vR4tN8w"'),
]


@pytest.mark.parametrize(
    "sample_id,line", HEURISTIC_IN_CODE, ids=[s[0] for s in HEURISTIC_IN_CODE]
)
def test_kilo_secret_scan_warns_but_does_not_block_heuristics_in_code(sample_id, line):
    proc = _run_secret_scan_js_proc(line, "app.py")
    assert proc.returncode == 0, f"false block on {sample_id}"
    # A warning proves the pattern fired; silence would mean the test passes for
    # the wrong reason.
    assert "WARNING" in proc.stderr, f"{sample_id} did not even warn"


# Ordinary code must never block, whatever the pattern tier.
CODE_SHAPES = [
    "token = compute_token(payload)",
    "api_key = config.get_api_key()",
    "return Client(api_key=api_key)",
    "self.password = request.form['password']",
    'DATABASE_URL = "postgres://localhost:5432/app"  # dev@example.com',
]


@pytest.mark.parametrize("line", CODE_SHAPES)
def test_kilo_secret_scan_allows_ordinary_code(line):
    assert _run_secret_scan_js(line) == 0, f"false block on {line}"


# Values that DO match a keyword pattern and are waived by the allowlist, which
# is anchored to the whole value.
ALLOWLISTED_VALUES = [
    'password = "test1234"',
    'api_key = "dummy-key"',
    'secret = "changeme"',
    'api_key = "<your-key-here>"',
    'api_key = "ANTHROPIC_API_KEY"',
    'password = "Incorrect password"',
    'aws_key = "0000000000000000000000000000000000000000"',
]


@pytest.mark.parametrize("line", ALLOWLISTED_VALUES)
def test_kilo_secret_scan_allows_placeholder_values_in_config(line):
    """In a config file these would block unless the allowlist waived them."""
    assert _run_secret_scan_js(line, ".env") == 0, f"false block on {line}"


# Runtime references are excluded by the patterns themselves (`(?!\$)` and
# `[^\s"'$]`), not by the allowlist, so they never even produce a finding.
RUNTIME_REFERENCES = [
    'api_key = "${GITHUB_TOKEN}"',
    "password = $DB_PASS",
    'API_KEY="${API_KEY:-}"',
    'api_key = os.environ["ANTHROPIC_API_KEY"]',
]


@pytest.mark.parametrize("line", RUNTIME_REFERENCES)
def test_kilo_secret_scan_ignores_runtime_references(line):
    assert _run_secret_scan_js(line, ".env") == 0, f"false block on {line}"


# Promoting .vue/.html/.js/.ts to scanning turned a benign-URL false positive
# into a block, so the URL patterns refuse `/`, `?` and `#` in the userinfo.
BENIGN_URLS = [
    (
        "vue_click_attribute",
        '<a href="http://localhost:3000" @click="open">Open</a>',
        "App.vue",
    ),
    ("vite_client", 'import "http://localhost:3000/@vite/client"', "App.vue"),
    ("path_at_sign", 'const u = "https://example.com/users/@me";', "src/index.ts"),
    (
        "postgres_host_port",
        'DATABASE_URL = "postgres://localhost:5432/app"  # dev@example.com',
        "db.py",
    ),
]


@pytest.mark.parametrize(
    "sample_id,line,file_path", BENIGN_URLS, ids=[s[0] for s in BENIGN_URLS]
)
def test_kilo_secret_scan_does_not_block_benign_urls(sample_id, line, file_path):
    assert _run_secret_scan_js(line, file_path) == 0, f"false block on {sample_id}"


# A format pattern must still identify a credential by SHAPE. These lines are
# prose, a constant name, or a template -- each blocked before the fix named in
# its comment.
FORMAT_LOOKALIKES = [
    # `Bearer` followed by a 20-char snake_case word is a sentence.
    (
        "bearer_sentence",
        'const err = "Invalid Bearer authorization_header";',
        "auth.ts",
    ),
    ("bearer_reference", "// Bearer process.env.API_TOKEN", "auth.ts"),
    # A constant name, not a token: no digit in the 20+ char tail.
    (
        "github_constant_name",
        'GITHUB_TOKEN_AUTHENTICATION_HEADER = "Authorization"',
        "config.py",
    ),
    ("github_variable_name", "github_token_environment_variable = read()", "config.py"),
    # A runtime template, not a literal credential.
    (
        "postgres_template",
        "const url = `postgres://${dbUser}:${dbPass}@localhost:5432/app`;",
        "db.ts",
    ),
    (
        "mongodb_template",
        "const u = `mongodb://${user}:${pass}@cluster0.example.net/db`;",
        "db.ts",
    ),
]


@pytest.mark.parametrize(
    "sample_id,line,file_path",
    FORMAT_LOOKALIKES,
    ids=[s[0] for s in FORMAT_LOOKALIKES],
)
def test_kilo_secret_scan_does_not_block_format_lookalikes(sample_id, line, file_path):
    assert _run_secret_scan_js(line, file_path) == 0, f"false block on {sample_id}"


def test_basic_auth_still_matches_a_real_credential_url():
    assert _run_secret_scan_js('url = "https://admin:s3cr3tpass@db.internal"') == 2


def test_postgres_uri_still_matches_a_real_credential_url():
    assert (
        _run_secret_scan_js('url = "postgres://user:s3cr3t@db.internal:5432/app"') == 2
    )


def test_kilo_secret_scan_still_blocks_the_aws_documented_example():
    """`AKIAIOSFODNN7EXAMPLE` is in the must-detect corpus; a format pattern is
    never waived by the allowlist."""
    assert _run_secret_scan_js("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE") == 2


def test_kilo_secret_scan_warns_but_does_not_block_in_prose():
    """Docs quote keys. A README edit must not be blocked over an example."""
    proc = _run_secret_scan_js_proc(MUST_DETECT[0][1], "README.md")
    assert proc.returncode == 0
    assert "WARNING" in proc.stderr


def test_kilo_secret_scan_blocks_a_json_config_secret_value():
    """`"api_key": "..."` is missed by all three scanners when the value has no
    recognisable prefix (a known gap), so this pins the half that works: a
    format-identifiable key inside a .json config."""
    line = '{"api_key": "sk-ant-api03-AbCdEf1234567890AbCdEfGh"}'
    assert _run_secret_scan_js(line, "config.json") == 2


# ── 4. Cross-scanner parity invariant ────────────────────────────────────


@pytest.mark.parametrize("sample_id,line", MUST_DETECT, ids=[s[0] for s in MUST_DETECT])
def test_all_three_scanners_agree(sample_id, line):
    """The drift that caused this file: a format covered by one detector and
    missed by another. Pin agreement, not just individual coverage."""
    verdicts = {
        "security_scan.py": bool(_ci_scan(line)),
        "guard.py": _run_guard(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "config.py", "content": line},
            }
        )
        == 2,
        "secret-scan.js": _run_secret_scan_js(line) == 2,
    }
    missed = [name for name, caught in verdicts.items() if not caught]
    assert not missed, f"{sample_id} caught by some scanners but missed by: {missed}"


# ── 5. strict mode: injection patterns in code, never in prose ────────────
# `UNSAFE_PATTERNS` sat behind `--strict` with no caller, so a whole class of
# injection patterns was never scanned by any gate. Turning it on over prose
# produced 68 findings, every one a false positive, so docs are excluded while
# secrets stay included everywhere.


def test_strict_does_not_scan_prose_for_injection_patterns(tmp_path):
    doc = tmp_path / "skill.md"
    doc.write_text(
        "Never call `eval(` or `os.system(` on user input.\n", encoding="utf-8"
    )
    assert ci_scan_file(doc, strict=True) == []


def test_strict_still_scans_secrets_in_prose(tmp_path):
    doc = tmp_path / "skill.md"
    doc.write_text(
        'ANTHROPIC_API_KEY="sk-ant-AbCdEf1234567890AbCdEfGhIjKl"\n', encoding="utf-8"
    )
    assert ci_scan_file(doc, strict=True), "a secret in a .md file must still be caught"


def test_strict_flags_injection_patterns_in_code(tmp_path):
    code = tmp_path / "app.py"
    code.write_text("os.system(user_input)\n", encoding="utf-8")
    findings = ci_scan_file(code, strict=True)
    assert any("UNSAFE" in finding.message for finding in findings)


def test_non_strict_ignores_injection_patterns(tmp_path):
    code = tmp_path / "app.py"
    code.write_text("os.system(user_input)\n", encoding="utf-8")
    assert ci_scan_file(code, strict=False) == []


# The bare `exec\(` rule also matched the argv wrapper `exec(cmd, args)` and its
# definition, so turning on --strict flagged 17 benign hook call sites. Both
# directions are pinned: the injection forms still fire, the argv forms do not.

EXEC_INJECTION_FORMS = [
    "exec(userInput)",
    'exec("rm -rf " + userInput)',
    "exec(`rm -rf ${target}`)",
]

EXEC_ARGV_FORMS = [
    "exec('git', ['status'])",
    "exec(cmd, args)",
    "const r = exec(cmd, ['--version'])",
    "function exec(command, args, cwd = process.cwd()) {",
]


@pytest.mark.parametrize("line", EXEC_INJECTION_FORMS)
def test_exec_pattern_flags_injection_forms(line):
    hits = [
        desc
        for _rule_id, pattern, desc in CI_UNSAFE_PATTERNS
        if re.search(pattern, line)
    ]
    assert any("exec(" in desc for desc in hits), f"missed: {line}"


@pytest.mark.parametrize("line", EXEC_ARGV_FORMS)
def test_exec_pattern_allows_argv_forms(line):
    hits = [
        desc
        for _rule_id, pattern, desc in CI_UNSAFE_PATTERNS
        if re.search(pattern, line)
    ]
    assert not any("exec(" in desc for desc in hits), f"false positive: {line}"


def test_strict_mode_skips_vendored_code_and_data_files(tmp_path):
    """UNSAFE_PATTERNS apply to first-party code only.

    Measured on a real project: strict mode raised 150 findings and every one of them was inside
    jquery/select2/xregexp under a vendor/ directory, or a JSON snapshot that quoted an HTML
    snippet. Secrets are still scanned everywhere - this narrows the code-pattern pass, nothing else.
    """
    app = tmp_path / "app.js"
    app.write_text("el.innerHTML = payload;\n", encoding="utf-8")

    vendor = tmp_path / "lib" / "vendor"
    vendor.mkdir(parents=True)
    jquery = vendor / "jquery.js"
    jquery.write_text("window.eval(payload);\n", encoding="utf-8")

    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        '{"html": "<b dangerouslySetInnerHTML=x></b>"}\n', encoding="utf-8"
    )

    app_findings = ci_scan_file(app, strict=True)
    assert any("UNSAFE" in finding.message for finding in app_findings), (
        "a real finding was lost"
    )

    assert ci_scan_file(jquery, strict=True) == [], "vendored library code was scanned"
    assert ci_scan_file(snapshot, strict=True) == [], "a JSON data file was scanned"

    # Django's collectstatic output holds bundled CKEditor/YUI/admin JS: generated, not authored.
    collected = tmp_path / "staticfiles" / "ckeditor" / "plugin.js"
    collected.parent.mkdir(parents=True)
    collected.write_text("window.exec(payload);\n", encoding="utf-8")
    assert ci_scan_file(collected, strict=True) == [], (
        "collected static output was scanned"
    )


def test_secrets_are_still_scanned_in_json_and_vendored_code(tmp_path):
    """Narrowing the code-pattern pass must not narrow the secret pass."""
    token = "sk-ant-api03-" + "a" * 40
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    secret_file = vendor / "bundle.js"
    secret_file.write_text(f"const k = '{token}';\n", encoding="utf-8")

    data_file = tmp_path / "config.json"
    data_file.write_text('{"api_key": "' + token + '"}\n', encoding="utf-8")

    for path in (secret_file, data_file):
        findings = ci_scan_file(path, strict=True)
        assert any("SECRET" in finding.message for finding in findings), (
            f"secret missed in {path.name}"
        )


def test_inline_allow_marker_skips_unsafe_but_never_secrets(tmp_path):
    """A line may opt out of the unsafe pass with a reason; the secret pass has no opt-out.

    Seven of the eight findings in a real project were innerHTML assignments to literal markup (or
    restoring the button's own saved markup), which are not injection sites. Suppressing them with a
    separate allowlist file would put the justification far from the code; an inline marker keeps it
    in place and shows up in the same diff.
    """
    allowed = tmp_path / "allowed.js"
    allowed.write_text(
        "el.innerHTML = '<b>static</b>'; // security-scan:allow static literal markup\n",
        encoding="utf-8",
    )
    assert ci_scan_file(allowed, strict=True) == []

    unaffected = tmp_path / "unaffected.js"
    unaffected.write_text("el.innerHTML = userInput;\n", encoding="utf-8")
    assert any(
        "UNSAFE" in finding.message for finding in ci_scan_file(unaffected, strict=True)
    )

    token = "sk-ant-api03-" + "b" * 40
    secret = tmp_path / "secret.js"
    secret.write_text(
        f"const k = '{token}'; // security-scan:allow this must not hide a secret\n",
        encoding="utf-8",
    )
    findings = ci_scan_file(secret, strict=True)
    assert any("SECRET" in finding.message for finding in findings), (
        "the marker excused a secret"
    )
