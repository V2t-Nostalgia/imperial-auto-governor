param(
    [string]$SavePath,
    [int]$Owner = 0,
    [string]$OutputPath = (
        Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) `
            "artifacts\save_analysis\planet_profiles.json"
    )
)

$ErrorActionPreference = "Stop"

if (-not $SavePath) {
    $saveRoot = Join-Path $env:USERPROFILE `
        "Documents\Paradox Interactive\Stellaris\save games"
    $latest = Get-ChildItem $saveRoot -Recurse -Filter *.sav -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $latest) {
        throw "No Stellaris save was found under $saveRoot"
    }
    $SavePath = $latest.FullName
}

$extractor = Join-Path $PSScriptRoot "extract_planet_profiles.py"
& python $extractor $SavePath --owner $Owner --output $OutputPath
if ($LASTEXITCODE -ne 0) {
    throw "Planet profile extraction failed."
}

Write-Host "Planet profiles exported to: $OutputPath"
