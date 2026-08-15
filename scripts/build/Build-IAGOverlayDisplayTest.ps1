param(
    [string]$Python = "python",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptRoot "..\.."))
$BuildBase = Join-Path $ProjectRoot "build\overlay-display-test"
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $BuildBase "dist"
}
$WorkRoot = Join-Path $BuildBase "work"
$ResolvedBuildBase = [System.IO.Path]::GetFullPath($BuildBase).TrimEnd('\')

function Remove-IAGDisplayTestTree {
    param([Parameter(Mandatory = $true)][string]$Path)
    $ResolvedTarget = [System.IO.Path]::GetFullPath($Path)
    if (-not $ResolvedTarget.StartsWith(
        $ResolvedBuildBase + '\',
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to clean outside the display-test build directory: $ResolvedTarget"
    }
    if (Test-Path -LiteralPath $ResolvedTarget) {
        Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
    }
}

Remove-IAGDisplayTestTree -Path $OutputRoot
Remove-IAGDisplayTestTree -Path $WorkRoot

& $Python -m pip install --disable-pip-version-check `
    "pyinstaller>=6.12,<7" `
    "PySide6>=6.8,<7"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install display-test build dependencies."
}

& $Python -m PyInstaller --noconfirm --clean `
    --distpath $OutputRoot `
    --workpath $WorkRoot `
    (Join-Path $ProjectRoot "apps\game_overlay\IAGOverlayDisplayTest.spec")
if ($LASTEXITCODE -ne 0) {
    throw "Overlay display-test PyInstaller build failed."
}

$DisplayOutput = Join-Path $OutputRoot "IAGOverlayDisplayTest"
Copy-Item -LiteralPath (Join-Path $ProjectRoot "LICENSE") -Destination $DisplayOutput
Copy-Item -LiteralPath (Join-Path $ProjectRoot "NOTICE") -Destination $DisplayOutput
Copy-Item -LiteralPath (Join-Path $ProjectRoot "ORIGIN.md") -Destination $DisplayOutput
Copy-Item -LiteralPath `
    (Join-Path $ProjectRoot "apps\game_overlay\DISPLAY_TEST_README.md") `
    -Destination (Join-Path $DisplayOutput "README.md")

$Archive = Join-Path $BuildBase "IAGOverlayDisplayTest-windows-x64.zip"
if (Test-Path -LiteralPath $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
Compress-Archive -LiteralPath $DisplayOutput -DestinationPath $Archive
$Hash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash

Write-Host "Display-test GUI created: $DisplayOutput"
Write-Host "Archive: $Archive"
Write-Host "SHA-256: $Hash"
