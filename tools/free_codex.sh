#!/usr/bin/env bash
# free_codex.sh - load nearest project .env and run Codex CLI with its FreeModel key.
dir="$PWD"; envf=""
while [ -n "$dir" ]; do [ -f "$dir/.env" ] && { envf="$dir/.env"; break; }; p="$(dirname "$dir")"; [ "$p" = "$dir" ] && break; dir="$p"; done
[ -z "$envf" ] && { echo "free_codex: no .env found" >&2; exit 2; }
set -a
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|\#*) continue ;; *=*) export "${line%%=*}=${line#*=}" ;; esac
done < "$envf"
set +a
[ -z "$FREEMODEL_API" ] && { echo "free_codex: FREEMODEL_API not set" >&2; exit 2; }
cfg="$HOME/.codex/config.toml"
if [ ! -f "$cfg" ]; then
  mkdir -p "$HOME/.codex"
  oa="${FREEMODE_BASE_URL_OPENAI:-https://api.freemodel.dev}"
  printf 'model = "gpt-6.1-sol"\nmodel_provider = "freemodel"\n\n[model_providers.freemodel]\nname = "FreeModel"\nbase_url = "%s/v1"\nenv_key = "FREEMODEL_API"\nwire_api = "responses"\n' "$oa" > "$cfg"
fi
exec codex "$@"
