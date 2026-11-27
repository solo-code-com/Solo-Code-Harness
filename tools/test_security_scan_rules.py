#!/usr/bin/env python3
"""Rule ids, severities and inline suppression for the CI security gate."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "security_scan.py"


def _load():
    spec = importlib.util.spec_from_file_location("security_scan_gate", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load()


def test_every_rule_id_is_unique_and_namespaced(gate):
    ids = [
        rule_id
        for table in (gate.SECRET_PATTERNS, gate.UNSAFE_PATTERNS, gate.AGENT_TEXT_RULES)
        for rule_id, *_rest in table
    ] + [
        "agent.mcp-unpinned",
        "agent.mcp-insecure-http",
        "agent.mcp-plaintext-token",
        "agent.invisible-unicode",
    ]

    assert len(ids) == len(set(ids))
    assert all(rule_id.startswith(("secret.", "code.", "agent.")) for rule_id in ids)


def test_secret_rules_are_high_and_code_rules_are_medium(gate, tmp_path):
    secret = tmp_path / "leak.py"
    secret.write_text("aws_access_key = " + "AKIA" + "A" * 16 + "\n", encoding="utf-8")

    code = tmp_path / "page.js"
    code.write_text("el.innerHTML = value;\n", encoding="utf-8")

    assert {f.severity for f in gate.scan_file(secret, strict=True)} == {"high"}
    assert {f.severity for f in gate.scan_file(code, strict=True)} == {"medium"}


def test_a_finding_carries_its_rule_id_and_line_hash(gate, tmp_path):
    found = tmp_path / "leak.py"
    found.write_text("k = " + "AKIA" + "B" * 16 + "\n", encoding="utf-8")

    (finding,) = gate.scan_file(found, strict=True)

    assert finding.rule_id == "secret.aws-access-key"
    assert finding.kind == "secret"
    assert finding.line == 1
    assert len(finding.line_hash) == 16


def test_a_bare_marker_silences_the_whole_line(gate, tmp_path):
    code = tmp_path / "page.js"
    code.write_text(
        "el.innerHTML = x; // security-scan: allow reviewed by hand\n", encoding="utf-8"
    )

    assert gate.scan_file(code, strict=True) == []


def test_the_repository_passes_its_own_gate():
    """The gate must be green on this checkout, secrets and agent rules together."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(ROOT), "--strict"],
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert proc.returncode == 0, proc.stdout[-2000:]


def test_docstrings_quote_constructs_without_firing(gate, tmp_path):
    doc = tmp_path / "skill.md"
    doc.write_text("Never call `eval(` in production.\n", encoding="utf-8")

    assert gate.scan_file(doc, strict=True) == []


def _mcp(tmp_path, servers):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
    return path


def test_an_unpinned_mcp_server_is_reported(gate, tmp_path):
    path = _mcp(tmp_path, {"demo": {"command": "npx", "args": ["-y", "@scope/pkg"]}})

    rule_ids = {f.rule_id for f in gate.scan_agent_file(path)}

    assert rule_ids == {"agent.mcp-unpinned"}


def test_a_pinned_or_placeholder_server_is_not_reported(gate, tmp_path):
    path = _mcp(
        tmp_path,
        {
            "pinned": {"command": "npx", "args": ["-y", "pkg@1.2.3"]},
            "placeholder": {
                "command": "node",
                "args": ["s.js"],
                "env": {"API_KEY": "${API_KEY_VALUE}"},
            },
            "short": {"command": "node", "args": ["s.js"], "env": {"API_KEY": "abc"}},
            "url_only": {"url": "http://localhost:3000/mcp"},
        },
    )

    assert gate.scan_agent_file(path) == []


def test_latest_is_not_a_pin(gate, tmp_path):
    path = _mcp(tmp_path, {"demo": {"command": "npx", "args": ["-y", "pkg@latest"]}})

    assert {f.rule_id for f in gate.scan_agent_file(path)} == {"agent.mcp-unpinned"}


def test_plaintext_http_is_high_and_loopback_is_ignored(gate, tmp_path):
    path = _mcp(
        tmp_path,
        {
            "remote": {"url": "http://example.com/mcp"},
            "local": {"url": "http://127.0.0.1:9000/mcp"},
            "secure": {"url": "https://example.com/mcp"},
        },
    )

    (finding,) = gate.scan_agent_file(path)

    assert finding.rule_id == "agent.mcp-insecure-http"
    assert finding.severity == "high"


def test_a_plaintext_token_in_a_server_env_is_reported(gate, tmp_path):
    token = "abcdefghijklmnopqrstuvwxyz"
    path = _mcp(
        tmp_path,
        {"demo": {"command": "node", "args": ["s.js"], "env": {"GITHUB_TOKEN": token}}},
    )

    (finding,) = gate.scan_agent_file(path)

    assert finding.rule_id == "agent.mcp-plaintext-token"
    assert finding.kind == "agent"


def test_a_malformed_mcp_manifest_is_ignored(gate, tmp_path):
    broken = tmp_path / ".mcp.json"
    broken.write_text("{not json", encoding="utf-8")
    listed = tmp_path / "other.json"
    listed.write_text(json.dumps({"mcpServers": []}), encoding="utf-8")
    nested = tmp_path / "third.json"
    nested.write_text(json.dumps({"mcpServers": {"x": "not a dict"}}), encoding="utf-8")

    assert gate.scan_agent_file(broken) == []
    assert gate.scan_agent_file(listed) == []
    assert gate.scan_agent_file(nested) == []


def test_permission_flags_are_reported_in_a_config_only(gate, tmp_path):
    config = tmp_path / "opencode.json"
    config.write_text(
        json.dumps(
            {
                "bypassPermissions": True,
                "launch": ["agy", "--dangerously-skip-permissions"],
            }
        ),
        encoding="utf-8",
    )
    skill = tmp_path / ".kilo" / "skill" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "The wrapper passes --dangerously-skip-permissions to agy.exe.\n"
        "It grants bypassPermissions when asked.\n",
        encoding="utf-8",
    )

    rule_ids = {f.rule_id for f in gate.scan_agent_file(config)}

    assert rule_ids == {
        "agent.bypass-permissions",
        "agent.dangerously-skip-permissions",
    }
    assert gate.scan_agent_file(skill) == []


def test_invisible_unicode_is_reported_in_an_instruction(gate, tmp_path):
    skill = tmp_path / ".kilo" / "skill" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("normal\u200btext\n", encoding="utf-8")

    (finding,) = gate.scan_agent_file(skill)

    assert finding.rule_id == "agent.invisible-unicode"
    assert finding.line == 1


def test_the_agent_layer_ignores_plain_code(gate, tmp_path):
    code = tmp_path / "app.py"
    code.write_text("print('hello')\n", encoding="utf-8")

    assert gate.scan_agent_file(code) == []


def test_flag_value_reads_both_forms(gate):
    assert gate._flag_value(["--baseline", "b.json"], "--baseline") == Path("b.json")
    assert gate._flag_value(["--baseline=b.json"], "--baseline") == Path("b.json")
    assert gate._flag_value(["--strict"], "--baseline") is None


def test_relative_path_is_posix_and_survives_a_foreign_path(gate, tmp_path):
    inside = tmp_path / "sub" / "a.py"

    assert gate._relative(str(inside), tmp_path) == "sub/a.py"
    assert gate._relative(str(Path("C:/elsewhere/b.py")), tmp_path).endswith("b.py")


def test_write_baseline_is_sorted_and_reloadable(gate, tmp_path):
    first = gate.Finding(
        str(tmp_path / "b.py"), "code.eval", "UNSAFE: x", "code", "medium", 3, "h1"
    )
    second = gate.Finding(
        str(tmp_path / "a.py"),
        "secret.aws-access-key",
        "SECRET: y",
        "secret",
        "high",
        1,
        "h2",
    )
    target = tmp_path / "base.json"

    assert gate.write_baseline(target, [first, second], tmp_path) == 2

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert [row["path"] for row in payload["findings"]] == ["b.py", "a.py"]
    assert gate.load_baseline(target) == {
        ("code.eval", "b.py", "h1"): 1,
        ("secret.aws-access-key", "a.py", "h2"): 1,
    }


def test_load_baseline_rejects_a_malformed_file(gate, tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    shapes = tmp_path / "shapes.json"
    shapes.write_text(json.dumps({"findings": "nope"}), encoding="utf-8")

    for path in (broken, shapes):
        with pytest.raises(SystemExit):
            gate.load_baseline(path)


def test_load_baseline_skips_entries_that_are_not_strings(gate, tmp_path):
    path = tmp_path / "base.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "findings": [
                    {"rule": 1},
                    "junk",
                    {"rule": "a.b", "path": "p", "line_hash": "h"},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert gate.load_baseline(path) == {("a.b", "p", "h"): 1}


def test_sarif_document_shape(gate, tmp_path):
    finding = gate.Finding(
        str(tmp_path / "app.py"),
        "secret.aws-access-key",
        "SECRET: AWS Access Key ID",
        "secret",
        "high",
        7,
        "h",
    )

    document = gate.build_sarif([finding], tmp_path)

    assert document["version"] == "2.1.0"
    run = document["runs"][0]
    assert [rule["id"] for rule in run["tool"]["driver"]["rules"]] == [
        "secret.aws-access-key"
    ]
    (result,) = run["results"]
    assert result["level"] == "error"
    location = result["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "app.py"
    assert location["region"]["startLine"] == 7


def test_sarif_with_no_findings_still_has_a_run(gate, tmp_path):
    document = gate.build_sarif([], tmp_path)

    assert document["runs"][0]["results"] == []
    assert document["runs"][0]["tool"]["driver"]["rules"] == []


def test_sarif_level_mapping(gate):
    assert gate._sarif_level("high") == "error"
    assert gate._sarif_level("medium") == "warning"
    assert gate._sarif_level("low") == "note"


def test_a_marker_never_silences_a_secret(gate, tmp_path):
    """A leaked key has no legitimate exception, whatever the marker says."""
    leak = tmp_path / "leak.py"
    leak.write_text(
        'api_key = "'
        + "a" * 32
        + '"  # security-scan: allow secret.hardcoded-api-key\n',
        encoding="utf-8",
    )

    assert {f.rule_id for f in gate.scan_file(leak, strict=True)} == {
        "secret.hardcoded-api-key"
    }


def test_a_bare_marker_cannot_silence_a_secret_either(gate, tmp_path):
    leak = tmp_path / "leak.py"
    leak.write_text(
        'api_key = "' + "b" * 32 + '"  # security-scan: allow reviewed\n',
        encoding="utf-8",
    )

    assert gate.scan_file(leak, strict=True) != []


def test_the_baseline_records_multiplicity(gate, tmp_path):
    """Two identical findings must not collapse into one entry."""
    same = gate.Finding(
        str(tmp_path / "dup.py"),
        "code.os-system",
        "UNSAFE: x",
        "code",
        "medium",
        1,
        "h",
    )
    target = tmp_path / "base.json"

    assert gate.write_baseline(target, [same, same], tmp_path) == 2

    assert gate.load_baseline(target) == {("code.os-system", "dup.py", "h"): 2}


def test_a_manifest_is_found_by_its_content_not_its_name(gate, tmp_path):
    """VS Code writes `servers`; the modelcontextprotocol shape writes `mcpServers`."""
    vscode = tmp_path / "config.json"
    vscode.write_text(
        json.dumps({"servers": {"demo": {"command": "npx", "args": ["-y", "pkg"]}}}),
        encoding="utf-8",
    )
    plain = tmp_path / "package.json"
    plain.write_text(json.dumps({"name": "demo", "version": "1.0.0"}), encoding="utf-8")

    assert {f.rule_id for f in gate.scan_agent_file(vscode)} == {"agent.mcp-unpinned"}
    assert gate.scan_agent_file(plain) == []


def test_a_catalogue_of_servers_is_skipped(gate, tmp_path):
    """A file a human copies from is not a file an agent runs."""
    catalogue = tmp_path / "reference-servers.json"
    catalogue.write_text(
        json.dumps({"mcpServers": {"demo": {"command": "npx", "args": ["-y", "pkg"]}}}),
        encoding="utf-8",
    )

    assert gate.scan_agent_file(catalogue) == []


def test_a_uvx_double_equals_pin_counts_as_pinned(gate):
    assert gate._is_pinned("mcp-atlassian==0.21.0") is True
    assert gate._is_pinned("mcp-atlassian==latest") is False
    assert gate._is_pinned("mcp-atlassian") is False


def test_mcp_findings_hash_the_evidence_not_the_server_name(gate, tmp_path):
    """Two different tokens in one server must produce two different hashes."""
    path = _mcp(
        tmp_path,
        {
            "demo": {
                "command": "node",
                "args": ["s.js"],
                "env": {
                    "GITHUB_TOKEN": "abcdefghijklmnopqrstuvwxyz",
                    "SLACK_TOKEN": "zyxwvutsrqponmlkjihgfedcba",
                },
            }
        },
    )

    findings = gate.scan_agent_file(path)

    assert {f.rule_id for f in findings} == {"agent.mcp-plaintext-token"}
    assert len({f.line_hash for f in findings}) == 2


def test_a_flag_without_a_value_stops_the_run(gate):
    with pytest.raises(SystemExit):
        gate._flag_value(["--baseline"], "--baseline")
    with pytest.raises(SystemExit):
        gate._flag_value(["--baseline="], "--baseline")


def test_a_config_file_is_recognised_by_name_or_by_root(gate, tmp_path):
    assert gate._is_agent_config(Path("package.json")) is False
    assert gate._is_agent_config(Path("tsconfig.json")) is False
    assert gate._is_agent_config(Path("opencode.json")) is True
    assert gate._is_agent_config(tmp_path / ".kilo" / "agent.json") is True


def test_a_catalogue_is_recognised_by_name_not_by_parent(gate):
    assert gate._is_mcp_catalogue(Path("reference-servers.json")) is True
    assert gate._is_mcp_catalogue(Path("/home/dev/example-app/.mcp.json")) is False


def test_a_manifest_does_not_shadow_the_permission_rules(gate, tmp_path):
    config = tmp_path / "opencode.json"
    config.write_text(
        json.dumps({"servers": {}, "bypassPermissions": True}), encoding="utf-8"
    )

    assert {f.rule_id for f in gate.scan_agent_file(config)} == {
        "agent.bypass-permissions"
    }


def test_a_baseline_entry_claiming_no_occurrence_is_ignored(gate, tmp_path):
    path = tmp_path / "base.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "findings": [
                    {
                        "rule": "code.eval",
                        "path": "a.py",
                        "line_hash": "h1",
                        "count": 0,
                    },
                    {
                        "rule": "code.exec",
                        "path": "a.py",
                        "line_hash": "h2",
                        "count": -5,
                    },
                    {
                        "rule": "code.os-system",
                        "path": "a.py",
                        "line_hash": "h3",
                        "count": True,
                    },
                    {
                        "rule": "code.shell-true",
                        "path": "a.py",
                        "line_hash": "h4",
                        "count": 2,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert gate.load_baseline(path) == {("code.shell-true", "a.py", "h4"): 2}


def test_the_root_mcp_manifest_is_also_an_agent_config(gate, tmp_path):
    manifest = tmp_path / ".mcp.json"
    manifest.write_text(
        json.dumps({"mcpServers": {}, "bypassPermissions": True}), encoding="utf-8"
    )

    assert gate._is_agent_config(manifest) is True
    assert {f.rule_id for f in gate.scan_agent_file(manifest)} == {
        "agent.bypass-permissions"
    }


def test_a_reference_directory_is_pruned_only_at_the_scan_root(gate, tmp_path):
    """A project may legitimately have its own `tmp/` deeper in the tree."""
    (tmp_path / "ref").mkdir()
    (tmp_path / "ref" / "top.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "ref").mkdir(parents=True)
    nested = tmp_path / "src" / "ref" / "nested.py"
    nested.write_text("x = 1\n", encoding="utf-8")

    iterated = gate.iter_files(tmp_path, frozenset({"ref"}))
    kept = [path for path in iterated if not gate.should_skip(path)]

    assert nested in kept
    assert tmp_path / "ref" / "top.py" not in iterated


def test_is_allowed_is_a_plain_marker_check(gate):
    assert gate.is_allowed("no marker here") is False
    assert gate.is_allowed("x // security-scan: allow") is True
    assert gate.is_allowed("x // security-scan: allow reviewed by hand") is True
    assert gate.is_allowed("# security-scan: allowed by review") is False


def test_a_marker_silences_every_code_rule_on_its_line(gate, tmp_path):
    """No ids to name any more: the line is excused, whatever it holds."""
    code = tmp_path / "page.js"
    code.write_text(
        "eval(x); document.write(y); // security-scan: allow reviewed\n",
        encoding="utf-8",
    )

    assert gate.scan_file(code, strict=True) == []


def test_the_marker_silences_agent_rules_too(gate, tmp_path):
    config = tmp_path / "opencode.json"
    config.write_text(json.dumps({"bypassPermissions": True}), encoding="utf-8")
    excused = tmp_path / ".kilo" / "agent.json"
    excused.parent.mkdir(parents=True)
    excused.write_text(
        '{\n  "bypassPermissions": true // security-scan: allow reviewed\n}\n',
        encoding="utf-8",
    )

    assert {f.rule_id for f in gate.scan_agent_file(config)} == {
        "agent.bypass-permissions"
    }
    assert gate.scan_agent_file(excused) == []


def test_the_marker_silences_invisible_unicode(gate, tmp_path):
    skill = tmp_path / ".kilo" / "skill" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "hidden\u200bhere // security-scan: allow pasted by hand\n", encoding="utf-8"
    )

    assert gate.scan_agent_file(skill) == []


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_cli_writes_a_baseline_and_reports_only_new_findings(tmp_path):
    target = tmp_path / "app.py"
    baseline = tmp_path / "base.json"
    target.write_text('api_key = "' + "a" * 32 + '"\n', encoding="utf-8")

    written = _run(str(tmp_path), "--write-baseline", str(baseline))
    assert written.returncode == 0
    assert "Baseline written" in written.stdout
    assert baseline.is_file()

    same = _run(str(tmp_path), "--baseline", str(baseline))
    assert same.returncode == 0
    assert "0 new" in same.stdout

    target.write_text('api_key = "' + "b" * 32 + '"\n', encoding="utf-8")
    changed = _run(str(tmp_path), "--baseline", str(baseline))
    assert changed.returncode == 1
    assert "1 new" in changed.stdout


def test_cli_writes_a_sarif_document(tmp_path):
    target = tmp_path / "app.py"
    sarif = tmp_path / "out.sarif"
    target.write_text('api_key = "' + "c" * 32 + '"\n', encoding="utf-8")

    result = _run(str(tmp_path), "--strict", "--sarif", str(sarif))

    assert result.returncode == 1
    document = json.loads(sarif.read_text(encoding="utf-8"))
    assert document["version"] == "2.1.0"
    assert len(document["runs"][0]["results"]) >= 1


def test_cli_refuses_a_flag_without_a_value():
    result = _run(".", "--baseline")

    assert result.returncode != 0
    assert "needs a path" in result.stderr
