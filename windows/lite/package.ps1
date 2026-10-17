$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

& (Join-Path $PSScriptRoot "build.ps1")
if ($LASTEXITCODE -ne 0) { throw "Portable build failed with code $LASTEXITCODE" }

$iscc = (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue)?.Source
if (-not $iscc) {
    $candidate = Join-Path $env:USERPROFILE "AppData\Local\Programs\Inno Setup 6\ISCC.exe"
    if (Test-Path -LiteralPath $candidate) { $iscc = $candidate }
}
if (-not $iscc) {
    throw "Inno Setup 6 compiler ISCC.exe not found. Install Inno Setup 6 and rerun package.ps1."
}

& $iscc (Join-Path $PSScriptRoot "installer\ChatAssistantLite.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup exited with code $LASTEXITCODE" }

$setup = Join-Path $PSScriptRoot "dist-release\ChatAssistantLite-Setup.exe"
if (-not (Test-Path -LiteralPath $setup)) {
    throw "Installer compilation completed without creating $setup"
}
Write-Output "Installer: $setup"
