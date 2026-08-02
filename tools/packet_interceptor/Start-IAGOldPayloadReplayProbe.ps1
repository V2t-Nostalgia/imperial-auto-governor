param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$LocalIP,
    [int]$LocalPort = 64821,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RemoteIP,
    [string]$SourceLog = "",
    [int]$TraceIndex = 515,
    [ValidateRange(30, 900)]
    [int]$TimeoutSeconds = 120,
    [ValidateRange(1, 30)]
    [int]$ObserveSeconds = 5,
    [ValidateRange(25, 1500)]
    [int]$CarrierLength = 25
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

$pythonScript = Join-Path $PSScriptRoot "iag_old_payload_replay_probe.py"
$readyPath = Join-Path $PSScriptRoot "active_replay_probe.json"
$logDirectory = Join-Path $PSScriptRoot "logs"
$logPath = Join-Path $logDirectory (
    "replay_probe_{0}.jsonl" -f [DateTime]::Now.ToString("yyyyMMdd_HHmmss")
)
if (-not $SourceLog) {
    $SourceLog = Join-Path $logDirectory "interceptor_20260614_191750.jsonl"
}

if (Test-Path -LiteralPath $readyPath) {
    throw "A replay probe is already registered: $readyPath"
}

if (-not (Test-IsAdministrator)) {
    $forwarded = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-LocalIP", $LocalIP,
        "-LocalPort", $LocalPort,
        "-RemoteIP", $RemoteIP,
        "-SourceLog", "`"$SourceLog`"",
        "-TraceIndex", $TraceIndex,
        "-TimeoutSeconds", $TimeoutSeconds,
        "-ObserveSeconds", $ObserveSeconds,
        "-CarrierLength", $CarrierLength
    )
    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $forwarded `
        -Verb RunAs `
        -WindowStyle Normal
    return
}

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
& python $pythonScript `
    --local-ip $LocalIP `
    --local-port $LocalPort `
    --remote-ip $RemoteIP `
    --source-log $SourceLog `
    --trace-index $TraceIndex `
    --timeout-seconds $TimeoutSeconds `
    --observe-seconds $ObserveSeconds `
    --carrier-length $CarrierLength `
    --log $logPath `
    --ready-file $readyPath

$exitCode = $LASTEXITCODE
Write-Host "Replay probe exited with code $exitCode. Log: $logPath"
Write-Host "Press Enter to close."
[void](Read-Host)
exit $exitCode
