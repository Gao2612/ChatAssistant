# Run using Windows PowerShell 5.1, not PowerShell 7 (WinExe output is unsupported there).
$ErrorActionPreference = 'Stop'
$probeCode = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'WeChatProbe.cs'))
$probeOutput = Join-Path $PSScriptRoot 'WeChatProbe-v2.exe'
if (Test-Path -LiteralPath $probeOutput) {
    throw 'Output already exists. Preserve or rename the previous binary before rebuilding.'
}
Add-Type -TypeDefinition $probeCode -ReferencedAssemblies System.Windows.Forms,System.Drawing -OutputAssembly $probeOutput -OutputType WindowsApplication
Write-Output $probeOutput
