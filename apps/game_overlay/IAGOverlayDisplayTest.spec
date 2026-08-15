# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


SPEC_ROOT = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_ROOT.parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
APP_ROOT = PROJECT_ROOT / "apps" / "game_overlay"


a = Analysis(
    [str(APP_ROOT / "display_test_main.py")],
    pathex=[str(PROJECT_ROOT), str(SRC_ROOT)],
    binaries=[],
    datas=[
        (str(APP_ROOT / "resources" / "analysis_phrases_zh.txt"), "resources"),
        (
            str(APP_ROOT / "resources" / "fonts" / "fusion_pixel"),
            "resources/fonts/fusion_pixel",
        ),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IAGOverlayDisplayTest",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="IAGOverlayDisplayTest",
)
