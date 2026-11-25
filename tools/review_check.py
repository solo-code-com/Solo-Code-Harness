#!/usr/bin/env python3
"""
Review Check - evidence gate for review reports
===============================================
Verify that every high and medium finding in a review report carries a `ran`
proof before the review can be accepted.

Usage:
    python tools/review_check.py REPORT [--json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

_HEADER_RE = re.compile(
    r"^\s*(\d+)[.:]\s*(high|medium|low)\s*\|\s*(code|feature)\s*\|\s*(.+?)\s*$"
)
_PROOF_ARG_RE = re.compile(r"^(ran|read)\s+(.+)$")


class ReportReadError(OSError):
    """Raised when a review report file is missing or cannot be read."""


def _parse_proof_line(rest: str) -> tuple[str | None, str | None, str | None]:
    """Parse the text after `proof:` into (proof_kind, proof, error)."""
    if rest == "inferred":
        return "inferred", "", None
    match = _PROOF_ARG_RE.match(rest)
    if match:
        return match.group(1), match.group(2).strip(), None
    return None, None, f"invalid proof {rest!r}"


def check_text(text: str) -> dict[str, object]:
    """Parse and evaluate a review report string.

    Returns a dictionary containing `ok`, `findings`, `reasons`, and `counts`.
    """
    findings: list[dict[str, object]] = []
    errors: list[str | None] = []
    has_no_findings = False

    current: dict[str, object] | None = None
    current_error: str | None = None
    saw_proof = False

    def _finalize_current() -> None:
        if current is None:
            return
        err = current_error if saw_proof else "missing proof"
        findings.append(current)
        errors.append(err)

    for line in text.splitlines():
        header = _HEADER_RE.match(line)
        if header:
            _finalize_current()
            current = {
                "number": int(header.group(1)),
                "severity": header.group(2),
                "kind": header.group(3),
                "text": header.group(4).strip(),
                "proof_kind": None,
                "proof": None,
            }
            current_error = None
            saw_proof = False
            continue

        stripped = line.strip()
        if current is None:
            if stripped in ("No findings", "No findings."):
                has_no_findings = True
            continue

        if stripped.startswith("proof:"):
            if saw_proof:
                current["proof_kind"] = None
                current["proof"] = None
                current_error = "multiple proof lines"
            else:
                saw_proof = True
                rest = stripped[len("proof:") :].strip()
                proof_kind, proof, proof_err = _parse_proof_line(rest)
                current["proof_kind"] = proof_kind
                current["proof"] = proof
                current_error = proof_err

    _finalize_current()

    reasons: list[str] = []
    if not findings and not has_no_findings:
        reasons.append(
            "unparseable report: no finding headers and no 'No findings.' line"
        )
    else:
        for finding, err in zip(findings, errors, strict=True):
            number = finding["number"]
            severity = finding["severity"]
            proof_kind = finding["proof_kind"]
            if err is not None:
                reasons.append(f"finding {number} ({severity}): {err}")
            elif severity in ("high", "medium") and proof_kind != "ran":
                reasons.append(
                    f"finding {number} ({severity}): proof is {proof_kind}, expected ran"
                )

    counts = {
        "total": len(findings),
        "verified": sum(1 for item in findings if item["proof_kind"] == "ran"),
        "read": sum(1 for item in findings if item["proof_kind"] == "read"),
        "inferred": sum(1 for item in findings if item["proof_kind"] == "inferred"),
    }

    return {
        "ok": len(reasons) == 0,
        "findings": findings,
        "reasons": reasons,
        "counts": counts,
    }


def check_file(path: Path) -> dict[str, object]:
    """Read `path`, check its review evidence, and include its byte sha256."""
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReportReadError(f"cannot read report ({path}): {exc}") from exc

    result = check_text(text)
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review_check.py",
        description="Verify that review report findings carry run-backed evidence.",
    )
    parser.add_argument("report", type=Path, help="path to the review report file")
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the check result as JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: exit 0 accepted, 1 rejected, 2 missing or unreadable."""
    args = build_parser().parse_args(argv)
    try:
        result = check_file(args.report)
    except ReportReadError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload = {
            "ok": result["ok"],
            "findings": result["findings"],
            "reasons": result["reasons"],
        }
        print(json.dumps(payload, indent=2))
        return 0 if result["ok"] else 1

    for reason in result["reasons"]:
        print(reason)

    counts = result["counts"]
    verdict = "accepted" if result["ok"] else "rejected"
    print(
        f"{verdict}: verified={counts['verified']}, "
        f"read={counts['read']}, inferred={counts['inferred']}"
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
