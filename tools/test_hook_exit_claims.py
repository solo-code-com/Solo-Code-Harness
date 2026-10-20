#!/usr/bin/env python3
"""Exit-code claims in hooks must match what the hook can actually return.

A hook header is documentation, and SPEC.md §7.2.1's rule is that a claim about
behaviour has to be checkable. Two real defects this gate was written from:

  * `.kilo/hooks/post-tool-use/quality-gate.js` advertised `1 = WARNING` while
    all three of its returns were `process.exit(0)`.
  * `.kilo/hooks/hookify/hookify-engine.js` advertised "Exit codes: always 0"
    while returning `process.exit(2)` when a blocking rule matches.

Both were invisible to `garden.py check_enforcement_claims()`, which reads only
`*.md` and only matches prose naming a script in backticks -- hook headers are
comments inside `.js`/`.py`, outside its reach.

Scope rules:
  * Only a file that prints an `Exit codes:` block is checked. No claim, nothing
    to verify.
  * A file with a non-literal exit argument -- `sys.exit(main())`,
    `process.exit(hits ? 0 : 1)` -- is skipped, because the codes it can return
    are not statically knowable.
  * There is deliberately no opt-out marker. The indirect-exit rule covers the
    legitimate cases, and adding a second escape hatch is how the false
    `GATE_GUARD_BYPASS` claim survived for months.

Usage:
    python -m pytest tools/test_hook_exit_claims.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HOOK_DIRS = (ROOT / ".kilo" / "hooks", ROOT / ".claude" / "hooks")
HOOK_SUFFIXES = (".js", ".py")

# A declared code: 1-3 digits immediately followed by "=" ("2 = BLOCK") or by "("
# ("0 (success)"). The lookbehind keeps "v3.1.0" and "10 minutes" out.
DECLARED_CODE = re.compile(r"(?<![\w.])([0-9]{1,3})\s*(?:=|\()")
# A literal exit: process.exit(2) / sys.exit(1) / exit(0)
LITERAL_EXIT = re.compile(r"(?:process\.exit|sys\.exit|exit)\(\s*([0-9]{1,3})\s*\)")
# An exit whose argument is an expression or a name -> codes are not statically
# knowable, so the file is exempt from both directions of the check.
INDIRECT_EXIT = re.compile(r"(?:process\.exit|sys\.exit)\(\s*[^)\s0-9]")

MAX_BLOCK_LINES = 9


def extract_exit_block(text: str) -> str | None:
    """Return the `Exit codes:` block (header plus comment continuation), or None."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.search(r"exit codes?", line, re.IGNORECASE):
            continue
        block = [line]
        for follower in lines[index + 1 : index + MAX_BLOCK_LINES]:
            if not follower.strip():
                break
            # Continuation lines stay inside the comment block: " * 2 = BLOCK",
            # "# 1 = blocked", "// 0 = allow".
            if not re.match(r"\s*(?:\*|#|//|\")", follower):
                break
            block.append(follower)
        return "\n".join(block)
    return None


def declared_codes(block: str) -> set[int]:
    return {int(code) for code in DECLARED_CODE.findall(block)}


def literal_exits(text: str) -> set[int]:
    return {int(code) for code in LITERAL_EXIT.findall(text)}


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"(?m)//.*$")
_TRIPLE_DOUBLE = re.compile(r'"""(?:.|\n)*?"""')
_TRIPLE_SINGLE = re.compile(r"'''(?:.|\n)*?'''")
_HASH_COMMENT = re.compile(r"(?m)#.*$")


def strip_comments(text: str) -> str:
    """Remove comments and docstrings before looking for exit calls.

    Without this, a comment that merely mentions `sys.exit(main())` exempts the
    whole file -- an escape hatch triggerable from one line of prose, which the
    module deliberately does not otherwise offer. A coarse stripper is fine here
    because the only thing searched for afterwards is an exit call.
    """
    text = _BLOCK_COMMENT.sub("", text)
    text = _TRIPLE_DOUBLE.sub("", text)
    text = _TRIPLE_SINGLE.sub("", text)
    text = _LINE_COMMENT.sub("", text)
    return _HASH_COMMENT.sub("", text)


def check_hook(path: Path) -> list[str]:
    """Return claim mismatches for one hook file, or [] when it is honest."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    block = extract_exit_block(text)
    if block is None:
        return []  # no claim made

    code = strip_comments(text)
    if INDIRECT_EXIT.search(code):
        return []  # codes not statically knowable

    declared = declared_codes(block)
    actual = literal_exits(code)

    issues: list[str] = []
    for code in sorted(declared - actual):
        if code != 0:
            issues.append(
                f"{path.name}: advertises exit {code} but never returns it "
                f"(returns: {sorted(actual) or 'nothing'})"
            )
    for code in sorted(actual - declared):
        if code != 0:
            issues.append(
                f"{path.name}: returns exit {code} without advertising it "
                f"(advertises: {sorted(declared)})"
            )
    return issues


def hook_files() -> list[Path]:
    found: list[Path] = []
    for hook_dir in HOOK_DIRS:
        if not hook_dir.is_dir():
            continue
        found.extend(
            p
            for p in sorted(hook_dir.rglob("*"))
            if p.is_file()
            and p.suffix in HOOK_SUFFIXES
            and "node_modules" not in p.parts
        )
    return found


# ── repository-wide gate ───────────────────────────────────────────────────────


def test_shipped_hooks_are_honest_about_exit_codes() -> None:
    """Every hook that prints an `Exit codes:` block must match its returns."""
    files = hook_files()
    assert files, "no hook files found -- the paths moved?"

    issues = [issue for path in files for issue in check_hook(path)]
    assert issues == [], "exit-code claims drifted from behaviour:\n  " + "\n  ".join(
        issues
    )


def test_the_gate_covers_the_hook_trees() -> None:
    """A gate that finds nothing because it looked nowhere is the defect itself."""
    names = {path.name for path in hook_files()}
    assert {"gate-guard.js", "quality-gate.js", "hookify-engine.js"} <= names


def test_the_repo_gate_actually_verifies_files() -> None:
    """`issues == []` is also what a gate that examines nothing returns.

    Without this, wrapping `check_hook` in an early `return []` would keep the
    repo-wide test above green forever.
    """

    def _text(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="ignore")

    parsed = [p for p in hook_files() if extract_exit_block(_text(p))]
    verified = [p for p in parsed if not INDIRECT_EXIT.search(strip_comments(_text(p)))]

    assert len(verified) >= 10, (
        f"only {len(verified)} of {len(parsed)} hook files are actually verified"
    )


# ── parser and rule tests (the gate must be able to fail) ─────────────────────


def _hook(tmp_path: Path, body: str, name: str = "fake-hook.js") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_advertised_code_without_a_return_is_flagged(tmp_path) -> None:
    """The quality-gate.js defect, reduced."""
    path = _hook(
        tmp_path,
        "/**\n * Exit codes:\n *   0 = PASS\n *   1 = WARNING\n */\nprocess.exit(0);\n",
    )
    issues = check_hook(path)
    assert len(issues) == 1
    assert "advertises exit 1" in issues[0]


def test_unadvertised_return_is_flagged(tmp_path) -> None:
    """The hookify-engine.js defect, reduced."""
    path = _hook(
        tmp_path,
        "/**\n * Exit codes: always 0 (non-blocking).\n */\nif (x) process.exit(2);\n"
        "process.exit(0);\n",
    )
    issues = check_hook(path)
    assert len(issues) == 1
    assert "returns exit 2 without advertising it" in issues[0]


def test_matching_claims_pass(tmp_path) -> None:
    path = _hook(
        tmp_path,
        "/**\n * Exit codes:\n *   0 = ALLOW\n *   2 = BLOCK\n */\nprocess.exit(2);\n",
    )
    assert check_hook(path) == []


def test_inline_declaration_form_passes(tmp_path) -> None:
    """`Exit codes: 0 (success), 1 (failure)` on one line."""
    path = _hook(
        tmp_path,
        "/**\n * Exit codes: 0 (success), 1 (failure)\n */\nprocess.exit(1);\n",
    )
    assert check_hook(path) == []


def test_indirect_exit_is_exempt(tmp_path) -> None:
    """`process.exit(hits ? 0 : 1)` cannot be resolved, so it is not a violation."""
    path = _hook(
        tmp_path,
        "/**\n * Exit codes: 0 (found), 1 (none), 2 (error)\n */\n"
        "process.exit(hits.length ? 0 : 1);\n",
    )
    assert check_hook(path) == []


def test_indirect_python_exit_is_exempt(tmp_path) -> None:
    path = _hook(
        tmp_path,
        '"""\nExit codes: 0 = passed; 1 = a gate failed.\n"""\nimport sys\nsys.exit(main())\n',
        name="fake_hook.py",
    )
    assert check_hook(path) == []


def test_file_without_a_claim_is_not_checked(tmp_path) -> None:
    """No claim means nothing to verify -- silence is not dishonesty."""
    path = _hook(
        tmp_path, "/**\n * Blocks dangerous commands.\n */\nprocess.exit(2);\n"
    )
    assert check_hook(path) == []


def test_prose_digits_are_not_read_as_codes(tmp_path) -> None:
    """A line count or a version must not be mistaken for a declared code."""
    path = _hook(
        tmp_path,
        "/**\n * Exit codes:\n *   0 = PASS\n *\n * Tried 3 times over 2 hours.\n"
        " */\nprocess.exit(0);\n",
    )
    assert check_hook(path) == []


def test_indirect_exit_only_in_a_comment_does_not_exempt(tmp_path) -> None:
    """A comment must not be able to switch the gate off for a whole file.

    One line of prose mentioning `sys.exit(main())` used to exempt every declared
    code -- the escape hatch the module's docstring promises does not exist.
    """
    path = _hook(
        tmp_path,
        "/**\n * Exit codes:\n *   0 = allow, 2 = BLOCK\n */\n"
        "// wrapper does sys.exit(main()) upstream\nprocess.exit(0);\n",
    )
    issues = check_hook(path)
    assert len(issues) == 1
    assert "advertises exit 2" in issues[0]


def test_multidigit_code_is_read_in_both_directions(tmp_path) -> None:
    """Codes above 9 are real (130 = interrupted) and must not be invisible."""
    path = _hook(
        tmp_path,
        "/**\n * Exit codes:\n *   0 = ok\n *   130 = interrupted\n */\n"
        "process.exit(130);\n",
    )
    assert check_hook(path) == []


def test_missing_multidigit_code_is_flagged(tmp_path) -> None:
    path = _hook(
        tmp_path,
        "/**\n * Exit codes:\n *   0 = ok\n *   128 = BLOCK\n */\nprocess.exit(0);\n",
    )
    issues = check_hook(path)
    assert len(issues) == 1
    assert "advertises exit 128" in issues[0]
