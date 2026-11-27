#!/usr/bin/env pwsh
# free_claude.ps1 - load the nearest project .env and run Claude Code with its FreeModel key.
$dir = (Get-Location).Path; $envFile = $null
while ($dir) {
  $cand = Join-Path $dir ".env"
  if (Test-Path -LiteralPath $cand) { $envFile = $cand; break }
  $parent = Split-Path -Parent $dir
  if (-not $parent -or $parent -eq $dir) { break }
  $dir = $parent
}
if (-not $envFile) { Write-Error "free_claude: no .env found in this dir or above"; exit 2 }
Get-Content -LiteralPath $envFile | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    $k = $matches[1]; $v = $matches[2].Trim().Trim([char]34).Trim([char]39)
    [Environment]::SetEnvironmentVariable($k, $v, "Process")
  }
}
if (-not $env:FREEMODEL_API) { Write-Error "free_claude: FREEMODEL_API not set in $envFile"; exit 2 }
$env:ANTHROPIC_API_KEY = $env:FREEMODEL_API
$env:ANTHROPIC_BASE_URL = if ($env:FREEMODE_BASE_URL_Anthropic) { $env:FREEMODE_BASE_URL_Anthropic } else { "https://cc.freemodel.dev" }
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"
# --bare (API-key auth instead of the OAuth login) is added only for an INTERACTIVE
# session; a headless run (-p/--print) authenticates from the env already and is left
# unchanged, so an arm calls the model exactly as it would on its own.
$launchArgs = @($args)
$headless = $launchArgs -contains "-p" -or $launchArgs -contains "--print"
$alreadyBare = $launchArgs -contains "--bare" -or $launchArgs -contains "-bare"
if (-not $headless -and -not $alreadyBare) {
  $launchArgs = @("--bare") + $launchArgs
}
& claude @launchArgs
