param(
    [string]$Python = "python",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptRoot "..\.."))
$BuildBase = Join-Path $ProjectRoot "build\host-bridge"
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $BuildBase "dist"
}
$BuildRoot = Join-Path $BuildBase "work"
$ResolvedBuildBase = [System.IO.Path]::GetFullPath($BuildBase).TrimEnd('\')

function Remove-IAGBuildTree {
    param([Parameter(Mandatory = $true)][string]$Path)
    for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        }
        catch {
            if ($Attempt -eq 5) { throw }
            Start-Sleep -Milliseconds (200 * $Attempt)
        }
        if (-not (Test-Path -LiteralPath $Path)) { return }
    }
}

foreach ($Target in @($OutputRoot, $BuildRoot)) {
    $ResolvedTarget = [System.IO.Path]::GetFullPath($Target)
    if (-not $ResolvedTarget.StartsWith($ResolvedBuildBase + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the project build directory: $ResolvedTarget"
    }
    if (Test-Path -LiteralPath $ResolvedTarget) {
        Remove-IAGBuildTree -Path $ResolvedTarget
    }
}

& $Python -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot "requirements.txt") "pyinstaller>=6.12,<7" "PySide6>=6.8,<7"
if ($LASTEXITCODE -ne 0) { throw "Failed to install Host Bridge build dependencies." }

& $Python -m PyInstaller --noconfirm --clean `
    --distpath $OutputRoot `
    --workpath $BuildRoot `
    (Join-Path $ProjectRoot "apps\host_bridge\IAGSaveUploaderGUI.spec")
if ($LASTEXITCODE -ne 0) { throw "Host Bridge PyInstaller build failed." }

$OverlayBuildRoot = Join-Path $BuildRoot "overlay"
& $Python -m PyInstaller --noconfirm --clean `
    --distpath $OutputRoot `
    --workpath $OverlayBuildRoot `
    (Join-Path $ProjectRoot "apps\game_overlay\IAGOverlay.spec")
if ($LASTEXITCODE -ne 0) { throw "Game Overlay PyInstaller build failed." }

$HostBridgeOutput = Join-Path $OutputRoot "IAGHostBridgeGUI"
$OverlayOutput = Join-Path $OutputRoot "IAGOverlay"
$BundledOverlay = Join-Path $HostBridgeOutput "overlay\IAGOverlay"
New-Item -ItemType Directory -Force (Split-Path -Parent $BundledOverlay) | Out-Null
Move-Item -LiteralPath $OverlayOutput -Destination $BundledOverlay

Write-Host "Windows Host Bridge created: $(Join-Path $OutputRoot 'IAGHostBridgeGUI')"
