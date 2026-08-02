param(
    [string]$RemoteIP,
    [int]$RemoteOutboundPort,
    [int]$RemoteInboundPort,
    [string]$LocalIP,
    [int]$LocalPort,
    [string]$SourceZone = "",
    [string]$TargetZone = "zone_trade",
    [ValidateRange(30, 1800)]
    [int]$TimeoutSeconds = 300,
    [ValidateRange(5, 60)]
    [int]$ObserveSeconds = 15,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

$pythonScript = Join-Path $PSScriptRoot "iag_zone_to_zone_replacer.py"
$readyPath = Join-Path $PSScriptRoot "active_zone_to_zone_replacer.json"
$logDirectory = Join-Path $PSScriptRoot "logs"
$logPath = Join-Path $logDirectory (
    "zone_to_zone_replacer_{0}.jsonl" -f [DateTime]::Now.ToString("yyyyMMdd_HHmmss")
)

if (-not $RemoteIP -or -not $LocalIP -or -not $LocalPort) {
    throw "RemoteIP, LocalIP, and LocalPort are required."
}
if (-not (Test-Path -LiteralPath $pythonScript)) {
    throw "Zone-to-zone replacer script not found: $pythonScript"
}
if (Test-Path -LiteralPath $readyPath) {
    throw "A zone-to-zone replacer is already registered: $readyPath"
}

if (-not (Test-IsAdministrator)) {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-RemoteIP", $RemoteIP,
        "-RemoteOutboundPort", $RemoteOutboundPort,
        "-RemoteInboundPort", $RemoteInboundPort,
        "-LocalIP", $LocalIP,
        "-LocalPort", $LocalPort,
        "-TargetZone", $TargetZone,
        "-TimeoutSeconds", $TimeoutSeconds,
        "-ObserveSeconds", $ObserveSeconds
    )
    if ($SourceZone) {
        $arguments += @("-SourceZone", $SourceZone)
    }
    if ($DryRun) {
        $arguments += "-DryRun"
    }
    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $arguments `
        -Verb RunAs `
        -WindowStyle Hidden
    return
}

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$arguments = @(
    $pythonScript,
    "--remote-ip", $RemoteIP,
    "--remote-outbound-port", $RemoteOutboundPort,
    "--remote-inbound-port", $RemoteInboundPort,
    "--local-ip", $LocalIP,
    "--local-port", $LocalPort,
    "--target-zone", $TargetZone,
    "--timeout-seconds", $TimeoutSeconds,
    "--observe-seconds", $ObserveSeconds,
    "--log", $logPath,
    "--ready-file", $readyPath
)
if ($SourceZone) {
    $arguments += @("--source-zone", $SourceZone)
}
if ($DryRun) {
    $arguments += "--dry-run"
}

& python @arguments
exit $LASTEXITCODE
