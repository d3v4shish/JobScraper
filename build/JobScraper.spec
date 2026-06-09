# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).resolve().parent
src_root = project_root / "src"
icon_path = src_root / "jobscraper" / "assets" / "app_icon.ico"
datas = []
for path in (src_root / "jobscraper" / "resources").glob("*.json"):
    datas.append((str(path), "jobscraper/resources"))
for path in (src_root / "jobscraper" / "assets").glob("*"):
    if path.is_file():
        datas.append((str(path), "jobscraper/assets"))

hiddenimports = collect_submodules("jobscraper")
excludes = [
    "playwright",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineQuick",
]


a = Analysis(
    [str(src_root / "jobscraper" / "__main__.py")],
    pathex=[str(src_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JobScraper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(icon_path),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="JobScraper",
)
