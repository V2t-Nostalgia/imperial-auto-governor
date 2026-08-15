# SPDX-FileCopyrightText: 2026 Nostalgia and Imperial Auto Governor contributors
# SPDX-License-Identifier: GPL-3.0-only

[CmdletBinding()]
param(
    [ValidateSet("Install", "Start", "Stop", "Test", "Status", "Remove")]
    [string]$Action = "Install",
    [string]$RuntimeRoot = (Join-Path $env:LOCALAPPDATA "ImperialAutoGovernor"),
    [switch]$SkipImagePull
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectName = "iag-research"
$ComposeFile = Join-Path $PSScriptRoot "compose.yaml"
$ResearchRoot = Join-Path $RuntimeRoot "research_services"
$SecretsRoot = Join-Path $RuntimeRoot "secrets"
$EnvironmentFile = Join-Path $ResearchRoot "compose.env"
$AgentConfig = Join-Path $RuntimeRoot "agent_config.json"

function Write-Utf8NoBom {
    param([string]$Path, [string]$Value)
    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [System.IO.File]::WriteAllText(
        $Path,
        $Value,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function New-HexSecret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Get-OrCreateSecret {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $existing = (Get-Content -LiteralPath $Path -Raw -Encoding UTF8).Trim()
        if ($existing -match "^[0-9a-f]{64}$") {
            return $existing
        }
        throw "Existing secret file has an invalid format: $Path"
    }
    $secret = New-HexSecret
    Write-Utf8NoBom -Path $Path -Value ($secret + "`n")
    return $secret
}

function Invoke-Docker {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Test-DockerReady {
    & docker info --format "{{.ServerVersion}}" *> $null
    return $LASTEXITCODE -eq 0
}

function Ensure-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker was not found. Install Docker Desktop with the WSL2 backend first."
    }
    if (Test-DockerReady) {
        return
    }
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path -LiteralPath $desktop -PathType Leaf)) {
        throw "Docker Desktop is stopped and was not found at: $desktop"
    }
    Write-Host "Starting Docker Desktop..."
    Start-Process -FilePath $desktop -WindowStyle Hidden
    $deadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 3
        if (Test-DockerReady) {
            return
        }
    } while ((Get-Date) -lt $deadline)
    throw "Docker Desktop did not become ready within 3 minutes."
}

function Write-RuntimeEnvironment {
    New-Item -ItemType Directory -Path $ResearchRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $SecretsRoot -Force | Out-Null
    $searxSecret = Get-OrCreateSecret (Join-Path $SecretsRoot "searxng_secret")
    $crawlToken = Get-OrCreateSecret (Join-Path $SecretsRoot "crawl4ai_api_token")
    $crawlSecret = Get-OrCreateSecret (Join-Path $SecretsRoot "crawl4ai_secret_key")
    $redisPassword = Get-OrCreateSecret (Join-Path $SecretsRoot "crawl4ai_redis_password")
    $contents = @(
        "SEARXNG_SECRET=$searxSecret"
        "CRAWL4AI_API_TOKEN=$crawlToken"
        "CRAWL4AI_SECRET_KEY=$crawlSecret"
        "REDIS_PASSWORD=$redisPassword"
        ""
    ) -join "`n"
    Write-Utf8NoBom -Path $EnvironmentFile -Value $contents
}

function Set-JsonProperty {
    param(
        [object]$Object,
        [string]$Name,
        [object]$Value
    )
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
    }
    else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Enable-AgentResearch {
    if (-not (Test-Path -LiteralPath $AgentConfig -PathType Leaf)) {
        throw "Agent config was not found at $AgentConfig. Start the Windows Agent and save its settings first."
    }
    $config = Get-Content -LiteralPath $AgentConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    Set-JsonProperty $config "web_research_enabled" $true
    Set-JsonProperty $config "searxng_url" "http://127.0.0.1:8080"
    Set-JsonProperty $config "crawl4ai_url" "http://127.0.0.1:11235"
    Set-JsonProperty $config "crawl4ai_api_token_file" (Join-Path $SecretsRoot "crawl4ai_api_token")
    Set-JsonProperty $config "stellaris_wiki_api_url" "https://stellaris.paradoxwikis.com/api.php"
    Set-JsonProperty $config "web_fetch_direct_fallback_enabled" $false
    if (-not ($config.PSObject.Properties.Name -contains "web_search_allowed_domains")) {
        Set-JsonProperty $config "web_search_allowed_domains" @(
            "store.steampowered.com",
            "steamcommunity.com",
            "forum.paradoxplaza.com",
            "stellaris.paradoxwikis.com",
            "github.com",
            "reddit.com"
        )
    }
    $temporary = $AgentConfig + ".tmp"
    Write-Utf8NoBom -Path $temporary -Value (($config | ConvertTo-Json -Depth 100) + "`n")
    Move-Item -LiteralPath $temporary -Destination $AgentConfig -Force
}

function Compose-Arguments {
    param([string[]]$Tail)
    return @(
        "compose",
        "--project-name", $ProjectName,
        "--env-file", $EnvironmentFile,
        "--file", $ComposeFile
    ) + $Tail
}

function Wait-HttpSuccess {
    param(
        [string]$Url,
        [int]$Seconds = 180,
        [hashtable]$Headers = @{}
    )
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try {
            return Invoke-RestMethod -Uri $Url -Headers $Headers -TimeoutSec 10
        }
        catch {
            Start-Sleep -Seconds 3
        }
    } while ((Get-Date) -lt $deadline)
    throw "Service did not become ready within $Seconds seconds: $Url"
}

function Test-ResearchServices {
    $tokenPath = Join-Path $SecretsRoot "crawl4ai_api_token"
    if (-not (Test-Path -LiteralPath $tokenPath -PathType Leaf)) {
        throw "Crawl4AI token is missing: $tokenPath"
    }
    $token = (Get-Content -LiteralPath $tokenPath -Raw -Encoding UTF8).Trim()
    $authorization = @{ Authorization = "Bearer $token" }

    $null = Wait-HttpSuccess "http://127.0.0.1:11235/health"
    $searx = Wait-HttpSuccess (
        "http://127.0.0.1:8080/search?q=Stellaris%204.4.6&format=json&language=all"
    )
    if (-not $searx.results -or @($searx.results).Count -lt 1) {
        throw "SearXNG returned successfully but contained no search results."
    }

    $unauthenticatedRejected = $false
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:11235/schema" -UseBasicParsing -TimeoutSec 15 | Out-Null
    }
    catch {
        $response = $_.Exception.Response
        $unauthenticatedRejected = $response -and [int]$response.StatusCode -eq 401
    }
    if (-not $unauthenticatedRejected) {
        throw "Crawl4AI /schema did not reject an unauthenticated request."
    }
    $schema = Invoke-RestMethod -Uri "http://127.0.0.1:11235/schema" -Headers $authorization -TimeoutSec 30
    if (-not $schema) {
        throw "Crawl4AI /schema returned no content."
    }

    $crawlBody = @{ urls = @("https://stellaris.paradoxwikis.com/") } | ConvertTo-Json -Depth 10
    $crawlRequest = @{
        Uri = "http://127.0.0.1:11235/crawl"
        Method = "Post"
        Headers = $authorization
        ContentType = "application/json"
        Body = $crawlBody
        TimeoutSec = 180
    }
    $crawl = Invoke-RestMethod @crawlRequest
    if (-not $crawl) {
        throw "Crawl4AI /crawl returned no content."
    }

    $wikiStatus = $null
    try {
        $wiki = Invoke-RestMethod -Uri "https://stellaris.paradoxwikis.com/api.php?action=query&format=json&formatversion=2&generator=search&gsrsearch=economy&gsrlimit=2&prop=extracts%7Cinfo&inprop=url&explaintext=1&exintro=1&origin=*" -Headers @{ "User-Agent" = "Imperial-Auto-Governor/1.0 read-only-research" } -TimeoutSec 30
        if (
            $wiki -isnot [string] -and
            $wiki.PSObject.Properties.Name -contains "query" -and
            $wiki.query.PSObject.Properties.Name -contains "pages" -and
            @($wiki.query.pages).Count -gt 0
        ) {
            $wikiStatus = "passed ($(@($wiki.query.pages).Count) MediaWiki API pages)"
        }
    }
    catch {
        Write-Verbose "MediaWiki API check failed: $($_.Exception.Message)"
    }
    if (-not $wikiStatus) {
        $wikiFallbackQuery = [uri]::EscapeDataString(
            "site:stellaris.paradoxwikis.com Stellaris economy"
        )
        $wikiFallback = Wait-HttpSuccess (
            "http://127.0.0.1:8080/search?q=$wikiFallbackQuery&format=json&language=all"
        )
        $wikiResults = @(
            $wikiFallback.results | Where-Object {
                try {
                    ([uri]$_.url).Host -eq "stellaris.paradoxwikis.com"
                }
                catch {
                    $false
                }
            }
        )
        if ($wikiResults.Count -lt 1) {
            throw "MediaWiki API was unavailable and the bounded SearXNG Wiki fallback returned no pages."
        }
        $wikiStatus = "MediaWiki API unavailable; SearXNG Wiki fallback passed ($($wikiResults.Count) results)"
    }

    [pscustomobject]@{
        SearXNG = "passed ($(@($searx.results).Count) results)"
        Crawl4AIHealth = "passed"
        Crawl4AIAuth = "passed (unauthenticated request rejected)"
        Crawl4AICrawl = "passed"
        WikiSearch = $wikiStatus
    } | Format-List
}

Ensure-DockerReady

switch ($Action) {
    "Install" {
        Write-RuntimeEnvironment
        Enable-AgentResearch
        if (-not $SkipImagePull) {
            Invoke-Docker (Compose-Arguments @("pull"))
        }
        Invoke-Docker (Compose-Arguments @("up", "--detach", "--wait"))
        Test-ResearchServices
        Write-Host "Web research is enabled. The Agent will load it on the next model turn."
    }
    "Start" {
        Write-RuntimeEnvironment
        Invoke-Docker (Compose-Arguments @("up", "--detach", "--wait"))
        Test-ResearchServices
    }
    "Stop" {
        Write-RuntimeEnvironment
        Invoke-Docker (Compose-Arguments @("stop"))
    }
    "Test" {
        Write-RuntimeEnvironment
        Test-ResearchServices
    }
    "Status" {
        Write-RuntimeEnvironment
        Invoke-Docker (Compose-Arguments @("ps"))
    }
    "Remove" {
        Write-RuntimeEnvironment
        Invoke-Docker (Compose-Arguments @("down", "--remove-orphans"))
        Write-Host "Containers removed. Agent config, secrets, and the SearXNG cache volume remain."
    }
}
