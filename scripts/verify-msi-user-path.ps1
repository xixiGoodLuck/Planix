param(
    [string]$InstallerPath = "",
    [string]$InstalledDir = "",
    [switch]$InstallMsi,
    [string]$ApiBaseUrl = "http://127.0.0.1:8003",
    [int]$TimeoutSeconds = 45,
    [switch]$UseRealLlm
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $InstallerPath) {
    $InstallerPath = Join-Path $Root "release\Planix-v1.1.4-windows-x64.msi"
}
$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Require-Path {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Label not found: $Path"
    }
    Write-Host "OK: $Label -> $Path" -ForegroundColor Green
}

function Invoke-Json {
    param(
        [string]$Method,
        [string]$Uri,
        [object]$Body = $null,
        [int]$TimeoutSec = 10
    )
    if ($null -eq $Body) {
        return Invoke-RestMethod -Uri $Uri -Method $Method -TimeoutSec $TimeoutSec
    }
    $json = $Body | ConvertTo-Json -Depth 12
    return Invoke-RestMethod -Uri $Uri -Method $Method -Body $json -ContentType "application/json" -TimeoutSec $TimeoutSec
}

function Wait-Api {
    param([string]$BaseUrl, [int]$Timeout)
    $deadline = (Get-Date).AddSeconds($Timeout)
    do {
        try {
            $health = Invoke-Json -Method GET -Uri "$BaseUrl/api/health" -TimeoutSec 3
            if ($health.status -eq "ok" -and $health.app -eq "planix-api") {
                Write-Host "OK: API health app=$($health.app) pid=$($health.pid) version=$($health.version)" -ForegroundColor Green
                return $health
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
    } while ((Get-Date) -lt $deadline)
    throw "API did not become healthy within $Timeout seconds: $BaseUrl/api/health"
}

function Stop-InstalledProcesses {
    param([string]$InstallRoot, [object]$DesktopProcess = $null)

    if ($DesktopProcess -and -not $DesktopProcess.HasExited) {
        try {
            if ($DesktopProcess.CloseMainWindow()) {
                Start-Sleep -Seconds 2
            }
        }
        catch {
        }
        if (-not $DesktopProcess.HasExited) {
            Stop-Process -Id $DesktopProcess.Id -Force -ErrorAction SilentlyContinue
        }
    }

    Get-Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessName -eq "planix-api" -and
            $_.Path -and
            $_.Path.StartsWith($InstallRoot, [System.StringComparison]::OrdinalIgnoreCase)
        } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}

function Resolve-InstallDir {
    param([string]$ExplicitDir)
    if ($ExplicitDir) {
        if (Test-Path -LiteralPath (Join-Path $ExplicitDir "planix.exe")) {
            return $ExplicitDir
        }
        Write-Host "WARN: planix.exe was not found under explicit InstalledDir: $ExplicitDir" -ForegroundColor Yellow
        Write-Host "      Falling back to common Windows install directories." -ForegroundColor Yellow
    }

    $candidates = @(@(
        "H:\planix",
        (Join-Path $env:LOCALAPPDATA "Programs\Planix"),
        (Join-Path $env:ProgramFiles "Planix"),
        (Join-Path ${env:ProgramFiles(x86)} "Planix")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ "planix.exe")) })

    if ($candidates.Count -gt 0) {
        return $candidates[0]
    }

    throw "InstalledDir was not provided and no common Planix install directory was found."
}

Write-Step "Installer and optional install"
Require-Path -Path $InstallerPath -Label "MSI"

if ($InstallMsi) {
    $msiArgs = @("/i", "`"$InstallerPath`"", "/passive", "/norestart")
    if ($InstalledDir) {
        $msiArgs += "APPLICATIONFOLDER=`"$InstalledDir`""
        $msiArgs += "INSTALLDIR=`"$InstalledDir`""
    }
    Write-Host "Running msiexec. The API key is not passed to the installer."
    $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $msiArgs -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "msiexec failed with exit code $($process.ExitCode)"
    }
}

$InstalledDir = Resolve-InstallDir -ExplicitDir $InstalledDir
$AppExe = Join-Path $InstalledDir "planix.exe"
$IndexPath = Join-Path $InstalledDir "resources\index.html"
$AssetsDir = Join-Path $InstalledDir "resources\assets"
$SidecarPath = Join-Path $InstalledDir "resources\binaries\planix-api.exe"

Write-Step "Installed layout"
Require-Path -Path $AppExe -Label "desktop exe"
Require-Path -Path $IndexPath -Label "frontend index"
Require-Path -Path $AssetsDir -Label "frontend assets"
Require-Path -Path $SidecarPath -Label "sidecar exe"

$DesktopLog = Join-Path $env:APPDATA "Planix\logs\desktop.log"
$LogStartLineCount = 0
if (Test-Path -LiteralPath $DesktopLog) {
    $LogStartLineCount = @(
        Get-Content -LiteralPath $DesktopLog -ErrorAction SilentlyContinue
    ).Count
}

Write-Step "Launch desktop app"
if ($UseRealLlm) {
    $env:USE_REAL_LLM = "1"
}
$started = Start-Process -FilePath $AppExe -PassThru
Wait-Api -BaseUrl $ApiBaseUrl -Timeout $TimeoutSeconds | Out-Null

Write-Step "Exercise core API flow"
$key = @($env:DEEPSEEK_API_KEY, $env:AI_API_KEY) |
    Where-Object { $_ -and $_.Trim() } |
    Select-Object -First 1
$provider = if ($key) { "deepseek" } else { "mock" }
$settingsBody = @{
    provider = $provider
    baseUrl = "https://api.deepseek.com"
    model = "deepseek-v4-flash"
    temperature = 0.3
    timeoutSeconds = 40
}
if ($key) {
    $settingsBody.apiKey = $key
}

$settings = Invoke-Json -Method PUT -Uri "$ApiBaseUrl/api/ai/settings" -Body $settingsBody
Write-Host "OK: settings saved provider=$($settings.provider) hasApiKey=$($settings.hasApiKey)" -ForegroundColor Green

$ai = Invoke-Json -Method POST -Uri "$ApiBaseUrl/api/ai/test" -Body @{ prompt = "Say OK in one short sentence." } -TimeoutSec 60
Write-Host "OK: AI test mode=$($ai.mode) ok=$($ai.ok)" -ForegroundColor Green

$today = Get-Date -Format "yyyy-MM-dd"
$planBody = @{
    date = $today
    time = "09:00"
    content = "MSI user-path verification task"
    done = $false
    result = ""
    priority = "medium"
    estimatedMinutes = 30
    source = "manual"
}
$plan = Invoke-Json -Method POST -Uri "$ApiBaseUrl/api/plans" -Body $planBody
Write-Host "OK: plan created id=$($plan.id)" -ForegroundColor Green

$documents = Invoke-Json -Method POST -Uri "$ApiBaseUrl/api/rag/documents" -Body @{
    title = "MSI verification material"
    content = "Planix verifies calendar, sidecar, PostgreSQL, RAG, and AI settings in a Windows MSI install."
    sourceType = "paste"
}
Write-Host "OK: RAG document created id=$($documents.id)" -ForegroundColor Green

$rag = Invoke-Json -Method POST -Uri "$ApiBaseUrl/api/rag/query" -Body @{
    goal = "Verify Planix MSI"
    deadline = ""
    dailyHours = 1
    materials = "Which capabilities were verified?"
    preferences = ""
    date = $today
    data = @{}
} -TimeoutSec 60
Write-Host "OK: RAG query mode=$($rag.mode) sources=$($rag.sources.Count)" -ForegroundColor Green

Write-Step "Restart app and verify persistence"
if ($started -and -not $started.HasExited) {
    Stop-InstalledProcesses -InstallRoot $InstalledDir -DesktopProcess $started
}
Start-Sleep -Seconds 3
$restarted = Start-Process -FilePath $AppExe -PassThru
Wait-Api -BaseUrl $ApiBaseUrl -Timeout $TimeoutSeconds | Out-Null
$plans = Invoke-Json -Method GET -Uri "$ApiBaseUrl/api/plans?date=$today"
if (-not ($plans | Where-Object { $_.id -eq $plan.id })) {
    throw "Created plan was not found after restart."
}
Write-Host "OK: plan persisted after restart" -ForegroundColor Green

Write-Step "Desktop log scan"
Require-Path -Path $DesktopLog -Label "desktop log"
$allLog = @(Get-Content -LiteralPath $DesktopLog -ErrorAction Stop)
$log = @($allLog | Select-Object -Skip $LogStartLineCount)
if ($log.Count -eq 0) {
    throw "No new desktop log lines were written during this verification run."
}
$badPatterns = @(
    "CORS",
    "404",
    "PORT CONFLICT",
    "sidecar missing",
    "sidecar start failure",
    "index.html existence check result: false",
    "asset not found",
    "mixed content"
)
foreach ($pattern in $badPatterns) {
    if ($log -match [regex]::Escape($pattern)) {
        throw "Desktop log contains suspicious pattern: $pattern"
    }
}
Write-Host "OK: desktop log has no known startup/API failure patterns in the latest 200 lines" -ForegroundColor Green

Write-Step "Cleanup"
if ($restarted -and -not $restarted.HasExited) {
    Stop-InstalledProcesses -InstallRoot $InstalledDir -DesktopProcess $restarted
}
Start-Sleep -Seconds 1
$leftovers = Get-Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ProcessName -eq "planix-api" -and
        $_.Path -and
        $_.Path.StartsWith($InstalledDir, [System.StringComparison]::OrdinalIgnoreCase)
    }
if ($leftovers) {
    throw "Sidecar process remained after cleanup: $($leftovers.Id -join ', ')"
}
Write-Host "MSI user-path verification passed." -ForegroundColor Green
