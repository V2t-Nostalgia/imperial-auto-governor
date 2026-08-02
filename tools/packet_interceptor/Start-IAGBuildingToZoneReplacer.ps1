param(
    [string]$RemoteIP,
    [int]$RemoteOutboundPort,
    [int]$RemoteInboundPort,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$LocalIP,
    [int]$LocalPort,
    [int]$Context822c = 0,
    [int]$BuildQueueID = 6,
    [int]$ColonyID = 0,
    [int]$DistrictID = 0,
    [int]$SlotSelector = 1,
    [string]$ZoneType = "zone_trade",
    [ValidateSet("inbound", "outbound", "both")]
    [string]$RewriteDirection = "inbound",
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

$pythonScript = Join-Path $PSScriptRoot "iag_building_to_zone_replacer.py"
$readyPath = Join-Path $PSScriptRoot "active_building_to_zone_replacer.json"
$logDirectory = Join-Path $PSScriptRoot "logs"
$logPath = Join-Path $logDirectory (
    "building_to_zone_replacer_{0}.jsonl" -f [DateTime]::Now.ToString("yyyyMMdd_HHmmss")
)

if (
    -not $RemoteIP -or
    -not $LocalPort
) {
    throw (
        "RemoteIP and LocalPort are required. Use port 0 to wildcard " +
        "RemoteOutboundPort or RemoteInboundPort."
    )
}

if (-not (Test-Path -LiteralPath $pythonScript)) {
    throw "Building-to-zone replacer script not found: $pythonScript"
}
if (Test-Path -LiteralPath $readyPath) {
    throw "A building-to-zone replacer is already registered: $readyPath"
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
        "-Context822c", $Context822c,
        "-BuildQueueID", $BuildQueueID,
        "-ColonyID", $ColonyID,
        "-DistrictID", $DistrictID,
        "-SlotSelector", $SlotSelector,
        "-ZoneType", $ZoneType,
        "-RewriteDirection", $RewriteDirection,
        "-TimeoutSeconds", $TimeoutSeconds,
        "-ObserveSeconds", $ObserveSeconds
    )
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
    "--context-822c", $Context822c,
    "--build-queue-id", $BuildQueueID,
    "--colony-id", $ColonyID,
    "--district-id", $DistrictID,
    "--slot-selector", $SlotSelector,
    "--zone-type", $ZoneType,
    "--rewrite-direction", $RewriteDirection,
    "--timeout-seconds", $TimeoutSeconds,
    "--observe-seconds", $ObserveSeconds,
    "--log", $logPath,
    "--ready-file", $readyPath
)
if ($DryRun) {
    $arguments += "--dry-run"
}

& python @arguments
exit $LASTEXITCODE
