#!/usr/bin/env python3
"""Guard Hook Test Suite - validates safety patterns"""

import base64
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / ".claude" / "hooks"))
import guard


def test_normalize_command():
    """Test command normalization strips wrappers."""
    assert guard.normalize_command("sudo ls") == "ls"
    assert guard.normalize_command("env FOO=bar ls") == "ls"


def test_protected_files():
    """Test protected config files list."""
    assert ".ruff.toml" in guard.PROTECTED_FILES
    assert "eslint.config.js" in guard.PROTECTED_FILES
    assert "biome.json" in guard.PROTECTED_FILES


def test_executor_mode():
    """Test executor mode exemptions."""
    assert guard.executor_mode_exempt(".solocode/executor-mode")
    assert guard.executor_mode_exempt(".gemini/antigravity/handoff/inbox/task.md")
    assert not guard.executor_mode_exempt("tools/test.py")


def test_mask_separator_quotes():
    """A quoted span holding a separator is data, not a command boundary."""
    assert guard.mask_separator_quotes("rg 'echo x; diskpart' .") == "rg '' ."
    assert guard.mask_separator_quotes('rg "a|b" .') == "rg '' ."
    # No separator inside the quotes, so the text must survive: `rm -rf '/'`
    # depends on the quoted root still reaching the pattern.
    assert guard.mask_separator_quotes("rm -rf '/'") == "rm -rf '/'"
    # An unbalanced quote passes through rather than swallowing the tail.
    assert guard.mask_separator_quotes("echo 'unclosed; tail") == "echo 'unclosed; tail"


def test_destructive_scan_ignores_data_in_quotes():
    """find_destructive reads the masked text, so naming a command stays allowed."""
    assert guard.find_destructive("rg 'echo x; diskpart' .") is None
    assert guard.find_destructive("rg 'Remove-Item x -Recurse -Force' .") is None
    assert guard.find_destructive("rm -rf /") is not None
    assert guard.find_destructive("Remove-Item C:/ -Recurse") is not None
    assert (
        guard.find_destructive('cmd /c "Remove-Item -Recurse -Force C:\\\\data"')
        is not None
    )
    # A shell wrapper after a separator, which normalize_command only unwraps at
    # the start of the command, must still be caught.
    assert guard.find_destructive("echo hi && bash -c 'rm -rf /'") is not None
    assert guard.find_destructive("true; sh -c 'rm -rf /'") is not None
    # The same wrapper inside quoted search text is data, not command position.
    assert (
        guard.find_destructive("rg 'cmd /c \"Remove-Item -Recurse -Force C:\\data\"' .")
        is None
    )
    assert (
        guard.find_destructive(
            "rg 'powershell -Command \"Remove-Item -Recurse -Force C:\\data\"' ."
        )
        is None
    )
    # A `bash -c` wrapper after a separator is executable command text; masking
    # its quoted payload as search data first erased the destructive command.
    assert guard.find_destructive("echo hi && bash -c 'rm -rf /etc'") is not None
    assert guard.find_destructive("echo hi && bash -c 'cd / && rm -rf /'") is not None
    assert (
        guard.find_destructive("echo hi && bash -c 'rm -rf /; echo done'") is not None
    )
    # The Windows wrappers carry the same payload-before-masking defect.
    assert (
        guard.find_destructive(
            'echo hi && cmd /c "Remove-Item -Recurse -Force C:\\data; echo x"'
        )
        is not None
    )
    assert (
        guard.find_destructive(
            "echo hi && powershell -Command "
            '"Remove-Item -Recurse -Force C:\\data; echo x"'
        )
        is not None
    )
    # The same wrappers inside quoted search text are data, not command position.
    assert guard.find_destructive("rg \"echo hi && bash -c 'rm -rf /etc'\" .") is None
    assert (
        guard.find_destructive("rg \"echo hi && bash -c 'cd / && rm -rf /'\" .") is None
    )
    assert (
        guard.find_destructive("rg \"echo hi && bash -c 'rm -rf /; echo done'\" .")
        is None
    )
    assert guard.find_destructive("rg \"bash -c 'rm -rf /; echo done'\" .") is None
    assert (
        guard.find_destructive(
            "rg 'echo hi && cmd /c \"Remove-Item -Recurse -Force C:\\data; echo x\"' ."
        )
        is None
    )


def test_unwrap_command_wrappers():
    """An executable wrapper unwraps; the same text as search data does not."""
    assert (
        guard.unwrap_command_wrappers("echo hi && bash -c 'cd / && rm -rf /'")
        == "echo hi && cd / && rm -rf /"
    )
    assert (
        guard.unwrap_command_wrappers("rg \"bash -c 'rm -rf /; echo done'\" .")
        == "rg \"bash -c 'rm -rf /; echo done'\" ."
    )
    assert (
        guard.unwrap_command_wrappers('echo hi && cmd /c "Remove-Item C:/ -Recurse"')
        == "echo hi && Remove-Item C:/ -Recurse"
    )
    # A chained payload still unwraps, and `env` leads a wrapper just as `sudo`
    # does -- the prefix list mirrors _RM_CMD's composition prefixes.
    assert (
        guard.unwrap_command_wrappers("echo hi && bash -c 'rm -rf /; echo done'")
        == "echo hi && rm -rf /; echo done"
    )
    assert (
        guard.unwrap_command_wrappers("echo hi && env bash -c 'rm -rf /; echo done'")
        == "echo hi && rm -rf /; echo done"
    )
    # An ambiguous parse returns the text untouched so the masking path decides:
    # a candidate quote behind an odd backslash run, and one followed by a
    # backslash (the POSIX `'\''` concatenation).
    assert (
        guard.unwrap_command_wrappers('bash -c "echo C:\\"; rm -rf / && echo "done"')
        == 'bash -c "echo C:\\"; rm -rf / && echo "done"'
    )
    assert (
        guard.unwrap_command_wrappers(r"""bash -c 'echo '\''hi'\''; rm -rf /'""")
        == r"""bash -c 'echo '\''hi'\''; rm -rf /'"""
    )
    # A doubled quote is PowerShell's in-string quote, not the payload's end, so
    # this one unwraps now (round 10): treating `""` as the terminator was gap 2.
    assert (
        guard.unwrap_command_wrappers(
            'powershell -Command "Write-Host ""hi""; '
            + 'Remove-Item -Recurse -Force C:\\data"'
        )
        == 'Write-Host ""hi""; ' + "Remove-Item -Recurse -Force C:\\data"
    )
    # A nested wrapper whose inner quote is backslash-escaped only matches once
    # the payload is unescaped for its dialect (gap 3).
    assert (
        guard.unwrap_command_wrappers('bash -c "bash -c \\"rm -rf /\\""') == "rm -rf /"
    )
    # An unquoted `-c` payload is unescaped the way the shell does it (gap 9).
    assert guard.unwrap_command_wrappers(r"sh -c rm\ -rf\ /") == "rm -rf /"


def test_quoted_system_trees_and_chained_payloads():
    """Orchestrator follow-up: pin what mutation testing showed was unpinned."""
    # The quote terminator's job: a quoted system tree is the same wipe.
    assert guard.find_destructive("rm -rf '/etc'") is not None
    assert guard.find_destructive('rm -rf "/usr"') is not None
    # ...and the same tree chained inside an executable wrapper was still a miss.
    assert (
        guard.find_destructive("echo hi && bash -c 'rm -rf /etc; echo x'") is not None
    )
    # A search that names a quoted system tree stays data.
    assert guard.find_destructive("rg \"rm -rf '/etc'\" .") is None


def test_dialects_and_embedded_payloads():
    """Round-10: the nine quoting gaps, each with its search twin.

    A payload's end is read with the shell's own lexicon, `eval`'s argument and
    a piped shell's input are executable text, and a wrapper with an unquoted
    `-c` argument is unescaped the way the shell does. The twin pins the other
    direction: the same text inside a search is data.
    """
    # 1-6: the payload-end lexicon, per shell family.
    assert (
        guard.find_destructive("bash -c \"echo C:\\\"; bash -c 'rm -rf /; echo done'")
        is not None
    )
    assert (
        guard.find_destructive(
            'powershell -Command "Write-Host ""hi""; Remove-Item -Recurse -Force C:\\data"'
        )
        is not None
    )
    assert (
        guard.find_destructive('echo hi && bash -c "bash -c \\"rm -rf /\\""')
        is not None
    )
    assert (
        guard.find_destructive('echo hi && cmd /c "echo Don\'t & rd /s /q C:\\Windows"')
        is not None
    )
    assert (
        guard.find_destructive("echo hi && bash -c 'rm -rf /; echo C:\\'") is not None
    )
    assert (
        guard.find_destructive('echo hi && cmd /c "rd /s /q C:\\data & echo done\\"')
        is not None
    )
    # 7-9: text a command executes without appearing as a command.
    assert guard.find_destructive("eval 'rm -rf /'") is not None
    assert guard.find_destructive("echo 'rm -rf /' | bash") is not None
    assert guard.find_destructive(r"sh -c rm\ -rf\ /") is not None

    # The twins: the same text, named inside a search.
    assert (
        guard.find_destructive(
            "rg 'bash -c \"echo C:\\\"; bash -c 'rm -rf /; echo done'' ."
        )
        is None
    )
    assert (
        guard.find_destructive(
            'rg \'powershell -Command "Write-Host ""hi""; '
            "Remove-Item -Recurse -Force C:\\data\"' ."
        )
        is None
    )
    assert (
        guard.find_destructive('rg \'echo hi && bash -c "bash -c \\"rm -rf /\\""\' .')
        is None
    )
    assert (
        guard.find_destructive(
            """rg 'echo hi && cmd /c "echo Don''t & rd /s /q C:\\Windows"' ."""
        )
        is None
    )
    assert (
        guard.find_destructive(r"""rg "echo hi && bash -c 'rm -rf /; echo C:\'" .""")
        is None
    )
    assert (
        guard.find_destructive(
            """rg 'echo hi && cmd /c "rd /s /q C:\\data & echo done\\"' ."""
        )
        is None
    )
    assert guard.find_destructive("""rg "eval 'rm -rf /'" .""") is None
    assert guard.find_destructive("""rg "echo 'rm -rf /' | bash" .""") is None
    assert guard.find_destructive(r"""rg 'sh -c rm\ -rf\ /' .""") is None


def test_unescape_payload_is_dialect_aware():
    r"""Round-4: consume `escape + char` only where the dialect escapes it.

    `unescape_payload` removed every `\X`, but a POSIX shell strips a backslash
    only before `$`, a backtick, `"`, `\` or a newline. Decoding `\m` and `\ `
    turned `bash -c "r\m -rf /"` -- a command the shell would fail to run --
    into `rm -rf /` and blocked harmless work; the same decode is what keeps the
    nested escaped wrapper blocked.
    """
    posix = guard.DIALECTS["bash:double"][2]
    # A pseudo-escape survives: the false-block fix.
    assert guard.unescape_payload(r"r\m -rf /", "\\", posix) == r"r\m -rf /"
    # A real escape is still consumed, which the nested wrapper depends on.
    assert guard.unescape_payload('a\\"b', "\\", posix) == 'a"b'
    assert guard.unescape_payload("a\\\\b", "\\", posix) == "a\\b"
    # The backtick dialect escapes the next character generally: unchanged.
    assert guard.unescape_payload("a`b", "`", None) == "ab"
    # No escape character (POSIX single quotes): the payload is returned as-is.
    assert guard.unescape_payload(r"a\mb", None, None) == r"a\mb"


def test_payload_unescaping_stays_scoped_to_the_quote():
    r"""Two measured regressions of the dialect scanner, in one place.

    The payload is unescaped before the block patterns read it, so an
    over-broad decode rewrites text the shell would run DIFFERENTLY:

    - `` Remove-Item`t-Recurse`t-Force `` runs as three words (`` `t `` is a
      tab, a real separator), so the wipe must stay BLOCKED; decoding every
      escape turned it into `Remove-Itemt-Recurset-Force`, a name no pattern
      matches, and the wipe was allowed.
    - `'Write-Host ''data; rm -rf /'''`: the `''` is PowerShell's in-string
      quote (one literal quote), so the whole -Command argument is data and the
      command is ALLOWED; the doubled pair reached the patterns as two balanced
      empty spans, putting `rm` in command position.

    Only the quote character is unescaped. Every other escape is left as
    written, and the doubled in-string quote is halved for the single-quoted
    shells, which have no escape character to decode it.
    """
    ps_double = guard.DIALECTS["powershell:double"][2]
    cmd_double = guard.DIALECTS["cmd:double"][2]
    # A non-quote escape is literal text: the payload reaches the patterns as
    # written, so `Remove-Item`t-Recurse`t-Force` is one word, not the wipe.
    assert guard.unescape_payload("a`tb", "`", ps_double) == "a`tb"
    assert guard.unescape_payload("a^tb", "^", cmd_double) == "a^tb"
    assert (
        guard.unwrap_command_wrappers('powershell -Command "Write-Host `t x"')
        == "Write-Host `t x"
    )
    assert (
        guard.find_destructive(
            'powershell -Command "Remove-Item`t-Recurse`t-Force C:\\data"'
        )
        is not None
    )
    # The quote escape itself is still consumed -- the nested-wrapper case needs
    # it, and that is the ONLY escape the restriction keeps.
    assert guard.unescape_payload('a`"b', "`", ps_double) == 'a"b'
    assert guard.unescape_payload('a^"b', "^", cmd_double) == 'a"b'
    # A PowerShell in-string quote is one quote to the shell, so the argument is
    # data: the wipe that is NOT there must not be reported.
    assert (
        guard.find_destructive("powershell -Command 'Write-Host ''data; rm -rf /'''")
        is None
    )
    # ...while a real wipe in exactly that shape still blocks.
    assert (
        guard.find_destructive(
            "powershell -Command 'Write-Host ''x''; "
            "Remove-Item -Recurse -Force C:\\data'"
        )
        is not None
    )


def test_unquoted_eval_and_pipelined_shells():
    """Round-11: a shell runs text the guard never read, quoted or not.

    `eval` concatenates its arguments, so an unquoted argument executes exactly
    like the quoted form; a shell on the far side of a pipe reads stdin, so the
    unquoted words on the input side are the script too; and that shell may be
    named by a path (`/bin/bash`), not only by a bare identifier. Each case has
    its twin: the same text merely NAMED inside a search stays data.
    """
    assert guard.find_destructive("eval rm -rf /") is not None
    assert guard.find_destructive("echo rm -rf / | bash") is not None
    assert guard.find_destructive("echo 'rm -rf /' | /bin/bash") is not None
    # The twins: the same text, named inside a search, is data.
    assert guard.find_destructive('rg "eval rm -rf /" .') is None
    assert guard.find_destructive('rg "echo rm -rf / | bash" .') is None
    assert guard.find_destructive("""rg "echo 'rm -rf /' | /bin/bash" .""") is None


def test_mask_pairs_quotes_escape_aware():
    r"""Round-12: an escaped quote closed by the span's own end is content.

    `bash -c "echo \""; rg "echo; rm -rf /" .` is the shape: the wrapper's
    payload is `echo "` (bash prints a quote) and the search that follows names
    the wipe inside its pattern, so nothing destructive runs. The pairing read
    the escaped `\"` as the span's end, which shifted the spans and exposed the
    search data as a command. A quote behind an odd backslash run is no longer
    the span's end -- but only when the quote that really closes the span
    follows it (`\""`). On its own such a quote is still the end, which the
    round-9 pin below relies on to expose its wipe.
    """
    # The escaped quote followed by its closing quote: both are content, so the
    # search pattern holding the separator is blanked and the command allows.
    assert (
        guard.mask_separator_quotes(r"""bash -c "echo \""; rg "echo; rm -rf /" .""")
        == r"""bash -c "echo \""; rg '' ."""
    )
    # The payload closes right after the escaped quote, so the payload-end walk
    # cannot say where it really ends and the wrapper is left verbatim; the raw
    # text is then read and the search stays data.
    assert (
        guard.unwrap_command_wrappers(r"""bash -c "echo \""; rg "echo; rm -rf /" .""")
        == r"""bash -c "echo \""; rg "echo; rm -rf /" ."""
    )
    assert (
        guard.find_destructive(r"""bash -c "echo \""; rg "echo; rm -rf /" .""") is None
    )
    # An escaped quote NOT followed by its own quote is still the span's end --
    # the reading the round-9 pin depends on. Its wipe is exposed and stays
    # blocked, so being escape-aware must not go further than this.
    assert (
        guard.mask_separator_quotes(r'''bash -c "echo C:\"; rm -rf / && echo "done"''')
        == r'''bash -c "echo C:\"; rm -rf / && echo "done"'''
    )
    assert (
        guard.find_destructive(r'''bash -c "echo C:\"; rm -rf / && echo "done"''')
        is not None
    )


def test_base64_printed_form():
    """guard.py 663-664: a blob `echo` prints is piped into the decoder."""
    assert guard.decode_base64_payloads("echo cm0gLXJmIC90bXAveA== | base64 -d") == [
        "rm -rf /tmp/x"
    ]


def test_base64_herestring_form():
    """guard.py 665-666: a herestring blob is decoded the same way."""
    assert guard.decode_base64_payloads("base64 -d <<< cm0gLXJmIC90bXAveA==") == [
        "rm -rf /tmp/x"
    ]


def test_base64_file_forms(tmp_path, monkeypatch):
    """guard.py 667-678: a `.b64` file is read, a missing one is skipped."""
    (tmp_path / "payload.b64").write_text("cm0gLXJmIC90bXAveA==", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert guard.decode_base64_payloads("cat payload.b64 | base64 -d") == [
        "rm -rf /tmp/x"
    ]
    assert guard.decode_base64_payloads("base64 -d payload.b64") == ["rm -rf /tmp/x"]
    # A missing file cannot be decoded, and the name alone is not evidence.
    assert guard.decode_base64_payloads("cat missing.b64 | base64 -d") == []


def test_base64_padding_repair_and_urlsafe_fallback():
    """guard.py 682-696: an empty, unpadded, URL-safe or undecodable blob."""
    # A blob that cleans to empty contributes no payload.
    assert guard.decode_base64_payloads("echo '' | base64 -d") == []
    # The obfuscator's shape: padding dropped, repaired before either decode.
    assert guard.decode_base64_payloads("echo cm0gLXJmIC90bXAveA | base64 -d") == [
        "rm -rf /tmp/x"
    ]
    # `-` is outside the strict alphabet, so the URL-safe fallback reads it.
    assert guard.decode_base64_payloads("echo QUI- | base64 -d") == ["AB>"]
    # Neither decoder accepts it: no payload, and no exception.
    assert guard.decode_base64_payloads("echo A | base64 -d") == []


def test_base64_ignores_binary_and_non_printable():
    """guard.py 697-702: binary and control characters are not shell text."""
    # bytes ff fe fd, not UTF-8: the decode is dropped.
    assert guard.decode_base64_payloads("echo //79 | base64 -d") == []
    # bytes 00 01 02 decode, but they are not printable text.
    assert guard.decode_base64_payloads("echo AAEC | base64 -d") == []


def test_base64_dedupes_and_caps_payloads():
    """guard.py 703-706: one copy of a repeated blob, five distinct at most."""
    assert guard.decode_base64_payloads(
        "echo cm0gLXJmIC90bXAveA== | base64 -d; echo cm0gLXJmIC90bXAveA== | base64 -d"
    ) == ["rm -rf /tmp/x"]
    six = "; ".join(
        f"echo {base64.b64encode(f'echo {n}'.encode()).decode()} | base64 -d"
        for n in range(1, 7)
    )
    assert len(guard.decode_base64_payloads(six)) == 5


def test_base64_payload_reaches_the_block_patterns():
    """guard.py 752: a decoded blob is re-scanned as command text."""
    assert (
        guard.find_destructive("echo cm0gLXJmIC90bXAveA== | base64 -d | sh") is not None
    )


def test_main_denies_a_base64_encoded_secret(monkeypatch):
    """guard.py 1212-1217: the secret path decodes the blob and denies."""
    blob = base64.b64encode(b"ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnop").decode()
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": f"echo {blob} | base64 -d"}}
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    with pytest.raises(SystemExit) as exc:
        guard.main()
    assert exc.value.code == 2


def test_main_allows_a_harmless_base64_payload(monkeypatch):
    """guard.py 1218: a blob that decodes to harmless text returns 0."""
    blob = base64.b64encode(b"echo hello").decode()
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": f"echo {blob} | base64 -d"}}
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert guard.main() == 0


def run_self_test():
    """Run basic validation tests."""
    print("Guard hook self-test...")
    tests = [
        ("Command normalization", test_normalize_command),
        ("Protected files", test_protected_files),
        ("Executor mode", test_executor_mode),
        ("Separator-aware masking", test_mask_separator_quotes),
        ("Destructive scan masking", test_destructive_scan_ignores_data_in_quotes),
        ("Wrapper unwrapping", test_unwrap_command_wrappers),
        ("Quoted system trees", test_quoted_system_trees_and_chained_payloads),
        ("Dialects and embedded payloads", test_dialects_and_embedded_payloads),
        ("Dialect-aware unescaping", test_unescape_payload_is_dialect_aware),
        (
            "Quote-only payload unescaping",
            test_payload_unescaping_stays_scoped_to_the_quote,
        ),
        (
            "Unquoted eval and piped shells",
            test_unquoted_eval_and_pipelined_shells,
        ),
        ("Escape-aware masking", test_mask_pairs_quotes_escape_aware),
    ]

    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  [OK] {name}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")

    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if run_self_test() else 1)
