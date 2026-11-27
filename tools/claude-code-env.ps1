# claude-code-env.ps1 - Claude Code launcher for FreeModel
#
# Bridges the project .env to Claude Code's runtime env: reads the nearest .env
# (walking up), maps FREEMODEL_API -> ANTHROPIC_API_KEY and
# FREEMODE_BASE_URL_Anthropic -> ANTHROPIC_BASE_URL, then launches claude with any
# pass-through arguments. No global setup needed; each project uses its own .env key.
#
# Usage:
#   .\claude-code-env.ps1                 # interactive Claude Code
#   .\claude-code-env.ps1 -Models         # list the FreeModel Claude model ids and exit
#   .\claude-code-env.ps1 -p "do X"       # one-shot prompt (pass-through)
#   .\claude-code-env.ps1 --help          # pass-through flags

$dir = (Get-Location).Path; $envFile = $null
while ($dir) {
  $cand = Join-Path $dir ".env"
  if (Test-Path -LiteralPath $cand) { $envFile = $cand; break }
  $parent = Split-Path -Parent $dir
  if (-not $parent -or $parent -eq $dir) { break }
  $dir = $parent
}
if (-not $envFile) { [Console]::Error.WriteLine("claude-code-env: no .env found in this dir or above"); exit 2 }
Get-Content -LiteralPath $envFile | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim().Trim([char]34).Trim([char]39), "Process")
  }
}
if (-not $env:FREEMODEL_API) { [Console]::Error.WriteLine("claude-code-env: FREEMODEL_API not set in $envFile"); exit 2 }
$env:ANTHROPIC_API_KEY = $env:FREEMODEL_API
$env:ANTHROPIC_BASE_URL = if ($env:FREEMODE_BASE_URL_Anthropic) { $env:FREEMODE_BASE_URL_Anthropic } else { "https://cc.freemodel.dev" }
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"
if ($args -contains "-Models" -or $args -contains "--models") {
  try {
    $r = Invoke-RestMethod -Uri "$($env:ANTHROPIC_BASE_URL)/v1/models" -Headers @{ "x-api-key" = $env:ANTHROPIC_API_KEY; "anthropic-version" = "2023-06-01" } -TimeoutSec 30
    "Claude models via FreeModel ($($env:ANTHROPIC_BASE_URL)):"
    $r.data | ForEach-Object { "  " + $_.id }
  } catch { [Console]::Error.WriteLine("claude-code-env: models query failed: $_") }
  exit 0
}
& claude @args
