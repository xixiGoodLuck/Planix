# Planix Desktop

This folder contains the Tauri desktop shell for Planix. The desktop app bundles the Vite web build and launches the FastAPI backend as a Tauri sidecar named `planix-api`.

The portfolio-facing documentation version is `v3.0.0`. This is a documentation and release-presentation label; it does not automatically change package, Cargo, backend health, or build-script versions.

## Development

```powershell
.\scripts\dev-desktop.ps1
cd apps\desktop
npm install
npm run dev
```

The desktop dev window loads:

```text
http://127.0.0.1:5176
```

## Release Strategy

1. `scripts/build-web.ps1` builds `Frontend/dist` and syncs it into `apps/desktop/src-tauri/resources`.
2. `scripts/build-backend.ps1` packages the FastAPI backend as `planix-api`.
3. Tauri bundles the web dist and launches the backend through the `planix-api` sidecar.
4. The sidecar requires PostgreSQL 17 and forwards `DATABASE_URL` plus optional `PLANIX_DB_POOL_MIN`, `PLANIX_DB_POOL_MAX`, and `PLANIX_DB_POOL_TIMEOUT` values.

## Windows Installers

The intended public installer naming for the portfolio release is:

```text
release/Planix-v3.0.0-windows-x64-setup.exe
release/Planix-v3.0.0-windows-x64-setup.exe.sha256
release/Planix-v3.0.0-windows-x64.msi
release/Planix-v3.0.0-windows-x64.msi.sha256
```

Normal users should install with `Planix-v3.0.0-windows-x64-setup.exe`. The `.msi` is kept as a backup or enterprise installer. `.sha256` files are checksum files only and are not installers.

## Build Commands

Use the project release script for local installer builds:

```powershell
.\scripts\check-packaging-toolchain.ps1
.\scripts\build-release.ps1 -Version 3.0.0
```

Do not commit generated installer binaries, release folders, Tauri targets, or sidecar build output.
