param([string]$ApiBaseUrl = "http://127.0.0.1:8003")

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { $Python = "python" }

function Invoke-Step([string]$Name, [scriptblock]$Script) {
  Write-Host "[RUN ] $Name"
  $global:LASTEXITCODE = 0
  & $Script
  if ($global:LASTEXITCODE -ne 0) { throw "$Name failed with exit code $global:LASTEXITCODE" }
  Write-Host "[PASS] $Name" -ForegroundColor Green
}

Set-Location $RepoRoot
$health = Invoke-RestMethod -Method Get -Uri "$ApiBaseUrl/health" -TimeoutSec 5
if ($health.status -ne "ok" -or $health.database -ne "postgresql") { throw "Backend health is not ready" }
$learning = Invoke-RestMethod -Method Get -Uri "$ApiBaseUrl/api/learning/health" -TimeoutSec 5
if (-not $learning.status) { throw "Learning health response is invalid" }

Invoke-Step "Backend compile" { & $Python -m compileall Backend\backend }
Invoke-Step "Backend Learning tests" { & $Python -m pytest Backend\backend\tests }
Invoke-Step "Frontend build" { Push-Location Frontend; try { & npm.cmd run build } finally { Pop-Location } }
Invoke-Step "Frontend tests" { Push-Location Frontend; try { & npm.cmd run test } finally { Pop-Location } }
Write-Host "Planix Learning verification: PASS" -ForegroundColor Green
