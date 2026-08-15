# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

SPEC_ROOT = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_ROOT.parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
APP_ROOT = PROJECT_ROOT / "apps" / "control_center"
ECONOMY_ROOT = SRC_ROOT / "iag" / "applications" / "economy_governance"
LLM_ROOT = SRC_ROOT / "iag" / "infrastructure" / "llm"
CONTENT_ROOT = PROJECT_ROOT / "content_packs" / "vanilla_4_4"
pydivert_datas, pydivert_binaries, pydivert_hidden = collect_all("pydivert")
pydivert_datas = [
    item
    for item in pydivert_datas
    if "/tests/" not in str(item[0]).replace("\\", "/").lower()
    and not str(item[0]).replace("\\", "/").lower().endswith("/tests")
]
pydivert_hidden = [
    name for name in pydivert_hidden if not name.startswith("pydivert.tests")
]

datas = [
    (str(APP_ROOT / "agent_config.windows.example.json"), "."),
    (str(APP_ROOT / "web"), "web"),
    (str(APP_ROOT / "schemas"), "schemas"),
    (str(ECONOMY_ROOT / "prompts"), "iag/applications/economy_governance/prompts"),
    (str(ECONOMY_ROOT / "schemas"), "iag/applications/economy_governance/schemas"),
    (str(LLM_ROOT / "model_templates.json"), "iag/infrastructure/llm"),
    (str(CONTENT_ROOT), "content_packs/vanilla_4_4"),
]

a = Analysis(
    [str(APP_ROOT / "windows_agent_gui.py")],
    pathex=[str(PROJECT_ROOT), str(SRC_ROOT)],
    binaries=pydivert_binaries,
    datas=datas + pydivert_datas,
    hiddenimports=pydivert_hidden + [
        "apps.control_center.web_console",
        "iag.applications.economy_governance.planner",
        "iag.applications.fleet_operations.agent_tools",
        "iag.stellaris.execution.fixed_click",
        "iag.stellaris.execution.session_proxy",
        "iag.stellaris.execution.session_proxy_controller",
        "iag.stellaris.execution.packet.autonomous_commands",
        "iag.stellaris.execution.packet.iag_stream_command_injector",
        "iag.stellaris.execution.windows_fixed_click",
        "iag.stellaris.state.extract_game_state",
        "iag.stellaris.state.fleet_profiles",
        "iag.stellaris.state.planet_profiles",
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
