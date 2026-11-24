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

import base64
import json
import os
import re
import sys
from pathlib import Path

# ─── Shared Block/Secret Rules ───────────────────────────────────────────────
# The rules live in tools/guard_rules.py so a delegation wrapper can reuse them:
# a worker driven by one never runs this hook, and a single definition is the
# only way the two gates stay in agreement. sys.path is set here because this
# file runs as a script, not as part of an importable package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from guard_rules import (  # noqa: E402
    BLOCK_PATTERNS,
    PROTECTED_FILES,
    SECRET_PATTERNS,
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

# A destructive payload can arrive BASE64-ENCODED, so the text the guard sees
# never names the command: `echo cm0gLXJmIC90bXAveA== | base64 -d | sh` wipes
# /tmp. The decoder is only recognised where its INPUT is visible -- a blob a
# printing command pipes into it, a `.b64` file it is handed, or a herestring --
# and the decoded text is only inspected, never run. A command that merely NAMES
# a decoder (`rg "echo cm0g | base64 -d" .`) is search data and is blanked by
# the mask before this reads it; a blob that decodes to binary or non-UTF-8 text
# is ignored, because a binary blob is not a shell script and an over-block
# teaches people to switch the guard off.
_B64_DECODER = (
    r"(?:base64\s+(?:-d|--decode|-decode)\b"
    r"|openssl\s+(?:enc|base64)\s+(?:-d|--decode|-decode)(?:\s+-base64)?)"
)
# A quoted blob (the common spelling) or a bare one. Base64 is its own alphabet,
# so nothing else can be mistaken for a blob.
_B64_BLOB = r"(?:'[^']*'|\"[^\"]*\"|[A-Za-z0-9+/=\s_-]+)"
# Only a file that NAMES itself base64 is read: an arbitrary path would make the
# guard read files a command only mentions.
_B64_FILE = r"(?P<file>[^\s|;&'\"]+\.b64(?:\.txt)?)"
_B64_PRINTED = re.compile(
    rf"(?:^|[;&|\n])\s*(?:echo|printf)\s+(?P<blob>{_B64_BLOB})\s*\|\s*{_B64_DECODER}",
    re.I,
)
_B64_CAT_FILE = re.compile(
    rf"(?:^|[;&|\n])\s*cat\s+{_B64_FILE}\s*\|\s*{_B64_DECODER}", re.I
)
_B64_ARG_FILE = re.compile(rf"{_B64_DECODER}\s+{_B64_FILE}", re.I)
_B64_HERESTRING = re.compile(rf"{_B64_DECODER}\s*<<<\s*(?P<blob>{_B64_BLOB})", re.I)

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


def decode_base64_payloads(command: str) -> list[str]:
    """Decode the base64 blobs a MASKED command feeds to a base64 decoder.

    Catches: a blob `echo`/`printf` prints into `base64 -d` or
    `openssl enc -d -base64`; the contents of a `.b64`/`.b64.txt` file that is
    piped through `cat ... | base64 -d` or given to `base64 -d <file>`; and a
    herestring blob (`base64 -d <<< ...`). Must NOT fire on a command that
    merely names a decoder -- quoted search data is blanked by the mask before
    this runs -- nor on a blob that does not decode to printable UTF-8 text.

    Returns the decoded strings, de-duplicated, at most five per command. The
    caller re-scans them with the block patterns; nothing is ever executed.
    """
    blobs: list[str] = []
    for match in _B64_PRINTED.finditer(command):
        blobs.append(match.group("blob"))
    for match in _B64_HERESTRING.finditer(command):
        blobs.append(match.group("blob"))
    for pattern in (_B64_CAT_FILE, _B64_ARG_FILE):
        for match in pattern.finditer(command):
            try:
                blobs.append(
                    Path(match.group("file")).read_text(
                        encoding="utf-8", errors="ignore"
                    )
                )
            except OSError:
                # A missing or unreadable file cannot be decoded, and the name
                # alone is not evidence of a hidden command.
                continue

    decoded: list[str] = []
    for blob in blobs:
        cleaned = re.sub(r"[\s'\"]", "", blob)
        if not cleaned:
            continue
        # An obfuscator drops the padding: `cm0gLXJmIC90bXAveA` is the shape a
        # strict b64decode rejects, so repair it before either decode.
        padded = cleaned + "=" * (-len(cleaned) % 4)
        try:
            raw = base64.b64decode(padded, validate=True)
        except ValueError:
            # Strict decoding rejects both a missing padding and a foreign
            # character; the lenient URL-safe decode reads the same blob.
            try:
                raw = base64.urlsafe_b64decode(padded)
            except (ValueError, TypeError):
                continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not text or not all(ch.isprintable() or ch in "\n\r\t" for ch in text):
            continue
        if text not in decoded:
            decoded.append(text)
        if len(decoded) >= 5:
            break
    return decoded


def executable_payloads(masked: str) -> list[str]:
    """Command text a masked command runs without naming it.

    Four shapes, all read from the MASKED command so the data-vs-command
    decision is made once, by the mask, and no rule can read quoted search data
    as a script: a quoted `eval` argument, an unquoted `eval` argument run, the
    input side of a pipeline that reaches a shell, and the text a base64 blob
    decodes to when the pipeline decodes instead of printing. The pipeline
    input contributes its quoted spans AND its unquoted argument run, because
    the shell runs whatever the input PRINTS, quoted or not.
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
    # A decoded blob is a script exactly when the decoder's output reaches a
    # shell -- but the decoded text is inspected for the block patterns either
    # way, and the caller bounds it by MAX_PAYLOAD_DEPTH. It is not executed.
    payloads.extend(decode_base64_payloads(masked))
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
        # A secret can arrive base64-encoded too (`echo <b64> | base64 -d`), so
        # the decoded text is checked on the same path. `python -c` itself is
        # still allowed: only text that really decodes is inspected.
        for decoded in decode_base64_payloads(mask_separator_quotes(command)):
            secret = find_secret(decoded)
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
