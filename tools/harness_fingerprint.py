#!/usr/bin/env python3
"""One fingerprint rule and one .harness.lock parser for every tool.

This module exists because the same two rules were copied into
``tools/deploy.py``, ``tools/garden.py`` and ``tools/failure_ledger.py``:
the canonicalisation-plus-SHA-256 digest of the declared harness files, and
the INI-style reader that turns ``[shared_files] paths = [...]`` back into a
list. Three copies of a hash rule diverge quietly -- a BOM stripped in one
tool and kept in another makes the same tree hash two ways -- and a parser
that disagrees with the writer is worse than no parser: it silently changes
which files the fingerprint proves. ``paths = ["tools/a,b.py"]`` split on
commas becomes two files that do not exist, and an early ``]`` cut loses the
rest of the list, so the digest still verifies while covering the wrong set.

The writer in ``tools/deploy.py`` emits a JSON-shaped list of quoted strings
with a trailing comma. The reader here therefore slices the bracketed block
by counting brackets outside quotes and hands it to ``json.loads`` after
removing the generator's single trailing comma; anything it cannot read
exactly is reported as ``None`` (ambiguous) instead of guessed, and the
callers turn that into an issue rather than silence.

Usage:
    from tools.harness_fingerprint import content_fingerprint
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

# A non-comment ``paths = [`` assignment: the anchor at the start of the line
# (after optional indentation) is what keeps a commented-out block out.
_PATHS_ASSIGNMENT = re.compile(r"(?m)^[ \t]*paths[ \t]*=[ \t]*\[")


def canonical_bytes(path: Path) -> bytes:
    """Read a file's bytes with a UTF-8 BOM stripped and CRLF converted to LF."""
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.replace(b"\r\n", b"\n")


def content_fingerprint(paths: list[str], root: Path) -> str:
    """SHA-256 over the sorted (path, content) pairs of a harness path list.

    A declared path that does not exist under ``root`` is an error, never a
    silent skip: a fingerprint computed over a subset cannot prove anything.
    """
    digest = hashlib.sha256()
    for path in sorted(paths):
        file_path = root / path
        if not file_path.is_file():
            raise RuntimeError(
                f"harness path declared in .harness.lock does not exist: {path}"
            )
        digest.update(path.encode("utf-8") + b"\0")
        digest.update(canonical_bytes(file_path) + b"\0")
    return "sha256:" + digest.hexdigest()


def optional_fingerprint(paths: list[str], root: Path) -> str | None:
    """``content_fingerprint`` for readers that must never raise or write.

    A sweep over other people's checkouts reports a mismatch instead of
    crashing, so a missing or unreadable declared path yields ``None``.
    """
    digest = hashlib.sha256()
    for path in sorted(paths):
        file_path = root / path
        if not file_path.is_file():
            return None
        try:
            data = canonical_bytes(file_path)
        except OSError:
            return None
        digest.update(path.encode("utf-8") + b"\0")
        digest.update(data + b"\0")
    return "sha256:" + digest.hexdigest()


def _is_header(line: str) -> bool:
    """Whether a lock line is an INI-style ``[section]`` header."""
    stripped = line.strip()
    return stripped.startswith("[") and stripped.endswith("]")


def lock_section(text: str, section: str) -> str:
    """Body of one INI-style section, or "" when absent or duplicated.

    A lock that names the same section twice is ambiguous: the two bodies
    cannot both be authoritative, so the caller is told there is no body
    instead of silently keeping the first.
    """
    lines = text.splitlines()
    headers = [
        index
        for index, line in enumerate(lines)
        if _is_header(line) and line.strip()[1:-1].strip() == section
    ]
    if len(headers) != 1:
        return ""
    body: list[str] = []
    for line in lines[headers[0] + 1 :]:
        if _is_header(line):
            break
        body.append(line)
    return "\n".join(body)


def lock_value(text: str, section: str, key: str) -> str | None:
    """First non-comment ``key = value`` in a section, quotes stripped."""
    for raw in lock_section(text, section).splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def _slice_bracket(text: str, open_index: int) -> str | None:
    """Slice ``[ ... ]`` from ``open_index``, ignoring brackets inside quotes."""
    depth = 0
    quote = ""
    index = open_index
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in ("'", '"'):
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[open_index : index + 1]
        index += 1
    return None


def lock_shared_paths(text: str) -> list[str] | None:
    """The ``[shared_files] paths`` list, parsed exactly, or ``None``.

    ``None`` means the lock is absent, duplicated, malformed or unreadable --
    never a best-effort list that would make the fingerprint cover a different
    set of files than the writer recorded. An empty but well-formed
    ``paths = [ ... ]`` block is a legitimate lock for a target that shipped
    no shared files, so it parses to ``[]``.

    ``json.loads`` decodes escapes, so a declared filename containing a literal
    backslash-n would come back as a newline (no current path contains a
    backslash -- latent only); for that reason the reader accepts only the
    exact format the writer emits (double-quoted strings, trailing comma) and
    rejects single-quoted locks on purpose rather than guessing at a dialect.
    """
    body = lock_section(text, "shared_files")
    if not body:
        return None
    matches = list(_PATHS_ASSIGNMENT.finditer(body))
    if len(matches) != 1:
        return None
    block = _slice_bracket(body, matches[0].end() - 1)
    if block is None:
        return None
    block = re.sub(r",(\s*\]\s*)$", r"\1", block)
    try:
        parsed = json.loads(block)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None
    if not all(isinstance(item, str) for item in parsed):
        return None
    return list(parsed)
