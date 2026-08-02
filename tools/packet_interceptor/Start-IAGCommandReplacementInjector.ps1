param(
    [string]$RemoteIP,
    [int]$RemoteOutboundPort,
    [int]$RemoteInboundPort,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$LocalIP,
    [int]$LocalPort,
    [int]$PlanetID = 3,
    [int]$BuildQueueID = 6,
    [int]$Placement = 0,
    [string]$BuildingID = "building_research_lab_1",
    [ValidateRange(30, 1800)]
    [int]$TimeoutSeconds = 300,
    [ValidateRange(5, 60)]
    [int]$ObserveSeconds = 15,
    [switch]$DryRun,
    [switch]$AllowAnyCommand
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

$pythonScript = Join-Path $PSScriptRoot "iag_command_replacement_injector.py"
$readyPath = Join-Path $PSScriptRoot "active_command_replacer.json"
$logDirectory = Join-Path $PSScriptRoot "logs"
$logPath = Join-Path $logDirectory (
    "command_replacer_{0}.jsonl" -f [DateTime]::Now.ToString("yyyyMMdd_HHmmss")
)

if (
    -not $RemoteIP -or
    -not $RemoteOutboundPort -or
    -not $RemoteInboundPort -or
    -not $LocalPort
) {
    throw (
        "RemoteIP, RemoteOutboundPort, RemoteInboundPort, and LocalPort " +
        "are required."
    )
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
        "-PlanetID", $PlanetID,
        "-BuildQueueID", $BuildQueueID,
        "-Placement", $Placement,
        "-BuildingID", $BuildingID,
        "-TimeoutSeconds", $TimeoutSeconds,
        "-ObserveSeconds", $ObserveSeconds
    )
    if ($DryRun) {
        $arguments += "-DryRun"
    }
    if ($AllowAnyCommand) {
        $arguments += "-AllowAnyCommand"
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
    "--planet-id", $PlanetID,
    "--build-queue-id", $BuildQueueID,
    "--placement", $Placement,
    "--building-id", $BuildingID,
    "--timeout-seconds", $TimeoutSeconds,
    "--observe-seconds", $ObserveSeconds,
    "--log", $logPath,
    "--ready-file", $readyPath
)
if ($DryRun) {
    $arguments += "--dry-run"
}
if ($AllowAnyCommand) {
    $arguments += "--allow-any-command"
}

& python @arguments
exit $LASTEXITCODE
