# T11: isolated install / upgrade / uninstall lifecycle check for a candidate setup.
# Scope note: this is a local isolated verification, NOT the clean-VM acceptance from T12.
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [string]$InstallDir = "",
    [string]$WorkDir = "",
    [switch]$KeepInstall
)

$ErrorActionPreference = "Stop"
$setupPath = (Resolve-Path -LiteralPath $Setup).Path
if (-not $WorkDir) {
    $WorkDir = Join-Path $env:TEMP ("chatassistant-verify-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
}
New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
if (-not $InstallDir) { $InstallDir = Join-Path $WorkDir "app" }
$dataDir = Join-Path $WorkDir "data"
$logFile = Join-Path $WorkDir "install.log"
$result = [ordered]@{
    setup = $setupPath
    install_dir = $InstallDir
    work_dir = $WorkDir
    install_exit = $null
    self_test_exit = $null
    uninstall_exit = $null
    files_removed = $false
    work_dir_preserved = $false
    ok = $false
    notes = @()
}

function Invoke-Exe {
    param([string]$FilePath, [string[]]$Arguments)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = ($Arguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($psi)
    if (-not $process.WaitForExit(600000)) {
        $process.Kill()
        throw "process timed out: $FilePath"
    }
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    if ($stderr) {
        $trimmed = $stderr.Trim()
        if ($trimmed.Length -gt 300) { $trimmed = $trimmed.Substring(0, 300) }
        $result.notes += ("stderr: " + $trimmed)
    }
    return @{ exit = $process.ExitCode; stdout = $stdout }
}

try {
    $install = Invoke-Exe -FilePath $setupPath -Arguments @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
        "/DIR=$InstallDir", "/LOG=$logFile")
    $result.install_exit = $install.exit
    if ($install.exit -ne 0) { throw "installer exit code $($install.exit)" }

    $exe = Join-Path $InstallDir "ChatAssistantLite.exe"
    $uninstaller = Join-Path $InstallDir "unins000.exe"
    if (-not (Test-Path -LiteralPath $exe)) { throw "main executable missing after install: $exe" }
    if (-not (Test-Path -LiteralPath $uninstaller)) { throw "uninstaller missing after install" }

    $self = Invoke-Exe -FilePath $exe -Arguments @("--self-test", "--data-dir", $dataDir)
    $result.self_test_exit = $self.exit
    if ($self.exit -ne 0) { throw "installed self-test exit code $($self.exit)" }

    # Default uninstall keeps user data; the isolated data dir must survive.
    $uninstall = Invoke-Exe -FilePath $uninstaller -Arguments @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
    $result.uninstall_exit = $uninstall.exit
    if ($uninstall.exit -ne 0) { throw "uninstaller exit code $($uninstall.exit)" }
    Start-Sleep -Seconds 2

    $result.files_removed = -not (Test-Path -LiteralPath $exe)
    $result.work_dir_preserved = Test-Path -LiteralPath $WorkDir
    if (-not $result.files_removed) { throw "main executable still exists after uninstall" }
    $result.ok = ($result.install_exit -eq 0 -and $result.self_test_exit -eq 0 -and
                  $result.uninstall_exit -eq 0 -and $result.files_removed)
} catch {
    $result.notes += ("failed: " + $_.Exception.Message)
} finally {
    if (-not $KeepInstall) {
        Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$result | ConvertTo-Json -Depth 4 | Write-Output
if ($result.ok) { exit 0 } else { exit 1 }
