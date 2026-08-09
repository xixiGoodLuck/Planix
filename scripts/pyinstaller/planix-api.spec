# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules, copy_metadata


ROOT = Path.cwd()
entry = ROOT / "scripts" / "pyinstaller" / "planix_api_entry.py"

hiddenimports = [
    "backend.app.main",
    "backend.app.routers.health",
    "backend.app.routers.plans",
    "backend.app.routers.month_notes",
    "backend.app.routers.settings",
    "backend.app.routers.planning",
    "backend.app.routers.command",
    "backend.app.routers.context_settings",
]
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("pydantic_core")
hiddenimports += collect_submodules("psycopg")
hiddenimports += collect_submodules("psycopg_pool")

# brotli - httpx uses it for response decompression
try:
    hiddenimports += collect_submodules("brotli")
except Exception:
    pass

datas = []
datas += copy_metadata("fastapi")
datas += copy_metadata("uvicorn")
datas += copy_metadata("pydantic")
binaries = collect_dynamic_libs("psycopg_binary")

a = Analysis(
    [str(entry)],
    pathex=[str(ROOT / "Backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="planix-api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
)
