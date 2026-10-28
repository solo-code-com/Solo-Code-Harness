#!/usr/bin/env bash
# antigravity-env.sh — Antigravity CLI (agy) launcher for the Solo-Code harness (POSIX)
#
# Bridges .env configuration to Antigravity CLI on Linux / macOS / WSL.
# Reads .env, exports GEMINI_API_KEY if present, and invokes agy.
#
# Usage:
#   ./antigravity-env.sh                                  # interactive agy TUI
#   ./antigravity-env.sh "run audit"                      # prompt
#   ./antigravity-env.sh --version                        # check version
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        trimmed="$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        [ -z "$trimmed" ] && continue
        case "$trimmed" in
            \#*) continue ;;
            *=*)
                key="${trimmed%%=*}"
                val="${trimmed#*=}"
                key="$(echo "$key" | tr -d '[:space:]')"
                if [ -z "${!key:-}" ]; then
                    if [[ "$val" =~ ^\".*\"$ ]] || [[ "$val" =~ ^\'.*\'$ ]]; then
                        val="${val:1:-1}"
                    fi
                    export "$key=$val"
                fi
                ;;
        esac
    done < "$ENV_FILE"
fi

AGY_BIN=""
if command -v agy >/dev/null 2>&1; then
    AGY_BIN="$(command -v agy)"
elif [ -x "$HOME/.local/bin/agy" ]; then
    AGY_BIN="$HOME/.local/bin/agy"
elif [ -x "$HOME/.gemini/antigravity/bin/agy" ]; then
    AGY_BIN="$HOME/.gemini/antigravity/bin/agy"
elif [ -x "/usr/local/bin/agy" ]; then
    AGY_BIN="/usr/local/bin/agy"
fi

if [ -z "$AGY_BIN" ]; then
    echo "ERROR: Antigravity CLI (agy) not found on PATH or standard install locations." >&2
    echo "See docs for installing Google Antigravity CLI: agy." >&2
    exit 1
fi

exec "$AGY_BIN" "$@"
