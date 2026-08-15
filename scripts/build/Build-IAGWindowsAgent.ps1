param(
    [string]$Python = "python",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptRoot "..\.."))
$BuildBase = Join-Path $ProjectRoot "build\windows-agent"
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $BuildBase "dist"
}
$BuildRoot = Join-Path $BuildBase "work"
$ResolvedBuildBase = [System.IO.Path]::GetFullPath($BuildBase).TrimEnd('\')
foreach ($Target in @($OutputRoot, $BuildRoot)) {
    $ResolvedTarget = [System.IO.Path]::GetFullPath($Target)
    if (-not $ResolvedTarget.StartsWith($ResolvedBuildBase + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the project build directory: $ResolvedTarget"
    }
    if (Test-Path -LiteralPath $ResolvedTarget) {
        Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
    }
}

& $Python -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot "requirements.txt") "pyinstaller>=6.12,<7"
if ($LASTEXITCODE -ne 0) { throw "Failed to install Windows build dependencies." }

& $Python -m PyInstaller --noconfirm --clean `
    --distpath $OutputRoot `
    --workpath $BuildRoot `
    (Join-Path $ProjectRoot "apps\control_center\IAGWindowsAgent.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

Write-Host "Windows agent created: $(Join-Path $OutputRoot 'IAGWindowsAgent')"
