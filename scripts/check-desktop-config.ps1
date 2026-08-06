$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$ConfigPath = Join-Path $Root "apps\desktop\src-tauri\tauri.conf.json"
$CargoPath = Join-Path $Root "apps\desktop\src-tauri\Cargo.toml"
$MainPath = Join-Path $Root "apps\desktop\src-tauri\src\main.rs"
$SpecPath = Join-Path $Root "scripts\pyinstaller\planix-api.spec"
$EntryPath = Join-Path $Root "scripts\pyinstaller\planix_api_entry.py"
$HealthScriptPath = Join-Path $Root "scripts\wait-api-health.ps1"

foreach ($Path in @($ConfigPath, $CargoPath, $MainPath, $SpecPath, $EntryPath, $HealthScriptPath)) {
    if (-not (Test-Path $Path)) {
        throw "Missing required Phase 7 desktop file: $Path"
    }
}

$Config = Get-Content -Raw $ConfigPath | ConvertFrom-Json

if ($Config.productName -ne "Planix") {
    throw "Unexpected desktop productName: $($Config.productName)"
}

if ($Config.identifier -ne "com.planix.app") {
    throw "Unexpected bundle identifier: $($Config.identifier)"
}

if ($Config.mainBinaryName -ne "planix") {
    throw "Unexpected mainBinaryName: $($Config.mainBinaryName)"
}

if ($Config.build.devUrl -ne "http://127.0.0.1:5176") {
    throw "Unexpected devUrl: $($Config.build.devUrl)"
}

if ($Config.build.frontendDist -ne "resources") {
    throw "Unexpected frontendDist: $($Config.build.frontendDist)"
}

if ($Config.bundle.windows.webviewInstallMode.type -ne "embedBootstrapper") {
    throw "Windows bundle should embed the WebView2 bootstrapper."
}

$Targets = @($Config.bundle.targets)
if (-not ($Targets -contains "nsis") -or -not ($Targets -contains "msi")) {
    throw "Desktop bundle targets must include both nsis and msi."
}

if ($Config.bundle.publisher -ne "Planix") {
    throw "Unexpected bundle publisher: $($Config.bundle.publisher)"
}

if ($Config.bundle.windows.nsis.installerIcon -ne "icons/icon.ico") {
    throw "NSIS installerIcon should use icons/icon.ico."
}

if ($Config.bundle.windows.nsis.uninstallerIcon -ne "icons/icon.ico") {
    throw "NSIS uninstallerIcon should use icons/icon.ico."
}

if ($Config.bundle.windows.nsis.installMode -ne "currentUser") {
    throw "NSIS installMode should be currentUser."
}

$Resources = $Config.bundle.resources
if ($Resources.'resources/index.html' -ne "resources/index.html") {
    throw "Desktop bundle must copy resources/index.html."
}
if ($Resources.'resources/assets' -ne "resources/assets") {
    throw "Desktop bundle must copy resources/assets."
}
if ($Resources.'resources/binaries/planix-api.exe' -ne "resources/binaries/planix-api.exe") {
    throw "Desktop bundle must copy resources/binaries/planix-api.exe."
}

$WebEntryPath = Join-Path $Root "Frontend\index.html"
if (-not (Test-Path $WebEntryPath)) {
    throw "Missing frontend entry: $WebEntryPath"
}

Write-Host "Desktop scaffold configuration looks ready for Phase 8 packaging."
