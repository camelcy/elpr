$ErrorActionPreference = 'Stop'
$listener = Get-NetTCPConnection -LocalPort 27119 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $listener) {
    Write-Host 'Service is not running.'
    exit 0
}
$process = Get-Process -Id $listener.OwningProcess -ErrorAction Stop
if ($process.ProcessName -notlike 'python*') {
    throw "Port 27119 belongs to a non-Python process (PID $($process.Id)); refusing to stop it."
}
Stop-Process -Id $process.Id
Write-Host "Service stopped (PID $($process.Id))."

