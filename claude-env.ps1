$claudeArgs = @($args)

# Launcher profiles:
# - gateway (default): API-key auth against ANTHROPIC_BASE_URL (a named provider). Full mode by default
#   (hooks, skills, auto-memory, CLAUDE.md auto-discovery all active).
# - native: native Claude Code mode; prefers API key if present, otherwise lets
#   Claude Code use its normal auth flow.
# - kilo: same runtime behavior as native, but keeps a distinct profile name so
#   IDE integrations can target it explicitly without changing the default profile.
#
# Bare mode (--bare), when explicitly requested:
# skips hooks, skill-dir discovery, LSP, plugin sync, attribution,
# auto-memory, background prefetches and keychain reads. This launcher
# restores CLAUDE.md discovery with --add-dir . when bare is on, but hooks
# (including .claude/hooks/guard.py) stay off -- there is no equivalent flag
# to bring those back in bare mode.
#
# History: this launcher used to force --bare for every gateway-profile run,
# on the assumption that full mode would prompt for Claude OAuth login
# instead of honoring ANTHROPIC_API_KEY against a third-party gateway. That
# assumption was tested and found FALSE on 2026-08-08 (claude 2.1.222,
# freemodel gateway cc.freemodel.dev): `claude -p` succeeded with no login
# prompt using only ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL, no --bare needed.
# Forcing --bare bought nothing but silently disabled every guard/quality/
# security hook in CLAUDE.md's "Claude Code Assets" list on every single
# gateway-profile launch. Full mode is now the default for every profile;
# pass --bare explicitly if a future provider swap needs it again.

$profile = "gateway"
$useBare = $false
$normalizedArgs = New-Object System.Collections.Generic.List[string]

for ($i = 0; $i -lt $claudeArgs.Count; $i++) {
    $arg = $claudeArgs[$i]

    if ($arg -eq "--profile") {
        if (($i + 1) -ge $claudeArgs.Count) {
            Write-Error "Missing value for --profile. Use: gateway, native, or kilo."
            exit 1
        }
        $candidate = $claudeArgs[$i + 1].ToLowerInvariant()
        if (@("gateway", "native", "kilo") -notcontains $candidate) {
            Write-Error "Invalid --profile '$candidate'. Use: gateway, native, or kilo."
            exit 1
        }
        $profile = $candidate
        $i++
        continue
    }

    if ($arg -like "--profile=*") {
        $candidate = $arg.Substring(10).ToLowerInvariant()
        if (@("gateway", "native", "kilo") -notcontains $candidate) {
            Write-Error "Invalid --profile '$candidate'. Use: gateway, native, or kilo."
            exit 1
        }
        $profile = $candidate
        continue
    }

    if ($arg -eq "--full-mode") {
        # No-op now that full mode is the default everywhere; kept so
        # existing callers/scripts that pass it do not break.
        $useBare = $false
        continue
    }

    if ($arg -eq "--bare") {
        # Explicit opt-in escape hatch. Not forwarded raw -- the block below
        # re-adds it (plus --add-dir .) so CLAUDE.md discovery survives bare.
        $useBare = $true
        continue
    }

    [void]$normalizedArgs.Add($arg)
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $scriptRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Error "Missing .env beside claude-env.ps1: $envFile"
    exit 1
}

Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
        return
    }

    $parts = $line.Split("=", 2)
    if ($parts.Length -ne 2) {
        return
    }

    $key = $parts[0].Trim()
    $value = $parts[1].Trim()

    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }

    [Environment]::SetEnvironmentVariable($key, $value, "Process")
}

$settingsPath = Join-Path $HOME ".claude\settings.json"
$settings = $null
if (Test-Path $settingsPath) {
    try { $settings = Get-Content -Raw $settingsPath | ConvertFrom-Json } catch { }
}
$hasApiKeyHelper = ($settings -and $settings.apiKeyHelper)
if (($profile -ne "gateway") -and $hasApiKeyHelper -and $env:ANTHROPIC_API_KEY) {
    Write-Warning "apiKeyHelper detected -- unsetting ANTHROPIC_API_KEY to avoid auth conflict."
    Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
}

if (($profile -eq "gateway") -and (-not $hasApiKeyHelper) -and (-not $env:ANTHROPIC_API_KEY)) {
    Write-Error "ANTHROPIC_API_KEY is empty. Fill it in .env first (or configure apiKeyHelper in ~/.claude/settings.json)."
    exit 1
}

if (($profile -eq "gateway") -and (-not $env:ANTHROPIC_BASE_URL)) {
    Write-Error "ANTHROPIC_BASE_URL is empty. Fill it in .env first."
    exit 1
}

$approver = Join-Path $scriptRoot "tools\approve_api_key.py"
if ((Test-Path $approver) -and $env:ANTHROPIC_API_KEY) {
    if ($profile -eq "gateway") {
        # Gateway mode is an explicit opt-in to the key from .env. Approve it
        # before starting an interactive session so Claude Code does not fall
        # through to its native OAuth login flow.
        & python $approver --apply
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Could not approve the ANTHROPIC_API_KEY for Claude Code."
            exit $LASTEXITCODE
        }
    }
    else {
        & python $approver --check
    }
}

if ($profile -eq "gateway") {
    if ($useBare) {
        Write-Warning "Profile gateway: running Claude Code with --bare (explicitly requested). Hooks, auto-memory, and CLAUDE.md auto-discovery are reduced; this launcher restores CLAUDE.md via --add-dir . only."
    }
    else {
        Write-Host "Profile gateway: running Claude Code in full mode via ANTHROPIC_API_KEY/ANTHROPIC_BASE_URL (verified 2026-11-23, DeepSeek /anthropic: no OAuth prompt). Hooks, skills, and auto-memory are active. Pass --bare only if a provider swap needs it."
    }
}
elseif ($profile -eq "native") {
    if ($env:ANTHROPIC_API_KEY -or $hasApiKeyHelper) {
        Write-Host "Profile native: preferring API-based auth in full mode."
    }
    else {
        Write-Warning "Profile native: no API key/apiKeyHelper detected. Claude Code may prompt for native login."
    }
}
elseif ($profile -eq "kilo") {
    Write-Host "Profile kilo: full mode selected for IDE-integrated Kilo workflows."
}

if ($useBare) {
    if ($normalizedArgs -notcontains "--bare") {
        [void]$normalizedArgs.Insert(0, "--bare")
    }
    if ($normalizedArgs -notcontains "--add-dir") {
        [void]$normalizedArgs.Add("--add-dir")
        [void]$normalizedArgs.Add(".")
    }
}

& claude @normalizedArgs
