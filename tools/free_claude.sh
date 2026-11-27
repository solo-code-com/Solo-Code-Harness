#!/usr/bin/env bash
# free_claude.sh - load nearest project .env and run Claude Code with its FreeModel key.
dir="$PWD"; envf=""
while [ -n "$dir" ]; do [ -f "$dir/.env" ] && { envf="$dir/.env"; break; }; p="$(dirname "$dir")"; [ "$p" = "$dir" ] && break; dir="$p"; done
[ -z "$envf" ] && { echo "free_claude: no .env found" >&2; exit 2; }
set -a
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|\#*) continue ;; *=*) export "${line%%=*}=${line#*=}" ;; esac
done < "$envf"
set +a
[ -z "$FREEMODEL_API" ] && { echo "free_claude: FREEMODEL_API not set" >&2; exit 2; }
export ANTHROPIC_API_KEY="$FREEMODEL_API"
export ANTHROPIC_BASE_URL="${FREEMODE_BASE_URL_Anthropic:-https://cc.freemodel.dev}"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
exec claude "$@"
