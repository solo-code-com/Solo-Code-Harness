"""Load the project `.env` into the process environment for the CLI delegates.

Why this exists: the delegates spawn their CLI directly instead of going through
`opencode-env.ps1` / `codex-env.ps1`, so anything the launcher *synthesises* from `.env` is
missing. The concrete failure this fixes (measured 2026-10-16): the generated OpenCode config
declares the `freemodel` provider with `env: ["FREEMODEL_API_KEY"]`, the launcher derives that
from `OPENAI_API_KEY`, and a delegate that bypasses the launcher starts OpenCode without the
variable, so every `freemodel/...` model fails while `opencode-go/...` works.

Values are read but never logged, and an already-set process variable wins unless `override`
is passed: the shell's environment stays the operator's decision.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Variables the CLI config reads but `.env` does not define directly: the launcher derives
#: them, and so does this module so a direct spawn behaves like the launcher.
ALIASES: dict[str, str] = {
    "FREEMODEL_API_KEY": "OPENAI_API_KEY",
}

#: Base URLs that must not carry a trailing `/v1`, because the generated provider config appends
#: it (`"baseURL": "${OPENAI_BASE_URL}/v1"`). Only names whose template appends `/v1` belong here:
#: providers whose template uses the base URL verbatim (no appended `/v1`) are excluded, since
#: stripping their `/v1` would break those calls. A test asserts this
#: list against the templates actually present in the generated config.
NORMALIZE_BASE_URLS = ("OPENAI_BASE_URL",)

#: Default models per role, so a provider outage is a one-line `.env` change instead of a code
#: edit. The delegate's own constant is the fallback when the variable is unset.
MODEL_OVERRIDES: dict[str, str] = {
    "opencode": "SOLOCODE_MODEL_OPENCODE",
    "kilo": "SOLOCODE_MODEL_KILO",
    "codex": "SOLOCODE_MODEL_CODEX",
    "antigravity": "SOLOCODE_MODEL_ANTIGRAVITY",
}


def parse_env_file(path: Path) -> dict[str, str]:
    """Read a `.env` file into a dict. Quotes are stripped; comments and blanks are skipped."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name.strip()] = value
    return values


def load_project_env(
    root: Path | None = None, *, override: bool = False
) -> dict[str, str]:
    """Export the project `.env` into `os.environ`, with the launcher's aliases and trimming."""
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    values = parse_env_file(root / ".env")
    for name, value in values.items():
        if value and (override or not os.environ.get(name)):
            os.environ[name] = value
    for alias, source in ALIASES.items():
        if os.environ.get(source) and not os.environ.get(alias):
            os.environ[alias] = os.environ[source]
    for name in NORMALIZE_BASE_URLS:
        current = os.environ.get(name)
        if current:
            trimmed = current.strip().rstrip("/")
            if trimmed.endswith("/v1"):
                trimmed = trimmed[: -len("/v1")]
            os.environ[name] = trimmed
    return values


def model_override(role: str) -> str | None:
    """The `.env` override for a role's default model, or None when unset."""
    variable = MODEL_OVERRIDES.get(role)
    if not variable:
        return None
    return os.environ.get(variable) or None


#: Where each provider's API lives: the single answer to "which service does this model's key
#: come from". `base_env` is a literal URL or the name of a `.env` variable (read raw, so the
#: paths below keep their `/v1`). Consumed by `tools/check_model_catalog.py` and
#: `tools/model_api.py`.
PROVIDERS: dict[str, dict[str, str]] = {
    "freemodel": {
        "base_env": "OPENAI_BASE_URL",
        "key_env": "FREEMODEL_API_KEY",
        "models_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
    },
    "anthropic": {
        "base_env": "ANTHROPIC_BASE_URL",
        "key_env": "ANTHROPIC_API_KEY",
        "models_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
    },
    "deepseek": {
        "base_env": "DEEPSEEK_BASE_URL",
        "key_env": "DEEPSEEK_API_KEY",
        "models_path": "/models",
        "chat_path": "/chat/completions",
    },
    "openrouter": {
        "base_env": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "models_path": "/models",
        "chat_path": "/chat/completions",
    },
    "commandcode": {
        "base_env": "COMMANDCODE_BASE_URL",
        "key_env": "COMMANDCODE_API_KEY",
        "models_path": "/models",
        "chat_path": "/chat/completions",
    },
}


def provider_endpoint(provider: str, kind: str = "chat") -> tuple[str, str]:
    """(url, api key) for a provider and an endpoint kind (`chat` or `models`).

    The base url comes from the raw `.env` value (falling back to the process environment), and
    the key honours the aliases above, so a `freemodel` call works without the launcher.
    """
    spec = PROVIDERS.get(provider)
    if not spec:
        raise KeyError(f"unknown provider {provider!r}; known: {', '.join(PROVIDERS)}")
    env_path = Path(__file__).resolve().parent.parent / ".env"
    base = spec["base_env"]
    if not base.startswith("http"):
        base = parse_env_file(env_path).get(base, "") or os.environ.get(base, "")
        if not base and spec["base_env"] == "COMMANDCODE_BASE_URL":
            base = "https://api.commandcode.ai/provider/v1"
    key = os.environ.get(spec["key_env"], "") or parse_env_file(env_path).get(
        spec["key_env"], ""
    )
    if not key and spec["key_env"] == "FREEMODEL_API_KEY":
        key = os.environ.get("OPENAI_API_KEY", "") or parse_env_file(env_path).get(
            "OPENAI_API_KEY", ""
        )
    path = spec["chat_path"] if kind == "chat" else spec["models_path"]
    return base.rstrip("/") + path, key
