#!/usr/bin/env python3
"""
A skill written by a model and loaded automatically is advice with the authority of
a rule. This module keeps the generation but removes the authority: a proposal is
stored under .solocode/skill-proposals/, never loaded, and must pass its own canary
command before a person is even asked to read it. Promotion is manual by design --
the point is that a human moves the file, not that the tool refuses to.

Layout:
    .solocode/skill-proposals/<slug>/
        SKILL.md       Proposed skill instructions (<= 64 KB, non-empty)
        proposal.json  Manifest with metadata, canary argv, state, and last_run
        ...            Optional canary script or fixtures referenced by canary

Usage:
    python tools/skill_proposal.py propose --slug my-skill --skill path/to/SKILL.md \\
        --canary "python -c 'import sys; sys.exit(0)'" --rationale "why"
    python tools/skill_proposal.py list [--json]
    python tools/skill_proposal.py check <slug> [--json]
    python tools/skill_proposal.py promote <slug> [--json]
    python tools/skill_proposal.py expired [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess  # noqa: S404 - runs operator-supplied canary argv without shell
import sys
import unicodedata
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parent.parent
PROPOSAL_DIR = Path(".solocode") / "skill-proposals"
SKILL_FILENAME = "SKILL.md"
MANIFEST_FILENAME = "proposal.json"
MAX_SKILL_BYTES = 64 * 1024
CANARY_TIMEOUT_SECONDS = 120
OUTPUT_TAIL_CHARS = 2000
RETENTION_DAYS = 90
RETENTION_DELTA = timedelta(days=RETENTION_DAYS)

VALID_STATES = frozenset({"proposed", "rejected", "promoted"})


class ProposalError(Exception):
    """Base error for skill proposal operations."""


class ProposalMissingError(ProposalError, FileNotFoundError):
    """Raised when a target proposal or input skill file does not exist."""


class ProposalValidationError(ProposalError, ValueError):
    """Raised when a slug, skill file, or canary command is invalid."""


class ProposalExistsError(ProposalError, FileExistsError):
    """Raised when proposing a slug that already exists."""


class PromotionRefusedError(ProposalError, ValueError):
    """Raised when promotion preconditions are not satisfied."""


def _resolve_now(now: datetime | None) -> datetime:
    """Return a timezone-aware UTC datetime for `now` (or current UTC time)."""
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp string into an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_slug(slug: str) -> str:
    """Validate that `slug` is a safe single directory name."""
    if not isinstance(slug, str) or not slug or not slug.strip():
        raise ProposalValidationError("slug must be a non-empty string")
    if (
        slug in {".", ".."}
        or ".." in slug
        or "/" in slug
        or "\\" in slug
        or "\0" in slug
    ):
        raise ProposalValidationError(f"invalid slug: {slug!r}")
    if len(slug) > 40:
        raise ProposalValidationError(
            f"slug exceeds 40 characters ({len(slug)}): {slug!r}"
        )
    return slug


def new_slug(text: str) -> str:
    """Convert arbitrary text into an ASCII kebab-case slug of at most 40 chars."""
    if not isinstance(text, str):
        raise ProposalValidationError("text must be a string")
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    collapsed = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    trimmed = collapsed[:40].rstrip("-")
    if not trimmed:
        raise ProposalValidationError(f"cannot derive a slug from {text!r}")
    return trimmed


def _proposals_root(root: Path | str) -> Path:
    """Return the proposal storage directory under `root`."""
    return Path(root) / PROPOSAL_DIR


def _proposal_dir(root: Path | str, slug: str) -> Path:
    """Return the directory for `slug` under `root`, guarding against traversal."""
    clean_slug = _validate_slug(slug)
    base = _proposals_root(root)
    target = base / clean_slug
    if target.resolve().parent != base.resolve():
        raise ProposalValidationError(f"slug escapes proposal directory: {slug!r}")
    return target


def _manifest_path(proposal_dir: Path) -> Path:
    return proposal_dir / MANIFEST_FILENAME


def _write_manifest(proposal_dir: Path, manifest: dict[str, Any]) -> None:
    _manifest_path(proposal_dir).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def _read_manifest(root: Path | str, slug: str) -> tuple[Path, dict[str, Any]]:
    proposal_dir = _proposal_dir(root, slug)
    manifest_file = _manifest_path(proposal_dir)
    if not manifest_file.is_file():
        raise ProposalMissingError(f"proposal not found: {slug!r}")
    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProposalValidationError(f"corrupt manifest for {slug!r}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProposalValidationError(f"manifest for {slug!r} must be a JSON object")
    return proposal_dir, data


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _combine_output(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    out = _coerce_text(stdout)
    err = _coerce_text(stderr)
    if err:
        if out and not out.endswith("\n"):
            out += "\n"
        out += err
    return out[-OUTPUT_TAIL_CHARS:]


def run_canary(proposal_dir: Path | str, canary: Sequence[str]) -> tuple[bool, str]:
    """Run `canary` argv inside `proposal_dir` with a 120s timeout."""
    argv = [str(part) for part in canary]
    if not argv or any(not part for part in argv):
        return False, "canary command must be a non-empty list of argv strings"

    try:
        proc = subprocess.run(  # noqa: S603 - explicit argv list, shell=False
            argv,
            cwd=str(proposal_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CANARY_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        tail = _combine_output(exc.stdout, exc.stderr)
        timeout_note = f"[timeout after {CANARY_TIMEOUT_SECONDS}s]"
        combined = f"{tail}\n{timeout_note}" if tail else timeout_note
        return False, combined[-OUTPUT_TAIL_CHARS:]
    except OSError as exc:
        return False, f"canary execution error: {exc}"[-OUTPUT_TAIL_CHARS:]

    return proc.returncode == 0, _combine_output(proc.stdout, proc.stderr)


def _validate_skill_text(skill_text: str) -> None:
    if not isinstance(skill_text, str):
        raise ProposalValidationError("skill_text must be a string")
    if not skill_text.strip():
        raise ProposalValidationError("skill file is empty")
    encoded_size = len(skill_text.encode("utf-8"))
    if encoded_size > MAX_SKILL_BYTES:
        raise ProposalValidationError(
            f"skill file exceeds 64 KB limit ({encoded_size} > {MAX_SKILL_BYTES} bytes)"
        )


def _validate_canary(canary: Sequence[str]) -> list[str]:
    if isinstance(canary, str | bytes) or not isinstance(canary, Sequence):
        raise ProposalValidationError("canary must be a list of argv strings")
    argv = list(canary)
    if not argv or any(not isinstance(part, str) or not part for part in argv):
        raise ProposalValidationError(
            "canary must be a non-empty list of non-empty strings"
        )
    return argv


def propose(
    root: Path | str,
    slug: str,
    skill_text: str,
    canary: Sequence[str],
    rationale: str,
    evidence: str | Sequence[str] = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a skill proposal, run its canary, and store the manifest on disk."""
    clean_slug = _validate_slug(slug)
    _validate_skill_text(skill_text)
    canary_argv = _validate_canary(canary)

    proposal_dir = _proposal_dir(root, clean_slug)
    if proposal_dir.exists():
        raise ProposalExistsError(f"proposal already exists: {clean_slug!r}")

    created_dt = _resolve_now(now)
    expires_dt = created_dt + RETENTION_DELTA

    proposal_dir.mkdir(parents=True, exist_ok=False)
    (proposal_dir / SKILL_FILENAME).write_text(skill_text, encoding="utf-8")

    manifest: dict[str, Any] = {
        "slug": clean_slug,
        "created": created_dt.isoformat(),
        "expires": expires_dt.isoformat(),
        "canary": canary_argv,
        "rationale": rationale,
        "evidence": evidence if isinstance(evidence, str) else list(evidence),
        "state": "rejected",
        "promoted_at": None,
        "last_run": {
            "at": created_dt.isoformat(),
            "ok": False,
            "output": "",
        },
    }
    _write_manifest(proposal_dir, manifest)

    ok, output = run_canary(proposal_dir, canary_argv)
    run_dt = _resolve_now(now)
    manifest["state"] = "proposed" if ok else "rejected"
    manifest["last_run"] = {
        "at": run_dt.isoformat(),
        "ok": ok,
        "output": output,
    }
    _write_manifest(proposal_dir, manifest)
    return manifest


def check(root: Path | str, slug: str, now: datetime | None = None) -> dict[str, Any]:
    """Re-run a proposal's canary and update `last_run` and `state`."""
    proposal_dir, manifest = _read_manifest(root, slug)
    canary_argv = _validate_canary(manifest.get("canary", []))

    ok, output = run_canary(proposal_dir, canary_argv)
    run_dt = _resolve_now(now)

    current_state = manifest.get("state")
    if current_state == "promoted" and ok:
        new_state = "promoted"
    else:
        new_state = "proposed" if ok else "rejected"

    manifest["state"] = new_state
    manifest["last_run"] = {
        "at": run_dt.isoformat(),
        "ok": ok,
        "output": output,
    }
    _write_manifest(proposal_dir, manifest)
    return manifest


def list_proposals(root: Path | str) -> list[dict[str, Any]]:
    """Return all proposal manifests under `root`, newest first."""
    base = _proposals_root(root)
    if not base.is_dir():
        return []

    manifests: list[dict[str, Any]] = []
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        manifest_file = _manifest_path(entry)
        if not manifest_file.is_file():
            continue
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProposalValidationError(
                f"corrupt manifest in {entry.name!r}: {exc}"
            ) from exc
        if isinstance(data, dict):
            manifests.append(data)

    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    manifests.sort(
        key=lambda item: (
            _parse_iso(item.get("created")) or epoch,
            str(item.get("slug", "")),
        ),
        reverse=True,
    )
    return manifests


def expired(root: Path | str, now: datetime | None = None) -> list[str]:
    """Return slugs whose `expires` passed or whose `promoted_at` is > 90 days old."""
    now_dt = _resolve_now(now)
    stale: list[str] = []
    for manifest in list_proposals(root):
        slug = manifest.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        expires_dt = _parse_iso(manifest.get("expires"))
        promoted_dt = _parse_iso(manifest.get("promoted_at"))
        is_expired = (expires_dt is not None and expires_dt < now_dt) or (
            promoted_dt is not None and (now_dt - promoted_dt) > RETENTION_DELTA
        )
        if is_expired:
            stale.append(slug)
    return stale


def _promote_with_manifest(
    root: Path | str, slug: str, now: datetime | None = None
) -> tuple[Path, dict[str, Any]]:
    proposal_dir, manifest = _read_manifest(root, slug)
    state = manifest.get("state")
    last_run = manifest.get("last_run")
    last_ok = isinstance(last_run, dict) and last_run.get("ok") is True

    if state != "proposed" or not last_ok:
        raise PromotionRefusedError(
            f"cannot promote {slug!r}: state={state!r}, last_run.ok={last_ok!r}"
        )

    manifest["state"] = "promoted"
    manifest["promoted_at"] = _resolve_now(now).isoformat()
    _write_manifest(proposal_dir, manifest)

    destination = Path(root) / ".kilo" / "skill" / manifest["slug"]
    return destination, manifest


def promote(root: Path | str, slug: str, now: datetime | None = None) -> Path:
    """Mark a passing proposal promoted and return `.kilo/skill/<slug>` without creating it."""
    destination, _ = _promote_with_manifest(root, slug, now=now)
    return destination


def _parse_canary_string(raw: str) -> list[str]:
    """Split a CLI `--canary` string into an argv list."""
    if not raw or not raw.strip():
        raise ProposalValidationError("--canary must not be empty")
    try:
        if os.name == "nt":
            tokens = shlex.split(raw, posix=False)
            argv = [
                tok[1:-1]
                if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ('"', "'")
                else tok
                for tok in tokens
            ]
        else:
            argv = shlex.split(raw, posix=True)
    except ValueError as exc:
        raise ProposalValidationError(f"invalid --canary command: {exc}") from exc
    if not argv:
        raise ProposalValidationError("--canary must not be empty")
    return argv


class _CliParser(argparse.ArgumentParser):
    """ArgumentParser that exits with status 1 on usage errors."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _add_common_flags(parser: argparse.ArgumentParser, *, is_root: bool) -> None:
    root_default: Any = ROOT if is_root else argparse.SUPPRESS
    json_default: Any = False if is_root else argparse.SUPPRESS
    parser.add_argument(
        "--root",
        default=root_default,
        help="repository root holding .solocode/skill-proposals (default: repo root)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=json_default,
        help="emit machine-readable JSON output",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = _CliParser(
        prog="skill_proposal.py",
        description=(
            "Stage, canary-check, list, expire, and promote skill proposals under "
            ".solocode/skill-proposals/."
        ),
    )
    _add_common_flags(parser, is_root=True)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_CliParser)

    propose_p = sub.add_parser("propose", help="stage a new skill proposal")
    _add_common_flags(propose_p, is_root=False)
    propose_p.add_argument(
        "--slug",
        default=None,
        help="proposal slug (defaults to kebab-case of --skill filename)",
    )
    propose_p.add_argument(
        "--skill",
        required=True,
        metavar="FILE",
        help="path to proposed SKILL.md file",
    )
    propose_p.add_argument(
        "--canary",
        required=True,
        metavar="CMD ARGS",
        help="canary command string run with proposal directory as cwd",
    )
    propose_p.add_argument(
        "--rationale",
        required=True,
        metavar="TEXT",
        help="why this skill is proposed",
    )
    propose_p.add_argument(
        "--evidence",
        action="append",
        default=[],
        metavar="TEXT",
        help="supporting evidence (repeatable)",
    )

    list_p = sub.add_parser("list", help="list proposals, newest first")
    _add_common_flags(list_p, is_root=False)

    check_p = sub.add_parser("check", help="re-run a proposal's canary")
    _add_common_flags(check_p, is_root=False)
    check_p.add_argument("slug", metavar="SLUG", help="proposal slug")

    promote_p = sub.add_parser(
        "promote", help="mark a passing proposal promoted and print destination"
    )
    _add_common_flags(promote_p, is_root=False)
    promote_p.add_argument("slug", metavar="SLUG", help="proposal slug")

    expired_p = sub.add_parser("expired", help="list expired proposal slugs")
    _add_common_flags(expired_p, is_root=False)

    return parser


def _run_propose_cli(args: argparse.Namespace, root: Path) -> int:
    skill_path = Path(args.skill)
    if not skill_path.is_file():
        raise ProposalMissingError(f"skill file not found: {skill_path}")
    try:
        skill_text = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProposalValidationError(
            f"cannot read skill file {skill_path}: {exc}"
        ) from exc

    slug = args.slug if args.slug is not None else new_slug(skill_path.stem)
    canary = _parse_canary_string(args.canary)
    evidence = "\n".join(args.evidence)
    manifest = propose(
        root=root,
        slug=slug,
        skill_text=skill_text,
        canary=canary,
        rationale=args.rationale,
        evidence=evidence,
    )
    if args.json:
        print(json.dumps(manifest, indent=2))
    else:
        print(f"{manifest['slug']}: {manifest['state']}")
    return 0 if manifest["state"] == "proposed" else 2


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    root = Path(args.root)

    try:
        if args.command == "propose":
            return _run_propose_cli(args, root)

        if args.command == "list":
            items = list_proposals(root)
            if args.json:
                print(json.dumps(items, indent=2))
            elif not items:
                print("(no proposals)")
            else:
                for item in items:
                    print(
                        f"{item.get('slug')}\t{item.get('state')}\t{item.get('created')}"
                    )
            return 0

        if args.command == "check":
            manifest = check(root, args.slug)
            if args.json:
                print(json.dumps(manifest, indent=2))
            else:
                print(f"{manifest['slug']}: {manifest['state']}")
            return 0 if manifest["state"] in {"proposed", "promoted"} else 2

        if args.command == "promote":
            dest, manifest = _promote_with_manifest(root, args.slug)
            if args.json:
                print(
                    json.dumps(
                        {
                            "slug": manifest["slug"],
                            "destination": str(dest),
                            "promoted_at": manifest["promoted_at"],
                        },
                        indent=2,
                    )
                )
            else:
                print(dest)
            return 0

        if args.command == "expired":
            stale = expired(root)
            if args.json:
                print(json.dumps(stale, indent=2))
            else:
                for slug in stale:
                    print(slug)
            return 0
    except (ProposalMissingError, ProposalValidationError, ProposalExistsError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    except PromotionRefusedError as exc:
        print(f"[REFUSED] {exc}", file=sys.stderr)
        return 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
