#!/usr/bin/env pwsh
# free_codex.ps1 - load the nearest project .env and run Codex CLI with its FreeModel key.
$dir = (Get-Location).Path; $envFile = $null
while ($dir) {
  $cand = Join-Path $dir ".env"
  if (Test-Path -LiteralPath $cand) { $envFile = $cand; break }
  $parent = Split-Path -Parent $dir
  if (-not $parent -or $parent -eq $dir) { break }
  $dir = $parent
}
if (-not $envFile) { Write-Error "free_codex: no .env found in this dir or above"; exit 2 }
Get-Content -LiteralPath $envFile | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    $k = $matches[1]; $v = $matches[2].Trim().Trim([char]34).Trim([char]39)
    [Environment]::SetEnvironmentVariable($k, $v, "Process")
  }
}
if (-not $env:FREEMODEL_API) { Write-Error "free_codex: FREEMODEL_API not set in $envFile"; exit 2 }
$cfgDir = Join-Path $HOME ".codex"; $cfg = Join-Path $cfgDir "config.toml"
if (-not (Test-Path -LiteralPath $cfg)) {
  New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
  $oa = if ($env:FREEMODE_BASE_URL_OPENAI) { $env:FREEMODE_BASE_URL_OPENAI } else { "https://api.freemodel.dev" }
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
& codex @args
