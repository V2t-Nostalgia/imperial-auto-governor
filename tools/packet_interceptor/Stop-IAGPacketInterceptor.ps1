param()

$ErrorActionPreference = "Stop"
$activePath = Join-Path $PSScriptRoot "active_interceptor.json"

if (-not (Test-Path -LiteralPath $activePath)) {
    Write-Host "No active IAG interceptor registration was found."
    exit 0
}

$active = Get-Content -LiteralPath $activePath -Raw | ConvertFrom-Json
$process = Get-Process -Id $active.pid -ErrorAction SilentlyContinue
if ($process) {
    Stop-Process -Id $process.Id -Force
    Write-Host "Stopped interceptor process $($process.Id)."
}
else {
    Write-Host "The registered interceptor process is no longer running."
}

Remove-Item -LiteralPath $activePath -Force -ErrorAction SilentlyContinue
