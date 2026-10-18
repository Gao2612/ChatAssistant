$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if ($env:CHATASSISTANT_PYTHON) {
    $python = $env:CHATASSISTANT_PYTHON
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found at '$python'. Create windows/lite/.venv, install requirements-build.txt, or set CHATASSISTANT_PYTHON to the environment's python.exe."
}

$pyinstallerVersion = & $python -c "import importlib.metadata as m; print(m.version('PyInstaller'))" 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the build environment. Install windows/lite/requirements-build.txt. Details: $pyinstallerVersion"
}
if ($pyinstallerVersion.Trim() -ne "6.22.3") {
    throw "PyInstaller 6.22.3 is required (detected $pyinstallerVersion). Install windows/lite/requirements-build.txt."
}

foreach ($module in @("PySide6", "rapidocr_onnxruntime", "windows_capture", "pypdf")) {
    & $python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('$module') else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "Required package '$module' is missing from '$python'. Install requirements-build.txt."
    }
}

$python3Dll = & $python -c "import pathlib,sys; print(pathlib.Path(sys.base_prefix) / 'python3.dll')"
if (-not (Test-Path -LiteralPath $python3Dll)) {
    throw "The Python stable-ABI runtime DLL was not found: $python3Dll"
}

$outputDir = Join-Path $PSScriptRoot "dist-release"
$workDir = Join-Path $PSScriptRoot "build-pyinstaller"
# PowerShell 5.1 turns native stderr progress into a terminating error when
# $ErrorActionPreference is Stop; keep Continue for native build tools and check
# their exit codes explicitly.
$previousPolicy = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name ChatAssistantLite `
    "--icon=$PSScriptRoot\assets\ChatAssistant.ico" `
    "--add-data=assets;assets" `
    "--distpath=$outputDir" `
    "--workpath=$workDir" `
    --collect-all rapidocr_onnxruntime `
    --collect-all windows_capture `
    --collect-all pypdf `
    "--add-binary=$python3Dll;." `
    "--add-data=licenses;licenses" `
    main.py
$pyInstallerExit = $LASTEXITCODE
$ErrorActionPreference = $previousPolicy

if ($pyInstallerExit -ne 0) { throw "PyInstaller exited with code $pyInstallerExit" }

$appDir = Join-Path $outputDir "ChatAssistantLite"
$exe = Join-Path $appDir "ChatAssistantLite.exe"
$internalDir = Join-Path $appDir "_internal"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "PyInstaller reported success but expected output was not found: $exe"
}

# PyInstaller resolves these through the build host's PATH, which can point to
# unrelated Codex/Poppler compatibility DLLs. Windows supplies the API sets and
# ICU used by Qt; shipping the host copies shadows the system versions and can
# prevent QtWidgets from loading.
Get-ChildItem -LiteralPath $internalDir -Filter "api-ms-win-*.dll" -File | Remove-Item -Force
Get-ChildItem -LiteralPath $internalDir -Filter "icu*.dll" -File | Remove-Item -Force

# T11: write and verify the build manifest against the source tree shipped here.
$buildInfo = Join-Path $appDir "build_info.json"
$previousPolicy = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $python (Join-Path $PSScriptRoot "tools\write_build_info.py") --output $buildInfo --source-root $PSScriptRoot
$writeInfoExit = $LASTEXITCODE
& $python (Join-Path $PSScriptRoot "tools\verify_build_info.py") --info $buildInfo --source-root $PSScriptRoot
$verifyInfoExit = $LASTEXITCODE
$ErrorActionPreference = $previousPolicy
if ($writeInfoExit -ne 0) { throw "build_info.json generation failed (exit $writeInfoExit)" }
if ($verifyInfoExit -ne 0) { throw "build_info.json verification failed (exit $verifyInfoExit)" }

$env:QT_QPA_PLATFORM = "offscreen"
$smoke = [System.Diagnostics.ProcessStartInfo]::new()
$smoke.FileName = $exe
$smoke.Arguments = "--smoke"
$smoke.UseShellExecute = $false
$smoke.CreateNoWindow = $true
$smoke.RedirectStandardOutput = $true
$smoke.RedirectStandardError = $true
$smoke.Environment["QT_QPA_PLATFORM"] = "offscreen"
$process = [System.Diagnostics.Process]::Start($smoke)
if (-not $process.WaitForExit(90000)) {
    $process.Kill()
    throw "Packaged application did not exit from smoke mode within 90 seconds."
}
$stderr = $process.StandardError.ReadToEnd()
$stdout = $process.StandardOutput.ReadToEnd()
if ($process.ExitCode -ne 0) {
    throw "Packaged application smoke failed (exit $($process.ExitCode)).`n$stdout`n$stderr"
}
if ($stderr) { Write-Warning $stderr }

Write-Output "Built and smoke-tested portable directory: $appDir"
Write-Output "Launch: $exe"
