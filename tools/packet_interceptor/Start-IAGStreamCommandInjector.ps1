param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RemoteIP,
    [int]$RemotePort = 4380,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$LocalIP,
    [int]$LocalPort = 56144,
    [int]$PlanetID = 3,
    [int]$BuildQueueID = 6,
    [int]$Placement = 0,
    [string]$BuildingID = "building_research_lab_1",
    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 120,
    [ValidateRange(60, 86400)]
    [int]$SessionSeconds = 7200,
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

$pythonScript = Join-Path $PSScriptRoot "iag_stream_command_injector.py"
$activePath = Join-Path $PSScriptRoot "active_stream_injector.json"
$logDirectory = Join-Path $PSScriptRoot "logs"
$logPath = Join-Path $logDirectory (
    "stream_injector_{0}.jsonl" -f [DateTime]::Now.ToString("yyyyMMdd_HHmmss")
)

if (-not (Test-IsAdministrator)) {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-RemoteIP", $RemoteIP,
        "-RemotePort", $RemotePort,
        "-LocalIP", $LocalIP,
        "-LocalPort", $LocalPort,
        "-PlanetID", $PlanetID,
        "-BuildQueueID", $BuildQueueID,
        "-Placement", $Placement,
        "-BuildingID", $BuildingID,
        "-TimeoutSeconds", $TimeoutSeconds,
        "-SessionSeconds", $SessionSeconds
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
    "--remote-port", $RemotePort,
    "--local-ip", $LocalIP,
    "--local-port", $LocalPort,
    "--planet-id", $PlanetID,
    "--build-queue-id", $BuildQueueID,
    "--placement", $Placement,
    "--building-id", $BuildingID,
    "--timeout-seconds", $TimeoutSeconds,
    "--session-seconds", $SessionSeconds,
    "--log", $logPath,
    "--ready-file", $activePath
)
if ($DryRun) {
    $arguments += "--dry-run"
}

& python @arguments
exit $LASTEXITCODE
