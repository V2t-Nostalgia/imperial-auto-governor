# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH).resolve()
PACKET_ROOT = ROOT.parent / "packet_interceptor"
pydivert_datas, pydivert_binaries, pydivert_hidden = collect_all("pydivert")
psutil_datas, psutil_binaries, psutil_hidden = collect_all("psutil")
pydivert_datas = [
    item
    for item in pydivert_datas
    if "/tests/" not in str(item[0]).replace("\\", "/").lower()
    and not str(item[0]).replace("\\", "/").lower().endswith("/tests")
]
pydivert_hidden = [
    name for name in pydivert_hidden if not name.startswith("pydivert.tests")
]


a = Analysis(
    ["iag_save_uploader_gui.py"],
    pathex=[str(ROOT), str(PACKET_ROOT)],
    binaries=pydivert_binaries + psutil_binaries,
    datas=pydivert_datas + psutil_datas,
    hiddenimports=pydivert_hidden + psutil_hidden + [
        "iag_stream_command_injector",
        "iag_command_replacement_injector",
        "iag_building_to_zone_replacer",
        "iag_packet_interceptor",
        "iag_same_family_construction_rewriter",
        "iag_building_upgrade_rewriter",
        "iag_building_replacement_rewriter",
    ],
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
    name="IAGHostBridgeGUI",
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
    name="IAGHostBridgeGUI",
)
