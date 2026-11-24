"""Resolve the project data-tier marker and gate provider reachability.

Why: a provider that may see client code must not be reachable from a client
project by accident. The marker ``.harness-tier`` holds one of ``client``,
``personal`` or ``public``. A missing, unreadable or invalid marker resolves to
``client`` -- the strictest tier -- so a new project fails closed instead of
inheriting permissive defaults.

Two checks exist because providers reach a project in two ways: through an
environment variable a wrapper passes down, and through a provider block a
project declares in its own OpenCode config. The env check is opt-in per caller
(``var_names``) so one stale variable cannot break an unrelated delegation.

Usage:
    from tools.tier_gate import resolve_tier, refusal_for_project
    reason = refusal_for_project(Path("."))
"""

from __future__ import annotations

import os
from pathlib import Path

MARKER = ".harness-tier"
TIERS = ("client", "personal", "public")
STRICTEST = "client"

#: Hosts that may not receive client data. One list answers both "what is
#: banned" and "who enforces it" -- tools/provider_audit.py imports it.
BANNED_HOSTS = frozenset(
    {
        "api.freemodel.dev",
        "cc.freemodel.dev",
        "api-cc.freemodel.dev",
        "cc-t2.freemodel.dev",
        "work.freemodel.dev",
        "api-t2-sg.freemodel.dev",
    }
)

#: Config files a project may use to declare its own providers.
#: The OpenCode CLI also reads a user-level config, so a banned provider there is
#: reachable from every project. Kept as a module constant so tests can point it
#: at a temporary home instead of the real one.
HOME_CONFIG_FILES = (
    Path.home() / ".config" / "opencode" / "opencode.json",
    Path.home() / ".config" / "opencode" / "opencode.jsonc",
)

PROVIDER_CONFIG_FILES = (
    Path(".opencode") / "opencode.json",
    Path(".opencode") / "opencode.jsonc",
    Path("opencode.json"),
)

#: Environment variables a delegation may pass to a provider.
PROVIDER_URL_VARS = (
    "OPENAI_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "DEEPSEEK_BASE_URL",
    "COMMANDCODE_BASE_URL",
)


def resolve_tier(start: str | os.PathLike[str] | None = None) -> str:
    """Return the tier for ``start``, walking up to the filesystem root.

    Every uncertain case returns ``client``: a missing marker, an unreadable
    file, or a value outside ``TIERS``. This answer gates a spawn, so guessing
    "permissive" there would defeat the gate.
    """
    try:
        path = Path(start or ".").resolve()
    except OSError:
        return STRICTEST
    for candidate in (path, *path.parents):
        marker = candidate / MARKER
        try:
            if not marker.is_file():
                continue
            value = marker.read_text(encoding="utf-8", errors="replace").strip().lower()
        except OSError:
            return STRICTEST
        return value if value in TIERS else STRICTEST
    return STRICTEST


def banned_host(text: str | None) -> str | None:
    """Return the banned host named in ``text``, or None."""
    if not text:
        return None
    for host in sorted(BANNED_HOSTS):
        if host in text:
            return host
    return None


def refusal_for_project(
    start: str | os.PathLike[str] | None = None,
    var_names: tuple[str, ...] = (),
) -> str | None:
    """Return a refusal reason for this project, or None when it may run.

    ``client`` is the only tier that constrains the provider: an anonymous
    gateway may not see client data even when the operator points at it.
    ``personal`` and ``public`` always return None.
    """
    tier = resolve_tier(start)
    if tier != STRICTEST:
        return None

    root = Path(start or ".").resolve()

    for relative in PROVIDER_CONFIG_FILES:
        candidate = root / relative
        try:
            if not candidate.is_file():
                continue
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        host = banned_host(text)
        if host:
            return (
                f"tier={tier} refuses provider {host} declared in {candidate}. "
                "Remove the banned provider from this project's config."
            )

    for candidate in HOME_CONFIG_FILES:
        try:
            if not candidate.is_file():
                continue
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        host = banned_host(text)
        if host:
            return (
                f"tier={tier} refuses provider {host} declared in {candidate}. "
                "Remove it from the user-level OpenCode config."
            )

    for name in var_names:
        host = banned_host(os.environ.get(name))
        if host:
            return (
                f"tier={tier} refuses provider {host} from {name}. "
                "Point it at a named provider, or unset it for this run."
            )
    return None
