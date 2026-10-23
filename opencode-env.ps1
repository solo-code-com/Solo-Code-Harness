# opencode-env.ps1 — OpenCode CLI launcher for the Solo-Code harness
#
# Why this exists: bridges .env configuration to OpenCode runtime environment.
# It reads OPENAI_BASE_URL and OPENAI_API_KEY from .env,
# normalizes the base URL (stripping any trailing /v1 so {env:OPENAI_BASE_URL}/v1
# in opencode config resolves cleanly), exports them into the process environment,
# and launches opencode with any pass-through arguments.
#
# Usage:
#   .\opencode-env.ps1                                # interactive OpenCode TUI
#   .\opencode-env.ps1 run "run the tests"            # non-interactive command
#   .\opencode-env.ps1 run -m freemodel/gpt-6-sol     # specify model directly
#   .\opencode-env.ps1 --help                         # pass-through flags

$ErrorActionPreference = "Stop"

# Ensure working directory is the project root containing .env
if ($PSScriptRoot -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot ".env"))) {
    Set-Location $PSScriptRoot
}

$envFile = Join-Path (Get-Location) ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    Write-Error "No .env found at $envFile. Run this launcher from the project root."
    exit 1
}

$config = @{}
foreach ($line in Get-Content -LiteralPath $envFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
        continue
    }
    $name, $value = $trimmed.Split("=", 2)
    $name = $name.Trim()
    $value = $value.Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    $config[$name] = $value
}

# Export all loaded env vars into process environment
foreach ($entry in $config.GetEnumerator()) {
    if ($entry.Value) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
}

# Export OpenAI / FreeModel credentials. FREEMODEL_API_KEY is what the
# `.opencode/opencode.json` freemodel provider reads; OPENAI_API_KEY stays for
# Codex and OpenCode's built-in OpenAI provider.
if ($config.ContainsKey("OPENAI_API_KEY") -and $config["OPENAI_API_KEY"]) {
    $env:OPENAI_API_KEY = $config["OPENAI_API_KEY"]
    $env:FREEMODEL_API_KEY = $config["OPENAI_API_KEY"]
}

# Normalize OPENAI_BASE_URL: strip any trailing /v1 or slashes, because both the
# project config (`${OPENAI_BASE_URL}/v1`) and the global config
# (`{env:OPENAI_BASE_URL}/v1`) append the /v1 path themselves.
if ($config.ContainsKey("OPENAI_BASE_URL") -and $config["OPENAI_BASE_URL"]) {
    $baseUrl = $config["OPENAI_BASE_URL"].Trim().TrimEnd("/")
    if ($baseUrl.EndsWith("/v1")) {
        $baseUrl = $baseUrl.Substring(0, $baseUrl.Length - 3)
    }
    $env:OPENAI_BASE_URL = $baseUrl
}

# Export and normalize Anthropic credentials if present
if ($config.ContainsKey("ANTHROPIC_API_KEY") -and $config["ANTHROPIC_API_KEY"]) {
    $env:ANTHROPIC_API_KEY = $config["ANTHROPIC_API_KEY"]
}
if ($config.ContainsKey("ANTHROPIC_BASE_URL") -and $config["ANTHROPIC_BASE_URL"]) {
    $antUrl = $config["ANTHROPIC_BASE_URL"].Trim().TrimEnd("/")
    $freemodelDomains = @("api.freemodel.dev", "cc.freemodel.dev", "api-cc.freemodel.dev", "cc-t2.freemodel.dev")
    foreach ($domain in $freemodelDomains) {
        if ($antUrl -eq "https://${domain}/v1/messages") {
            $antUrl = "https://${domain}"
            break
        }
    }
    $env:ANTHROPIC_BASE_URL = $antUrl
}

# Resolve the OpenCode v2 executable.
# v2 ships as the npm package `@opencode/cli` (bins: opencode, opencode2). The
# retired v1 line (`opencode-ai`) and a v1 self-updating native binary at
# ~/.opencode/bin are deliberately NOT used — that install sat there as v1.18.31
# until 2026-09-28, when it was removed, and on PATH it would silently run the
# old CLI. Resolving the real executable behind the npm shim also avoids Windows
# `%*` argument mangling.
$opencodeBin = $null
$searchDirs = @()
$genericCmd = Get-Command opencode -ErrorAction SilentlyContinue
if ($genericCmd -and $genericCmd.Source) {
    $searchDirs += (Split-Path $genericCmd.Source -Parent)
}
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if ($nodeCmd -and $nodeCmd.Source) {
    $searchDirs += (Split-Path $nodeCmd.Source -Parent)
}
foreach ($dir in ($searchDirs | Select-Object -Unique)) {
    $candidate = Join-Path $dir "node_modules\@opencode\cli\bin\opencode.exe"
    if (Test-Path -LiteralPath $candidate) {
        $opencodeBin = $candidate
        break
    }
}

if (-not $opencodeBin) {
    Write-Error "OpenCode v2 (@opencode/cli) not found. Install with: npm install -g @opencode/cli"
    exit 1
}

$opencodeArgs = @($args)

# FreeModel renamed the Codex model from gpt-5.6-terra to gpt-6-sol.
# Keep older launcher invocations working while sending the current model ID.
for ($index = 0; $index -lt $opencodeArgs.Count; $index++) {
    if ($opencodeArgs[$index] -in @("freemodel/gpt-5.6-terra", "gpt-5.6-terra")) {
        $opencodeArgs[$index] = $opencodeArgs[$index].Replace("gpt-5.6-terra", "gpt-6-sol")
    }
}
# `opencode` writes progress to stderr, and ErrorActionPreference = "Stop" turns
# a native command's stderr into a terminating error in Windows PowerShell 5.1.
# That made every launch exit 1 with an empty Write-Error, even successful runs.
# Lower it for the native calls and rely on $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"

# OpenCode prefers a credential saved in its own store over the provider's `env`
# variable, so a stale stored FreeModel key silently shadows the .env key -- seen
# as "Unauthorized - Invalid token" while the same .env key works via curl.
# Remove it each launch so .env stays the single source of truth. Idempotent:
# with nothing stored it just reports the FREEMODEL_API_KEY fallback and exits
# non-zero, so the result is discarded.
& $opencodeBin auth logout freemodel "API key" *> $null

$exitCode = 1
& $opencodeBin @opencodeArgs
if ($null -ne $LASTEXITCODE) {
    $exitCode = $LASTEXITCODE
}
else {
    $exitCode = 0
}
exit $exitCode
