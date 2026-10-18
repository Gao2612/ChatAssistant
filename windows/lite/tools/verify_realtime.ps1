# T12 helper: collect read-only environment and package evidence for the acceptance matrix.
# This script never clicks WeChat, never sends messages and never reads chat content.
# Manual scenarios (scan login, IME, popups) are still recorded by the human in the matrix.
param(
    [string]$Setup = "",
    [string]$PortableExe = "",
    [string]$Output = "",
    [switch]$RunSelfTest
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Output) {
    $Output = Join-Path $root "test-output\realtime-evidence.json"
}
New-Item -ItemType Directory -Path (Split-Path -Parent $Output) -Force | Out-Null
$evidence = [ordered]@{
    collected_at = (Get-Date).ToString("s")
    windows = [System.Environment]::OSVersion.VersionString
    user = $env:USERNAME
    setup = $null
    setup_sha256 = $null
    portable_exe = $null
    portable_build_info = $null
    self_test = $null
    wechat_processes = @()
    wechat_windows = @()
    notes = @("只读证据采集；不包含聊天内容、密钥或账号令牌。")
}

function Get-Sha256([string]$Path) {
    if (Test-Path -LiteralPath $Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }
    return $null
}

if ($Setup) {
    $setupPath = (Resolve-Path -LiteralPath $Setup).Path
    $evidence.setup = $setupPath
    $evidence.setup_sha256 = Get-Sha256 $setupPath
}

if ($PortableExe) {
    $exePath = (Resolve-Path -LiteralPath $PortableExe).Path
    $evidence.portable_exe = $exePath
    $manifest = Join-Path (Split-Path -Parent $exePath) "build_info.json"
    if (Test-Path -LiteralPath $manifest) {
        $evidence.portable_build_info = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    if ($RunSelfTest) {
        $dataDir = Join-Path $env:TEMP ("chatassistant-t12-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
        $previous = $ErrorActionPreference
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $exePath
        $psi.Arguments = "--self-test --data-dir `"$dataDir`""
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $process = [System.Diagnostics.Process]::Start($psi)
        if (-not $process.WaitForExit(180000)) { $process.Kill(); $evidence.self_test = @{ exit = -1; note = "timeout" } }
        else {
            $stdout = $process.StandardOutput.ReadToEnd()
            $stderr = $process.StandardError.ReadToEnd()
            $evidence.self_test = @{ exit = $process.ExitCode; stdout = $stdout; stderr = $stderr }
        }
        Remove-Item -LiteralPath $dataDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Read-only WeChat process/window facts (no screen capture, no chat text).
$wechatExe = "C:\Program Files\Tencent\Weixin\Weixin.exe"
foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
    try {
        if ($process.Path -and ($process.Path -ieq $wechatExe)) {
            $evidence.wechat_processes += [ordered]@{
                id = $process.Id
                start_time = $process.StartTime.ToString("s")
                path = $process.Path
            }
        }
    } catch { }
}

$evidence | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Output -Encoding UTF8
Write-Output "Evidence written: $Output"
Write-Output "Reminder: fill the manual scenarios in docs/windows/WINDOWS_LITE_ACCEPTANCE_MATRIX.md; this helper never proves them."
