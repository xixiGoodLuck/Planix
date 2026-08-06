$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$WebDir = Join-Path $Root "Frontend"
$BackendDir = Join-Path $Root "Backend"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Create .venv or install Python 3.11+ before starting desktop development."
}

if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw "npm.cmd was not found. Install Node.js 20+ before starting desktop development."
}

$env:PLANIX_API_PORT = if ($env:PLANIX_API_PORT) { $env:PLANIX_API_PORT } else { "8003" }

Write-Host "Starting FastAPI backend on http://127.0.0.1:$env:PLANIX_API_PORT"
Start-Process -FilePath $Python -ArgumentList @(
    "-m",
    "uvicorn",
    "backend.app.main:app",
    "--env-file",
    "..\.env",
    "--host",
    "127.0.0.1",
    "--port",
    $env:PLANIX_API_PORT,
    "--reload"
) -WorkingDirectory $BackendDir -WindowStyle Hidden

& (Join-Path $PSScriptRoot "wait-api-health.ps1") -Url "http://127.0.0.1:$env:PLANIX_API_PORT/api/health" -TimeoutSeconds 30

Write-Host "Starting Vite frontend on http://127.0.0.1:5176"
Start-Process -FilePath "npm.cmd" -ArgumentList @(
    "run",
    "dev",
    "--",
    "--host",
    "127.0.0.1"
) -WorkingDirectory $WebDir -WindowStyle Hidden

Write-Host "Desktop dev services are starting. Run 'cd apps\desktop; npm run dev' after the ports are ready."
