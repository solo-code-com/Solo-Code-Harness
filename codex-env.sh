#!/usr/bin/env bash
# codex-env.sh — Codex CLI launcher for the Solo-Code harness (POSIX)
#
# Bridges .env configuration to Codex CLI on Linux / macOS / WSL.
# Loads OPENAI_BASE_URL and OPENAI_API_KEY from .env, passes base_url
# via `-c model_providers.freemodel.base_url=...` and tracks session lifecycle.
#
# Usage:
#   ./codex-env.sh                       # interactive Codex
#   ./codex-env.sh exec "run the tests"  # non-interactive
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    # Load variables from .env if not already set in process environment
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

if [ -z "${OPENAI_API_KEY:-}" ] && [ -n "${COMMANDCODE_API_KEY:-}" ]; then
    export OPENAI_API_KEY="$COMMANDCODE_API_KEY"
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "ERROR: OPENAI_API_KEY (or COMMANDCODE_API_KEY) is missing from environment and .env." >&2
    exit 1
fi

BASE_URL="${OPENAI_BASE_URL:-}"
if [ -z "$BASE_URL" ] && [ -n "${COMMANDCODE_BASE_URL:-}" ]; then
    BASE_URL="$COMMANDCODE_BASE_URL"
elif [ -z "$BASE_URL" ] && [ -n "${COMMANDCODE_API_KEY:-}" ]; then
    BASE_URL="https://api.commandcode.ai/provider/v1"
fi

if [ -n "$BASE_URL" ]; then
    BASE_URL="${BASE_URL%/}"
    if [[ "$BASE_URL" != */v1 ]]; then
        BASE_URL="$BASE_URL/v1"
    fi
fi

if [ -z "${CODEX_HOME:-}" ] && [ -d "$SCRIPT_DIR/.solocode/codex" ]; then
    export CODEX_HOME="$SCRIPT_DIR/.solocode/codex"
fi

if [ -d "$SCRIPT_DIR/.solocode/npm/node_modules/.bin" ]; then
    export PATH="$SCRIPT_DIR/.solocode/npm/node_modules/.bin:$PATH"
fi

if ! command -v codex >/dev/null 2>&1; then
    echo "ERROR: codex not found on PATH. Install with: npm install -g @openai/codex" >&2
    exit 1
fi

SESSION_TOOL="$SCRIPT_DIR/tools/codex_session.py"
SESSION_ID=""
if command -v python3 >/dev/null 2>&1 && [ -f "$SESSION_TOOL" ]; then
    SESSION_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
    python3 "$SESSION_TOOL" start --session-id "$SESSION_ID" || true
fi

cleanup() {
    local rc=$?
    if [ -n "$SESSION_ID" ] && [ -f "$SESSION_TOOL" ]; then
        python3 "$SESSION_TOOL" end --session-id "$SESSION_ID" --summary "Codex CLI exited with code $rc" || true
    fi
    exit $rc
}
trap cleanup EXIT

if [ -n "$BASE_URL" ]; then
    codex -c "model_providers.freemodel.base_url=\"$BASE_URL\"" "$@"
else
    codex "$@"
fi
