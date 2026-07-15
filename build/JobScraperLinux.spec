# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


build_root = Path(SPECPATH).resolve()
project_root = build_root.parent
src_root = project_root / "src"
icon_path = src_root / "jobscraper" / "assets" / "app_icon.ico"

datas = []
for path in (src_root / "jobscraper" / "resources").glob("*.json"):
    datas.append((str(path), "jobscraper/resources"))
for path in (src_root / "jobscraper" / "assets").glob("*"):
    if path.is_file():
        datas.append((str(path), "jobscraper/assets"))
hiddenimports = [
    module
    for module in collect_submodules("jobscraper")
    if module != "jobscraper.ai.questions"
]
excludes = [
    "jobscraper.ai.questions",
    "playwright",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineQuick",
]


a = Analysis(
    [str(build_root / "linux_entry.py")],
    pathex=[str(src_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
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
    strip=True,
    upx=True,
    console=True,
    icon=str(icon_path),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=True,
    upx_exclude=[],
    name="JobScraper",
)
