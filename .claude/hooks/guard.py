#!/usr/bin/env python3
"""
solocode-guard (Claude Code hook) — PreToolUse safety gate.

Port of .opencode/plugins/solocode-guard.js to a Claude Code PreToolUse hook.
Stdlib-only Python (no jq/bash) for Windows portability.

Wired via .claude/settings.json:
    "hooks": { "PreToolUse": [ { "matcher": "Bash|Edit|Write|MultiEdit",
        "hooks": [ { "type": "command",
            "command": "python .claude/hooks/guard.py" } ] } ] }

Protocol:
  - Reads the tool call JSON from stdin: {tool_name, tool_input, ...}.
  - Bash: blocks destructive commands and leaked secrets in the command string.
  - Edit/Write/MultiEdit: blocks writes to protected config files and blocks
    content containing hardcoded secrets.
  - Blocks by printing a PreToolUse deny decision to stdout AND exiting 2
    (stderr feedback) — both are honored by Claude Code.
  - Allows by exiting 0 with no output (normal permission flow applies).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# ─── Destructive Command Patterns (port of BLOCK_PATTERNS) ──────────────────
# `rm` must sit in COMMAND position -- start of line, after a shell separator, or
# as an xargs target. A bare `rm\s+` also matched `docker run --rm -v /var/...`
# and `rg "rm /" .`, and blocked both. An over-block is worse than a miss here:
# a guard that blocks routine work gets switched off.
_RM_CMD = r"(?:^|[;&|\n]\s*(?:(?:sudo\s+)?(?:bash|sh)\s+-c\s+['\"]?\s*)?|\bxargs\s+(?:-\S+\s+)*|\b(?:sudo|env|nice|nohup|command|time)\s+(?:\w+=\S*\s+)*)rm\s+(?:--\s+)?"
# The PowerShell/cmd counterpart of command position, with the wrappers that run a
# nested command. `rg 'Remove-Item x -Recurse -Force' .` must stay allowed: that
# text merely NAMES the invocation, and an over-block teaches people to switch the
# guard off, which is worse than a miss.
_PS_CMD = (
    r"(?:^|[;&|\n]\s*)(?:(?:powershell|pwsh|cmd)(?:\.exe)?\s+(?:[-/]\S+\s+)*['\"]?\s*)?"
)
# Only the flags that make an rm destructive, any order, and at least one of them.
# The `+` is on a flag+separator pair rather than zero-or-more flags: a flagless
# `rm` cannot remove a directory, and matching zero flags made `rm *.tmp` and
# `rm /tmp/test.pid` look like recursive wipes. A bare `[a-zA-Z]` class also
# swallowed `-v` and `-i`.
#
# A short bundle may mix in benign flags alongside the destructive ones (`-rfv`,
# `-fRv`). Requiring the bundle to hold *only* r/R/f/F let `rm -rfv /` through.
# At least one of r/R/f/F must still appear, or rm_system_dir would catch the
# routine `rm -v /var/log/app.log` and the guard would block ordinary logs.
#
# The separator is `\s+`, OR a zero-width boundary at end-of-command. Keeping the
# space inside the flag body made the flags-before-target patterns work but left
# `rm / -rf` (flags last) unmatched -- there is no trailing space to consume.
_RM_FLAG = r"(?:-[a-zA-Z]*[rRfF][a-zA-Z]*|--(?:recursive|force)|--)"
_RM_FLAGS = rf"(?:{_RM_FLAG}(?:\s+|(?=[;&|\n]|$)))+"
# A path that resolves to a filesystem root: `/`, `//`, `/./`, `/.`, `/../`, plus
# the Windows `C:/` and git-bash `/c/` spellings. Matching a bare `/` let
# `rm -rf //` and `rm -rf /./` through; omitting the drive roots let
# `rm -rf C:/` through. An optional leading quote is part of the path, so a
# quoted root (`rm -rf '/'`, `rm -rf "/"`) is still seen as a root.
_ROOT_PATH = r"(?:['\"])?(?:/(?:/|\.+/?)*|[A-Za-z]:[/\\]+|/[A-Za-z]/+)"
# A catastrophic target that may appear BEFORE the flags, as in `rm / -rf`. Reuses
# _ROOT_PATH and adds the whole-system trees and tmp so the flags-after form is
# caught by the same class of rule as the flags-first form. The trailing `/?` lets
# `rm /etc/ -rf` match; a routine file under one of these dirs (`rm /tmp/x.pid`)
# still fails the required `\s+<flags>` and stays allowed.
_TARGET_PATH = rf"(?:{_ROOT_PATH}(?:(?:etc|usr|var|bin|lib(?:64)?|boot|sbin|opt|root|sys|proc|dev|tmp)/?)?|~|\*)"
# Windows spellings of the same catastrophe. _TARGET_PATH carries the Unix system
# trees, and nothing carried these: measured 2026-10-16, `rm -rf C:/Windows` and
# `Remove-Item -Recurse C:\` were both allowed by both engines. A plain directory
# target (`Remove-Item -Recurse build`) deliberately still passes -- only a drive
# root or a Windows system tree is matched here.
_WIN_SYSTEM = (
    r"(?:['\"])?[A-Za-z]:[/\\]*(?:(?:Windows|WinNT|Users|ProgramData|System32|"
    r"SysWOW64|Program Files(?: \(x86\))?)(?:[/\\]|\s|$|\"|')|(?=\s|$|\*|\"|'))"
)

BLOCK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # A shell separator terminates a root token exactly as whitespace does:
    # `rm -rf /; next` is the same wipe, and it is what an unwrapped
    # `bash -c 'rm -rf /; echo done'` leaves behind.
    (
        "rm_root",
        re.compile(rf"{_RM_CMD}{_RM_FLAGS}{_ROOT_PATH}(?:\s|$|\*|\"|'|;|&|\|)"),
    ),
    # A root target that is NOT the first argument: `rm -rf build /`. rm_root
    # needs the root directly after the flags, so a second target hid it.
    (
        "rm_root_after_other_target",
        re.compile(rf"{_RM_CMD}{_RM_FLAGS}[^&|;\n]*\s+{_ROOT_PATH}(?:\s|$|\*|\"|')"),
    ),
    # The same wipe with the flags after the target: `rm / -rf`. Each flags-first
    # pattern above needs the flags before its path, so none of them saw this.
    # Benign options may sit on either side of the target and before the
    # destructive flags (`rm -v / -rf`, `rm build / -rf`); the flags still have to
    # be of the r/R/f/F kind, so `rm / -v` (benign only) stays allowed.
    (
        "rm_flags_after_target",
        re.compile(
            rf"{_RM_CMD}(?:[-\w]+\s+)*{_TARGET_PATH}\s+(?:[-\w]+\s+)*{_RM_FLAGS}"
        ),
    ),
    ("rm_home", re.compile(rf"{_RM_CMD}{_RM_FLAGS}~")),
    ("rm_wildcard", re.compile(rf"{_RM_CMD}{_RM_FLAGS}\*")),
    (
        "rm_no_preserve",
        re.compile(rf"{_RM_CMD}.*(?:--no-preserve-root|--preserve-root[=\s]+no)"),
    ),
    # Anchored to command position, and options between `git` and `push` are
    # allowed (`git -C . push --force`). Anchoring keeps a search or a commit
    # message that merely NAMES a force-push from being blocked
    # (`rg 'git push --force main'`, `git commit -m "git push --force main"`).
    (
        "force_push_main",
        re.compile(
            r"(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+\s+)*push\b[^&|;\n]*(?:--force|-f)\s+[^&|;\n]*(?:main|master)"
        ),
    ),
    # The flag may follow the branch, or be absent entirely. The segment stops at
    # a shell separator, because `git push origin main && npm install --force`
    # was being read as a force-push. `-fu` style bundles count (a bare `-f\b`
    # missed them). `--force-with-lease` stays allowed: it is the safe
    # alternative this harness recommends.
    (
        "force_push_any",
        re.compile(
            r"(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+\s+)*push\b[^&|;\n]*(?:--force\b(?!-)|(?:^|\s)(?!-{2})-[a-zA-Z]*f[a-zA-Z]*\b)"
        ),
    ),
    # The `+branch` refspec force-pushes without any flag. AGENTS.md names this
    # exact form as forbidden, and nothing matched it.
    (
        "force_push_refspec",
        re.compile(
            r"(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+\s+)*push\b[^&|;\n]*\s\+[^\s&|;]+"
        ),
    ),
    # Deleting a protected branch on the remote. `git push origin --delete main`
    # is a force-push in effect (it destroys the remote branch) and nothing
    # matched it: force_push_any only looks for --force/-f. Anchored to command
    # position so a search that merely NAMES it -- `rg 'git push --delete main'`
    # -- is not blocked. git accepts `--delete <remote> <branch>`, so an optional
    # remote token may sit between the flag and the branch. The branch name must
    # be terminated (not `main-fix`), and the `:main` refspec form only counts
    # when the colon opens a token (`origin :main`), so a push to `<src>:main`
    # -- which writes rather than deletes -- is left alone.
    (
        "force_push_delete_main",
        re.compile(
            r"(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+\s+)*push\b[^&|;\n]*(?:--delete\s+|-d\s+|(?:^|\s):)(?:[^\s&|;]+\s+)?(?:refs/heads/)?(?:main|master)(?![\w-])"
        ),
    ),
    # Anchored to the subcommand: `reset` must come directly after `git` (only
    # options may precede it). The unanchored form blocked a commit message that
    # merely mentioned the phrase. `--ha`/`--har` are accepted because git takes
    # unambiguous abbreviations.
    (
        "git_reset_hard",
        re.compile(
            r"(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+\s+)*reset\b[^&|;\n]*--ha(?:rd?)?"
        ),
    ),
    ("drop_table", re.compile(r"DROP\s+(?:TABLE|DATABASE)", re.I)),
    ("truncate_table", re.compile(r"TRUNCATE\s+TABLE", re.I)),
    ("dd_raw", re.compile(r"dd\s+if=")),
    ("mkfs", re.compile(r"mkfs\.")),
    ("shred", re.compile(r"shred\s+")),
    ("dev_write", re.compile(r">\s*/dev/sd[a-z]")),
    ("win_del_force", re.compile(r"(?:^|[;&|]\s*)del\s+/f\s+/s")),
    (
        "win_remove_recursive",
        re.compile(
            rf"{_PS_CMD}Remove-Item\b[^|;&\n]*-Recurse\b[^|;&\n]*-Force\b", re.I
        ),
    ),
    # Anchored to a real format invocation: `format` in command position with a
    # drive letter or /fs: switch. A bare \bformat\s also matched
    # `--output-format json`, so the guard blocked ruff and grep -- and a guard
    # that blocks routine tooling teaches people to work around it.
    ("format_disk", re.compile(r"(?:^|[;&|]\s*)format\s+(?:/\S+\s+)*[a-zA-Z]:", re.I)),
    # Anchored to command position: a search or commit message that merely NAMES
    # the command (`rg 'diskpart'`, `rg 'shutdown'`) must not be blocked.
    ("diskpart", re.compile(r"(?:^|[;&|]\s*)diskpart\b")),
    ("shutdown_system", re.compile(r"(?:^|[;&|]\s*)(?:shutdown|reboot|halt)\b")),
    # `./` itself is the target to block (a recursive wipe of the current
    # directory), not any path that merely starts with `.` -- `rm -rf ./build` is
    # routine and was being caught. The trailing boundary keeps `rm -rf ./`
    # (also `./*`, `./ ; ...`) blocked while `rm -rf ./build` is allowed.
    ("rm_relative_wildcard", re.compile(rf"{_RM_CMD}{_RM_FLAGS}\./(?:\s|$|\*)")),
    # A root-relative path that escapes back to root via `..` (`/home/../`,
    # `C:/../`, `/c/../`). _ROOT_PATH stops at the first path segment, so these
    # resolved-to-root wipes were missed; the dirs*fragment steps over the
    # segments before the `..`. The `..` must be the LAST component of the path
    # (optionally chained, `../../`), or a file merely NAMED `..backup`, and a
    # targeted path like `/home/../workspace/build`, would be blocked (§ the
    # `(?:[/\\]\.\.)*[/\\]?(?=\s|$|[;&|*])` tail is what enforces that).
    (
        "rm_root_escape",
        re.compile(
            rf"{_RM_CMD}{_RM_FLAGS}{_ROOT_PATH}(?:[^/\s]+[/\\])*\.\.(?:[/\\]\.\.)*[/\\]?(?=\s|$|[;&|*])"
        ),
    ),
    (
        "git_clean_force",
        re.compile(
            r"(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--\S+\s+)*clean\s+-f"
        ),
    ),
    (
        "rm_system_dir",
        re.compile(
            rf"{_RM_CMD}{_RM_FLAGS}{_ROOT_PATH}(?:etc|usr|var|bin|lib(?:64)?|boot|sbin|opt|root|sys|proc|dev)(?:/|\s|$|\"|'|;|&|\|)"
        ),
    ),
    # Anchored to command position so a search that merely NAMES the pipe
    # (`rg 'curl http://x | bash'`) is not blocked. Covers piping into a shell
    # (`sh`/`bash`/`zsh`/`ksh`, with or without `sudo`) and into any interpreter
    # that executes the payload (`python`, `perl`, `ruby`, `node`, `pwsh`, `iex`).
    (
        "curl_pipe_shell",
        re.compile(
            r"(?:^|[;&|]\s*)(?:curl|wget)\s+[^|&;\n]*\|\s*(?:(?:sudo|env)\s+(?:\w+=\S*\s+)*)?(?:(?:ba|z|k)?sh\b|python[0-9.]*\b|perl\b|ruby\b|node\b|pwsh\b|powershell\b|iex\b)"
        ),
    ),
    # The PowerShell download-and-execute idiom. curl_pipe_shell only knew the
    # POSIX `| bash`; `irm <url> | iex` runs whatever the URL returns with no
    # file on disk. irm/iwr and Invoke-WebRequest/Invoke-RestMethod are the same
    # cmdlet. Anchored to command position so `rg 'irm x | iex'` is not blocked;
    # piping to Out-File (a file write) is a different, legitimate shape.
    (
        "powershell_download_pipe_iex",
        re.compile(
            r"(?:^|[;&|]\s*)(?:irm|iwr|Invoke-WebRequest|Invoke-RestMethod)\b[^|&;\n]*\|\s*(?:iex|Invoke-Expression)\b",
            re.I,
        ),
    ),
    ("dd_device_write", re.compile(r"dd\s+.*of=/dev/")),
    (
        "chmod_chown_system",
        re.compile(
            r"(?:chmod|chown)\s+-R\s+(?:[^/\s]+\s+)*/(?:etc|usr|var|bin|lib(?:64)?|boot|sbin|opt|root|sys|proc|dev)(?:/|\s|$)"
        ),
    ),
    # noqa: S108 -- this is a regex fragment matching the literal path, not a
    # temp path the code uses; splitting the f-string made bandit see `/tmp/` as
    # its own constant and fire hardcoded_tmp_directory.
    ("rm_temp_linux", re.compile(rf"{_RM_CMD}{_RM_FLAGS}/tmp/")),  # noqa: S108
    ("rm_temp_win", re.compile(rf"{_RM_CMD}{_RM_FLAGS}\$?(?:env:)?TEMP\b", re.I)),
    # The Windows `del`/`rd` family, anchored to command position like
    # `diskpart` and `format_disk` (measured 2026-10-16). Unanchored, the
    # patterns blocked a search, an echo or a commit message that merely
    # NAMED the invocation -- `rg 'rd /s' .`, `rg 'del /q' .` -- which
    # teaches people to switch the guard off, the worse failure. The real
    # invocations still block; `win_del_force` and `del_temp_win` carry the
    # same defect.
    (
        "del_temp_win",
        re.compile(r"(?:^|[;&|]\s*)del\s+(?:/f\s+)?/[qs]\s+\$?(?:env:)?TEMP\b", re.I),
    ),
    (
        "win_rd_recursive",
        re.compile(r"(?:^|[;&|]\s*)(?:rd|rmdir)\s+(?:.*\s)?/s\b", re.I),
    ),
    ("win_del_any", re.compile(r"(?:^|[;&|]\s*)del\s+(?:.*\s)?/[qsf]\b", re.I)),
    # The Windows-root pair measured open on 2026-10-16 (both engines allowed them).
    ("rm_win_system", re.compile(rf"{_RM_CMD}{_RM_FLAGS}{_WIN_SYSTEM}", re.I)),
    (
        "win_remove_recurse_root",
        re.compile(
            rf"{_PS_CMD}Remove-Item\b[^|;&\n]*-Recurse\b[^|;&\n]*{_WIN_SYSTEM}", re.I
        ),
    ),
    # Flag order reversed: `Remove-Item C:/ -Recurse` stayed open on 2026-10-16
    # because the pattern above only reads flag-then-target.
    (
        "win_remove_root_then_recurse",
        re.compile(
            rf"{_PS_CMD}Remove-Item\b[^|;&\n]*{_WIN_SYSTEM}[^|;&\n]*-Recurse\b", re.I
        ),
    ),
    ("win_format_volume", re.compile(r"(?:^|[;&|]\s*)Format-Volume\b", re.I)),
    ("win_stop_computer", re.compile(r"(?:^|[;&|]\s*)Stop-Computer\b", re.I)),
    ("win_restart_computer", re.compile(r"(?:^|[;&|]\s*)Restart-Computer\b", re.I)),
    # Windows disk/registry/shadow-copy destruction the port never carried over.
    # Anchored to command position like format_disk, so a search that only NAMES
    # the cmdlet -- `rg 'reg delete'` -- is not blocked, while each destructive
    # verb still blocks and its read-only sibling (Get-Disk, `reg query`,
    # `vssadmin list shadows`) stays allowed.
    # `reg delete` blocks even a single-value delete (`... /v Cache`) on purpose:
    # the operator asked for `reg delete` wholesale, and a registry write is not
    # regenerable. Narrow it only as a deliberate, reviewed decision.
    ("win_clear_disk", re.compile(r"(?:^|[;&|]\s*)clear-disk\b", re.I)),
    ("win_reg_delete", re.compile(r"(?:^|[;&|]\s*)reg(?:\.exe)?\s+delete\b", re.I)),
    (
        "win_vssadmin_delete",
        re.compile(r"(?:^|[;&|]\s*)vssadmin(?:\.exe)?\s+delete\b", re.I),
    ),
]

# ─── Secret Detection Patterns (port of SECRET_PATTERNS) ────────────────────
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}")),
    (
        "aws_secret_key",
        re.compile(
            r"(?:aws|amazon).{0,20}(?:secret|key|token).{0,10}[:=]\s*[\"'][A-Za-z0-9/+=]{20,}",
            re.I,
        ),
    ),
    # `(?!\$)` keeps a quoted *reference* out of the block list: the old pattern
    # blocked `API_KEY="$(op read op://vault/key)"`, which pushed users toward
    # inlining the literal secret instead of resolving it at runtime.
    (
        "generic_api_key",
        re.compile(
            r"(?:api[_-]?key|apikey|secret|password)\s*[:=]\s*[\"'](?!\$)[^\"']{8,}[\"']",
            re.I,
        ),
    ),
    # Unquoted `NAME=value` assignments. `generic_api_key` above requires quotes,
    # so `export ANTHROPIC_API_KEY=<token>` and `AWS_SECRET_ACCESS_KEY=<token>`
    # both passed straight through -- the exact forms an agent types when it
    # means to use a key. The `[a-z_-]*` tail matches the `_ACCESS_KEY` /
    # `_TOKEN` compound names. Two exclusions keep the detector honest:
    #   `[^\s\"'$]` first char -> `KEY="$(op read ...)"` and `KEY=${VAR:-}` are
    #     references, not literals, and must not block.
    #   the lookahead -> `sk-ant-xxxxxxxxxxxx` is a doc placeholder, and
    #     `MUST_NOT_DETECT[markdown_placeholder]` pins that it stays allowed.
    (
        "env_assignment_secret",
        re.compile(
            r"(?:api[_-]?key|secret|token|password|passwd|credential)[a-z_-]*\s*[:=]\s*(?![^\s\"']*(?:x{3}|\*{3}))[^\s\"'$][^\s\"']{15,}",
            re.I,
        ),
    ),
    (
        "private_key_pem",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "jwt_token",
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ),
    (
        "github_token",
        re.compile(
            r"(?:gh[pousr]_|github[_-]?pat[_-]?|github[_-]?token[_-]?)[A-Za-z0-9_]{20,}",
            re.I,
        ),
    ),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("stripe_key", re.compile(r"(?:sk|pk)_(?:test|live)_[0-9a-zA-Z]{24,}")),
    ("mongodb_uri", re.compile(r"mongodb(?:\+srv)?://[^:]+:[^@]+@")),
    ("postgres_uri", re.compile(r"postgres(?:ql)?://[^:]+:[^@]+@")),
    ("redis_uri", re.compile(r"redis://[^:]+:[^@]+@")),
    (
        "hardcoded_token",
        re.compile(
            r"(?:token|bearer)\s*[:=]\s*[\"'][A-Za-z0-9._\-+/=]{20,}[\"']", re.I
        ),
    ),
    (
        "discord_webhook",
        re.compile(
            r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+", re.I
        ),
    ),
    ("basic_auth", re.compile(r"https?://[^:]+:[^@]+@")),
    # Prefixed-token formats. `generic_api_key` only fires on a QUOTED value,
    # so bare `KEY=sk-ant-...` shell/env forms passed straight through -- this
    # project's own Anthropic key format included. Length floors sit above
    # doc-placeholder length so README examples do not trip the gate.
    # Pinned by tools/test_secret_patterns.py.
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9\-_]{24,}")),
    ("openai_project_key", re.compile(r"sk-proj-[A-Za-z0-9\-_]{20,}")),
    ("npm_token", re.compile(r"npm_[A-Za-z0-9]{36}")),
    ("gitlab_pat", re.compile(r"glpat-[A-Za-z0-9\-_]{20,}")),
    ("digitalocean_token", re.compile(r"dop_v1_[A-Za-z0-9]{64}")),
    # Authorization header form: no quotes, no "=", so `hardcoded_token` missed it.
    # `re.I` is required: `authorization: bearer <tok>` (lowercase) is what curl
    # examples actually use, and it passed the case-sensitive form.
    ("bearer_header", re.compile(r"Bearer\s+[A-Za-z0-9._\-+/=]{20,}", re.I)),
    # Legacy bare `sk-` keys with no recognised prefix. Mirrors
    # .github/scripts/security_scan.py. Does not overlap `anthropic_key`: that
    # needs 24 contiguous chars after `sk-ant-`, and this needs 20 contiguous
    # alphanumerics right after `sk-`, which the `-` in `sk-ant-` breaks.
    ("openai_legacy_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
]

# ─── Protected Config Files (port of PROTECTED_FILES) ───────────────────────
PROTECTED_FILES: frozenset[str] = frozenset(
    {
        ".eslintrc",
        ".eslintrc.js",
        ".eslintrc.cjs",
        ".eslintrc.json",
        ".eslintrc.yml",
        ".eslintrc.yaml",
        "eslint.config.js",
        "eslint.config.mjs",
        "eslint.config.cjs",
        "eslint.config.ts",
        "eslint.config.mts",
        "eslint.config.cts",
        ".prettierrc",
        ".prettierrc.js",
        ".prettierrc.cjs",
        ".prettierrc.json",
        ".prettierrc.yml",
        ".prettierrc.yaml",
        "prettier.config.js",
        "prettier.config.cjs",
        "prettier.config.mjs",
        "biome.json",
        "biome.jsonc",
        ".ruff.toml",
        "ruff.toml",
        ".shellcheckrc",
        ".stylelintrc",
        ".stylelintrc.json",
        ".stylelintrc.yml",
        ".markdownlint.json",
        ".markdownlint.yaml",
        ".markdownlintrc",
        ".flake8",
        ".pylintrc",
        "tox.ini",
        ".golangci.yml",
        ".golangci.yaml",
        ".golangci.json",
        ".editorconfig",
    }
)


# ─── Shell dialects (how a quoted payload ends, per shell family) ───────────
# One lexicon cannot read POSIX, `cmd.exe` and PowerShell quoting at once, so
# the payload-end walk is dialect-aware. Keyed `<shell>:<single|double>`, with
# an entry for every shell/quote pair the wrapper pattern below can produce, so
# the lookup can never fail open on a shape a caller forgot to add.
#
#   escape    -- the character that escapes the closing quote, or None when the
#                quote is literal (POSIX single quotes: a backslash never
#                escapes them, which is why the old parity rule misfired).
#   doubled   -- a doubled quote is the in-string quote (`""` in PowerShell and
#                `cmd`) and hides where the payload really ends.
#   escapable -- the characters `escape` may escape, or None for "any". POSIX
#                double quotes are the narrow case: the shell removes a
#                backslash only before `$`, a backtick, `"`, `\` or a newline,
#                so `\m` is a literal backslash-m. Decoding it into `m` was the
#                round-4 over-block that made `bash -c "r\m -rf /"` look like a
#                wipe. PowerShell's backtick and `cmd`'s `^` escape the closing
#                quote and nothing else, so their set holds only `"`: decoding
#                every escape rewrote `-Recurse`t-Force` into `-Recurset-Force`
#                and let a wipe through.
_POSIX_DOUBLE_ESCAPES: frozenset[str] = frozenset('$`"\\\n')
# The Windows shells escape the closing quote with their own escape character
# (`` `" `` in PowerShell, `^"` in cmd) and NOTHING else -- `` `t `` is a tab,
# a real word separator, so decoding it glues two flag words into one and hides
# the wipe from the patterns. Only the quote character may be consumed.
_QUOTE_ONLY_ESCAPES: frozenset[str] = frozenset('"')
DIALECTS: dict[str, tuple[str | None, bool, frozenset[str] | None]] = {
    "bash:single": (None, False, None),
    "sh:single": (None, False, None),
    "bash:double": ("\\", False, _POSIX_DOUBLE_ESCAPES),
    "sh:double": ("\\", False, _POSIX_DOUBLE_ESCAPES),
    "cmd:single": (None, False, None),
    "cmd:double": ("^", True, _QUOTE_ONLY_ESCAPES),
    "powershell:single": (None, True, None),
    "powershell:double": ("`", True, _QUOTE_ONLY_ESCAPES),
    "pwsh:single": (None, True, None),
    "pwsh:double": ("`", True, _QUOTE_ONLY_ESCAPES),
}

# The wrappers whose payload is executed, at command position: a POSIX
# `bash|sh -c` (with the composition prefixes `_RM_CMD` also accepts) or a
# Windows `powershell|pwsh|cmd` with its switches. `bq` is the opening quote
# when there is one; a `-c` payload may also be UNQUOTED (`sh -c rm\ -rf\ /`),
# in which case the group matches empty and the payload is the argument run.
_WRAPPER_RE = re.compile(
    r"(?:(?:(?:sudo\s+)?(?:env|nohup|nice|command|time)\s+)*(?:sudo\s+)?"
    r"(?P<shell>bash|sh)\s+-c\s+(?P<bq>['\"]|(?=\S))"
    r"|(?P<win>powershell|pwsh|cmd)(?:\.exe)?\s+"
    r"(?:[-/]\S+\s+)*(?P<wq>['\"]))",
    re.I,
)


def dialect_of(
    shell: str, quote: str
) -> tuple[str | None, bool, frozenset[str] | None]:
    """The payload-end lexicon for a shell family and its opening quote."""
    style = "single" if quote == "'" else "double"
    return DIALECTS[f"{shell.lower()}:{style}"]


def unescape_payload(
    payload: str, escape: str | None, escapable: frozenset[str] | None
) -> str:
    r"""Remove one level of a dialect's escapes (`\X` -> `X`).

    A wrapper's payload is written for the shell that will run it, so its
    escapes have to come off before the payload is read as commands: the inner
    wrapper in `bash -c "bash -c \"rm -rf /\""` only matches once `\"` is `"`.

    Only a pair the shell really treats as an escape is consumed (`escapable`,
    or None for "any"). A POSIX double quote escapes just `$`, a backtick, `"`,
    `\` and a newline, so `\m` is left alone -- stripping it turned
    `bash -c "r\m -rf /"`, which runs a command named `r\m`, into a wipe that
    was never there. An unquoted `-c` payload passes None: there the shell
    strips a backslash before any character.
    """
    if not escape:
        return payload
    out: list[str] = []
    index = 0
    while index < len(payload):
        if (
            payload[index] == escape
            and index + 1 < len(payload)
            and (escapable is None or payload[index + 1] in escapable)
        ):
            out.append(payload[index + 1])
            index += 2
            continue
        out.append(payload[index])
        index += 1
    return "".join(out)


def find_closing_quote(
    text: str, start: int, quote: str, escape: str | None, doubled: bool
) -> tuple[int, int] | None:
    """Locate a payload's closing quote by the dialect's own rules.

    Returns `(end, escaped_at)`: the index of the closing quote, and the index
    of the first quote the payload had escaped (`-1` when there was none).
    `None` means no closing quote could be located, and the caller must then
    leave the text alone rather than guess where the payload ends.
    """
    index = start
    escaped_at = -1
    while index < len(text):
        char = text[index]
        if (
            doubled
            and char == quote
            and index + 1 < len(text)
            and text[index + 1] == quote
        ):
            index += 2
            continue
        if char == quote:
            if escape is None:
                return index, escaped_at
            run = 0
            cursor = index - 1
            while cursor >= start and text[cursor] == escape:
                run += 1
                cursor -= 1
            if run % 2 == 0:
                return index, escaped_at
            if escaped_at < 0:
                escaped_at = index
        index += 1
    return None


# ─── Text a command runs without naming it ──────────────────────────────────
# `eval` is not a wrapper, it runs its argument. The argument may be quoted
# (`eval 'rm -rf /'`) or unquoted (`eval rm -rf /`), and `eval` concatenates
# its arguments, so an unquoted run up to the next separator executes too. The
# quote is therefore OPTIONAL and an empty group means "read the argument run".
# Read from the MASKED text so an `eval` that is itself inside quoted search
# data is not at command position and stays data (`rg "eval 'rm -rf /'" .` is a
# search).
_EVAL_ARGUMENT = re.compile(r"(?:^|[;&|\n]\s*)eval\s+(?P<q>['\"]?)", re.I)

# A stage of a pipeline that IS a shell does not run the input command, it runs
# the text that command PRINTS. The quoted spans on the input side are
# therefore the script: `echo 'rm -rf /' | bash` executes `rm -rf /`. Masked
# first for the same reason as `eval`: in `rg "echo 'rm -rf /' | bash" .` the
# whole expression is one quoted span, the mask erases it, and the rule never
# fires. The shell may be named by a path (`/bin/bash`, `/usr/bin/sh`) as well
# as by a bare identifier.
_PIPE_INTO_SHELL = re.compile(
    r"(?:^|[;&\n])(?P<input>[^;&\n]*?)\|\s*(?:(?:sudo|env)\s+(?:\w+=\S*\s+)*)?"
    r"(?:/?(?:[\w.-]+/)+)?(?:bash|sh|zsh|ksh|pwsh|powershell)(?:\.exe)?(?![\w.-])",
    re.I,
)

#: Bound on the recursion into embedded payloads, so a pathological nesting
#: cannot walk forever.
MAX_PAYLOAD_DEPTH = 4


def quoted_spans(text: str) -> list[str]:
    """The contents of each quoted span, with the quote characters removed."""
    spans: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char not in "'\"":
            index += 1
            continue
        end = text.find(char, index + 1)
        if end == -1:
            break
        spans.append(text[index + 1 : end])
        index = end + 1
    return spans


def executable_payloads(masked: str) -> list[str]:
    """Command text a masked command runs without naming it.

    Three shapes, all read from the MASKED command so the data-vs-command
    decision is made once, by the mask, and no rule can read quoted search data
    as a script: a quoted `eval` argument, an unquoted `eval` argument run, and
    the input side of a pipeline that reaches a shell. The pipeline input
    contributes its quoted spans AND its unquoted argument run, because the
    shell runs whatever the input PRINTS, quoted or not.
    """
    payloads: list[str] = []
    for match in _EVAL_ARGUMENT.finditer(masked):
        quote = match.group("q")
        if quote:
            escape, doubled, escapable = dialect_of("bash", quote)
            found = find_closing_quote(masked, match.end(), quote, escape, doubled)
            if found:
                payloads.append(
                    unescape_payload(masked[match.end() : found[0]], escape, escapable)
                )
        else:
            # An unquoted argument run: `eval` concatenates its arguments, so
            # the run up to the next separator is the text it executes. The
            # shell strips a backslash before any character, so the unescape
            # set is "any" (None), exactly as for an unquoted `-c` payload.
            run_end = match.end()
            while run_end < len(masked) and masked[run_end] not in ";&|\n":
                run_end += 1
            payloads.append(unescape_payload(masked[match.end() : run_end], "\\", None))
    for match in _PIPE_INTO_SHELL.finditer(masked):
        input_side = match.group("input")
        payloads.extend(quoted_spans(input_side))
        # The shell runs whatever the piped text is, quoted or not: with no
        # quotes anywhere (`echo rm -rf / | bash`) the argument run after the
        # printing command is the script, so the words contribute a payload too.
        words = input_side.split()
        if len(words) > 1:
            payloads.append(" ".join(words[1:]))
    return payloads


def unwrap_command_wrappers(command: str) -> str:
    """Replace an executable wrapper's payload with the text it actually runs.

    A quoted payload after `bash|sh -c` or a Windows `powershell|pwsh|cmd`
    wrapper is executed, so its separators are real command separators and its
    content must reach the block patterns. Search data that merely NAMES such a
    wrapper is untouched: a wrapper is unwrapped only where the quote state and
    the command position both agree it is executable.

    Round 10 changed three things. The payload's end is read with the shell's
    own lexicon (see `DIALECTS`); the payload is unescaped before it is scanned,
    so a nested wrapper written with escaped quotes still matches; and an
    unquoted `-c` argument is unescaped too, because the shell strips those
    escapes before the words run. An ambiguous wrapper is now skipped ALONE --
    the earlier per-command bail handed the whole line back, which silently
    stripped the unwrapping from a clean wrapper later on the same line.

    "Ambiguous" still means the text is left untouched rather than guessed at.
    Four shapes are ambiguous: no closing quote for the dialect, a quote the
    payload escaped with a separator before the payload really ends (the two
    readings disagree about whether that separator is executable), and a
    payload whose closing quote is followed by a backslash (the POSIX `'\\''`
    concatenation, which the masking path has to see whole), and a payload
    whose closing quote comes right after a quote it escaped (the two
    readings disagree about where the payload ends, which shifts every
    later span).
    """
    if not command:
        return ""

    def rewrite(text: str) -> str:
        out: list[str] = []
        index = 0
        quote: str | None = None
        command_position = True

        while index < len(text):
            if quote is None and command_position:
                match = _WRAPPER_RE.match(text, index)
                if match:
                    shell = (match.group("shell") or match.group("win")).lower()
                    wrapper_quote = match.group("bq") or match.group("wq")
                    payload_start = match.end()

                    if wrapper_quote:
                        escape, doubled, escapable = dialect_of(shell, wrapper_quote)
                        found = find_closing_quote(
                            text, payload_start, wrapper_quote, escape, doubled
                        )
                        if found:
                            payload_end, escaped_at = found
                            escaped_separator = escaped_at >= 0 and any(
                                separator in text[escaped_at:payload_end]
                                for separator in ";&|\n"
                            )
                            # A closing quote another backslash follows is the
                            # `'\''` concatenation: the payload text is only
                            # half of what the shell sees, so do not read it.
                            trailing_escape = (
                                payload_end + 1 < len(text)
                                and text[payload_end + 1] == "\\"
                            )
                            # An escaped quote the payload closes right after
                            # is the third disagreement about where the
                            # payload ends: read as an escape the payload
                            # runs on, read as the delimiter it ends at the
                            # escaped quote. Leave the text alone so the raw
                            # pair decides -- `bash -c "echo \""; rg
                            # "echo; rm -rf /" .` prints a quote and
                            # searches, it does not wipe.
                            escaped_closes_payload = (
                                escaped_at >= 0 and payload_end == escaped_at + 1
                            )
                            if (
                                not escaped_separator
                                and not trailing_escape
                                and not escaped_closes_payload
                            ):
                                body = unescape_payload(
                                    text[payload_start:payload_end],
                                    escape,
                                    escapable,
                                )
                                # A single-quoted shell has no escape
                                # character, so its in-string quote is the
                                # doubled pair. `find_closing_quote` only
                                # SKIPS that pair, so it still reaches the
                                # patterns as two balanced empty spans and the
                                # text between them reads in command position.
                                # Halve it to the one quote the shell sees. The
                                # double-quoted dialects keep `""` for the
                                # payload-end walk: rewriting it there could let
                                # a leading `""` mask a separator.
                                if doubled and escape is None:
                                    body = body.replace(
                                        wrapper_quote + wrapper_quote,
                                        wrapper_quote,
                                    )
                                out.append(rewrite(body))
                                index = payload_end + 1
                                command_position = False
                                continue
                    else:
                        # An unquoted `-c` argument. `sh -c rm\ -rf\ /` runs
                        # `rm -rf /`, because the shell removes the backslash
                        # escapes before the words reach `sh`. The argument run
                        # ends at the next separator.
                        run_end = payload_start
                        while run_end < len(text) and text[run_end] not in ";&|\n":
                            run_end += 1
                        # An unquoted `-c` argument escapes ANY next
                        # character, so its escapable set is None.
                        run = unescape_payload(text[payload_start:run_end], "\\", None)
                        out.append(rewrite(run))
                        index = run_end
                        command_position = False
                        continue

                    # Per-wrapper bail: keep this wrapper verbatim and carry on
                    # after its opening quote, so a clean wrapper later on the
                    # line is still unwrapped. Resuming with no quote state is
                    # what lets the scan reach that later wrapper; the text
                    # itself is left exactly as it came in.
                    out.append(text[index:payload_start])
                    index = payload_start
                    command_position = False
                    continue

            char = text[index]
            out.append(char)

            if quote is None:
                # A quote behind a backslash is a literal quote in POSIX, so it
                # must not open a span: `bash -c "echo C:\"; bash ...` would
                # otherwise swallow the rest of the line and hide the wrapper.
                if char in "'\"" and (index == 0 or text[index - 1] != "\\"):
                    quote = char
                elif char in ";&|\n":
                    command_position = True
                elif not char.isspace():
                    command_position = False
            elif char == quote:
                if quote == "'":
                    quote = None
                else:
                    run = 0
                    cursor = index - 1
                    while cursor >= 0 and text[cursor] == "\\":
                        run += 1
                        cursor -= 1
                    if run % 2 == 0:
                        quote = None

            index += 1

        return "".join(out)

    return rewrite(command)


def normalize_command(command: str) -> str:
    """Strip command-position wrappers + collapse whitespace to defeat bypasses."""
    if not command or not isinstance(command, str):
        return ""
    cmd = re.sub(r"\s+", " ", command).strip()
    cmd = unwrap_command_wrappers(cmd)
    while True:
        prev = cmd
        cmd = re.sub(r"^sudo\s+", "", cmd)
        cmd = re.sub(r"^env(\s+\w+=[^\s]*)+\s+", "", cmd)
        cmd = re.sub(r"^(?:bash|sh)\s+-c\s+", "", cmd)
        cmd = re.sub(r"^(?:nohup|nice|command|time)\s+", "", cmd)
        cmd = re.sub(r"^(['\"])(.*)\1$", r"\2", cmd)
        if cmd == prev:
            break
    cmd = re.sub(r"^/?(?:[\w.-]+/)+([\w.-]+)", r"\1", cmd)
    return cmd


def mask_separator_quotes(command: str) -> str:
    """Blank quoted spans that contain a shell separator.

    `rg 'echo x; diskpart' .` was blocked: the `;` inside the quotes read as a
    separator and put `diskpart` in command position. Only spans containing a
    separator are blanked, so a quoted root keeps the protection it relies on
    (`rm -rf '/'` must stay blocked) and a wrapper's quoted command is untouched
    (`bash -c 'rm -rf /'` is unwrapped before this runs only when it is
    at executable command position; quoted search data remains untouched).

    The pairing is escape-aware: a quote behind an odd backslash run is content,
    not the span's end, when the quote that really closes the span follows it
    (`\""`). Reading the escaped quote as the end shifted the spans, and made
    `bash -c "echo \""; rg "echo; rm -rf /" .` -- where bash prints a quote and
    then searches -- block on the search data. An escaped quote NOT followed by
    its own quote is still the end, the reading the round-9 pin relies on to
    expose its wipe.
    """
    out: list[str] = []
    index = 0
    while index < len(command):
        char = command[index]
        if char not in "'\"":
            out.append(char)
            index += 1
            continue
        end = index + 1
        while end < len(command):
            if command[end] != char:
                end += 1
                continue
            run = 0
            cursor = end - 1
            while cursor >= 0 and command[cursor] == "\\":
                run += 1
                cursor -= 1
            if run % 2 == 0:
                break
            if end + 1 < len(command) and command[end + 1] == char:
                end += 1
                continue
            break
        if end >= len(command):
            out.append(command[index:])
            break
        span = command[index : end + 1]
        out.append("''" if any(sep in span for sep in ";&|") else span)
        index = end + 1
    return "".join(out)


def find_destructive(command: str, depth: int = 0) -> str | None:
    """Name the destructive pattern a command matches, or None.

    `depth` bounds the recursion into the text a command runs without naming it
    (an `eval` argument, the input side of a pipeline into a shell), so a
    deliberately nested command still reaches the block patterns but cannot
    walk forever.
    """
    unwrapped = unwrap_command_wrappers(command)
    normalized = normalize_command(unwrapped)
    for raw in (unwrapped, normalized):
        if not raw:
            continue
        candidate = mask_separator_quotes(raw)
        for name, pattern in BLOCK_PATTERNS:
            if pattern.search(candidate):
                return name
        if depth < MAX_PAYLOAD_DEPTH:
            for payload in executable_payloads(candidate):
                if not payload:
                    continue
                hit = find_destructive(payload, depth + 1)
                if hit:
                    return hit
    return None


def find_secret(text: str) -> str | None:
    if not text:
        return None
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return name
    return None


# ─── Skill risk declaration ─────────────────────────────────────────────────
#
# A skill that TELLS the agent to run a side-effecting command (deploy, push,
# migrate) must declare `risk: side-effecting` in its frontmatter. The point is
# not the label -- it is that adding such an instruction becomes a deliberate,
# visible act instead of a line that slips into a skill silently.
#
# Only fires on imperative instructions ("Run the migration", "Deploy the ...")
# so a skill may still freely NAME a command: permission-guard documents
# `rm -rf` precisely because it blocks it, and must not be forced to declare
# itself side-effecting for doing so.

SKILL_RISK_VALUES = {"none", "side-effecting"}

_SIDE_EFFECT_INSTRUCTION = re.compile(
    r"^\s*(?:[-*]|\d+\.)?\s*(?:Run|Execute|Push|Deploy|Apply|Publish|Release)\b"
    r"[^\n]*?(git\s+push|deploy|migrat|reset\s+--hard|--force)",
    re.I | re.M,
)


def parse_frontmatter_value(content: str, key: str) -> str | None:
    """Read one top-level scalar from a `---`-delimited frontmatter block."""
    if not content.startswith("---"):
        return None
    end = content.find("\n---", 3)
    if end == -1:
        return None
    for line in content[3:end].splitlines():
        m = re.match(rf"^{re.escape(key)}\s*:\s*(.*)$", line.strip())
        if m:
            return m.group(1).strip().strip("\"'")
    return None


def check_skill_risk(
    file_path: str, content: str, *, is_full_content: bool
) -> str | None:
    """Return a denial reason if a SKILL.md's risk declaration is wrong.

    Content-based, not path-based: the check reads what the skill instructs,
    so it cannot be bypassed by writing the file somewhere else first.

    A partial Edit supplies only the replacement fragment, which carries no
    frontmatter -- reading `risk:` from it would report "not declared" for
    every skill, including correctly-declared ones. So for fragments the
    declaration is read from the file on disk instead.
    """
    if os.path.basename(file_path) != "SKILL.md" or not content:
        return None

    declared_source = content
    if not is_full_content:
        try:
            with open(file_path, encoding="utf-8") as fh:
                declared_source = fh.read()
        except OSError:
            # New file via Edit, or unreadable: no frontmatter to judge
            # against, so stay silent rather than block on a guess.
            return None

    declared = parse_frontmatter_value(declared_source, "risk")
    if declared is not None and declared not in SKILL_RISK_VALUES:
        return (
            f"SKILL.md declares unknown risk '{declared}'. "
            f"Use one of: {', '.join(sorted(SKILL_RISK_VALUES))}."
        )

    hit = _SIDE_EFFECT_INSTRUCTION.search(content)
    if hit and declared != "side-effecting":
        return (
            f"SKILL.md instructs a side-effecting action "
            f"({hit.group(0).strip()[:60]!r}) but does not declare "
            f"'risk: side-effecting' in its frontmatter. Add it, or "
            f"reword so the skill describes rather than instructs."
        )
    return None


def deny(reason: str) -> None:
    """Emit a Claude PreToolUse deny decision and exit non-zero."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    print(f"[solocode-guard] BLOCKED: {reason}", file=sys.stderr)
    sys.exit(2)


# ─── Executor Mode (orchestrator/executor split) ────────────────────────────
# When enabled, the orchestrator (Claude) may not write files directly: code
# changes must be routed to a worker engine (Kilo CLI/DeepSeek, gpt-5.6-sol) via
# tools/kilo_cli_delegate.py. Claude keeps planning, reviewing and verifying.
#
# State lives in .solocode/executor-mode (gitignored, per-machine):
#   file absent            -> ENABLED  (default-on, by design)
#   file contains off|0|disabled|false -> disabled
#   any other content      -> ENABLED
#
# SCOPE, STATED HONESTLY: this gates Edit/Write/MultiEdit only. Bash is
# deliberately NOT gated, so `python -c`, heredocs, `sed -i` and `>` still
# write files. This is a speed bump plus an audit trail, not a sandbox --
# chosen over a stricter gate because Bash-write blocking is an unwinnable
# arms race that also breaks legitimate verification scripts.
EXECUTOR_MODE_OFF_VALUES: frozenset[str] = frozenset(
    {
        "off",
        "0",
        "disabled",
        "false",
        "no",
    }
)

# Paths the orchestrator must still write for delegation itself to work.
# Kept deliberately short: every entry is a hole in the gate.
EXECUTOR_MODE_ALLOWED_PREFIXES: tuple[str, ...] = (
    # Gemini/Antigravity handoff briefs -- writing the plan IS the delegation.
    ".gemini/antigravity/handoff/inbox/",
    # The toggle itself, so the mode can be turned off without hand-editing.
    ".solocode/executor-mode",
)


def project_root() -> Path:
    """Repo root: CLAUDE_PROJECT_DIR if set, else this hook's grandparent."""
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parent.parent.parent


def executor_mode_enabled(root: Path | None = None) -> bool:
    """True when the orchestrator must delegate writes. Default: True."""
    root = root or project_root()
    state_file = root / ".solocode" / "executor-mode"
    try:
        raw = state_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True  # Absent or unreadable -> default-on.
    return raw.strip().split("#", 1)[0].strip().lower() not in EXECUTOR_MODE_OFF_VALUES


def executor_mode_exempt(file_path: str, root: Path | None = None) -> bool:
    """True if this path is delegation plumbing that stays writable."""
    if not file_path:
        return False
    root = root or project_root()
    path = Path(file_path)
    try:
        rel = (path if path.is_absolute() else root / path).resolve()
        rel_str = rel.relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        # Outside the repo (or unresolvable): not delegation plumbing.
        rel_str = path.as_posix().lstrip("./")
    return rel_str.startswith(EXECUTOR_MODE_ALLOWED_PREFIXES)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # No parseable input — do not block.

    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}

    if tool == "Bash":
        command = tool_input.get("command", "") or ""
        hit = find_destructive(command)
        if hit:
            deny(
                f"destructive command pattern '{hit}' detected. "
                f"Run manually if you are certain this is safe."
            )
        secret = find_secret(command)
        if secret:
            deny(
                f"possible secret '{secret}' in command. Use environment variables instead."
            )
        return 0

    if tool in ("Edit", "Write", "MultiEdit"):
        file_path = tool_input.get("file_path", "") or ""
        basename = os.path.basename(file_path)
        if basename in PROTECTED_FILES:
            deny(
                f"'{basename}' is a protected config file. Confirm before editing linter/formatter config."
            )
        # Scan proposed content for secrets.
        full_content = tool_input.get("content", "") or ""
        content = full_content or tool_input.get("new_string", "") or ""
        fragments = tool_input.get("edits", []) or []
        for edit in fragments:
            content += "\n" + (edit.get("new_string", "") or "")
        secret = find_secret(content)
        if secret:
            deny(
                f"possible secret '{secret}' in file content. Use environment variables instead."
            )
        risk_issue = check_skill_risk(
            file_path,
            content,
            is_full_content=bool(full_content) and not fragments,
        )
        if risk_issue:
            deny(risk_issue)
        # Executor mode last: the security denials above carry more specific,
        # more urgent messages, so they should win when both would fire.
        if executor_mode_enabled() and not executor_mode_exempt(file_path):
            deny(
                f"executor mode is ON -- the orchestrator does not write files "
                f"directly. Route this change to a worker:\n"
                f'  python tools/opencode_delegate.py "<self-contained task naming '
                f'{file_path}>" --free\n'
                f"Then verify the result yourself (read the file back, run the "
                f"gates) before accepting it. Note: workers can misreport which "
                f"path they wrote -- confirm with git status.\n"
                f"To disable: echo off > .solocode/executor-mode"
            )
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
