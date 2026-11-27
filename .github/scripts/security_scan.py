#!/usr/bin/env python3
"""
Security Scanner
=================
Scans codebase for common security issues: hardcoded secrets,
unsafe patterns, and misconfigurations.

Secrets are always scanned, in every file. `UNSAFE_PATTERNS` (eval/exec/
os.system/shell=True/innerHTML) are code constructs: they run only under
`--strict`, and never against documentation, because instruction and skill docs
quote those constructs on purpose -- a security skill that never names `eval(`
teaches nothing. Scanning prose for them produced 68 findings, all false
positives, which is why the strict half sat unused.

Usage:
    python .github/scripts/security_scan.py <project_path>
    python .github/scripts/security_scan.py . --strict
    python .github/scripts/security_scan.py . --strict --sarif scan.sarif
    python .github/scripts/security_scan.py . --write-baseline .security-baseline.json
    python .github/scripts/security_scan.py . --baseline .security-baseline.json

A baseline turns the gate from "no findings at all" into "no new findings": the
file records each finding as rule + path + a hash of the line, so a finding that
moves down its file still matches. Review a change to it like code - an entry
added to it hides that finding.
"""

import hashlib
import json
import os
import re
import subprocess  # noqa: S404 — runs `git ls-files` on the scanned path only
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

# Patterns to flag
SECRET_PATTERNS: list[tuple[str, str, str]] = [
    (
        "secret.hardcoded-api-key",
        r'(?:api_key|apikey|api-key)\s*[:=]\s*["\'][\w\-]{20,}["\']',
        "Hardcoded API key",
    ),
    (
        "secret.hardcoded-password",
        r'(?:password|passwd)\s*[:=]\s*["\'][^"\']+["\']',
        "Hardcoded password",
    ),
    (
        "secret.hardcoded-token",
        r'(?:secret|token)\s*[:=]\s*["\'][\w\-]{20,}["\']',
        "Hardcoded secret/token",
    ),
    ("secret.openai-key", r"sk-[a-zA-Z0-9]{20,}", "OpenAI/Anthropic API key pattern"),
    (
        "secret.private-key",
        r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----",
        "Private key in source",
    ),
    (
        "secret.connection-string",
        r'(?:mongodb|postgres|mysql)://[^"\']+@',
        "Database connection string with credentials",
    ),
    (
        "secret.jwt-shaped",
        r"eyJ[a-zA-Z0-9\-_]+\.eyJ[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+",
        "JWT token (possible hardcoded)",
    ),
    ("secret.aws-access-key", r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    ("secret.github-token", r"gh[p|o|u|s|r]_[A-Za-z0-9_]{36,255}", "GitHub Token"),
    ("secret.github-pat", r"github_pat_[A-Za-z0-9_]{22,}", "GitHub fine-grained PAT"),
    ("secret.google-api-key", r"AIza[0-9A-Za-z\-_]{35}", "Google API Key"),
    ("secret.slack-token", r"xox[bpras]-[0-9a-zA-Z]{10,}", "Slack Token"),
    # Prefixed-token formats. The generic `sk-[a-zA-Z0-9]{20,}` above stops at
    # the first "-", so it never matched sk-ant-/sk-proj- keys -- including
    # this project's own Anthropic key format. Length floors are set above
    # doc-placeholder length (e.g. "sk-ant-xxxxxxxxxxxx") to avoid firing on
    # README examples. Pinned by tools/test_secret_patterns.py.
    ("secret.anthropic-key", r"sk-ant-[A-Za-z0-9\-_]{24,}", "Anthropic API key"),
    (
        "secret.openai-project-key",
        r"sk-proj-[A-Za-z0-9\-_]{20,}",
        "OpenAI project key",
    ),
    ("secret.npm-token", r"npm_[A-Za-z0-9]{36}", "npm access token"),
    (
        "secret.gitlab-token",
        r"glpat-[A-Za-z0-9\-_]{20,}",
        "GitLab personal access token",
    ),
    ("secret.digitalocean-token", r"dop_v1_[A-Za-z0-9]{64}", "DigitalOcean token"),
    # Authorization headers carry no quotes and no "=", so the quoted
    # secret/token pattern above never saw them.
    (
        "secret.bearer-token",
        r"Bearer\s+[A-Za-z0-9._\-+/=]{20,}",
        "Bearer token in Authorization header",
    ),
]

UNSAFE_PATTERNS: list[tuple[str, str, str]] = [
    ("code.inner-html", r"\.innerHTML\s*=", "Unsafe innerHTML assignment (XSS risk)"),
    ("code.eval", r"eval\(", "Use of eval() — code injection risk"),
    # Only a one-argument (or string-argument) call is the injection risk. Two
    # shapes are safe and are what this repo actually uses:
    #   exec('git', ['status'])      -- quoted command + argv array
    #   exec(cmd, args)              -- the thin spawnSync wrapper's own signature
    # Without both exclusions, enabling --strict flagged 17 benign hook sites and
    # was useless as a gate.
    (
        "code.exec",
        r"exec\((?!\s*(?:['\"`][^'\"`]*['\"`]|\w+)\s*,)",
        "Use of exec() — code injection risk",
    ),
    ("code.os-system", r"os\.system\(", "Use of os.system() — command injection risk"),
    (
        "code.shell-true",
        r"subprocess\.call\(.*shell\s*=\s*True",
        "Shell=True in subprocess — injection risk",
    ),
    ("code.document-write", r"document\.write\(", "document.write() — XSS risk"),
    (
        "code.dangerous-html",
        r"dangerouslySetInnerHTML",
        "React dangerouslySetInnerHTML — XSS risk",
    ),
    (
        "code.angular-bypass",
        r"bypassSecurityTrust",
        "Angular bypassSecurityTrust — XSS risk",
    ),
]

SKIP_DIRS = {
    "node_modules",
    "__pycache__",
    ".next",
    "dist",
    "build",
    ".venv",
    "venv",
    ".cache",
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".solocode",
}
# Files git-ignored in this repo — skip to avoid flagging local dev secrets
SKIP_NAMES: set[str] = {".env"}
SKIP_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".map",
    ".lock",
    ".min.js",
    ".min.css",
    ".pyc",
}

# Only code can contain this class of construction. A JSON snapshot that happens to quote an HTML
# snippet produced "React dangerouslySetInnerHTML" findings on three data files that never render
# anything - part of the 150 false positives strict mode raised on a real project.
UNSAFE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".html",
    ".htm",
    ".sh",
    ".bash",
    ".ps1",
}

# Third-party and generated code a project vendors in place. Secrets are still scanned there - a
# leaked key is a leaked key wherever it sits - but `UNSAFE_PATTERNS` are not: an eval() inside
# jQuery, CKEditor or YUI is the library's business, not the project's. Measured on a real project:
# the first pass raised 150 false positives (jquery/select2/xregexp under vendor/, JSON snapshots
# quoting HTML), and once those were excluded another 97 appeared from bundled CKEditor and Django
# admin files under `staticfiles/` - Django's collectstatic output, the same class as dist/build.
VENDOR_DIRS = {
    "vendor",
    "vendors",
    "third_party",
    "thirdparty",
    "bower_components",
    "external",
    "staticfiles",
}


def _is_vendored(file_path: Path) -> bool:
    return any(part.lower() in VENDOR_DIRS for part in file_path.parts)


# Inline opt-out for the code and agent rules: the reason lives next to the code it
# excuses. Secrets are never allowlisted this way - a leaked key has no legitimate
# exception - so `scan_file` does not consult this for them.
ALLOW_MARKER = re.compile(r"security-scan\s*:\s*allow\b", re.IGNORECASE)


def is_allowed(line: str) -> bool:
    """Does this line opt out of the code and agent rules? Secrets are never excused."""
    return bool(ALLOW_MARKER.search(line))


# ── Agent setup ──────────────────────────────────────────────────────────────
# The files that decide what an AI agent may do. These are not code constructs a
# project can avoid by writing differently: they are permission grants, and a
# wrong one is invisible in a diff that reviews code only.
AGENT_CONFIG_SUFFIXES = {".json", ".jsonc"}
AGENT_INSTRUCTION_ROOTS = {".kilo", ".opencode", ".agents", ".gemini"}
AGENT_INSTRUCTION_SUFFIXES = {".md"}
PACKAGE_RUNNERS = {"npx", "uvx", "bunx", "pnpm", "yarn", "npm"}
AGENT_TOKEN_KEY = re.compile(r"(TOKEN|SECRET|KEY|PASSWORD|PASSWD)", re.IGNORECASE)
MCP_SERVER_KEYS = ("mcpServers", "servers")
MCP_CATALOGUE_MARKERS = ("reference", "example", "template", "sample", "snippet")
INVISIBLE_CODEPOINTS = (
    set(range(0x200B, 0x2010))
    | set(range(0x202A, 0x202F))
    | set(range(0x2060, 0x2065))
    | {0xFEFF, 0x00AD}
)

# (rule_id, pattern, description). Only config files are searched: the flag is
# dangerous when a settings file passes it, not when a document names it. Every
# skill of this harness that explains the flag would otherwise be a finding.
AGENT_TEXT_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "agent.bypass-permissions",
        re.compile(r'"bypassPermissions"\s*:\s*true'),
        "Agent config grants bypassPermissions",
    ),
    (
        "agent.dangerously-skip-permissions",
        re.compile(r"--dangerously-skip-permissions"),
        "Agent config passes --dangerously-skip-permissions",
    ),
]

AGENT_CONFIG_NAMES = {
    "opencode.json",
    "kilo.jsonc",
    "settings.json",
    "hooks.json",
    "mcp.json",
    ".mcp.json",
}


def _is_agent_config(file_path: Path) -> bool:
    """A settings file an agent reads.

    By name anywhere - `package.json` is not one, however many JSON keys it has -
    or any JSON under an agent root.
    """
    if file_path.name.lower() in AGENT_CONFIG_NAMES:
        return True
    return (
        bool(AGENT_INSTRUCTION_ROOTS & set(file_path.parts))
        and file_path.suffix.lower() in AGENT_CONFIG_SUFFIXES
    )


def _is_agent_instruction(file_path: Path) -> bool:
    """A skill or instruction file: the prose an agent is told to follow."""
    return (
        bool(AGENT_INSTRUCTION_ROOTS & set(file_path.parts))
        and file_path.suffix.lower() in AGENT_INSTRUCTION_SUFFIXES
    )


def _is_pinned(spec: str) -> bool:
    """A spec is pinned when it names a version other than `latest`.

    npm writes `pkg@1.2.3`; `uvx` and `pip` write `pkg==1.2.3`.
    """
    if "==" in spec:
        version = spec.split("==", 1)[1].strip()
        return bool(version) and version != "latest"
    at = spec.rfind("@")
    if at <= 0:
        return False
    return spec[at + 1 :] not in {"", "latest"}


def _is_mcp_catalogue(file_path: Path) -> bool:
    """A file that catalogues servers for a human to copy from, never read at run time.

    Only the file name is inspected: matching the whole path would skip a live
    `.mcp.json` that happens to sit under a directory called `example-app`.
    """
    name = file_path.name.lower()
    return any(marker in name for marker in MCP_CATALOGUE_MARKERS)


def _mcp_servers(text: str) -> dict | None:
    """The server map of an MCP manifest, whatever the file is called.

    `mcpServers` is the modelcontextprotocol shape; `servers` is what VS Code
    writes in `.vscode/mcp.json`.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    for key in MCP_SERVER_KEYS:
        servers = payload.get(key)
        if isinstance(servers, dict):
            return servers
    return None


def _server_line(text: str, name: str) -> int:
    """The line that names a server in an MCP manifest, or 1 when it cannot be found."""
    for index, line in enumerate(text.splitlines(), 1):
        if f'"{name}"' in line:
            return index
    return 1


def _mcp_findings(file_path: Path, text: str, servers: dict) -> list["Finding"]:
    """An MCP server that runs whatever npm published last, or over plain http."""
    findings: list[Finding] = []

    for name, server in servers.items():
        if not isinstance(server, dict):
            continue
        line_no = _server_line(text, str(name))

        command = server.get("command")
        args = server.get("args")
        if isinstance(command, str) and isinstance(args, list):
            runner = command.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].removesuffix(".exe")
            package = next(
                (
                    item
                    for item in args
                    if isinstance(item, str) and not item.startswith("-")
                ),
                None,
            )
            if runner in PACKAGE_RUNNERS and package and not _is_pinned(package):
                findings.append(
                    Finding(
                        str(file_path),
                        "agent.mcp-unpinned",
                        f"AGENT: MCP server {name} runs {package} at an unpinned version",
                        "agent",
                        "medium",
                        line_no,
                        _line_hash(f"{name}:{package}"),
                    )
                )

        url = server.get("url")
        if isinstance(url, str) and url.startswith("http://"):
            host = urlsplit(url).hostname or ""
            if host not in {"localhost", "127.0.0.1", "::1"}:
                findings.append(
                    Finding(
                        str(file_path),
                        "agent.mcp-insecure-http",
                        f"AGENT: MCP server {name} is reached over plaintext http",
                        "agent",
                        "high",
                        line_no,
                        _line_hash(f"{name}:{url}"),
                    )
                )

        env = server.get("env")
        if isinstance(env, dict):
            for key, value in env.items():
                if not isinstance(value, str) or len(value) < 20:
                    continue
                if value.startswith(("${", "$", "%")):
                    continue
                if not AGENT_TOKEN_KEY.search(str(key)):
                    continue
                findings.append(
                    Finding(
                        str(file_path),
                        "agent.mcp-plaintext-token",
                        f"AGENT: MCP server {name} carries {key} in plain text",
                        "agent",
                        "high",
                        line_no,
                        _line_hash(f"{name}:{key}"),
                    )
                )

    return findings


def scan_agent_file(file_path: Path) -> list["Finding"]:
    """Rules for the files that grant an agent its power. Called under --strict."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    findings: list[Finding] = []
    if not _is_mcp_catalogue(file_path):
        servers = _mcp_servers(text)
        if servers is not None:
            findings.extend(_mcp_findings(file_path, text, servers))
    is_config = _is_agent_config(file_path)
    is_instruction = _is_agent_instruction(file_path)
    if not (is_config or is_instruction):
        return findings

    for line_no, line in enumerate(text.splitlines(), 1):
        if is_allowed(line):
            continue

        if is_config:
            for rule_id, pattern, description in AGENT_TEXT_RULES:
                if pattern.search(line):
                    findings.append(
                        Finding(
                            str(file_path),
                            rule_id,
                            f"AGENT: {description}",
                            "agent",
                            "high",
                            line_no,
                            _line_hash(line),
                        )
                    )

        if is_instruction:
            bad = next((ch for ch in line if ord(ch) in INVISIBLE_CODEPOINTS), None)
            if bad is not None:
                findings.append(
                    Finding(
                        str(file_path),
                        "agent.invisible-unicode",
                        f"AGENT: invisible or bidi character U+{ord(bad):04X}",
                        "agent",
                        "medium",
                        line_no,
                        _line_hash(line),
                    )
                )

    return findings


def untracked_top_level_dirs(root: Path) -> set[str]:
    """Top-level directories inside `root` containing zero git-tracked files.

    Reference repos (unpacked upstream projects kept alongside the harness for
    study) contain no tracked files; every harness directory contains many.
    Using that signal instead of a hardcoded name list means a newly unpacked
    repo is excluded the moment it lands -- the previous list named 17 repos,
    all of which had since been deleted, while the one repo actually present
    was absent from it and pushed the gate to 248 false positives.

    Deliberately NOT `git ls-files --others --directory`: that reports any
    directory holding untracked files, which collapses to the whole directory
    and would have excluded `.claude`, `.github`, and `tools` -- silencing the
    gate over most of the harness.

    Returns an empty set when `root` is not a git repo (this script is also run
    against arbitrary external paths), so scanning degrades to "check
    everything" rather than silently skipping real findings.
    """
    try:
        proc = subprocess.run(  # noqa: S603,S607 — fixed argv, no shell
            ["git", "-C", str(root), "ls-files"],  # noqa: S607 -- partial path, fixed argv
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode != 0:
        return set()

    tracked_top_level = {
        line.split("/")[0] for line in proc.stdout.splitlines() if "/" in line
    }
    return {
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and entry.name not in tracked_top_level
    }


def should_skip(file_path: Path) -> bool:
    # Skip files that intentionally contain mock secrets for testing
    name = file_path.name.lower()
    # Files that contain dangerous-looking literals on purpose: this scanner's
    # own pattern table, the evaluation harness, and the detectors' own tests.
    if name in {
        "eval_harness.py",
        "guard.test.js",
        "secret-scan.test.js",
        "security_scan.py",
        "test_claude_guard.py",
        "test_secret_patterns.py",
        "test_security_scan_rules.py",
    }:
        return True
    if name in SKIP_NAMES:
        return True
    parts = file_path.parts
    for part in parts:
        if part in SKIP_DIRS:
            return True
    return file_path.suffix.lower() in SKIP_EXTENSIONS


def iter_files(root: Path, extra_skip_dirs: frozenset[str] = frozenset()) -> list[Path]:
    """Every file under `root`, with rejected directories never descended into.

    `should_skip` filters a file, but `rglob` has already walked the directory by
    then: on this checkout, `.solocode/restore/` alone holds 360k files, and
    walking them cost 78 s against 2 s once the walk prunes instead. The untracked
    reference directories are pruned only at the top level: they are discovered
    there, and pruning a same-named subdirectory deeper in the tree would hide
    real files.
    """
    found: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        at_root = Path(current) == root
        dirnames[:] = [
            name
            for name in dirnames
            if name not in SKIP_DIRS and not (at_root and name in extra_skip_dirs)
        ]
        found.extend(Path(current) / name for name in filenames)
    return found


class Finding(NamedTuple):
    """One rule hit. `line_hash` lets a baseline survive a line that moved."""

    path: str
    rule_id: str
    message: str
    kind: str  # "secret" | "code" | "agent"
    severity: str  # "high" | "medium"
    line: int
    line_hash: str


SECRET_SEVERITY = "high"
CODE_SEVERITY = "medium"


def _line_hash(line: str) -> str:
    """sha256 of the stripped line, truncated: the same finding moves with its line."""
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()[:16]


def scan_file(file_path: Path, strict: bool) -> list[Finding]:
    findings = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return findings

    check_unsafe = (
        strict
        and file_path.suffix.lower() in UNSAFE_SUFFIXES
        and not _is_vendored(file_path)
    )
    for line_no, line in enumerate(content.splitlines(), 1):
        # Secrets are never silenced; unsafe patterns may opt out inline.
        for rule_id, pattern, description in SECRET_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                findings.append(
                    Finding(
                        str(file_path),
                        rule_id,
                        f"SECRET: {description}",
                        "secret",
                        SECRET_SEVERITY,
                        line_no,
                        _line_hash(line),
                    )
                )
        if check_unsafe and not is_allowed(line):
            for rule_id, pattern, description in UNSAFE_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(
                        Finding(
                            str(file_path),
                            rule_id,
                            f"UNSAFE: {description}",
                            "code",
                            CODE_SEVERITY,
                            line_no,
                            _line_hash(line),
                        )
                    )
    return findings


def _relative(path: str, root: Path) -> str:
    """The path as the baseline and SARIF record it: POSIX, relative to the scan root."""
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return Path(path).as_posix()


def _flag_value(argv: list[str], flag: str) -> Path | None:
    """`--flag VALUE` or `--flag=VALUE`; a flag with no value stops the run."""
    for index, item in enumerate(argv):
        if item == flag:
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                raise SystemExit(f"{flag} needs a path")
            return Path(argv[index + 1])
        if item.startswith(flag + "="):
            value = item.split("=", 1)[1]
            if not value:
                raise SystemExit(f"{flag} needs a path")
            return Path(value)
    return None


def load_baseline(path: Path) -> dict[tuple[str, str, str], int]:
    """Known findings as (rule, path, line_hash) -> how many times it was seen."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read baseline {path}: {exc}") from exc
    entries = payload.get("findings") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise SystemExit(f"Cannot read baseline {path}: no 'findings' list")
    known: dict[tuple[str, str, str], int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rule, rel, digest = (
            entry.get("rule"),
            entry.get("path"),
            entry.get("line_hash"),
        )
        if not (
            isinstance(rule, str) and isinstance(rel, str) and isinstance(digest, str)
        ):
            continue
        count = entry.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            continue
        known[(rule, rel, digest)] = known.get((rule, rel, digest), 0) + count
    return known


def write_baseline(path: Path, findings: list[Finding], root: Path) -> int:
    """Save every finding once, with its multiplicity, so the file diffs cleanly.

    A count is what makes a duplicate line detectable: two identical findings
    share a key, and without the count the second one would look known.
    """
    counts = Counter(
        (finding.rule_id, _relative(finding.path, root), finding.line_hash)
        for finding in findings
    )
    payload = {
        "version": 1,
        "findings": [
            {"rule": rule, "path": rel, "line_hash": digest, "count": count}
            for (rule, rel, digest), count in sorted(counts.items())
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return sum(counts.values())


def _sarif_level(severity: str) -> str:
    return {"high": "error", "medium": "warning"}.get(severity, "note")


def build_sarif(findings: list[Finding], root: Path) -> dict:
    """A SARIF 2.1.0 document, so findings land in code scanning and on the pull request."""
    rules: dict[str, dict] = {}
    for finding in findings:
        rules.setdefault(
            finding.rule_id,
            {
                "id": finding.rule_id,
                "name": finding.rule_id.split(".", 1)[-1],
                "shortDescription": {"text": finding.message.split(": ", 1)[-1]},
                "defaultConfiguration": {"level": _sarif_level(finding.severity)},
                "properties": {"kind": finding.kind},
            },
        )
    results = [
        {
            "ruleId": finding.rule_id,
            "level": _sarif_level(finding.severity),
            "message": {"text": finding.message},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": _relative(finding.path, root)},
                        "region": {"startLine": max(1, finding.line)},
                    }
                }
            ],
        }
        for finding in findings
    ]
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "solo-code-security-scan",
                        "rules": [rules[key] for key in sorted(rules)],
                    }
                },
                "results": results,
            }
        ],
    }


def main():
    strict = "--strict" in sys.argv
    project_path = Path(
        sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "."
    ).resolve()

    if not project_path.exists():
        print(f"Path not found: {project_path}")
        sys.exit(1)

    print(f"Scanning: {project_path}")
    print(f"Mode: {'STRICT' if strict else 'SECRETS ONLY'}\n")

    reference_dirs = frozenset(untracked_top_level_dirs(project_path))
    if reference_dirs:
        print(
            f"Skipping untracked reference dirs: {', '.join(sorted(reference_dirs))}\n"
        )

    all_findings = []
    file_count = 0
    for file_path in iter_files(project_path, reference_dirs):
        if not should_skip(file_path):
            file_count += 1
            findings = scan_file(file_path, strict)
            if strict:
                findings += scan_agent_file(file_path)
            all_findings.extend(findings)

    print(f"Files scanned: {file_count}")

    write_path = _flag_value(sys.argv, "--write-baseline")
    if write_path is not None:
        count = write_baseline(write_path, all_findings, project_path)
        print(f"Baseline written: {write_path} ({count} finding(s))")

    baseline_path = _flag_value(sys.argv, "--baseline")
    if baseline_path is not None:
        known = load_baseline(baseline_path)
        matched: Counter = Counter()
        new_findings = []
        for finding in all_findings:
            key = (
                finding.rule_id,
                _relative(finding.path, project_path),
                finding.line_hash,
            )
            if matched[key] < known.get(key, 0):
                matched[key] += 1
                continue
            new_findings.append(finding)
        stale = sum(max(0, total - matched[key]) for key, total in known.items())
        all_findings = new_findings
        print(
            f"Baseline: {sum(matched.values())} known, "
            f"{len(all_findings)} new, {stale} stale"
        )

    sarif_path = _flag_value(sys.argv, "--sarif")
    if sarif_path is not None:
        sarif_path.parent.mkdir(parents=True, exist_ok=True)
        sarif_path.write_text(
            json.dumps(build_sarif(all_findings, project_path), indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"SARIF written: {sarif_path}")

    if write_path is not None:
        sys.exit(0)

    if all_findings:
        by_severity: dict[str, int] = {}
        for finding in all_findings:
            by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
        summary = "  ".join(
            f"{name}: {count}" for name, count in sorted(by_severity.items())
        )
        print(f"\nFindings: {len(all_findings)}")
        print(f"  {summary}\n")
        for finding in all_findings:
            print(
                f"  [{finding.rule_id}] {finding.path}:{finding.line} - {finding.message}"
            )
        print(f"\n{len(all_findings)} issue(s) found.")
        sys.exit(1)
    else:
        print("No issues found.")
        sys.exit(0)


if __name__ == "__main__":
    main()
