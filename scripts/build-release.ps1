param(
    [string]$Version = "1.1.4",
    [switch]$CreateGitHubRelease
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$CleanVersion = $Version.TrimStart("v")
$Tag = "v$CleanVersion"
$ReleaseDir = Join-Path $Root "release"
$SetupName = "Planix-$Tag-windows-x64-setup.exe"
$SetupHashName = "Planix-$Tag-windows-x64-setup.exe.sha256"
$MsiName = "Planix-$Tag-windows-x64.msi"
$MsiHashName = "Planix-$Tag-windows-x64.msi.sha256"
$SetupPath = Join-Path $ReleaseDir $SetupName
$SetupHashPath = Join-Path $ReleaseDir $SetupHashName
$MsiPath = Join-Path $ReleaseDir $MsiName
$MsiHashPath = Join-Path $ReleaseDir $MsiHashName
$DesktopDir = Join-Path $Root "apps\desktop"
$TauriBundleDir = Join-Path $DesktopDir "src-tauri\target\release\bundle"
$NsisTargetDir = Join-Path $TauriBundleDir "nsis"
$MsiTargetDir = Join-Path $TauriBundleDir "msi"
$WebIndexPath = Join-Path $Root "Frontend\dist\index.html"
$DesktopResourceIndexPath = Join-Path $Root "apps\desktop\src-tauri\resources\index.html"
$SidecarPath = Join-Path $Root "apps\desktop\src-tauri\resources\binaries\planix-api.exe"

function Write-ReleaseHash {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ArtifactPath,
        [Parameter(Mandatory = $true)]
        [string]$ArtifactName,
        [Parameter(Mandatory = $true)]
        [string]$HashPath
    )

    $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $ArtifactPath
    Set-Content -Path $HashPath -Value "$($Hash.Hash.ToLower())  $ArtifactName" -Encoding ASCII
}

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

& (Join-Path $PSScriptRoot "build-web.ps1")
if (-not (Test-Path $WebIndexPath)) {
    throw "Missing frontend asset after web build: $WebIndexPath"
}
if (-not (Test-Path $DesktopResourceIndexPath)) {
    throw "Missing desktop frontend resource after web build: $DesktopResourceIndexPath"
}

& (Join-Path $PSScriptRoot "build-backend.ps1")
if (-not (Test-Path $SidecarPath)) {
    throw "Missing packaged resource sidecar: $SidecarPath"
}

Push-Location $DesktopDir
try {
    if (Test-Path "node_modules") {
        npm.cmd install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed for apps/desktop."
        }
    }
    elseif (Test-Path "package-lock.json") {
        npm.cmd ci
        if ($LASTEXITCODE -ne 0) {
            throw "npm ci failed for apps/desktop."
        }
    }
    else {
        npm.cmd install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed for apps/desktop."
        }
    }

    & (Join-Path $PSScriptRoot "check-packaging-toolchain.ps1")

    if (Test-Path $TauriBundleDir) {
        Remove-Item -LiteralPath $TauriBundleDir -Recurse -Force
    }

    $env:RUST_BACKTRACE = "1"
    $BuildLog = Join-Path $Root "desktop-build-error.log"
    $BuildOutput = & cmd.exe /d /s /c "npm.cmd run build 2>&1"
    $BuildExitCode = $LASTEXITCODE
    $BuildOutput | Tee-Object -FilePath $BuildLog
    if ($BuildExitCode -ne 0) {
        Write-Host "Tauri/Rust build failed. Full error log: $BuildLog" -ForegroundColor Red
        throw "npm run build failed for apps/desktop. See $BuildLog for details."
    }
}
finally {
    Pop-Location
}

$NsisSetup = Get-ChildItem -Path $NsisTargetDir -Filter "*.exe" -Recurse -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (-not $NsisSetup) {
    throw "Tauri build did not produce an NSIS setup exe under $NsisTargetDir"
}

$Msi = Get-ChildItem -Path $MsiTargetDir -Filter "*.msi" -Recurse -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (-not $Msi) {
    throw "Tauri build did not produce an MSI under $MsiTargetDir"
}

Copy-Item -LiteralPath $NsisSetup.FullName -Destination $SetupPath -Force
Copy-Item -LiteralPath $Msi.FullName -Destination $MsiPath -Force
Write-ReleaseHash -ArtifactPath $SetupPath -ArtifactName $SetupName -HashPath $SetupHashPath
Write-ReleaseHash -ArtifactPath $MsiPath -ArtifactName $MsiName -HashPath $MsiHashPath

Write-Host "Release setup installer: $SetupPath"
Write-Host "Setup SHA256 file: $SetupHashPath"
Write-Host "Release MSI installer: $MsiPath"
Write-Host "MSI SHA256 file: $MsiHashPath"

if ($CreateGitHubRelease) {
    $NotesPath = Join-Path $Root "docs\release-v$CleanVersion.md"
    if (-not (Test-Path $NotesPath)) {
        $NotesPath = Join-Path $Root "docs\release-v1.1.4.md"
    }
    & (Join-Path $PSScriptRoot "check-packaging-toolchain.ps1") -RequireGitHubAuth
    $GhCommand = Get-Command "gh.exe" -ErrorAction SilentlyContinue
    if (-not $GhCommand) {
        $GhCommand = Get-Command "gh.cmd" -ErrorAction SilentlyContinue
    }
    if (-not $GhCommand) {
        throw "GitHub CLI is required for local release publishing."
    }

    git diff --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "Working tree has uncommitted changes. Commit before publishing a GitHub Release."
    }

    $ReleaseAssets = @($SetupPath, $SetupHashPath, $MsiPath, $MsiHashPath)
    & $GhCommand.Source release view $Tag *> $null
    if ($LASTEXITCODE -eq 0) {
        & $GhCommand.Source release upload $Tag @ReleaseAssets --clobber
    }
    else {
        & $GhCommand.Source release create $Tag @ReleaseAssets --title "Planix $Tag" --notes-file $NotesPath
    }
}
