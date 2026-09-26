param(
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptRoot "..\.."))
$ServiceRoot = Join-Path $ProjectRoot "services\stellaris_native_runtime"
$BuildBase = Join-Path $ProjectRoot "build\stellaris_native_runtime\windows-x64"
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $BuildBase "dist"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$ObjectRoot = Join-Path $BuildBase "obj"
$ResolvedBuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build")).TrimEnd('\')
foreach ($Target in @($OutputRoot, $ObjectRoot)) {
    $ResolvedTarget = [System.IO.Path]::GetFullPath($Target)
    if (-not $ResolvedTarget.StartsWith($ResolvedBuildRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the project build directory: $ResolvedTarget"
    }
    if (Test-Path -LiteralPath $ResolvedTarget) {
        Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
    }
    New-Item -ItemType Directory -Path $ResolvedTarget | Out-Null
}

$VsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path -LiteralPath $VsWhere)) {
    throw "Visual Studio Build Tools were not found (vswhere.exe is missing)."
}
$VsRoot = (& $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath).Trim()
if ([string]::IsNullOrWhiteSpace($VsRoot)) {
    throw "Visual Studio C++ x64 build tools were not found."
}
$VcVars = Join-Path $VsRoot "VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path -LiteralPath $VcVars)) {
    throw "vcvars64.bat was not found under $VsRoot."
}

function Quote-CmdArgument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Invoke-DeveloperCommand([string[]]$Arguments) {
    $Compiler = ($Arguments | ForEach-Object { Quote-CmdArgument $_ }) -join ' '
    $Command = "call $(Quote-CmdArgument $VcVars) >nul && cl.exe $Compiler"
    & $env:COMSPEC /d /s /c $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Native runtime compilation failed with exit code $LASTEXITCODE."
    }
}

$IncludeRoot = Join-Path $ServiceRoot "include"
$ActionsRoot = Join-Path $ServiceRoot "actions"
$ActionSources = @(Get-ChildItem -LiteralPath $ActionsRoot -Filter "*.cpp" -File | Sort-Object Name | ForEach-Object { $_.FullName })
if ($ActionSources.Count -eq 0) {
    throw "No native runtime actions were discovered."
}
$Common = @(
    "/nologo",
    "/std:c++20",
    "/W4",
    "/WX",
    "/EHsc",
    "/permissive-",
    "/O2",
    "/MD",
    "/DWIN32_LEAN_AND_MEAN",
    "/DNOMINMAX",
    "/I$IncludeRoot",
    "/I$ActionsRoot"
)
$RuntimeSources = @(
    (Join-Path $ServiceRoot "runtime_windows.cpp"),
    (Join-Path $ServiceRoot "game_api.cpp")
) + $ActionSources + @((Join-Path $ServiceRoot "versions\stellaris_4_4_6_windows.cpp"))

Push-Location $ObjectRoot
try {
    Invoke-DeveloperCommand ($Common + @(
        "/LD",
        "/Fe:$(Join-Path $OutputRoot 'iag_stellaris_native_runtime.dll')"
    ) + $RuntimeSources + @(
        "/link",
        "/IMPLIB:$(Join-Path $ObjectRoot 'iag_stellaris_native_runtime.lib')"
    ))

    Invoke-DeveloperCommand ($Common + @(
        "/Fe:$(Join-Path $OutputRoot 'iag_stellaris_native_runtime_loader.exe')",
        (Join-Path $ServiceRoot "platform\windows\native_runtime_loader.cpp")
    ))

    Invoke-DeveloperCommand ($Common + @(
        "/Fe:$(Join-Path $ObjectRoot 'iag_native_runtime_action_contract_test.exe')",
        (Join-Path $ServiceRoot "tests\action_contract_test.cpp"),
        (Join-Path $ServiceRoot "game_api.cpp")
    ) + $ActionSources + @(
        (Join-Path $ServiceRoot "versions\stellaris_4_4_6_windows.cpp")
    ))
    & (Join-Path $ObjectRoot "iag_native_runtime_action_contract_test.exe")
    if ($LASTEXITCODE -ne 0) {
        throw "Native runtime action contract test failed."
    }
} finally {
    Pop-Location
}

Write-Host "Windows native runtime created: $OutputRoot"
