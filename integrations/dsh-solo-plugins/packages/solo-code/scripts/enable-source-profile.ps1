# Configure the source-managed `headless` profile to mount this bundle, then
# relink its node_modules. Run in a normal PowerShell, outside the agent sandbox:
# the profile directory lives under %USERPROFILE%\.dsh.
#
# Windows PowerShell 5.1 writes `Set-Content -Encoding utf8` WITH a BOM, which
# pnpm rejects as "Invalid package.json", so this script writes UTF-8 without a
# BOM through .NET and restores the backup if anything fails.
[CmdletBinding()]
param(
  [string]$Package = 'F:\Project\g-harness\g-harness\integrations\dsh-solo-plugins\packages\solo-code',
  [string]$ProfileDir = "$env:USERPROFILE\.dsh\profiles\headless",
  [string]$Bundle = '@solo-code/dsh-solo-code',
  [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Package)) { throw "package not found: $Package" }
if (-not (Test-Path (Join-Path $Package 'cordis.patch.yml'))) {
  throw "not a bundle (no cordis.patch.yml): $Package"
}
if (-not (Test-Path $ProfileDir)) { throw "profile not found: $ProfileDir" }

$manifestPath = Join-Path $ProfileDir 'package.json'
$backupPath = "$manifestPath.bak"
if (-not (Test-Path $backupPath)) { Copy-Item $manifestPath $backupPath -Force }

function Read-Manifest([string]$Path) {
  return [System.IO.File]::ReadAllText($Path) | ConvertFrom-Json
}

function Write-Manifest([string]$Path, $Value) {
  $json = $Value | ConvertTo-Json -Depth 10
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $json, $utf8NoBom)
}

# Recover from a previous BOM-damaged write.
$bytes = [System.IO.File]::ReadAllBytes($manifestPath)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
  Copy-Item $backupPath $manifestPath -Force
  Write-Host 'restored package.json from backup (previous write added a BOM)'
}

$manifest = Read-Manifest $manifestPath
if (-not $manifest.PSObject.Properties['dsh']) {
  throw "not a dsh profile (no dsh field): $manifestPath"
}
if (-not $manifest.PSObject.Properties['dependencies']) {
  $manifest | Add-Member -NotePropertyName dependencies -NotePropertyValue ([pscustomobject]@{})
}

# Drop the retired solo packages; keep only the consolidated bundle.
foreach ($name in @($manifest.dependencies.PSObject.Properties.Name)) {
  if ($name -like '@solo-code/dsh-solo-*' -and $name -ne $Bundle) {
    $manifest.dependencies.PSObject.Properties.Remove($name)
  }
}
$link = 'link:' + ($Package -replace '\\', '/')
$manifest.dependencies | Add-Member -NotePropertyName $Bundle -NotePropertyValue $link -Force

if (-not $manifest.dsh.PSObject.Properties['profile']) {
  throw "not a dsh profile (no dsh.profile field): $manifestPath"
}
$bundles = @($manifest.dsh.profile.bundles)
if ($bundles -notcontains $Bundle) {
  $manifest.dsh.profile.bundles = @($bundles) + $Bundle
}

Write-Manifest $manifestPath $manifest
$null = Read-Manifest $manifestPath
Write-Host "updated $manifestPath"

if ($SkipInstall) {
  Write-Host 'install skipped. Next: scripts/source-run.ps1'
  return
}

Push-Location $ProfileDir
try {
  pnpm install
  if ($LASTEXITCODE -ne 0) {
    Copy-Item $backupPath $manifestPath -Force
    Write-Warning 'pnpm install failed; package.json restored from backup'
    throw 'pnpm install failed in the profile'
  }
} finally {
  Pop-Location
}

Write-Host 'profile configured. Next: scripts/source-run.ps1'
