# Build portable + installer and (optionally) verify the install lifecycle.
# PowerShell 5.1 compatible: no null-conditional operator (?. ) is used.
param([switch]$VerifyInstall)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

& (Join-Path $PSScriptRoot "build.ps1")
if ($LASTEXITCODE -ne 0) { throw "Portable build failed with code $LASTEXITCODE" }

$isccCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
$iscc = ""
if ($isccCommand) { $iscc = $isccCommand.Source }
if (-not $iscc) {
    $candidate = Join-Path $env:USERPROFILE "AppData\Local\Programs\Inno Setup 6\ISCC.exe"
    if (Test-Path -LiteralPath $candidate) { $iscc = $candidate }
}
if (-not $iscc) {
    throw "Inno Setup 6 compiler ISCC.exe not found. Install Inno Setup 6 and rerun package.ps1."
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if ($env:CHATASSISTANT_PYTHON) { $python = $env:CHATASSISTANT_PYTHON }
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found at '$python'. Set CHATASSISTANT_PYTHON."
}

$manifest = Join-Path $PSScriptRoot "dist-release\ChatAssistantLite\build_info.json"
if (-not (Test-Path -LiteralPath $manifest)) {
    throw "build_info.json missing from portable output: $manifest"
}
& $python (Join-Path $PSScriptRoot "tools\verify_build_info.py") --info $manifest --source-root $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw "build_info.json verification failed" }

& $iscc (Join-Path $PSScriptRoot "installer\ChatAssistantLite.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup exited with code $LASTEXITCODE" }

$setup = Join-Path $PSScriptRoot "dist-release\ChatAssistantLite-Setup.exe"
if (-not (Test-Path -LiteralPath $setup)) {
    throw "Installer compilation completed without creating $setup"
}
if ($VerifyInstall) {
    & (Join-Path $PSScriptRoot "tools\verify_install.ps1") -Setup $setup
    if ($LASTEXITCODE -ne 0) { throw "install lifecycle verification failed" }
}
Write-Output "Installer: $setup"
