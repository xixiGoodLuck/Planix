param(
    [string]$BaseUrl = "http://127.0.0.1:8003"
)

$ErrorActionPreference = "Stop"

Write-Host "Planix Backend Smoke Test"
Write-Host "Target: $BaseUrl"
Write-Host ""

$passed = 0
$failed = 0

function Test-Step {
    param([string]$Name, [scriptblock]$Block)
    try {
        & $Block
        Write-Host "  PASS $Name" -ForegroundColor Green
        $script:passed++
    }
    catch {
        Write-Host "  FAIL $Name" -ForegroundColor Red
        Write-Host "    Error: $_" -ForegroundColor Red
        $script:failed++
    }
}

Write-Host "1. Health check"
Test-Step -Name "GET /api/health returns app=planix-api" -Block {
    $r = Invoke-RestMethod -Uri "$BaseUrl/api/health" -Method GET -TimeoutSec 5
    if ($r.status -ne "ok") { throw "Expected status=ok, got $($r.status)" }
    if ($r.app -ne "planix-api") { throw "Expected app=planix-api, got $($r.app)" }
    if (-not $r.pid) { throw "Missing pid field" }
    if (-not $r.version) { throw "Missing version field" }
    if ($r.version -ne "1.1.4") { throw "Expected version=1.1.4, got $($r.version)" }
    Write-Host "    app=$($r.app) pid=$($r.pid) version=$($r.version)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "2. Read AI settings"
Test-Step -Name "GET /api/ai/settings returns provider=deepseek by default" -Block {
    $r = Invoke-RestMethod -Uri "$BaseUrl/api/ai/settings" -Method GET -TimeoutSec 5
    if (-not $r.PSObject.Properties.Name -contains "hasApiKey") {
        throw "Response missing hasApiKey field"
    }
    if ($r.provider -ne "deepseek") { throw "Expected provider=deepseek, got $($r.provider)" }
    if ($r.baseUrl -ne "https://api.deepseek.com") { throw "Expected DeepSeek baseUrl, got $($r.baseUrl)" }
    if ($r.model -ne "deepseek-v4-flash") { throw "Expected deepseek-v4-flash, got $($r.model)" }
    Write-Host "    provider=$($r.provider) hasApiKey=$($r.hasApiKey)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "3. Save AI settings"
Test-Step -Name "PUT /api/ai/settings keeps DeepSeek as default provider" -Block {
    $body = @{
        provider       = "deepseek"
        baseUrl        = "https://api.deepseek.com"
        model          = "deepseek-v4-flash"
        temperature    = 0.3
        timeoutSeconds = 40
    } | ConvertTo-Json
    $r = Invoke-RestMethod -Uri "$BaseUrl/api/ai/settings" -Method PUT -Body $body -ContentType "application/json" -TimeoutSec 5
    if ($r.provider -ne "deepseek") { throw "Expected provider=deepseek, got $($r.provider)" }
    Write-Host "    provider=$($r.provider) hasApiKey=$($r.hasApiKey)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "4. Verify settings persisted"
Test-Step -Name "GET /api/ai/settings after save" -Block {
    $r = Invoke-RestMethod -Uri "$BaseUrl/api/ai/settings" -Method GET -TimeoutSec 5
    if ($r.provider -ne "deepseek") { throw "Expected provider=deepseek, got $($r.provider)" }
    Write-Host "    provider=$($r.provider) persistence OK" -ForegroundColor Gray
}

Write-Host ""
Write-Host "5. Test AI mock mode"
Test-Step -Name "POST /api/ai/test returns mock result" -Block {
    $body = @{ prompt = "Say hi" } | ConvertTo-Json
    $r = Invoke-RestMethod -Uri "$BaseUrl/api/ai/test" -Method POST -Body $body -ContentType "application/json" -TimeoutSec 10
    if ($r.mode -ne "mock") { throw "Expected mode=mock, got $($r.mode)" }
    if ($r.ok -ne $true) { throw "Expected ok=true" }
    Write-Host "    mode=$($r.mode) message=$($r.message)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "6. Duplicate sidecar preflight"
$logPath = "$env:APPDATA\Planix\logs\desktop.log"
Test-Step -Name "Preflight health detection keeps API healthy" -Block {
    if (Test-Path $logPath) {
        $log = Get-Content $logPath -Tail 20 -ErrorAction SilentlyContinue
        if ($log -match "skip spawning sidecar") {
            Write-Host "    Log confirms existing API detected and sidecar skipped." -ForegroundColor Green
        }
        elseif ($log -match "PORT CONFLICT") {
            Write-Host "    Log shows port conflict detection." -ForegroundColor Yellow
        }
        else {
            Write-Host "    No duplicate-launch marker found yet." -ForegroundColor Cyan
        }
    }
    else {
        Write-Host "    desktop.log not found; skipping detailed log check." -ForegroundColor Gray
    }

    $r = Invoke-RestMethod -Uri "$BaseUrl/api/health" -Method GET -TimeoutSec 5
    if ($r.status -ne "ok") { throw "Health check failed on second attempt" }
    Write-Host "    Health check still OK." -ForegroundColor Green
}

Write-Host ""
Write-Host "Results"
Write-Host "Passed: $passed   Failed: $failed" -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Red" })
if ($failed -eq 0) {
    Write-Host "All smoke tests passed!" -ForegroundColor Green
}
else {
    Write-Host "Some tests failed." -ForegroundColor Red
    Write-Host "Check log: $logPath"
    exit 1
}
