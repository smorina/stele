# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the portable macOS Stele.app bundle."""

import os
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = Path(SPECPATH).parent
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
VERSION = PROJECT["project"]["version"]
MINIMUM_MACOS = os.environ.get("STELE_MACOS_MIN_VERSION", "13.0")

datas = [
    (str(ROOT / "src/stele/ui/static/app.html"), "stele/ui/static"),
    (str(ROOT / "assets/bluenoise/vnc64.npy"), "stele/assets/bluenoise"),
]
binaries = []
hiddenimports = [
    "cv2",
    "fitz",
    "gdstk",
    "klayout.db",
    "numpy",
    "PIL",
    "pydantic",
    "pypdfium2",
    "pypdfium2_raw",
    "shapely",
    "yaml",
]

# KLayout discovers its format plug-ins dynamically. PDFium likewise loads its
# bundled native library at runtime, so static import analysis cannot see every
# file that must travel with the app.
for package in ("klayout", "pypdfium2_raw"):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

# stele doctor reports exact bundled versions through importlib.metadata.
for distribution in (
    "gdstk",
    "PyMuPDF",
    "pypdfium2",
    "opencv-python-headless",
    "shapely",
    "numpy",
    "Pillow",
    "pydantic",
    "PyYAML",
    "klayout",
):
    datas += copy_metadata(distribution, recursive=True)

analysis = Analysis(
    [str(ROOT / "src/stele/app.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Stele",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="Stele",
)
app = BUNDLE(
    collection,
    name="Stele.app",
    bundle_identifier="org.smorina.stele",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "Stele",
        "CFBundleName": "Stele",
        "CFBundleShortVersionString": VERSION,
        "LSApplicationCategoryType": "public.app-category.graphics-design",
        "LSMinimumSystemVersion": MINIMUM_MACOS,
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "AGPL-3.0-or-later",
    },
)
