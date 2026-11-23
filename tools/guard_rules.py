#!/usr/bin/env python3
"""
guard_rules.py — the destructive-command and secret rules shared by the gates.

Moved verbatim out of .claude/hooks/guard.py, which only runs inside Claude
Code: the delegation wrappers drive workers that never meet that hook, so they
read the same rules from here instead of growing a second, drifting copy.

Stdlib-only, and deliberately independent of guard.py -- guard.py imports this
module, so importing back would be a cycle.
"""

from __future__ import annotations

import re

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


def match_block_pattern(text: str) -> str | None:
    """Name the first BLOCK_PATTERNS entry that matches `text`, or None.

    A RAW match: no shell-wrapper unwrapping, no quote masking, no payload
    recursion. Those live in guard.py's `find_destructive`, which reads a
    command for real; this only answers whether the text NAMES a blocked
    command, which is what a delegation prompt is judged on.
    """
    if not text:
        return None
    for name, pattern in BLOCK_PATTERNS:
        if pattern.search(text):
            return name
    return None


#: Command fragments that must never appear in a delegation prompt. The
#: BLOCK_PATTERNS above anchor on command position because they inspect shell
#: strings; a prompt is prose, where "please run git push --force" carries the
#: same intent and no anchor to match. Substring matching is the honest test.
FORBIDDEN_IN_PROMPT: tuple[str, ...] = (
    "git push --force",
    "git push -f",
    "git reset --hard",
    "git clean -fd",
    "rm -rf /",
    "rm -rf ~",
    "drop table",
    "drop database",
)


def match_prompt(text: str) -> str | None:
    """Return the first forbidden fragment in ``text``, ignoring case."""
    lowered = text.lower()
    for fragment in FORBIDDEN_IN_PROMPT:
        if fragment in lowered:
            return fragment
    return None
