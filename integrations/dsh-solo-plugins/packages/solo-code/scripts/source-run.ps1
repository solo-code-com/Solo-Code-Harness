# Repair the source checkout's pnpm links and prove the profile composition
# includes the Solo-Code rows.
#
# Run this in a normal PowerShell, not inside a sandboxed agent shell: pnpm
# writes its Corepack cache outside the workspace, which the agent sandbox denies.
[CmdletBinding()]
param(
  [string]$Checkout = 'F:\Project\g-harness\deepseek-harness-master',
  [string]$Profile = 'headless'
)

$ErrorActionPreference = 'Stop'

if ($Profile -eq 'desktop') {
  throw "profile 'desktop' is managed by the Electron app; use the default -Profile headless."
}
if (-not (Test-Path $Checkout)) { throw "checkout not found: $Checkout" }

$bundleLink = Join-Path $env:USERPROFILE ".dsh\profiles\$Profile\node_modules\@solo-code\dsh-solo-code"
if (-not (Test-Path $bundleLink)) {
  throw "the bundle is not mounted in profile '$Profile'; run scripts/enable-source-profile.ps1 first."
}

Push-Location $Checkout
try {
  $tsxManifest = Join-Path $Checkout 'node_modules\tsx\package.json'
  if (-not (Test-Path $tsxManifest)) {
    Write-Host 'node_modules is incomplete; relinking from the local pnpm store first...'
    pnpm install --offline
    if ($LASTEXITCODE -ne 0) {
      Write-Host 'offline relink failed; running a normal install (network, several minutes)...'
      pnpm install
      if ($LASTEXITCODE -ne 0) { throw 'pnpm install failed' }
    }
  }

  Write-Host "composing profile '$Profile' from source..."
  $config = pnpm dsh --profile $Profile --dump-config 2>&1
  if ($LASTEXITCODE -ne 0) {
    $text = ($config | Out-String)
    if ($text -match 'managed exclusively') {
      throw "profile '$Profile' is owned by the Electron app. Use -Profile headless after scripts/enable-source-profile.ps1."
    }
    throw "pnpm dsh --dump-config failed:`n$text"
  }

  $hits = $config | Select-String -Pattern 'solo-code'
  if (-not $hits) {
    Write-Warning 'solo-code is not part of the source composition.'
    exit 1
  }
  $hits | Select-Object -First 10
  Write-Host 'OK: the source composition includes the Solo-Code rows.'
} finally {
  Pop-Location
}
