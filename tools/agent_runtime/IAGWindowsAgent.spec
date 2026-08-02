# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

root = Path(SPECPATH)
save_state_root = root.parent / "save_state"

datas = [
    (str(root / "agent_config.windows.example.json"), "."),
    (str(root / "capabilities.json"), "."),
    (str(root / "model_templates.json"), "."),
    (str(root / "web"), "web"),
    (str(root / "strategy"), "strategy"),
    (str(root / "schemas"), "schemas"),
]

a = Analysis(
    [str(root / "windows_agent_gui.py")],
    pathex=[str(root), str(save_state_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "web_console",
        "windows_fixed_click",
        "fixed_click",
        "extract_game_state",
        "extract_planet_profiles",
        "psutil",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["NetfilterQueue"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IAGWindowsAgent",
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="IAGWindowsAgent",
)
