#!/usr/bin/env python3
"""
Shared configuration for Solo-Code launcher scripts.

Single source of truth for model definitions, pricing, complexity keywords,
environment variables, .env parsing, and session logging.

Used by:
  - solocode.sh / solocode.ps1  (via CLI: --env-bash, --env-json, --resolve-model, --log-session)
  - tools/cost.py                (import: PRICING, LOG_PATH)
  - tools/setup-global-config.py (import: load_api_key, get_env_vars)

CLI Usage:
  python tools/solocode_config.py --env-json pro          # JSON for PowerShell
  python tools/solocode_config.py --env-bash flash        # KEY=VALUE for bash
  python tools/solocode_config.py --resolve-model "text"  # Print canonical model name
  python tools/solocode_config.py --log-session MODEL DUR MIN EXIT MODE  # Append to usage.log
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
LOG_PATH = ROOT / ".claude" / "usage.log"

# ═══════════════════════════════════════════════════════════════════
# Model definitions — single source of truth
# ═══════════════════════════════════════════════════════════════════

MODELS: dict[str, dict[str, str]] = {
    "pro": {
        "name": "deepseek-v4-pro[1m]",
        "label": "PRO",
    },
}

# DeepSeek pricing per 1M tokens (updated June 2026 — verified current).
# The flash entries stay: deepseek-v4-flash is retired for new sessions, but
# tools/cost.py prices historical usage.log rows that still name it.
PRICING: dict[str, dict[str, float]] = {
    "deepseek-v4-pro[1m]": {"input": 0.435, "cache": 0.003625, "output": 0.87},
    "deepseek-v4-flash[1m]": {"input": 0.14, "cache": 0.0028, "output": 0.28},
    # Legacy names (without [1m] suffix)
    "deepseek-v4-pro": {"input": 0.435, "cache": 0.003625, "output": 0.87},
    "deepseek-v4-flash": {"input": 0.14, "cache": 0.0028, "output": 0.28},
    # Official API id for V4.1 Flash -- what harness_config [models].fallback now
    # names. No price in the official /models response, so this carries over the
    # retired v4-flash row; verify against the DeepSeek pricing page.
    "deepseek-flash[1m]": {"input": 0.14, "cache": 0.0028, "output": 0.28},
    "deepseek-flash": {"input": 0.14, "cache": 0.0028, "output": 0.28},
}

# Token consumption estimates (tokens per minute) — for cost estimation
TOKENS_PER_MINUTE: dict[str, float] = {
    "input_estimate": 3000,
    "output_estimate": 800,
    "cache_hit_ratio": 0.7,
}


# ═══════════════════════════════════════════════════════════════════
# .env parsing
# ═══════════════════════════════════════════════════════════════════


def load_api_key(root: Path | None = None) -> str:
    """Read DEEPSEEK_API_KEY from project .env file.

    Returns the key string, or empty string if not found / not configured.
    """
    env_file = (root or ROOT) / ".env"
    if not env_file.is_file():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("=", 1)
        if len(parts) == 2 and parts[0].strip() == "DEEPSEEK_API_KEY":
            val = parts[1].strip().strip('"').strip("'")
            if val and val != "YOUR_DEEPSEEK_API_KEY_HERE":
                return val
    return ""


def require_api_key(root: Path | None = None) -> str:
    """Load API key or exit with error."""
    key = load_api_key(root)
    if not key:
        env_path = (root or ROOT) / ".env"
        print(f"ERROR: DEEPSEEK_API_KEY not configured in {env_path}", file=sys.stderr)
        sys.exit(1)
    return key


# ═══════════════════════════════════════════════════════════════════
# Model resolution
# ═══════════════════════════════════════════════════════════════════


def resolve_model(
    prompt_text: str = "", model_flag: str = "pro"
) -> tuple[str, str, bool]:
    """Resolve the canonical model name.

    Returns (model_name, model_label, auto_detected).

    Every flag resolves to pro: deepseek-v4-flash was retired 2026-07-25 for
    being unreliable, so there is no second tier left to route to. `auto` no
    longer inspects the prompt (there is nothing to choose between) and
    `flash` is kept only as an accepted, deprecated alias so already-deployed
    callers don't break.
    """
    if model_flag not in ("pro", "flash", "auto"):
        print(f"ERROR: Unknown model '{model_flag}'. Use: pro, auto", file=sys.stderr)
        sys.exit(1)

    if model_flag == "flash":
        print(
            "[solocode_config] 'flash' is deprecated and ignored "
            "(deepseek-v4-flash was retired); using pro.",
            file=sys.stderr,
        )

    model = MODELS["pro"]
    return model["name"], model["label"], model_flag == "auto"


# ═══════════════════════════════════════════════════════════════════
# Environment variables
# ═══════════════════════════════════════════════════════════════════


def get_env_vars(api_key: str, model_name: str) -> dict[str, str]:
    """Build the environment variable dict for launching Claude Code."""
    return {
        "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
        "ANTHROPIC_API_KEY": api_key,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        "ANTHROPIC_MODEL": model_name,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro[1m]",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-pro[1m]",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-pro[1m]",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_EFFORT_LEVEL": "xhigh",  # xhigh = recommended for coding/agentic (Opus 4.7+); max may overthink
        "CLAUDE_CODE_FALLBACK_MODEL": "deepseek-v4-pro[1m]",  # auto-fallback if the primary is overloaded
    }


# ═══════════════════════════════════════════════════════════════════
# Session logging
# ═══════════════════════════════════════════════════════════════════


def log_session(
    model_name: str,
    duration_min: float,
    exit_code: int,
    mode: str = "manual",
    log_path: Path | None = None,
) -> None:
    """Append a session entry to the usage log."""
    path = log_path or LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "model": model_name,
        "mode": mode,
        "duration_min": duration_min,
        "exit_code": exit_code,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════════════════
# CLI interface
# ═══════════════════════════════════════════════════════════════════


def _cmd_env_json(model_flag: str, prompt_text: str = "") -> int:
    """Print env vars as JSON (for PowerShell)."""
    api_key = require_api_key()
    model_name, model_label, auto = resolve_model(
        prompt_text=prompt_text, model_flag=model_flag
    )
    env = get_env_vars(api_key, model_name)
    env["SOLOCODE_MODEL_LABEL"] = model_label
    env["SOLOCODE_AUTO_DETECTED"] = "true" if auto else "false"
    print(json.dumps(env, indent=2))
    return 0


def _cmd_env_bash(model_flag: str, prompt_text: str = "") -> int:
    """Print env vars as KEY=VALUE lines (for bash source)."""
    api_key = require_api_key()
    model_name, model_label, auto = resolve_model(
        prompt_text=prompt_text, model_flag=model_flag
    )
    env = get_env_vars(api_key, model_name)
    env["SOLOCODE_MODEL_LABEL"] = model_label
    env["SOLOCODE_AUTO_DETECTED"] = "true" if auto else "false"
    for key, value in env.items():
        # Values are URL-safe (alphanumeric, colons, slashes, dots) — no quoting needed
        print(f"{key}={value}")
    return 0


def _cmd_resolve_model(prompt_text: str, model_flag: str) -> int:
    """Print the canonical model name."""
    model_name, model_label, auto = resolve_model(
        prompt_text=prompt_text, model_flag=model_flag
    )
    print(model_name)
    return 0


def _cmd_log_session(args: argparse.Namespace) -> int:
    """Append a session entry to usage.log."""
    log_session(
        model_name=args.model,
        duration_min=args.duration_min,
        exit_code=args.exit_code,
        mode=args.mode,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Solo-Code shared configuration",
        prog="solocode_config.py",
    )
    sub = parser.add_subparsers(dest="command", help="Subcommand")

    # --env-json <pro|flash> [--prompt <text>]
    p_json = sub.add_parser("env-json", help="Print env vars as JSON")
    p_json.add_argument(
        "model",
        choices=["pro", "flash", "auto"],
        help="Model tier (all resolve to pro; flash is a deprecated alias)",
    )
    p_json.add_argument("--prompt", default="", help="Prompt text for auto-detection")

    # --env-bash <pro|flash> [--prompt <text>]
    p_bash = sub.add_parser("env-bash", help="Print env vars as KEY=VALUE")
    p_bash.add_argument(
        "model",
        choices=["pro", "flash", "auto"],
        help="Model tier (all resolve to pro; flash is a deprecated alias)",
    )
    p_bash.add_argument("--prompt", default="", help="Prompt text for auto-detection")

    # --resolve-model <prompt_text>
    p_resolve = sub.add_parser("resolve-model", help="Resolve model from prompt text")
    p_resolve.add_argument(
        "prompt", nargs="*", default=[], help="Prompt text to analyze"
    )
    p_resolve.add_argument(
        "--model",
        default="auto",
        choices=["pro", "flash", "auto"],
        help="Model flag (default: auto; all resolve to pro)",
    )

    # --log-session
    p_log = sub.add_parser("log-session", help="Append session to usage.log")
    p_log.add_argument("model", help="Canonical model name")
    p_log.add_argument("duration_min", type=float, help="Session duration in minutes")
    p_log.add_argument("exit_code", type=int, help="Claude exit code")
    p_log.add_argument("mode", choices=["manual", "auto"], help="Detection mode")

    args = parser.parse_args()

    if args.command == "env-json":
        return _cmd_env_json(args.model, args.prompt)
    elif args.command == "env-bash":
        return _cmd_env_bash(args.model, args.prompt)
    elif args.command == "resolve-model":
        prompt = " ".join(args.prompt) if args.prompt else ""
        return _cmd_resolve_model(prompt, args.model)
    elif args.command == "log-session":
        return _cmd_log_session(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
