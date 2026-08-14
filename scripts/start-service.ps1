param([string]$ConfigPath = 'D:\elpr\config.json')

$ErrorActionPreference = 'Stop'
$listener = Get-NetTCPConnection -LocalPort 27119 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host "Service is already listening (PID $($listener[0].OwningProcess))."
    exit 0
}
$projectPath = Split-Path -Parent $PSScriptRoot
$stdout = Join-Path $projectPath 'data\service.stdout.log'
$stderr = Join-Path $projectPath 'data\service.stderr.log'
$process = Start-Process -FilePath 'python.exe' `
    -ArgumentList (Join-Path $projectPath 'service.py'),'--config',$ConfigPath `
    -WorkingDirectory $projectPath `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru
Write-Host "Service started (PID $($process.Id))."

