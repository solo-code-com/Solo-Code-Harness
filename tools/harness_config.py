#!/usr/bin/env python3
"""
Hierarchical configuration loader for Solo-Code harness.

Ports the layered config pattern from CocoIndex Code (src/cocoindex_code/settings.py).
Uses stdlib-only: tomllib (3.11+) with JSON fallback for 3.10.

Config resolution order (highest priority first):
  1. Per-project config:  .solocode/settings.toml  (or .json)
  2. Global user config:   ~/.solocode/settings.toml  (or .json)
  3. Built-in defaults (hardcoded in DEFAULT_CONFIG)

Environment variable overrides:
  SOLOCODE_HARNESS_DIR  — override global config directory
  SOLOCODE_CONFIG_PROFILE — select named profile (e.g. "strict", "minimal")

Usage:
    from tools.harness_config import load_harness_config, HarnessConfig

    config = load_harness_config()
    print(config.get("gates.security", True))
    print(config.get("hooks.profile", "standard"))
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# tomllib is stdlib in Python 3.11+
if sys.version_info >= (3, 11):
    import tomllib as _toml
else:
    _toml = None  # type: ignore[assignment]

# ═══════════════════════════════════════════════════════════════════
# Built-in defaults
# ═══════════════════════════════════════════════════════════════════

DEFAULT_CONFIG: dict[str, Any] = {
    "hooks": {
        "profile": "standard",  # minimal | standard | strict
        "auto_index_on_startup": False,
    },
    "gates": {
        "security": True,
        "lint": True,
        "schema": True,
        "garden": True,
        "test": True,
        "guard": True,
    },
    "skills": {
        "auto_load": [
            "code-review-expert",
            "file-editor-pro",
            "systematic-debugging",
            "brainstorming",
            "testing-patterns",
            "api-patterns",
        ],
        "disabled": [],
    },
    "models": {
        "default": "deepseek-v4-pro[1m]",
        "fallback": "deepseek-flash[1m]",
    },
    "verify_on_commit": True,
    "auto_verify_before_push": True,
}

# Directories excluded from project-root discovery
_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".mypy_cache",
    }
)


# ═══════════════════════════════════════════════════════════════════
# Config file loading
# ═══════════════════════════════════════════════════════════════════


def _load_file(path: Path) -> dict[str, Any] | None:
    """Load a config file, returning None if missing or unparseable."""
    if not path.is_file():
        return None

    content = path.read_text(encoding="utf-8")
    ext = path.suffix.lower()

    if ext == ".toml":
        if _toml is not None:
            return _toml.loads(content)
        else:
            raise RuntimeError(
                "tomllib is not available on Python 3.10. "
                "Use JSON config instead or upgrade to Python 3.11+."
            )
    elif ext == ".json":
        return json.loads(content)
    elif ext == ".jsonc":
        # Strip // comments and trailing commas
        cleaned = _strip_jsonc(content)
        return json.loads(cleaned)
    else:
        return None


def _strip_jsonc(content: str) -> str:
    """Remove // line comments and trailing commas from JSONC."""
    lines = []
    for line in content.splitlines():
        # Remove // comments (but not inside strings — simple heuristic)
        in_string = False
        cleaned = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == '"' and (i == 0 or line[i - 1] != "\\"):
                in_string = not in_string
            if not in_string and ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                break
            cleaned.append(ch)
            i += 1
        result = "".join(cleaned).rstrip()
        # Remove trailing commas before ] or }
        if result.rstrip().endswith(",") and any(
            result.rstrip().endswith(c + ",") for c in "]}"
        ):
            result = result.rstrip()[:-1]
        if result:
            lines.append(result)
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Path discovery
# ═══════════════════════════════════════════════════════════════════


def _find_project_root(start: Path | None = None) -> Path:
    """Walk up from start (or CWD) to find the project root.

    Looks for these markers in order:
      1. Directory containing AGENTS.md + .kilo/
      2. Directory containing AGENTS.md
      3. Directory containing .git/
      4. Falls back to CWD
    """
    current = (start or Path.cwd()).resolve()
    while True:
        # Marker 1: AGENTS.md + .kilo/ (strongest signal)
        if (current / "AGENTS.md").is_file() and (current / ".kilo").is_dir():
            return current
        # Marker 2: AGENTS.md alone
        if (current / "AGENTS.md").is_file():
            return current
        # Marker 3: .git/ directory
        if (current / ".git").is_dir():
            return current

        parent = current.parent
        if parent == current:
            break  # Filesystem root
        current = parent

    # Fallback to CWD
    return Path.cwd()


def _get_global_config_dir() -> Path:
    """Return the global Solo-Code config directory.

    Default: ~/.solocode/
    Override: SOLOCODE_HARNESS_DIR env var
    """
    env_dir = os.environ.get("SOLOCODE_HARNESS_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return Path.home() / ".solocode"


def _find_config_file(directory: Path) -> Path | None:
    """Find a config file in directory, preferring .toml over .json."""
    for name in ("settings.toml", "settings.json", "settings.jsonc"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


# ═══════════════════════════════════════════════════════════════════
# Merging strategy
# ═══════════════════════════════════════════════════════════════════


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base. override wins on conflict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════


class HarnessConfig:
    """Layered configuration for Solo-Code harness.

    Attributes:
        data: Merged configuration dict.
        project_root: Detected project root path.
        global_dir: Global config directory path.
        sources: List of (source_name, dict) in merge order for debugging.
    """

    def __init__(
        self,
        data: dict[str, Any],
        project_root: Path,
        global_dir: Path,
        sources: list[tuple[str, dict[str, Any]]],
    ):
        self.data = data
        self.project_root = project_root
        self.global_dir = global_dir
        self._sources = sources

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Get a config value by dotted path.

        Example: config.get("gates.security", True)
        """
        keys = dotted_key.split(".")
        node: Any = self.data
        for key in keys:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return default
        return node

    def is_gate_enabled(self, gate: str) -> bool:
        """Check if a verification gate is enabled."""
        return bool(self.get(f"gates.{gate}", True))

    def is_skill_disabled(self, skill_name: str) -> bool:
        """Check if a skill is in the disabled list."""
        disabled: list[str] = self.get("skills.disabled", [])
        return skill_name in disabled

    def get_hooks_profile(self) -> str:
        """Get the active hooks profile name."""
        return str(self.get("hooks.profile", "standard"))

    def debug_print_sources(self) -> None:
        """Print config layering for debugging."""
        print("Config layers (highest priority first):")
        for i, (name, _data) in enumerate(reversed(self._sources)):
            print(f"  {i + 1}. {name}")
        print(f"\nProject root: {self.project_root}")
        print(f"Global dir:   {self.global_dir}")


def load_harness_config(
    project_root: Path | None = None,
    global_dir: Path | None = None,
) -> HarnessConfig:
    """Load the full layered harness configuration.

    Resolution order (highest priority first):
      1. Per-project `.solocode/settings.toml|json`
      2. Global `~/.solocode/settings.toml|json`
      3. Built-in defaults

    Returns a HarnessConfig with the merged result.
    """
    project_root = project_root or _find_project_root()
    global_dir = global_dir or _get_global_config_dir()

    sources: list[tuple[str, dict[str, Any]]] = []
    merged: dict[str, Any] = dict(DEFAULT_CONFIG)
    sources.append(("built-in defaults", DEFAULT_CONFIG))

    # Layer 2: Global config
    if global_dir.is_dir():
        global_file = _find_config_file(global_dir)
        if global_file:
            global_data = _load_file(global_file)
            if global_data:
                merged = _deep_merge(merged, global_data)
                sources.append((f"global ({global_file})", global_data))

    # Layer 1: Project config (highest priority)
    project_config_dir = project_root / ".solocode"
    if project_config_dir.is_dir():
        project_file = _find_config_file(project_config_dir)
        if project_file:
            project_data = _load_file(project_file)
            if project_data:
                merged = _deep_merge(merged, project_data)
                sources.append((f"project ({project_file})", project_data))

    # Apply environment variable overrides (highest priority)
    env_overrides = _load_env_overrides()
    if env_overrides:
        merged = _deep_merge(merged, env_overrides)
        sources.append(("environment variables", env_overrides))

    return HarnessConfig(
        data=merged,
        project_root=project_root,
        global_dir=global_dir,
        sources=sources,
    )


def _load_env_overrides() -> dict[str, Any]:
    """Parse environment variable overrides.

    SOLOCODE_HOOKS_PROFILE=strict     → hooks.profile = "strict"
    SOLOCODE_GATES_SECURITY=false     → gates.security = false
    SOLOCODE_SKILLS_DISABLED=skill_a,skill_b → skills.disabled = ["skill_a", "skill_b"]
    """
    overrides: dict[str, Any] = {}

    mapping = {
        "SOLOCODE_HOOKS_PROFILE": ("hooks.profile", str),
        "SOLOCODE_GATES_SECURITY": ("gates.security", lambda v: v.lower() == "true"),
        "SOLOCODE_GATES_LINT": ("gates.lint", lambda v: v.lower() == "true"),
        "SOLOCODE_GATES_SCHEMA": ("gates.schema", lambda v: v.lower() == "true"),
        "SOLOCODE_GATES_GARDEN": ("gates.garden", lambda v: v.lower() == "true"),
        "SOLOCODE_GATES_TEST": ("gates.test", lambda v: v.lower() == "true"),
        "SOLOCODE_GATES_GUARD": ("gates.guard", lambda v: v.lower() == "true"),
        "SOLOCODE_SKILLS_DISABLED": (
            "skills.disabled",
            lambda v: [s.strip() for s in v.split(",") if s.strip()],
        ),
        "SOLOCODE_VERIFY_ON_COMMIT": (
            "verify_on_commit",
            lambda v: v.lower() == "true",
        ),
        "SOLOCODE_AUTO_VERIFY_BEFORE_PUSH": (
            "auto_verify_before_push",
            lambda v: v.lower() == "true",
        ),
        "SOLOCODE_MODEL_DEFAULT": ("models.default", str),
    }

    for env_var, (dotted_key, converter) in mapping.items():
        value = os.environ.get(env_var)
        if value is not None:
            # Set nested dict
            keys = dotted_key.split(".")
            node = overrides
            for key in keys[:-1]:
                if key not in node:
                    node[key] = {}
                node = node[key]
            node[keys[-1]] = converter(value)

    return overrides


def create_default_config(
    path: Path | None = None,
    format: str = "toml",
) -> Path:
    """Create a default config file at the given path.

    Args:
        path: Where to create the file. Defaults to ~/.solocode/settings.toml
        format: "toml" or "json"

    Returns:
        Path to the created file.
    """
    if path is None:
        global_dir = _get_global_config_dir()
        global_dir.mkdir(parents=True, exist_ok=True)
        ext = ".toml" if format == "toml" else ".json"
        path = global_dir / f"settings{ext}"

    if path.exists():
        return path

    path.parent.mkdir(parents=True, exist_ok=True)

    if format == "toml":
        content = _generate_default_toml()
    else:
        content = json.dumps(DEFAULT_CONFIG, indent=2)

    path.write_text(content, encoding="utf-8")
    return path


def _generate_default_toml() -> str:
    """Generate a default settings.toml with comments."""
    return """# Solo-Code Harness Configuration
# ================================
# This file is read with lowest priority. Override in .solocode/settings.toml
# at the project root, or via environment variables (SOLOCODE_*).

[hooks]
# Profile: "minimal" | "standard" | "strict"
#   minimal  — only safety-critical hooks
#   standard — safety + quality hooks
#   strict   — all hooks including stop-gates
profile = "standard"

[gates]
# Verification gates. Disable individual gates to skip them in /verify.
security = true
lint = true
schema = true
garden = true
test = true
guard = true

[skills]
# Skills to auto-load at session start.
auto_load = [
    "code-review-expert",
    "file-editor-pro",
    "systematic-debugging",
    "brainstorming",
    "testing-patterns",
    "api-patterns",
]
# Skills to disable project-wide.
disabled = []

[models]
# Default and fallback model names.
default = "deepseek-v4-pro[1m]"
fallback = "deepseek-flash[1m]"

# Verify gates on every commit (hooks.PostToolUse quality-gate).
verify_on_commit = true
# Auto-run verification before git push.
auto_verify_before_push = true
"""


# ═══════════════════════════════════════════════════════════════════
# CLI interface (for testing / init)
# ═══════════════════════════════════════════════════════════════════


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Solo-Code hierarchical config loader",
        prog="harness_config.py",
    )
    sub = parser.add_subparsers(dest="command", help="Subcommand")

    # init — create default config
    p_init = sub.add_parser("init", help="Create default global config")
    p_init.add_argument(
        "--format",
        choices=["toml", "json"],
        default="toml",
        help="Config file format (default: toml)",
    )
    p_init.add_argument("--path", help="Custom path for config file")

    # show — load and display merged config
    p_show = sub.add_parser("show", help="Show merged config")
    p_show.add_argument("--project", help="Project root path")
    p_show.add_argument("--key", help="Dotted key to display (e.g. gates.security)")

    # debug — show layering
    p_debug = sub.add_parser("debug", help="Show config layering")
    p_debug.add_argument("--project", help="Project root path")

    args = parser.parse_args()

    if args.command == "init":
        fmt = args.format
        path = Path(args.path) if args.path else None
        created = create_default_config(path, format=fmt)
        print(f"Created: {created}")
        return 0

    elif args.command == "show":
        project = Path(args.project) if args.project else None
        config = load_harness_config(project_root=project)
        if args.key:
            value = config.get(args.key)
            print(json.dumps(value, indent=2, default=str))
        else:
            print(json.dumps(config.data, indent=2, default=str))
        return 0

    elif args.command == "debug":
        project = Path(args.project) if args.project else None
        config = load_harness_config(project_root=project)
        config.debug_print_sources()
        return 0

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
