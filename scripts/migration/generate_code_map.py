#!/usr/bin/env python3
"""Generate a Chinese, source-aware index for every maintained project file.

The generator intentionally reads code rather than importing it. This keeps the
documentation step free from platform-specific side effects such as WinDivert,
NetfilterQueue, X11, or GUI initialization.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs" / "code-map" / "FILE_INDEX.md"
IGNORED_PARTS = {
    ".git",
    ".idea",
    ".tmp",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "runtime",
    "state",
    "logs",
    "captures",
    "saves",
    "databases",
    "artifacts",
    "public_release",
}
IGNORED_FILES = {OUTPUT.relative_to(ROOT).as_posix(), "ENGINEERING_MANIFEST.json"}


FILE_NOTES = {
    "src/iag/core/contracts.py": "定义跨 Agent、Mandate 与执行审计边界；未知字段会被拒绝。",
    "src/iag/core/application_registry.py": "显式 Application 注册表；阻止目录扫描加载未知代码。",
    "src/iag/applications/registry.py": "平台内置 Application 的唯一登记入口。",
    "src/iag/applications/economy_governance/planner.py": "把存档事实与 Content Pack 映射转换为合法建设候选。",
    "src/iag/applications/economy_governance/agent_tools.py": "向模型暴露受控工具，并维护准备、执行与事实账本。",
    "src/iag/applications/economy_governance/conversation_agent.py": "运行带工具调用的持续战役会话与自主巡检。",
    "src/iag/applications/fleet_operations/agent_tools.py": "向模型暴露逐舰队授权的移动、舰船设计与编制增援工具；未验证攻击保持失败关闭。",
    "src/iag/applications/research_strategy/agent_tools.py": "向模型暴露存档约束的三系科研状态、准备与串行确认工具。",
    "src/iag/applications/save_continuations.py": "在新存档到达后续接已授权的确定性跨存档工作流，不重新调用模型。",
    "src/iag/infrastructure/llm/model_client.py": "保持规划器同步接口，并编排异步模型协议调用与 JSON 解析。",
    "src/iag/infrastructure/llm/chat_stream.py": "重组流式正文、私有推理和工具参数，只向叠加层发布普通正文。",
    "src/iag/infrastructure/llm/providers.py": "以 AsyncOpenAI 为默认传输，并保留显式 raw HTTP 兼容实现。",
    "src/iag/stellaris/state/extract_game_state.py": "解包 Stellaris 存档并生成标准化帝国状态。",
    "src/iag/stellaris/state/planet_profiles.py": "解析殖民地、区域、槽位、容量、拥有者和建设队列。",
    "src/iag/stellaris/state/fleet_profiles.py": "解析玩家舰队、军力、位置、模板编制、增援队列和已验证动作目标。",
    "src/iag/stellaris/state/ship_profiles.py": "解析玩家舰船设计、部件槽和直接船坞协议证据。",
    "src/iag/stellaris/state/research_profiles.py": "分离已完成科技、当前研究、合法候选和储存研究点。",
    "src/iag/stellaris/execution/iag_supervisor.py": "在载体点击与会话代理之间编排准备、执行、确认和失败保护。",
    "src/iag/stellaris/execution/session_proxy.py": "在整局可靠流中插入已验证命令并持续映射 offset、ACK 与 actor serial。",
    "src/iag/stellaris/execution/session_proxy_controller.py": "管理 Windows 会话代理的进房前启动、串行动作提交和离房后停止。",
    "src/iag/stellaris/execution/protocol_compatibility.py": "登记全部会话代理命令，并生成版本兼容性计划、结果分类和基线差异报告。",
    "src/iag/stellaris/execution/protocol_compatibility_control.py": "持久化网页协议验收计划，以无模型调用的逐项状态机驱动离线检查和会话代理。",
    "src/iag/stellaris/execution/packet/iag_packet_interceptor.py": "WinDivert 一次性捕获框架、方向过滤和审计日志。",
    "src/iag/stellaris/execution/packet/fleet_reinforcement_commands.py": "精确解析并构造 Fleet Manager 编制增减与两阶段增援记录。",
    "src/iag/stellaris/execution/packet/ship_commands.py": "精确解析并构造舰船设计与直接船坞记录。",
    "apps/control_center/web_console.py": "战役会话、模型配置、巡检、研究和执行状态的 HTTPS 控制面。",
    "apps/control_center/windows_agent_gui.py": "Windows Agent 一键启动、运行目录、证书和健康检查界面。",
    "apps/host_bridge/iag_host_interceptor.py": "房主侧入站命令识别、受约束改写与权威回包确认。",
    "apps/host_bridge/iag_save_uploader.py": "选择稳定的最新房主存档并上传到 Agent 控制面。",
    "apps/control_center/visible_reply_stream.py": "维护只含玩家输入与模型可见正文的有界事件流。",
    "apps/game_overlay/window.py": "透明、置顶、可拖动且可切换鼠标穿透的游戏会话界面。",
    "apps/game_overlay/transport.py": "以证书固定 HTTPS 接收可见 SSE 事件并发送玩家消息。",
    "scripts/diagnostics/run_protocol_compatibility_suite.py": "先离线构包，再在明确授权的可丢弃房间中逐项验证已登记协议命令。",
    "content_packs/vanilla_4_4/mappings/capabilities.json": "Stellaris 4.4 原版建设对象与平台动作的声明式能力清单。",
    "stellaris_mod/common/scripted_effects/iag_carrier_effects.txt": "创建和维护建设载体星系、殖民地及其安全状态。",
    "requirements.txt": "Python 3.11+ 统一依赖；Windows/Linux 抓包依赖用环境标记隔离。",
    "MIGRATION_MANIFEST.json": "记录旧仓库固定提交到新路径的初始复制映射与哈希。",
}

PREFIX_NOTES = (
    ("src/iag/core/", "平台无关核心：政策、会话、上下文和稳定契约。"),
    ("src/iag/stellaris/state/", "Stellaris 存档事实读取与规范化。"),
    ("src/iag/stellaris/execution/packet/", "协作建设命令的离线解析与受约束改写。"),
    ("src/iag/stellaris/execution/", "游戏侧点击、网络发现、执行监督和确认。"),
    ("src/iag/applications/economy_governance/", "经济治理 Application 的规则、工具和提示词。"),
    ("src/iag/applications/fleet_operations/", "实验性舰队状态、逐舰队权限、移动、设计与编制增援工具。"),
    ("src/iag/applications/research_strategy/", "实验性科研状态、合法候选与科技选择工具。"),
    ("src/iag/applications/", "平台原生 Application 注册、跨域调度与确定性续接。"),
    ("src/iag/infrastructure/llm/", "模型供应商协议和请求模板适配。"),
    ("src/iag/infrastructure/research/", "不可信网页资料的检索与正文提取适配。"),
    ("apps/control_center/web/", "灰风控制台的浏览器端界面资源。"),
    ("apps/control_center/", "Agent 控制面与 Windows 启动入口。"),
    ("apps/game_overlay/", "房主侧透明游戏会话叠加层。"),
    ("apps/host_bridge/", "房主 Windows 端存档上传和入站改写桥。"),
    ("content_packs/", "游戏或 Mod 内容到平台既有概念的声明式映射。"),
    ("stellaris_mod/", "建设载体 Clausewitz Mod 源码。"),
    ("scripts/build/", "Windows 可执行程序的可重复构建入口。"),
    ("scripts/deploy/", "Ubuntu 虚拟环境、控制台和 systemd 部署。"),
    ("scripts/diagnostics/", "只读诊断，以及需显式授权的协议兼容性验收工具。"),
    ("scripts/migration/", "可审计迁移、哈希与代码地图维护工具。"),
    ("services/research/", "可选的本地联网检索服务。"),
    ("docs/reference/v0_5_8/", "迁移基线 v0.5.8 的原始操作与研究记录。"),
    ("docs/", "平台架构、开发和迁移文档。"),
    ("LICENSES/", "代码与文档许可证全文。"),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migration_sources() -> dict[str, str]:
    path = ROOT / "MIGRATION_MANIFEST.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["target"]): str(item["source"])
        for item in data.get("files", [])
    }


def maintained_files() -> list[Path]:
    values: list[Path] = []
    for path in ROOT.rglob("*"):
        if (
            not path.is_file()
            or any(part in IGNORED_PARTS for part in path.parts)
            or any(part.endswith(".egg-info") for part in path.parts)
        ):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in IGNORED_FILES or path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        values.append(path)
    return sorted(values, key=lambda item: item.relative_to(ROOT).as_posix())


def python_summary(path: Path) -> tuple[str, str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return "Python 模块；详细职责见邻接 README。", "-"
    doc = (ast.get_docstring(tree) or "").strip().splitlines()
    symbols = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]
    summary = doc[0] if doc else "Python 模块；详细职责见邻接 README。"
    return summary, ", ".join(f"`{name}`" for name in symbols[:8]) or "-"


def generic_summary(relative: str, path: Path) -> tuple[str, str]:
    if relative in FILE_NOTES:
        return FILE_NOTES[relative], "-"
    if path.name == "README.md":
        return "该目录的职责、边界、主要文件和开发注意事项。", "-"
    suffix = path.suffix.lower()
    if suffix == ".py":
        return python_summary(path)
    if suffix == ".json":
        return "结构化配置、schema 或声明式映射；运行时按 JSON 校验。", "-"
    if suffix == ".toml":
        return "版本化 manifest 或 Python 构建配置。", "-"
    if suffix in {".yml", ".yaml"}:
        return "声明式本地化或服务配置。", "-"
    if suffix == ".service":
        return "systemd 服务模板；安装时替换项目、运行目录和用户占位符。", "-"
    if suffix == ".spec":
        return "PyInstaller 构建图；收集 monorepo 包、资源与平台二进制。", "-"
    if suffix == ".ps1":
        return "Windows PowerShell 操作、构建或诊断入口。", "-"
    if suffix == ".sh":
        return "Ubuntu 安装或启动入口；使用独立虚拟环境和 runtime 目录。", "-"
    if suffix == ".js":
        return "控制台浏览器端交互、状态渲染或 API 调用逻辑。", "-"
    if suffix == ".css":
        return "控制台视觉布局和主题样式。", "-"
    if suffix == ".html":
        return "控制台页面结构和静态资源入口。", "-"
    if suffix in {".mod", ".txt"} and relative.startswith("stellaris_mod/"):
        return "Stellaris Clausewitz 定义；只影响建设载体 Mod。", "-"
    if suffix == ".md":
        return "项目说明、设计依据或历史参考文档。", "-"
    return "项目维护文件；用途由路径和邻接 README 约束。", "-"


def category(relative: str) -> str:
    for prefix, note in PREFIX_NOTES:
        if relative.startswith(prefix):
            return note
    return "仓库级配置、许可证或治理文件。"


def escape(value: str) -> str:
    return " ".join(value.replace("|", "\\|").split())


def main() -> int:
    source_by_target = migration_sources()
    rows: list[str] = []
    for path in maintained_files():
        relative = path.relative_to(ROOT).as_posix()
        summary, symbols = generic_summary(relative, path)
        if relative in FILE_NOTES:
            summary = FILE_NOTES[relative]
        source = source_by_target.get(relative, "新工程文件")
        rows.append(
            f"| `{escape(relative)}` | {escape(category(relative))} | {escape(summary)} | {escape(symbols)} | `{escape(source)}` |"
        )

    header = """# 逐文件代码地图

本文件由 `scripts/migration/generate_code_map.py` 生成。它覆盖所有受维护文件，并把旧仓库来源与新职责放在同一张表中。不要手工编辑生成表；先修改代码、邻接 README 或生成器中的关键说明，再重新生成。

“主要入口”来自 Python AST，只列顶层公开 class/function；`-` 表示该文件主要是数据、脚本入口或文档。哈希与迁移差异见 `MIGRATION_MANIFEST.json` 和 `ENGINEERING_MANIFEST.json`。

| 文件 | 所属边界 | 职责 | 主要入口 | v0.5.8 来源 |
|---|---|---|---|---|
"""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT} with {len(rows)} entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
