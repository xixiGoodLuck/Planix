param(
  [string]$ApiBaseUrl = "http://127.0.0.1:8003"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
  $Python = "python"
}
$PytestTemp = Join-Path $RepoRoot ".tmp\pytest-demo"
New-Item -ItemType Directory -Force -Path $PytestTemp | Out-Null

function Write-Pass {
  param([string]$Message)
  Write-Host "[PASS] $Message" -ForegroundColor Green
}

function Write-Fail {
  param([string]$Message)
  Write-Host "[FAIL] $Message" -ForegroundColor Red
}

function Invoke-Step {
  param(
    [string]$Name,
    [scriptblock]$Script
  )
  Write-Host "[RUN ] $Name"
  $global:LASTEXITCODE = 0
  & $Script
  if ($global:LASTEXITCODE -ne 0) {
    throw "$Name failed with exit code $global:LASTEXITCODE"
  }
  Write-Pass $Name
}

Set-Location $RepoRoot
Write-Host "Planix Demo Verification"

try {
  $health = Invoke-RestMethod -Method Get -Uri "$ApiBaseUrl/api/health" -TimeoutSec 5
  if ($health.name -ne "planix-api") {
    throw "Unexpected API name: $($health.name)"
  }
  if ($health.version -ne "3.11-demo-reliability") {
    throw "Unexpected API version: $($health.version)"
  }
  Write-Pass "API health: $($health.name)"

  $requiredFeatures = @(
    "planQualityGate",
    "contextAwareRefinement",
    "calendarDraftContextRecovery",
    "demoMetrics"
  )
  foreach ($feature in $requiredFeatures) {
    if (-not $health.features.$feature) {
      throw "Feature flag is missing or disabled: $feature"
    }
    Write-Pass "$feature enabled"
  }
} catch {
  Write-Fail "API health/version/features"
  throw
}

Invoke-Step "Backend compile" {
  & $Python -m compileall Backend\backend
}

Invoke-Step "Backend golden demo tests" {
  & $Python -m pytest -p no:cacheprovider --basetemp $PytestTemp `
    Backend\backend\tests\test_planning.py `
    Backend\backend\tests\test_command.py `
    Backend\backend\tests\test_runtime.py `
    Backend\backend\tests\test_health.py
}

Invoke-Step "Frontend typecheck" {
  Push-Location Frontend
  try {
    & npx.cmd tsc -b
  } finally {
    Pop-Location
  }
}

Invoke-Step "Frontend key tests" {
  Push-Location Frontend
  try {
    & npm.cmd run test
  } finally {
    Pop-Location
  }
}

Write-Host "Demo readiness: PASS" -ForegroundColor Green
