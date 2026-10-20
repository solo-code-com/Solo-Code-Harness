#!/usr/bin/env python3
"""Gate Probe - does gate X catch defect Y?

Defects were fixed by hand in one session and gates were added for some of
them, but nothing records which gate catches which defect. A gate that catches
nothing looks exactly like one that catches everything, and this tool makes
that visible: one row per question, "does gate X flag the fixture for defect
Y?".

This is a REPORT, not a gate. It always exits 0 -- even when every defect is
caught, even when none is. A "no-gate" row is a finding (the defect has no
gate yet), never a failure. The tool is the deterministic, offline analogue of
a probe step: no model, no benchmark, no dependency, stdlib only.

The corpus is inline (the house pattern of tools/test_secret_patterns.py), not
a directory of fixture files. Each entry's fixture is written into a fresh
temp directory, the named adapter is run against it, and the outcome is
measured, not asserted: the corpus records no expected result, so this tool
cannot read an answer it was meant to compute. tools/test_gate_probe.py pins
the expected outcomes independently.

Three outcomes, and only three:
  caught  - an adapter exists and it flags the fixture
  missed  - an adapter exists and it does not flag the fixture
  no-gate - the defect has no gate, which is a finding, not a failure
An adapter that cannot run becomes an error string in its row, never a crash
and never a faked outcome.

Usage:
    python tools/gate_probe.py          print the matrix and summary, exit 0
    python tools/gate_probe.py --json   print the same data as JSON, exit 0
"""

from __future__ import annotations

import json
import subprocess  # noqa: S404 - runs this repo's own scripts only, fixed argv
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

JSONL_NAME = "gate-probe.jsonl"

CORPUS: list[dict] = [
    {
        "id": "hook-claims-missing-exit",
        "defect": "A hook header advertises exit code 1 but the body only exits 0.",
        "gate": "hook_exit_claims",
        "fixture": {
            "fake-hook.js": (
                "/**\n"
                " * Exit codes:\n"
                " *   0 = PASS\n"
                " *   1 = WARNING\n"
                " */\n"
                "process.exit(0);\n"
            ),
        },
    },
    {
        "id": "hook-claims-always-zero",
        "defect": "A hook header claims 'always 0' but the body returns exit 2.",
        "gate": "hook_exit_claims",
        "fixture": {
            "fake-hook.js": (
                "/**\n"
                " * Exit codes: always 0 (non-blocking).\n"
                " */\n"
                "if (blocked) process.exit(2);\n"
                "process.exit(0);\n"
            ),
        },
    },
    {
        "id": "comment-exempts-file",
        "defect": "A false claim plus a comment mentioning sys.exit(main()) upstream.",
        "gate": "hook_exit_claims",
        "fixture": {
            "fake-hook.js": (
                "/**\n"
                " * Exit codes:\n"
                " *   0 = allow\n"
                " *   2 = BLOCK\n"
                " */\n"
                "// wrapper does sys.exit(main()) upstream\n"
                "process.exit(0);\n"
            ),
        },
    },
    {
        "id": "non-harness-extension",
        "defect": "A project HTML page leaked into a harness directory.",
        "gate": "boundary_audit",
        "fixture": {
            ".kilo/leaked_page.html": "<html><body>project page</body></html>\n",
        },
    },
    {
        "id": "gui-asset-wrong-path",
        "defect": "A console asset dropped where the GUI allowance does not cover.",
        "gate": "boundary_audit",
        "fixture": {
            "tools/gui/deep/app.html": "<html><body>project app</body></html>\n",
        },
    },
    {
        "id": "root-level-project-file",
        "defect": "A project page at the repo root (a warning, not a strict error).",
        "gate": "boundary_audit",
        "fixture": {
            "index.html": "<html><body>project index</body></html>\n",
        },
    },
    {
        "id": "pip-dependency-in-tools",
        "defect": "A third-party pip import inside the harness tools tree.",
        "gate": "stdlib_only",
        "fixture": {
            "tools/x.py": "import requests\n",
        },
    },
    {
        "id": "fake-bypass-variable",
        "defect": "A documented bypass variable that no code actually reads.",
        "gate": "bypass_claims",
        "fixture": {
            "docs/notes.md": "To skip the gate, set FOO_BYPASS=1 in the environment.\n",
        },
    },
    {
        "id": "generator-newline-churn",
        "defect": "A source file lacks a trailing newline and a hook adds one back.",
        "gate": "text_hygiene",
        "fixture": {
            "src/app.py": "print('hello')",
            "tools/add-newline.js": "// re-adds a missing trailing newline\n",
        },
    },
]


def _run_script(script: Path, root: Path, extra: list[str]) -> bool | str:
    """Run a repo-local script against a temp root; True when it flags something.

    Returns an error string when the script is missing or cannot run, so a
    broken adapter becomes a visible row instead of a silent miss or a crash.
    """
    if not script.is_file():
        return f"missing adapter script: {script}"
    try:
        proc = subprocess.run(  # noqa: S603,S607 - fixed argv, no shell
            [sys.executable, str(script), str(root), *extra],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{script.name} could not run: {exc}"
    return proc.returncode != 0


def _boundary_audit(root: Path) -> bool | str:
    """boundary_audit: flags a non-harness file inside a harness directory."""
    return _run_script(
        ROOT / ".github" / "scripts" / "boundary_audit.py", root, ["--strict"]
    )


def _security_scan(root: Path) -> bool | str:
    """security_scan: flags a hardcoded secret or unsafe pattern."""
    return _run_script(ROOT / ".github" / "scripts" / "security_scan.py", root, [])


def _hook_exit_claims(root: Path) -> bool | str:
    """hook_exit_claims: flags a hook whose claimed exit codes drift from reality."""
    try:
        from tools.test_hook_exit_claims import check_hook
    except ImportError as exc:
        return f"cannot import hook_exit_claims adapter: {exc}"
    for hook in sorted(root.rglob("*")):
        if hook.is_file() and hook.suffix in (".js", ".py") and check_hook(hook):
            return True
    return False


def _stdlib_only(root: Path) -> bool | str:
    """stdlib_only: flags a third-party import in the tools/ or .github/scripts/ trees."""
    return _run_script(ROOT / "tools" / "check_stdlib_only.py", root, [])


def _text_hygiene(root: Path) -> bool | str:
    """text_hygiene: flags a trailing-space, no-final-newline, or CRLF finding."""
    return _run_script(ROOT / "tools" / "check_text_hygiene.py", root, [])


def _bypass_claims(root: Path) -> bool | str:
    """bypass_claims: flags a documented escape-hatch variable nothing reads."""
    return _run_script(ROOT / "tools" / "check_bypass_claims.py", root, [])


ADAPTERS: dict[str, Callable[[Path], bool | str]] = {
    "boundary_audit": _boundary_audit,
    "security_scan": _security_scan,
    "hook_exit_claims": _hook_exit_claims,
    "stdlib_only": _stdlib_only,
    "text_hygiene": _text_hygiene,
    "bypass_claims": _bypass_claims,
}


def _write_fixture(fixture: dict[str, str], root: Path) -> None:
    """Write one entry's fixture files into a fresh temp root."""
    for rel, content in fixture.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _judge(adapter: Callable[[Path], bool | str], root: Path) -> str:
    """Run one adapter and reduce its result to caught/missed/error."""
    try:
        result = adapter(root)
    except Exception as exc:  # noqa: BLE001 - an adapter crash is a row, not a crash
        return f"error: {exc}"
    if result is True:
        return "caught"
    if result is False:
        return "missed"
    return f"error: {result}"


def _probe_entry(entry: dict) -> dict:
    """Measure one entry: write its fixture, run its gate, name the outcome."""
    with tempfile.TemporaryDirectory(prefix="gate-probe-") as tmp:
        root = Path(tmp)
        _write_fixture(entry["fixture"], root)
        gate = entry["gate"]
        if gate is None:
            outcome = "no-gate"
        else:
            adapter = ADAPTERS.get(gate)
            if adapter is None:
                outcome = f"error: unknown gate {gate!r}"
            else:
                outcome = _judge(adapter, root)
    return {"id": entry["id"], "gate": gate, "outcome": outcome}


def run_probe() -> list[dict]:
    """Run every corpus entry and return one outcome row per entry."""
    return [_probe_entry(entry) for entry in CORPUS]


def summary_counts(rows: list[dict]) -> dict:
    """Count caught/missed/no-gate outcomes across the rows."""
    counts = {"caught": 0, "missed": 0, "no-gate": 0}
    for row in rows:
        outcome = row["outcome"]
        if outcome in counts:
            counts[outcome] += 1
    return counts


def render_matrix(rows: list[dict]) -> str:
    """Render the corpus results as an aligned id | gate | outcome table."""
    header = ("id", "gate", "outcome")
    table = [header]
    for row in rows:
        table.append(
            (row["id"], "None" if row["gate"] is None else row["gate"], row["outcome"])
        )
    widths = [max(len(cell) for cell in column) for column in zip(*table, strict=True)]
    lines = [" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(table[0]))]
    lines.append("-+-".join("-" * width for width in widths))
    for line in table[1:]:
        lines.append(" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(line)))
    return "\n".join(lines)


def render_summary(rows: list[dict]) -> str:
    """Render the one-line summary of the three outcome counts."""
    counts = summary_counts(rows)
    return (
        f"summary: caught={counts['caught']} missed={counts['missed']} "
        f"no-gate={counts['no-gate']}"
    )


def _utc_now() -> str:
    """Current UTC timestamp as ISO-8601, shared by every row in one run."""
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(rows: list[dict], root: Path = ROOT) -> None:
    """Append one JSON object per entry. A failed write must never raise."""
    path = root / ".solocode" / JSONL_NAME
    ts = _utc_now()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(
                    json.dumps(
                        {
                            "ts": ts,
                            "id": row["id"],
                            "gate": row["gate"],
                            "outcome": row["outcome"],
                        }
                    )
                    + "\n"
                )
    except OSError:
        pass


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    """CLI entry point. Always exits 0: this is a report, not a gate."""
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    rows = run_probe()
    if as_json:
        print(json.dumps({"rows": rows, "summary": summary_counts(rows)}, indent=2))
    else:
        print(render_matrix(rows))
        print(render_summary(rows))
    append_jsonl(rows, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
