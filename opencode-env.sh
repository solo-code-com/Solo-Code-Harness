#!/usr/bin/env bash
# opencode-env.sh — OpenCode CLI launcher for the Solo-Code harness (POSIX)
#
# Bridges .env configuration to OpenCode runtime environment on Linux / macOS / WSL.
# Reads .env, exports credentials into process environment, normalizes
# OPENAI_BASE_URL, and launches opencode with any pass-through arguments.
#
# Usage:
#   ./opencode-env.sh                                # interactive OpenCode TUI
#   ./opencode-env.sh run "run the tests"            # non-interactive command
#   ./opencode-env.sh run -m commandcode/deepseek-v4.1-flash
set -euo pipefail

# Ensure working directory is project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="$SCRIPT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: No .env found at $ENV_FILE. Copy .env.template to .env first." >&2
    exit 1
fi

# Load variables from .env
while IFS= read -r line || [ -n "$line" ]; do
    trimmed="$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -z "$trimmed" ] && continue
    case "$trimmed" in
        \#*) continue ;;
        *=*)
            key="${trimmed%%=*}"
            val="${trimmed#*=}"
            key="$(echo "$key" | tr -d '[:space:]')"
            # Strip outer quotes
            if [[ "$val" =~ ^\".*\"$ ]] || [[ "$val" =~ ^\'.*\'$ ]]; then
                val="${val:1:-1}"
            fi
            export "$key=$val"
            ;;
    esac
done < "$ENV_FILE"

# FreeModel credentials alias
if [ -n "${OPENAI_API_KEY:-}" ] && [ -z "${FREEMODEL_API_KEY:-}" ]; then
    export FREEMODEL_API_KEY="$OPENAI_API_KEY"
fi

# Normalize OPENAI_BASE_URL (strip /v1 because config appends it)
if [ -n "${OPENAI_BASE_URL:-}" ]; then
    clean_base="${OPENAI_BASE_URL%/}"
    clean_base="${clean_base%/v1}"
    export OPENAI_BASE_URL="$clean_base"
fi

# Find opencode binary
OPENCODE_BIN=""
if command -v opencode >/dev/null 2>&1; then
    OPENCODE_BIN="$(command -v opencode)"
elif [ -x "$SCRIPT_DIR/.solocode/npm/node_modules/@opencode/cli/bin/opencode.exe" ]; then
    OPENCODE_BIN="$SCRIPT_DIR/.solocode/npm/node_modules/@opencode/cli/bin/opencode.exe"
elif [ -x "$SCRIPT_DIR/.solocode/npm/node_modules/.bin/opencode" ]; then
    OPENCODE_BIN="$SCRIPT_DIR/.solocode/npm/node_modules/.bin/opencode"
elif [ -x "$HOME/.local/bin/opencode" ]; then
    OPENCODE_BIN="$HOME/.local/bin/opencode"
fi

if [ -z "$OPENCODE_BIN" ]; then
    echo "ERROR: OpenCode CLI (@opencode/cli) not found on PATH." >&2
    echo "Install with: npm install -g @opencode/cli" >&2
    exit 1
fi

if ! touch "$HOME/.test-write" 2>/dev/null; then
    mkdir -p "$SCRIPT_DIR/.solocode/home"
    export HOME="$SCRIPT_DIR/.solocode/home"
else
    rm -f "$HOME/.test-write"
fi

# Clear any stale stored credential for freemodel so .env stays source of truth
"$OPENCODE_BIN" auth logout freemodel "API key" >/dev/null 2>&1 || true

exec "$OPENCODE_BIN" "$@"
