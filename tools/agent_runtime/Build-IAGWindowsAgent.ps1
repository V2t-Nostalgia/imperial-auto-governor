param(
    [string]$Python = "python",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $ScriptRoot "windows_dist"
}
$BuildRoot = Join-Path $ScriptRoot "windows_build"
$ResolvedScriptRoot = [System.IO.Path]::GetFullPath($ScriptRoot).TrimEnd('\')
foreach ($Target in @($OutputRoot, $BuildRoot)) {
    $ResolvedTarget = [System.IO.Path]::GetFullPath($Target)
    if (-not $ResolvedTarget.StartsWith($ResolvedScriptRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean build path outside agent_runtime: $ResolvedTarget"
    }
    if (Test-Path -LiteralPath $ResolvedTarget) {
        Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
    }
}

& $Python -m pip install --disable-pip-version-check -r (Join-Path $ScriptRoot "requirements-windows.txt")
if ($LASTEXITCODE -ne 0) { throw "Failed to install Windows build dependencies." }

& $Python -m PyInstaller --noconfirm --clean `
    --distpath $OutputRoot `
    --workpath $BuildRoot `
    (Join-Path $ScriptRoot "IAGWindowsAgent.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

Write-Host "Windows agent created: $(Join-Path $OutputRoot 'IAGWindowsAgent')"
