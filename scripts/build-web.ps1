$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$WebDir = Join-Path $Root "Frontend"
$IndexPath = Join-Path $WebDir "index.html"
$DistIndexPath = Join-Path $WebDir "dist\index.html"
$DesktopResourcesDir = Join-Path $Root "apps\desktop\src-tauri\resources"
$DesktopAssetsDir = Join-Path $DesktopResourcesDir "assets"
$DesktopIndexPath = Join-Path $DesktopResourcesDir "index.html"

if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw "npm.cmd was not found. Install Node.js 20+ before building the web app."
}

if (-not (Test-Path $IndexPath)) {
    throw "Missing frontend entry: $IndexPath"
}

Push-Location $WebDir
try {
    if (Test-Path "node_modules") {
        npm.cmd install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed for Frontend."
        }
    }
    elseif (Test-Path "package-lock.json") {
        npm.cmd ci
        if ($LASTEXITCODE -ne 0) {
            throw "npm ci failed for Frontend."
        }
    }
    else {
        npm.cmd install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed for Frontend."
        }
    }

    npm.cmd run build
    if ($LASTEXITCODE -ne 0) {
        throw "npm run build failed for Frontend."
    }

    if (-not (Test-Path $DistIndexPath)) {
        throw "Frontend build is incomplete. Missing required asset: $DistIndexPath"
    }

    New-Item -ItemType Directory -Force -Path $DesktopResourcesDir | Out-Null
    Copy-Item -LiteralPath $DistIndexPath -Destination $DesktopIndexPath -Force

    if (Test-Path $DesktopAssetsDir) {
        Remove-Item -LiteralPath $DesktopAssetsDir -Recurse -Force
    }
    # Copy built assets (may be a single dir or multiple)
    $SourceAssetsDir = Join-Path $WebDir "dist\assets"
    if (Test-Path $SourceAssetsDir) {
        Copy-Item -LiteralPath $SourceAssetsDir -Destination $DesktopAssetsDir -Recurse -Force
    }

    if (-not (Test-Path $DesktopIndexPath)) {
        throw "Desktop resource sync failed. Missing: $DesktopIndexPath"
    }

    Write-Host "Frontend index asset verified: $DistIndexPath"
    Write-Host "Desktop frontend resources synced to: $DesktopResourcesDir"
}
finally {
    Pop-Location
}
