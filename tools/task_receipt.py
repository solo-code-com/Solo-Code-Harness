#!/usr/bin/env python3
"""task_receipt.py — record and verify lightweight receipts for delegated agent runs.

Why this exists: A delegated agent (OpenCode, Codex, Antigravity) executes work
asynchronously or in a separate session. The orchestrator and human operator
need a concise, durable proof record of:
  1. What the task was (brief/prompt)
  2. Which worker & model executed it
  3. What scope was allowed vs what actually changed (git status, diffstat)
  4. What commands were executed and their verification outcomes
  5. The review status (VERIFIED, SCOPE_VIOLATION, FAILED, PENDING)

This enables review without forcing the operator to read thousands of lines
of raw execution transcripts.

Receipts live in `.solocode/task-receipts/<timestamp>-<run_id>.json`.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # noqa: S404 — fixed argv
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RECEIPT_DIR = ROOT / ".solocode" / "task-receipts"


def _git_diff_stat(root: Path = ROOT) -> str:
    """Capture git diff --stat of unstaged + staged changes."""
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "diff", "HEAD", "--stat"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _git_status_porcelain(root: Path = ROOT) -> list[str]:
    """Capture list of modified/added files from git status."""
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "status", "--porcelain"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode == 0:
            return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    return []


def format_receipt_markdown(receipt: dict[str, Any]) -> str:
    """Format a structured task receipt into human-readable markdown."""
    status_icon = (
        "[PASS]"
        if receipt.get("status") == "VERIFIED"
        else f"[{receipt.get('status', 'PENDING')}]"
    )
    lines = [
        f"# Task Receipt: {receipt.get('run_id')} {status_icon}",
        f"- **Timestamp**: {receipt.get('created_at')}",
        f"- **Worker**: `{receipt.get('worker')}` (`{receipt.get('model')}`)",
        f"- **Scope**: `{receipt.get('allowed_directory') or 'unrestricted'}`",
        f"- **Status**: `{receipt.get('status')}`",
        "",
        "## 1. Task Brief",
        "```text",
        receipt.get("task", "").strip(),
        "```",
        "",
        "## 2. Changes Detected",
    ]
    changed_files = receipt.get("changed_files", [])
    if changed_files:
        lines.append("### Modified Paths:")
        for item in changed_files:
            lines.append(f"- `{item}`")
    else:
        lines.append("*(No workspace files modified)*")

    diffstat = receipt.get("diffstat", "")
    if diffstat:
        lines.extend(["", "### Diffstat:", "```text", diffstat, "```"])

    verifications = receipt.get("verifications", [])
    lines.extend(["", "## 3. Verifications"])
    if verifications:
        for v in verifications:
            v_status = (
                "PASS"
                if v.get("exit_code") == 0
                else f"FAIL (exit {v.get('exit_code')})"
            )
            lines.append(f"- `{v.get('command')}` -> **{v_status}**")
            if v.get("note"):
                lines.append(f"  Note: {v.get('note')}")
    else:
        lines.append("*(No automated verification recorded)*")

    lines.append("")
    return "\n".join(lines)


def record_task_receipt(
    task: str,
    worker: str,
    model: str,
    *,
    run_id: str | None = None,
    allowed_directory: str | None = None,
    status: str = "PENDING",
    verifications: list[dict[str, Any]] | None = None,
    receipt_dir: Path = DEFAULT_RECEIPT_DIR,
    root: Path = ROOT,
) -> Path:
    """Create and persist a task receipt file."""
    run_id = run_id or uuid.uuid4().hex[:12]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    receipt_dir.mkdir(parents=True, exist_ok=True)

    changed_files = _git_status_porcelain(root)
    diffstat = _git_diff_stat(root)

    data: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task": task,
        "worker": worker,
        "model": model,
        "allowed_directory": allowed_directory,
        "status": status,
        "changed_files": changed_files,
        "diffstat": diffstat,
        "verifications": verifications or [],
    }

    filename = f"{timestamp}_{run_id}.json"
    target_path = receipt_dir / filename
    target_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Also emit a human-readable companion .md
    md_path = receipt_dir / f"{timestamp}_{run_id}.md"
    md_path.write_text(format_receipt_markdown(data), encoding="utf-8")

    return target_path


def list_receipts(receipt_dir: Path = DEFAULT_RECEIPT_DIR) -> list[dict[str, Any]]:
    """List all receipts sorted newest first."""
    if not receipt_dir.is_dir():
        return []
    receipts = []
    for file in sorted(receipt_dir.glob("*.json"), reverse=True):
        try:
            content = json.loads(file.read_text(encoding="utf-8"))
            content["_file"] = str(file)
            receipts.append(content)
        except (OSError, json.JSONDecodeError):
            continue
    return receipts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Task receipt recorder and viewer")
    sub = parser.add_subparsers(dest="command")

    record_cmd = sub.add_parser("record", help="Record a new task receipt")
    record_cmd.add_argument("--task", required=True, help="Task description or brief")
    record_cmd.add_argument(
        "--worker", required=True, help="Worker name (opencode, codex, gemini)"
    )
    record_cmd.add_argument("--model", required=True, help="Model ID used")
    record_cmd.add_argument("--scope", default=None, help="Allowed directory scope")
    record_cmd.add_argument(
        "--status",
        default="PENDING",
        choices=["PENDING", "VERIFIED", "FAILED", "SCOPE_VIOLATION"],
    )
    record_cmd.add_argument("--run-id", default=None, help="Explicit run ID")

    sub.add_parser("list", help="List recorded receipts")

    show_cmd = sub.add_parser("show", help="Show receipt details")
    show_cmd.add_argument("run_id", help="Run ID or partial filename")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "record":
        path = record_task_receipt(
            task=args.task,
            worker=args.worker,
            model=args.model,
            allowed_directory=args.scope,
            status=args.status,
            run_id=args.run_id,
        )
        print(f"Receipt recorded: {path}")
        return 0

    if args.command == "list":
        receipts = list_receipts()
        if not receipts:
            print("No task receipts found.")
            return 0
        for r in receipts:
            print(
                f"[{r.get('status', 'UNKNOWN')}] {r.get('run_id')} ({r.get('created_at')}) worker={r.get('worker')} files={len(r.get('changed_files', []))}"
            )
        return 0

    if args.command == "show":
        for r in list_receipts():
            if args.run_id in r.get("run_id", "") or args.run_id in r.get("_file", ""):
                print(format_receipt_markdown(r))
                return 0
        print(f"Receipt matching '{args.run_id}' not found.", file=sys.stderr)
        return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
