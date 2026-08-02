param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RemoteIP,
    [int]$RemotePort = 46377,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$LocalIP,
    [int]$LocalPort = 61552,
    [ValidateRange(30, 3600)]
    [int]$TimeoutSeconds = 900,
    [switch]$ObserveOnly,
    [switch]$Discover,
    [switch]$TraceFlow,
    [int]$TraceLocalPort = 0,
    [switch]$TraceAllUDP,
    [string]$DiscoverIDs = "building_research_lab_1",
    [ValidateRange(1, 100)]
    [int]$DiscoverCount = 1,
    [switch]$DiscoverInbound,
    [switch]$DiscoverUnique,
    [string]$SourceID = "building_research_lab_1",
    [string]$TargetID = "building_bureaucratic_1",
    [ValidateSet("building", "zone")]
    [string]$RecordKind = "building",
    [switch]$AllowVariableLength,
    [switch]$PadVariableLength,
    [int]$SourceEA3F = 616,
    [int]$SourceFC29 = 308,
    [int]$SourcePlacement = 23,
    [int]$TargetEA3F = 616,
    [int]$TargetFC29 = 308,
    [int]$TargetPlacement = 23
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

$pythonScript = Join-Path $PSScriptRoot "iag_packet_interceptor.py"
$activePath = Join-Path $PSScriptRoot "active_interceptor.json"
$logDirectory = Join-Path $PSScriptRoot "logs"
$logPath = Join-Path $logDirectory (
    "interceptor_{0}.jsonl" -f [DateTime]::Now.ToString("yyyyMMdd_HHmmss")
)

if (-not (Test-Path -LiteralPath $pythonScript)) {
    throw "Interceptor script not found: $pythonScript"
}
if (Test-Path -LiteralPath $activePath) {
    throw "An interceptor is already registered: $activePath"
}
if ($AllowVariableLength) {
    throw (
        "Live variable-length rewriting is disabled because it still stalls " +
        "multiplayer synchronization. Use passive capture or offline replay."
    )
}

if (-not (Test-IsAdministrator)) {
    $forwarded = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-RemoteIP", $RemoteIP,
        "-RemotePort", $RemotePort,
        "-LocalIP", $LocalIP,
        "-LocalPort", $LocalPort,
        "-TimeoutSeconds", $TimeoutSeconds,
        "-SourceID", $SourceID,
        "-TargetID", $TargetID,
        "-RecordKind", $RecordKind,
        "-SourceEA3F", $SourceEA3F,
        "-SourceFC29", $SourceFC29,
        "-SourcePlacement", $SourcePlacement,
        "-TargetEA3F", $TargetEA3F,
        "-TargetFC29", $TargetFC29,
        "-TargetPlacement", $TargetPlacement
    )
    if ($ObserveOnly) {
        $forwarded += "-ObserveOnly"
    }
    if ($TraceFlow) {
        $forwarded += "-TraceFlow"
        if ($TraceAllUDP) {
            $forwarded += "-TraceAllUDP"
        }
        if ($TraceLocalPort -gt 0) {
            $forwarded += @("-TraceLocalPort", $TraceLocalPort)
        }
    }
    if ($AllowVariableLength) {
        $forwarded += "-AllowVariableLength"
    }
    if ($PadVariableLength) {
        $forwarded += "-PadVariableLength"
    }
    if ($Discover) {
        $forwarded += "-Discover"
        $forwarded += @("-DiscoverCount", $DiscoverCount)
        $forwarded += @("-DiscoverIDs", $DiscoverIDs)
        if ($DiscoverInbound) {
            $forwarded += "-DiscoverInbound"
        }
        if ($DiscoverUnique) {
            $forwarded += "-DiscoverUnique"
        }
    }
    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $forwarded `
        -Verb RunAs `
        -WindowStyle Normal
    return
}

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

$arguments = @(
    $pythonScript,
    "--remote-ip", $RemoteIP,
    "--remote-port", $RemotePort,
    "--local-ip", $LocalIP,
    "--local-port", $LocalPort,
    "--timeout-seconds", $TimeoutSeconds,
    "--log", $logPath,
    "--ready-file", $activePath,
    "--source-id", $SourceID,
    "--target-id", $TargetID,
    "--record-kind", $RecordKind,
    "--source-ea3f", $SourceEA3F,
    "--source-fc29", $SourceFC29,
    "--source-placement", $SourcePlacement,
    "--target-ea3f", $TargetEA3F,
    "--target-fc29", $TargetFC29,
    "--target-placement", $TargetPlacement
)
if ($ObserveOnly) {
    $arguments += "--observe-only"
}
if ($TraceFlow) {
    $arguments += "--trace-flow"
    if ($TraceAllUDP) {
        $arguments += "--trace-all-udp"
    }
    if ($TraceLocalPort -gt 0) {
        $arguments += @("--trace-local-port", $TraceLocalPort)
    }
}
if ($AllowVariableLength) {
    $arguments += "--allow-variable-length"
}
if ($PadVariableLength) {
    $arguments += "--pad-variable-length"
}
if ($Discover) {
    $arguments += "--discover"
    $arguments += @("--discover-count", $DiscoverCount)
    if ($DiscoverInbound) {
        $arguments += "--discover-inbound"
    }
    if ($DiscoverUnique) {
        $arguments += "--discover-unique"
    }
    foreach ($id in ($DiscoverIDs -split ",")) {
        $id = $id.Trim()
        if (-not $id) {
            continue
        }
        $arguments += @("--discover-id", $id)
    }
}

Write-Host "Starting IAG one-shot packet interceptor..."
Write-Host "Log: $logPath"
Write-Host "Close this window or run Stop-IAGPacketInterceptor.ps1 to abort."
& python @arguments
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "The exact TEST1 command was matched. See the JSONL log."
}
elseif ($exitCode -eq 2) {
    Write-Warning "No exact TEST1 command was matched before timeout/stop."
}
else {
    Write-Warning "Interceptor exited with code $exitCode."
}
Write-Host "Press Enter to close."
[void](Read-Host)
exit $exitCode
