#!/usr/bin/env python3
"""
Review Receipt - proof that a review was recorded for a tree
============================================================
`.kilo/command/review.md` ends by calling this tool, and `verify` is the gate
that reads it. The gap it closes: nothing recorded that an independent review
happened before a change landed, and a commit-message gate cannot reach a
project where the harness is installed but the commit history is not ours.

What a receipt proves - and what it does not
--------------------------------------------
A receipt is an unauthenticated local attestation. It proves that a receipt
file was written for this tree. It does not prove the review was good, and it
does not authenticate the reviewer: `reviewer` and `model` are declarative
labels anyone can type, and anyone able to write into the receipt directory can
satisfy the gate without running `/review` at all. Treat it as a record that a
step happened in a cooperative workflow, never as an authorization control. A
receipt carries no repository identity either, so two repositories sharing one
receipt_dir share receipts.

Change set
----------
Three reads against the index, so only staged changes count - in a pre-commit
hook, pre-commit stashes unstaged changes, so the hook sees only the staged tree:
`git diff --cached --name-only` (file list), `git diff --cached --numstat` (added plus
removed lines) and `git diff --cached --binary` (content). Paths are sorted, then
sha256 covers the newline-joined path list followed by the raw patch. The same
tree hashes identically; any content change moves the hash, which is why a
receipt for a stale tree cannot satisfy a later check.

`--binary` is load-bearing: without it git renders every binary change as the
same "Binary files ... differ" line, so two different binary contents hash
identically and a receipt would survive a binary edit. `--no-ext-diff` and
`--no-textconv` keep measurement a pure read, so a configured diff driver
cannot run during a gate. Untracked files are invisible to `git diff --cached`, so
an unadded new file does not move the hash, and neither does an unstaged
edit. Stage the change, then record, because only the index is hashed.
What the index hash does not distinguish: intent-to-add files and unstaged-only files are invisible, and renames, mode changes and submodules appear as patch content.

Exit codes
----------
0  success, or review not required
1  review required and no receipt exists for this tree
2  operational failure: git missing, not a git repo, unreadable policy, corrupt
   or mismatched receipt, unwritable receipt directory. A broken environment
   must never read as a pass.

`verify --advisory` measures instead of gates: the same decision, but it always
exits 0 and appends one row to `.solocode/review-advisory.jsonl`, so the firing
rate of a future blocking hook is a measured number, not a guess. `stats`
summarizes that log.

Usage:
    python tools/review_receipt.py record --reviewer codex --model gpt-6.1-sol
    python tools/review_receipt.py verify
    python tools/review_receipt.py verify --json
    python tools/review_receipt.py verify --advisory
    python tools/review_receipt.py stats
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess  # noqa: S404 - fixed argv, no shell, runs the repo's own git
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = Path("tools") / "config" / "review-policy.json"
SCHEMA_VERSION = 1
TOOL_VERSION = "1.0.0"
GIT = "git"

# Flags that keep every diff read pure: no configured driver, no filters, no
# colour codes standing in for content.
_READ_ONLY_DIFF_FLAGS = ["--no-ext-diff", "--no-textconv"]

# One JSON row per advisory run; lives beside the shared state, never committed.
ADVISORY_LOG = Path(".solocode") / "review-advisory.jsonl"


class ReceiptError(RuntimeError):
    """An operational failure, reported as exit code 2 - never as a pass."""


def _git_output(args: list[str], root: Path, git: str = GIT) -> str:
    """Run one git command in `root` and return stdout.

    Every failure raises ReceiptError naming the cause: a missing binary, a
    directory that is not a repository, an unreadable revision.
    """
    try:
        proc = subprocess.run(  # noqa: S603,S607 - fixed argv, no shell, no user input
            [git, *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise ReceiptError(f"git is not available: {git!r} was not found") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReceiptError(f"could not run git in {root}: {exc}") from exc

    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout).strip().splitlines()
        cause = lines[0] if lines else f"exit code {proc.returncode}"
        raise ReceiptError(f"git {args[0]} failed in {root}: {cause}")
    return proc.stdout


def compute_change_set(root: Path = ROOT, git: str = GIT) -> dict[str, object]:
    """Measure the current change set: files, line count and tree hash."""
    names = _git_output(
        ["diff", "--cached", "--name-only", *_READ_ONLY_DIFF_FLAGS], root, git
    )
    files = sorted({line.strip() for line in names.splitlines() if line.strip()})

    changed_lines = 0
    numstat = _git_output(
        ["diff", "--cached", "--numstat", *_READ_ONLY_DIFF_FLAGS], root, git
    )
    for row in numstat.splitlines():
        added, _, rest = row.partition("\t")
        removed = rest.partition("\t")[0]
        # Binary files report "-" instead of a count.
        changed_lines += int(added) if added.isdigit() else 0
        changed_lines += int(removed) if removed.isdigit() else 0

    diff = _git_output(
        ["diff", "--cached", "--binary", "--no-color", *_READ_ONLY_DIFF_FLAGS],
        root,
        git,
    )
    digest = hashlib.sha256()
    digest.update("\n".join(files).encode("utf-8"))
    digest.update(b"\n")
    digest.update(diff.encode("utf-8"))

    return {
        "files": files,
        "changed_files": len(files),
        "changed_lines": changed_lines,
        "tree_hash": digest.hexdigest(),
    }


def load_policy(root: Path = ROOT) -> dict[str, object]:
    """Load and validate the review policy.

    Deliberately strict: a policy that silently defaults to "no review needed"
    is a gate that passes for the wrong reason.
    """
    path = root / POLICY_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReceiptError(f"review policy not found: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"review policy unreadable ({path}): {exc}") from exc

    if not isinstance(payload, dict):
        raise ReceiptError(f"review policy must be a JSON object: {path}")

    for key in ("min_changed_files", "min_changed_lines"):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ReceiptError(
                f"review policy {key} must be a non-negative integer ({path})"
            )

    receipt_dir = payload.get("receipt_dir")
    if not isinstance(receipt_dir, str) or not receipt_dir.strip():
        raise ReceiptError(
            f"review policy receipt_dir must be a non-empty string ({path})"
        )
    relative = Path(receipt_dir)
    if (
        relative.is_absolute()
        or relative.drive
        or relative.root
        or ".." in relative.parts
    ):
        raise ReceiptError(
            f"review policy receipt_dir must stay inside the repository ({path})"
        )
    return payload


def _valid_receipt(stored: object, tree_hash: str) -> bool:
    """A receipt is usable only when every documented field is present and sane.

    Checked field by field rather than by truthiness: `True == 1` in Python, so
    an equality test alone would accept `"schema": true`.
    """
    if not isinstance(stored, dict):
        return False
    if isinstance(stored.get("schema"), bool) or stored.get("schema") != SCHEMA_VERSION:
        return False
    if stored.get("tree_hash") != tree_hash:
        return False
    for key in ("reviewer", "model", "tool_version"):
        value = stored.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    for key in ("changed_files", "changed_lines"):
        value = stored.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return False
    recorded_at = stored.get("recorded_at")
    if not isinstance(recorded_at, str):
        return False
    try:
        datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def receipt_path(policy: dict[str, object], tree_hash: str, root: Path = ROOT) -> Path:
    """One receipt file per tree hash, under the policy's receipt_dir."""
    return root / str(policy["receipt_dir"]) / f"{tree_hash}.json"


def _read_receipt(path: Path, tree_hash: str) -> dict[str, object] | None:
    """Return the stored receipt, None when absent, or raise when unusable.

    The filename is the tree hash, so a file whose contents name a different
    tree is corrupt rather than stale: something wrote the wrong bytes there.
    """
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"receipt unreadable ({path}): {exc}") from exc

    if not _valid_receipt(stored, tree_hash):
        raise ReceiptError(f"receipt is corrupt or names another tree ({path})")
    return stored


def record_receipt(
    reviewer: str, model: str, root: Path = ROOT, git: str = GIT
) -> tuple[Path, dict[str, object]]:
    """Write a receipt for the current change set and return (path, receipt)."""
    policy = load_policy(root)
    change = compute_change_set(root, git)
    tree_hash = str(change["tree_hash"])

    receipt: dict[str, object] = {
        "schema": SCHEMA_VERSION,
        "tree_hash": tree_hash,
        "reviewer": reviewer,
        "model": model,
        "recorded_at": (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "changed_files": change["changed_files"],
        "changed_lines": change["changed_lines"],
        "tool_version": TOOL_VERSION,
    }

    path = receipt_path(policy, tree_hash, root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ReceiptError(f"could not write receipt ({path}): {exc}") from exc
    return path, receipt


def verify(root: Path = ROOT, git: str = GIT) -> dict[str, object]:
    """Decide whether this change set needs a receipt it does not have.

    Returns the decision as a dict so the text and --json paths share one
    answer: exit_code, required, reason, plus the measured values.
    """
    policy = load_policy(root)
    change = compute_change_set(root, git)

    changed_files = change["changed_files"]
    changed_lines = change["changed_lines"]
    tree_hash = str(change["tree_hash"])
    required = (
        changed_files >= policy["min_changed_files"]
        and changed_lines >= policy["min_changed_lines"]
    )

    result: dict[str, object] = {
        "required": required,
        "tree_hash": tree_hash,
        "changed_files": changed_files,
        "changed_lines": changed_lines,
        "receipt": None,
        "exit_code": 0,
        "reason": "",
    }

    measured = (
        f"{changed_files} file(s) / {changed_lines} changed line(s) against "
        f"thresholds {policy['min_changed_files']} file(s) and "
        f"{policy['min_changed_lines']} line(s)"
    )

    if not required:
        result["reason"] = f"review not required: {measured}"
        return result

    path = receipt_path(policy, tree_hash, root)
    stored = _read_receipt(path, tree_hash)
    if stored is None:
        result["exit_code"] = 1
        result["reason"] = (
            f"review required: {measured} - no receipt for tree {tree_hash}; "
            "run `python tools/review_receipt.py record` after /review"
        )
        others = sorted(path.parent.glob("*.json")) if path.parent.is_dir() else []
        if others:
            result["reason"] += (
                f" ({len(others)} receipt(s) exist for other trees; a receipt for a "
                "stale tree does not count)"
            )
        return result

    result["receipt"] = str(path)
    result["reason"] = (
        f"review recorded for tree {tree_hash} by {stored.get('reviewer')} / "
        f"{stored.get('model')} at {stored.get('recorded_at')}"
    )
    return result


def _utc_now() -> str:
    """Current UTC timestamp as ISO-8601, written to every advisory row."""
    return datetime.now(timezone.utc).isoformat()


def _append_advisory_row(root: Path, row: dict[str, object]) -> None:
    """Append one advisory row. A missing or unwritable log must never raise."""
    path = root / ADVISORY_LOG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    except OSError:
        pass


def advisory_verify(root: Path = ROOT, git: str = GIT, verbose: bool = False) -> int:
    """Measure instead of gate: the same decision as verify, never non-zero.

    Mirrors `verify` exactly - same policy, change set, hash and receipt check -
    but records one row to ADVISORY_LOG and always returns 0. An operational
    failure (missing git, unreadable policy or receipt) becomes the row's
    `error` field rather than an exit code, so the measurement never blocks a
    commit.
    """
    row: dict[str, object] = {
        "ts": _utc_now(),
        "required": False,
        "had_receipt": False,
        "changed_files": 0,
        "changed_lines": 0,
        "tree_hash": "",
        "error": None,
    }
    try:
        result = verify(root, git)
        required = bool(result["required"])
        row["required"] = required
        row["changed_files"] = result["changed_files"]
        row["changed_lines"] = result["changed_lines"]
        row["tree_hash"] = str(result["tree_hash"])
        had_receipt = required and result.get("receipt") is not None
        row["had_receipt"] = had_receipt

        if required and not had_receipt:
            print(
                f"[review-advisory] review required: {result['changed_files']} "
                f"file(s) / {result['changed_lines']} line(s), no receipt for "
                f"{result['tree_hash']} - run: python tools/review_receipt.py "
                "record --reviewer NAME --model MODEL",
                file=sys.stderr,
            )
        elif verbose:
            print(result["reason"])
    except ReceiptError as exc:
        row["error"] = str(exc)

    _append_advisory_row(root, row)
    return 0


def stats(root: Path = ROOT) -> dict[str, object]:
    """Summarize ADVISORY_LOG: totals, the firing ratio and the latest firing.

    The ratio is required-without-receipt runs over total runs - the share of
    commits a blocking gate would have refused. A missing log reports zeros.
    """
    path = root / ADVISORY_LOG
    rows: list[dict[str, object]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
    except (OSError, UnicodeDecodeError):
        pass

    total = len(rows)
    required = sum(1 for row in rows if row.get("required") is True)
    required_without_receipt = sum(
        1
        for row in rows
        if row.get("required") is True and row.get("had_receipt") is not True
    )
    ratio = (required_without_receipt / total) if total else 0.0

    most_recent = "never"
    for row in rows:
        if row.get("required") is True and row.get("had_receipt") is not True:
            ts = row.get("ts")
            if isinstance(ts, str) and (most_recent == "never" or ts > most_recent):
                most_recent = ts

    return {
        "total": total,
        "required": required,
        "required_without_receipt": required_without_receipt,
        "ratio": ratio,
        "most_recent": most_recent,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review_receipt.py",
        description=(
            "Record and verify review receipts for the current change set. A receipt "
            "proves a review was recorded for that tree, not that the review was good."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", help="write one receipt for the current tree")
    record.add_argument(
        "--reviewer", required=True, help="declarative reviewer label, e.g. codex"
    )
    record.add_argument(
        "--model", required=True, help="declarative model label, e.g. gpt-6.1-sol"
    )
    record.add_argument(
        "--json",
        action="store_true",
        help="print the receipt as JSON instead of its path",
    )

    check = sub.add_parser(
        "verify", help="decide whether a receipt is required and present"
    )
    check.add_argument("--json", action="store_true", help="print the decision as JSON")
    check.add_argument(
        "--advisory",
        action="store_true",
        help="measure without blocking: always exit 0 and log one row",
    )
    check.add_argument(
        "--verbose",
        action="store_true",
        help="with --advisory, also print when review is not required or present",
    )

    sub.add_parser("stats", help="summarize the advisory measurement log")
    return parser


def main(argv: list[str] | None = None, root: Path = ROOT, git: str = GIT) -> int:
    """CLI entry point. `root` and `git` exist so tests can fence a temp repo."""
    args = build_parser().parse_args(argv)

    try:
        if args.command == "record":
            path, receipt = record_receipt(args.reviewer, args.model, root, git)
            print(json.dumps(receipt, indent=2) if args.json else path)
            return 0
        if args.command == "stats":
            report = stats(root)
            print(f"total runs: {report['total']}")
            print(f"required: {report['required']}")
            print(f"required without receipt: {report['required_without_receipt']}")
            print(f"ratio: {report['ratio']}")
            print(f"most recent required-without-receipt: {report['most_recent']}")
            return 0
        if args.advisory:
            return advisory_verify(root, git, args.verbose)
        result = verify(root, git)
    except ReceiptError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2) if args.json else result["reason"])
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
