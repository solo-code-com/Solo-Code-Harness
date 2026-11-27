# codex-env.ps1 - Codex CLI launcher for FreeModel
#
# Bridges the project .env to Codex: reads the nearest .env (walking up), sets
# FREEMODEL_API (the env_key Codex reads), ensures ~/.codex/config.toml points at
# the FreeModel provider, then launches codex with any pass-through arguments.
# Each project uses its own .env key; no global setup needed.
#
# Usage:
#   .\codex-env.ps1                 # interactive Codex
#   .\codex-env.ps1 -Models         # list the FreeModel GPT model ids and exit
#   .\codex-env.ps1 exec "do X"     # headless task (pass-through)
#   .\codex-env.ps1 --help          # pass-through flags

$dir = (Get-Location).Path; $envFile = $null
while ($dir) {
  $cand = Join-Path $dir ".env"
  if (Test-Path -LiteralPath $cand) { $envFile = $cand; break }
  $parent = Split-Path -Parent $dir
  if (-not $parent -or $parent -eq $dir) { break }
  $dir = $parent
}
if (-not $envFile) { [Console]::Error.WriteLine("codex-env: no .env found in this dir or above"); exit 2 }
Get-Content -LiteralPath $envFile | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim().Trim([char]34).Trim([char]39), "Process")
  }
}
if (-not $env:FREEMODEL_API) { [Console]::Error.WriteLine("codex-env: FREEMODEL_API not set in $envFile"); exit 2 }
$oa = if ($env:FREEMODE_BASE_URL_OPENAI) { $env:FREEMODE_BASE_URL_OPENAI } else { "https://api.freemodel.dev" }
$cfgDir = Join-Path $HOME ".codex"; $cfg = Join-Path $cfgDir "config.toml"
if (-not (Test-Path -LiteralPath $cfg)) {
  New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
  $lines = @(
    'model = "gpt-6.1-sol"',
    'model_provider = "freemodel"',
    '',
    '[model_providers.freemodel]',
    'name = "FreeModel"',
    ('base_url = "' + $oa + '/v1"'),
    'env_key = "FREEMODEL_API"',
    'wire_api = "responses"'
  )
  Set-Content -LiteralPath $cfg -Value $lines -Encoding utf8
}
if ($args -contains "-Models" -or $args -contains "--models") {
  try {
    $r = Invoke-RestMethod -Uri "$oa/v1/models" -Headers @{ "Authorization" = "Bearer $env:FREEMODEL_API" } -TimeoutSec 30
    "ChatGPT models via FreeModel ($oa):"
    $r.data | ForEach-Object { "  " + $_.id }
  } catch { [Console]::Error.WriteLine("codex-env: models query failed: $_") }
  exit 0
}
& codex @args
