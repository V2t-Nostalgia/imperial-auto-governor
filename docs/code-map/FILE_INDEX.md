# 逐文件代码地图

本文件由 `scripts/migration/generate_code_map.py` 生成。它覆盖所有受维护文件，并把旧仓库来源与新职责放在同一张表中。不要手工编辑生成表；先修改代码、邻接 README 或生成器中的关键说明，再重新生成。

“主要入口”来自 Python AST，只列顶层公开 class/function；`-` 表示该文件主要是数据、脚本入口或文档。哈希与迁移差异见 `MIGRATION_MANIFEST.json` 和 `ENGINEERING_MANIFEST.json`。

| 文件 | 所属边界 | 职责 | 主要入口 | v0.5.8 来源 |
|---|---|---|---|---|
| `.github/PULL_REQUEST_TEMPLATE.md` | 仓库级配置、许可证或治理文件。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `.github/scripts/check_dco.py` | 仓库级配置、许可证或治理文件。 | Fail when a commit message read from stdin lacks a DCO sign-off. | `main` | `新工程文件` |
| `.github/workflows/dco.yml` | 仓库级配置、许可证或治理文件。 | 声明式本地化或服务配置。 | - | `新工程文件` |
| `.github/workflows/tests.yml` | 仓库级配置、许可证或治理文件。 | 声明式本地化或服务配置。 | - | `新工程文件` |
| `.gitignore` | 仓库级配置、许可证或治理文件。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `新工程文件` |
| `.zenodo.json` | 仓库级配置、许可证或治理文件。 | 结构化配置、schema 或声明式映射；运行时按 JSON 校验。 | - | `新工程文件` |
| `AUTHORS.md` | 仓库级配置、许可证或治理文件。 | 项目说明、设计依据或历史参考文档。 | - | `AUTHORS.md` |
| `CITATION.cff` | 仓库级配置、许可证或治理文件。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `CITATION.cff` |
| `CONTRIBUTING.md` | 仓库级配置、许可证或治理文件。 | 项目说明、设计依据或历史参考文档。 | - | `CONTRIBUTING.md` |
| `DCO` | 仓库级配置、许可证或治理文件。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `DCO` |
| `DEDICATION.md` | 仓库级配置、许可证或治理文件。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `LICENSE` | 仓库级配置、许可证或治理文件。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `LICENSE` |
| `LICENSES/CC-BY-SA-4.0.txt` | 代码与文档许可证全文。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `LICENSES/CC-BY-SA-4.0.txt` |
| `LICENSES/GPL-3.0-only.txt` | 代码与文档许可证全文。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `LICENSES/GPL-3.0-only.txt` |
| `MIGRATION_MANIFEST.json` | 仓库级配置、许可证或治理文件。 | 记录旧仓库固定提交到新路径的初始复制映射与哈希。 | - | `新工程文件` |
| `NOTICE` | 仓库级配置、许可证或治理文件。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `NOTICE` |
| `ORIGIN.md` | 仓库级配置、许可证或治理文件。 | 项目说明、设计依据或历史参考文档。 | - | `ORIGIN.md` |
| `README.md` | 仓库级配置、许可证或治理文件。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `REUSE.toml` | 仓库级配置、许可证或治理文件。 | 版本化 manifest 或 Python 构建配置。 | - | `REUSE.toml` |
| `SECURITY.md` | 仓库级配置、许可证或治理文件。 | 项目说明、设计依据或历史参考文档。 | - | `SECURITY.md` |
| `TRADEMARKS.md` | 仓库级配置、许可证或治理文件。 | 项目说明、设计依据或历史参考文档。 | - | `TRADEMARKS.md` |
| `apps/README.md` | 仓库级配置、许可证或治理文件。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `apps/__init__.py` | 仓库级配置、许可证或治理文件。 | 可直接启动的进程入口；可复用业务逻辑必须留在 ``src/iag``。 | - | `新工程文件` |
| `apps/control_center/IAGWindowsAgent.spec` | Agent 控制面与 Windows 启动入口。 | PyInstaller 构建图；收集 monorepo 包、资源与平台二进制。 | - | `tools/agent_runtime/IAGWindowsAgent.spec` |
| `apps/control_center/README.md` | Agent 控制面与 Windows 启动入口。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `apps/control_center/__init__.py` | Agent 控制面与 Windows 启动入口。 | 玩家控制中心、持久会话服务和 Windows Agent 图形入口。 | - | `新工程文件` |
| `apps/control_center/agent_config.linux.example.json` | Agent 控制面与 Windows 启动入口。 | 结构化配置、schema 或声明式映射；运行时按 JSON 校验。 | - | `tools/agent_runtime/agent_config.example.json` |
| `apps/control_center/agent_config.windows.example.json` | Agent 控制面与 Windows 启动入口。 | 结构化配置、schema 或声明式映射；运行时按 JSON 校验。 | - | `tools/agent_runtime/agent_config.windows.example.json` |
| `apps/control_center/schemas/fixed_click_profile.schema.json` | Agent 控制面与 Windows 启动入口。 | 结构化配置、schema 或声明式映射；运行时按 JSON 校验。 | - | `tools/agent_runtime/schemas/fixed_click_profile.schema.json` |
| `apps/control_center/tests/README.md` | Agent 控制面与 Windows 启动入口。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `apps/control_center/tests/test_application_agent_routing.py` | Agent 控制面与 Windows 启动入口。 | Integration checks for independent Application conversation agents. | `ApplicationAgentRoutingTests` | `新工程文件` |
| `apps/control_center/tests/test_execution_modes_console.py` | Agent 控制面与 Windows 启动入口。 | Python 模块；详细职责见邻接 README。 | `settings_payload`, `ExecutionModeConsoleTests` | `新工程文件` |
| `apps/control_center/tests/test_model_configuration_frontend.py` | Agent 控制面与 Windows 启动入口。 | Static contracts for the model catalog and Application profile UI. | `ModelConfigurationFrontendTests` | `新工程文件` |
| `apps/control_center/tests/test_model_pool_console.py` | Agent 控制面与 Windows 启动入口。 | Narrow integration tests for model-pool console persistence. | `endpoint`, `ModelPoolConsoleTests` | `新工程文件` |
| `apps/control_center/tests/test_save_continuation_console.py` | Agent 控制面与 Windows 启动入口。 | Python 模块；详细职责见邻接 README。 | `SaveContinuationConsoleTests` | `新工程文件` |
| `apps/control_center/tests/test_visible_reply_stream.py` | Agent 控制面与 Windows 启动入口。 | Tests for the redacted game-overlay event boundary. | `VisibleReplyBrokerTests`, `OverlayTranscriptTests` | `新工程文件` |
| `apps/control_center/tests/test_windows_agent_bundle_resources.py` | Agent 控制面与 Windows 启动入口。 | Python 模块；详细职责见邻接 README。 | `WindowsAgentBundleResourceTests` | `新工程文件` |
| `apps/control_center/visible_reply_stream.py` | Agent 控制面与 Windows 启动入口。 | 维护只含玩家输入与模型可见正文的有界事件流。 | - | `新工程文件` |
| `apps/control_center/web/app.js` | 灰风控制台的浏览器端界面资源。 | 控制台浏览器端交互、状态渲染或 API 调用逻辑。 | - | `tools/agent_runtime/web/app.js` |
| `apps/control_center/web/app_v2.js` | 灰风控制台的浏览器端界面资源。 | 控制台浏览器端交互、状态渲染或 API 调用逻辑。 | - | `tools/agent_runtime/web/app_v2.js` |
| `apps/control_center/web/construction.css` | 灰风控制台的浏览器端界面资源。 | 控制台视觉布局和主题样式。 | - | `tools/agent_runtime/web/construction.css` |
| `apps/control_center/web/conversation.css` | 灰风控制台的浏览器端界面资源。 | 控制台视觉布局和主题样式。 | - | `tools/agent_runtime/web/conversation.css` |
| `apps/control_center/web/index.html` | 灰风控制台的浏览器端界面资源。 | 控制台页面结构和静态资源入口。 | - | `tools/agent_runtime/web/index.html` |
| `apps/control_center/web/styles.css` | 灰风控制台的浏览器端界面资源。 | 控制台视觉布局和主题样式。 | - | `tools/agent_runtime/web/styles.css` |
| `apps/control_center/web_console.py` | Agent 控制面与 Windows 启动入口。 | 战役会话、模型配置、巡检、研究和执行状态的 HTTPS 控制面。 | - | `tools/agent_runtime/web_console.py` |
| `apps/control_center/windows_agent_gui.py` | Agent 控制面与 Windows 启动入口。 | Windows Agent 一键启动、运行目录、证书和健康检查界面。 | - | `tools/agent_runtime/windows_agent_gui.py` |
| `apps/game_overlay/DISPLAY_TEST_README.md` | 房主侧透明游戏会话叠加层。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `apps/game_overlay/IAGOverlay.spec` | 房主侧透明游戏会话叠加层。 | PyInstaller 构建图；收集 monorepo 包、资源与平台二进制。 | - | `新工程文件` |
| `apps/game_overlay/IAGOverlayDisplayTest.spec` | 房主侧透明游戏会话叠加层。 | PyInstaller 构建图；收集 monorepo 包、资源与平台二进制。 | - | `新工程文件` |
| `apps/game_overlay/README.md` | 房主侧透明游戏会话叠加层。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `apps/game_overlay/__init__.py` | 房主侧透明游戏会话叠加层。 | Transparent in-game conversation overlay for Imperial Auto Governor. | - | `新工程文件` |
| `apps/game_overlay/display_test_main.py` | 房主侧透明游戏会话叠加层。 | Entrypoint for the local-only IAG overlay display test executable. | `parse_args`, `main` | `新工程文件` |
| `apps/game_overlay/display_test_window.py` | 房主侧透明游戏会话叠加层。 | Local-only overlay window used to test presentation without an Agent. | `chunk_visible_text`, `DisplayTestWindow` | `新工程文件` |
| `apps/game_overlay/hotkey.py` | 房主侧透明游戏会话叠加层。 | Configurable Windows global hotkey parsing and registration. | `HotkeySpec`, `parse_hotkey`, `WindowsHotkeyListener` | `新工程文件` |
| `apps/game_overlay/main.py` | 房主侧透明游戏会话叠加层。 | Entrypoint for the standalone Imperial Auto Governor game overlay. | `resource_root`, `parse_args`, `preview_configuration`, `main` | `新工程文件` |
| `apps/game_overlay/overlay_config.py` | 房主侧透明游戏会话叠加层。 | Load and persist the game overlay portion of the Host Bridge config. | `OverlayGeometry`, `OverlayConnection`, `OverlaySettings`, `OverlayConfiguration`, `ensure_analysis_phrase_file`, `load_analysis_phrases`, `bundled_font_file`, `load_overlay_configuration` | `新工程文件` |
| `apps/game_overlay/resources/analysis_phrases_zh.txt` | 房主侧透明游戏会话叠加层。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `新工程文件` |
| `apps/game_overlay/resources/fonts/fusion_pixel/LICENSES/ark-pixel/OFL.txt` | 房主侧透明游戏会话叠加层。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `新工程文件` |
| `apps/game_overlay/resources/fonts/fusion_pixel/LICENSES/cubic-11/OFL.txt` | 房主侧透明游戏会话叠加层。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `新工程文件` |
| `apps/game_overlay/resources/fonts/fusion_pixel/LICENSES/galmuri/LICENSE.txt` | 房主侧透明游戏会话叠加层。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `新工程文件` |
| `apps/game_overlay/resources/fonts/fusion_pixel/OFL.txt` | 房主侧透明游戏会话叠加层。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `新工程文件` |
| `apps/game_overlay/resources/fonts/fusion_pixel/fusion-pixel-12px-monospaced-zh_hans.ttf` | 房主侧透明游戏会话叠加层。 | 项目维护文件；用途由路径和邻接 README 约束。 | - | `新工程文件` |
| `apps/game_overlay/tests/test_display_test.py` | 房主侧透明游戏会话叠加层。 | Local display-test mode must remain independent from Agent authentication. | `DisplayTestConfigurationTests` | `新工程文件` |
| `apps/game_overlay/tests/test_hotkey.py` | 房主侧透明游戏会话叠加层。 | Pure hotkey parser tests; no global registration is performed. | `HotkeyParserTests` | `新工程文件` |
| `apps/game_overlay/tests/test_transport.py` | 房主侧透明游戏会话叠加层。 | SSE parser tests for the standalone overlay transport. | `SSEParserTests` | `新工程文件` |
| `apps/game_overlay/tests/test_window.py` | 房主侧透明游戏会话叠加层。 | Rendering-contract tests for the transparent game overlay. | `OverlayWindowTests` | `新工程文件` |
| `apps/game_overlay/transport.py` | 房主侧透明游戏会话叠加层。 | 以证书固定 HTTPS 接收可见 SSE 事件并发送玩家消息。 | - | `新工程文件` |
| `apps/game_overlay/window.py` | 房主侧透明游戏会话叠加层。 | 透明、置顶、可拖动且可切换鼠标穿透的游戏会话界面。 | - | `新工程文件` |
| `apps/host_bridge/IAGSaveUploaderGUI.spec` | 房主 Windows 端存档上传和入站改写桥。 | PyInstaller 构建图；收集 monorepo 包、资源与平台二进制。 | - | `tools/windows_save_uploader/IAGSaveUploaderGUI.spec` |
| `apps/host_bridge/README.md` | 房主 Windows 端存档上传和入站改写桥。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `apps/host_bridge/__init__.py` | 房主 Windows 端存档上传和入站改写桥。 | 运行在房主 Windows 机器上的存档上传与权威入站改写桥。 | - | `新工程文件` |
| `apps/host_bridge/iag_host_interceptor.py` | 房主 Windows 端存档上传和入站改写桥。 | 房主侧入站命令识别、受约束改写与权威回包确认。 | - | `tools/windows_save_uploader/iag_host_interceptor.py` |
| `apps/host_bridge/iag_save_uploader.example.json` | 房主 Windows 端存档上传和入站改写桥。 | 结构化配置、schema 或声明式映射；运行时按 JSON 校验。 | - | `tools/windows_save_uploader/iag_save_uploader.example.json` |
| `apps/host_bridge/iag_save_uploader.py` | 房主 Windows 端存档上传和入站改写桥。 | 选择稳定的最新房主存档并上传到 Agent 控制面。 | - | `tools/windows_save_uploader/iag_save_uploader.py` |
| `apps/host_bridge/iag_save_uploader_gui.py` | 房主 Windows 端存档上传和入站改写桥。 | Manual Windows GUI for the IAG Stellaris host save uploader. | `run_health_check`, `relaunch_as_administrator`, `default_config`, `normalize_server_url`, `display_server_url`, `read_config`, `resolve_relative`, `UploaderGUI` | `tools/windows_save_uploader/iag_save_uploader_gui.py` |
| `apps/host_bridge/tests/README.md` | 房主 Windows 端存档上传和入站改写桥。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `content_packs/README.md` | 游戏或 Mod 内容到平台既有概念的声明式映射。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `content_packs/vanilla_4_4/README.md` | 游戏或 Mod 内容到平台既有概念的声明式映射。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `content_packs/vanilla_4_4/mappings/capabilities.json` | 游戏或 Mod 内容到平台既有概念的声明式映射。 | Stellaris 4.4 原版建设对象与平台动作的声明式能力清单。 | - | `tools/agent_runtime/capabilities.json` |
| `content_packs/vanilla_4_4/pack.toml` | 游戏或 Mod 内容到平台既有概念的声明式映射。 | 版本化 manifest 或 Python 构建配置。 | - | `新工程文件` |
| `content_packs/vanilla_4_4/prompts/stellaris_4_4_sources.md` | 游戏或 Mod 内容到平台既有概念的声明式映射。 | 项目说明、设计依据或历史参考文档。 | - | `tools/agent_runtime/strategy/stellaris_4_4_sources.md` |
| `content_packs/vanilla_4_4/tests/README.md` | 游戏或 Mod 内容到平台既有概念的声明式映射。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `docs/ARCHITECTURE.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/DEVELOPMENT.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/FLEET_OPERATIONS.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/MIGRATION.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/PUBLICATION.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/RESEARCH_STRATEGY.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/SESSION_PROXY_MODE.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/VERIFICATION.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/code-map/README.md` | 平台架构、开发和迁移文档。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `docs/protocol/APPLICATION_COMMAND_FRAMING_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/ARMY_LANDING_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/ARMY_RECRUITMENT_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/COLONIZATION_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/CONSTRUCTION_SHIP_BUILD_STARBASE_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/FLEET_ATTACK_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/FLEET_COORDINATE_MOVE_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/FLEET_MAINTENANCE_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/FLEET_TEMPLATE_REINFORCEMENT_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/OOS_RESYNC_SERIAL_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/ORBITAL_BOMBARDMENT_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/PROTOCOL_COMPATIBILITY_SUITE.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/RESEARCH_SELECTION_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/SHIP_DESIGN_FULL_BLUEPRINT_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/STARBASE_MODULE_BUILDING_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/protocol/STARBASE_UPGRADE_4_4_6.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/reference/v0_5_8/AGENT_RUNTIME.md` | 迁移基线 v0.5.8 的原始操作与研究记录。 | 项目说明、设计依据或历史参考文档。 | - | `tools/agent_runtime/README.md` |
| `docs/reference/v0_5_8/HOST_BRIDGE.md` | 迁移基线 v0.5.8 的原始操作与研究记录。 | 项目说明、设计依据或历史参考文档。 | - | `tools/windows_save_uploader/README.md` |
| `docs/reference/v0_5_8/PACKET_INTERCEPTOR.md` | 迁移基线 v0.5.8 的原始操作与研究记录。 | 项目说明、设计依据或历史参考文档。 | - | `tools/packet_interceptor/README.md` |
| `docs/reference/v0_5_8/README.md` | 迁移基线 v0.5.8 的原始操作与研究记录。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `docs/reference/v0_5_8/RESEARCH_SERVICES.md` | 迁移基线 v0.5.8 的原始操作与研究记录。 | 项目说明、设计依据或历史参考文档。 | - | `docs/RESEARCH_SERVICES.md` |
| `docs/reference/v0_5_8/STEAM_RELAY_HOTFIX.md` | 迁移基线 v0.5.8 的原始操作与研究记录。 | 项目说明、设计依据或历史参考文档。 | - | `docs/STEAM_RELAY_HOTFIX.md` |
| `docs/reference/v0_5_8/TECHNICAL_OVERVIEW.md` | 迁移基线 v0.5.8 的原始操作与研究记录。 | 项目说明、设计依据或历史参考文档。 | - | `docs/TECHNICAL_OVERVIEW.md` |
| `docs/reference/v0_5_8/UBUNTU_AGENT_README.md` | 迁移基线 v0.5.8 的原始操作与研究记录。 | 项目说明、设计依据或历史参考文档。 | - | `docs/UBUNTU_AGENT_README.md` |
| `docs/reference/v0_5_8/WINDOWS_AGENT_README.md` | 迁移基线 v0.5.8 的原始操作与研究记录。 | 项目说明、设计依据或历史参考文档。 | - | `docs/WINDOWS_AGENT_README.md` |
| `docs/reference/v0_5_8/WINDOWS_FULL_DEPLOYMENT.md` | 迁移基线 v0.5.8 的原始操作与研究记录。 | 项目说明、设计依据或历史参考文档。 | - | `docs/WINDOWS_FULL_DEPLOYMENT.md` |
| `docs/releases/v0.5.10.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `docs/releases/v0.5.9.md` | 平台架构、开发和迁移文档。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `pyproject.toml` | 仓库级配置、许可证或治理文件。 | 版本化 manifest 或 Python 构建配置。 | - | `新工程文件` |
| `requirements.txt` | 仓库级配置、许可证或治理文件。 | Python 3.11+ 统一依赖；Windows/Linux 抓包依赖用环境标记隔离。 | - | `新工程文件` |
| `scripts/README.md` | 仓库级配置、许可证或治理文件。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `scripts/build/Build-IAGHostBridge.ps1` | Windows 可执行程序的可重复构建入口。 | Windows PowerShell 操作、构建或诊断入口。 | - | `tools/windows_save_uploader/Build-IAGHostBridge.ps1` |
| `scripts/build/Build-IAGOverlayDisplayTest.ps1` | Windows 可执行程序的可重复构建入口。 | Windows PowerShell 操作、构建或诊断入口。 | - | `新工程文件` |
| `scripts/build/Build-IAGWindowsAgent.ps1` | Windows 可执行程序的可重复构建入口。 | Windows PowerShell 操作、构建或诊断入口。 | - | `tools/agent_runtime/Build-IAGWindowsAgent.ps1` |
| `scripts/deploy/install_systemd_service.sh` | Ubuntu 虚拟环境、控制台和 systemd 部署。 | Ubuntu 安装或启动入口；使用独立虚拟环境和 runtime 目录。 | - | `tools/agent_runtime/install_systemd_service.sh` |
| `scripts/deploy/install_ubuntu.sh` | Ubuntu 虚拟环境、控制台和 systemd 部署。 | Ubuntu 安装或启动入口；使用独立虚拟环境和 runtime 目录。 | - | `tools/agent_runtime/install_ubuntu.sh` |
| `scripts/deploy/start_console.sh` | Ubuntu 虚拟环境、控制台和 systemd 部署。 | Ubuntu 安装或启动入口；使用独立虚拟环境和 runtime 目录。 | - | `tools/agent_runtime/start_console.sh` |
| `scripts/deploy/systemd/iag-agent-console.service` | Ubuntu 虚拟环境、控制台和 systemd 部署。 | systemd 服务模板；安装时替换项目、运行目录和用户占位符。 | - | `tools/agent_runtime/deploy/systemd/iag-agent-console.service` |
| `scripts/deploy/systemd/iag-agent-network-observer.service` | Ubuntu 虚拟环境、控制台和 systemd 部署。 | systemd 服务模板；安装时替换项目、运行目录和用户占位符。 | - | `tools/agent_runtime/deploy/systemd/iag-agent-network-observer.service` |
| `scripts/diagnostics/Export-IAGPlanetProfiles.ps1` | 只读诊断，以及需显式授权的协议兼容性验收工具。 | Windows PowerShell 操作、构建或诊断入口。 | - | `tools/save_state/Export-IAGPlanetProfiles.ps1` |
| `scripts/diagnostics/iag_udp_flow_analyzer.py` | 只读诊断，以及需显式授权的协议兼容性验收工具。 | Summarize passive UDP flow traces produced by iag_packet_interceptor.py. | `parse_endpoint`, `parse_args`, `main` | `tools/packet_interceptor/iag_udp_flow_analyzer.py` |
| `scripts/diagnostics/run_protocol_compatibility_suite.py` | 只读诊断，以及需显式授权的协议兼容性验收工具。 | 先离线构包，再在明确授权的可丢弃房间中逐项验证已登记协议命令。 | - | `新工程文件` |
| `scripts/migration/finalize_engineering_manifest.py` | 可审计迁移、哈希与代码地图维护工具。 | Finalize privacy-safe migration and engineering manifests. | `sha256`, `normalize_migration`, `maintained_files`, `main` | `新工程文件` |
| `scripts/migration/generate_code_map.py` | 可审计迁移、哈希与代码地图维护工具。 | Generate a Chinese, source-aware index for every maintained project file. | `sha256`, `migration_sources`, `maintained_files`, `python_summary`, `generic_summary`, `category`, `escape`, `main` | `新工程文件` |
| `scripts/migration/migrate_from_v058.py` | 可审计迁移、哈希与代码地图维护工具。 | 从冻结的 IAG v0.5.8 仓库复制已验证生产文件并生成来源清单。 | `sha256`, `tracked_files`, `copy_one`, `source_commit`, `parse_args`, `main` | `新工程文件` |
| `scripts/migration/migrate_runtime_config_v2.py` | 可审计迁移、哈希与代码地图维护工具。 | Add persistent-conversation defaults without replacing user credentials. | `migrate_config`, `main` | `tools/agent_runtime/migrate_config_v2.py` |
| `scripts/release/build_release.py` | 仓库级配置、许可证或治理文件。 | Build and verify the five public v0.5.10 release attachments. | `network_from_spec`, `ReleaseError`, `project_version`, `sha256_file`, `run`, `source_commit`, `tracked_files`, `recreate` | `新工程文件` |
| `scripts/validation/run_tests.py` | 仓库级配置、许可证或治理文件。 | Run every repository-local unittest module without requiring pytest. | `test_modules`, `build_suite`, `selected_test_modules`, `main` | `新工程文件` |
| `services/research/Manage-IAGResearchServices.ps1` | 可选的本地联网检索服务。 | Windows PowerShell 操作、构建或诊断入口。 | - | `tools/research_services/Manage-IAGResearchServices.ps1` |
| `services/research/README.md` | 可选的本地联网检索服务。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `services/research/compose.yaml` | 可选的本地联网检索服务。 | 声明式本地化或服务配置。 | - | `tools/research_services/compose.yaml` |
| `services/research/searxng/settings.yml` | 可选的本地联网检索服务。 | 声明式本地化或服务配置。 | - | `tools/research_services/searxng/settings.yml` |
| `src/iag/__init__.py` | 仓库级配置、许可证或治理文件。 | Imperial Auto Governor 群星多 Agent 平台。 | - | `新工程文件` |
| `src/iag/applications/README.md` | 平台原生 Application 注册、跨域调度与确定性续接。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/applications/__init__.py` | 平台原生 Application 注册、跨域调度与确定性续接。 | 平台内置的第一方领域 Application。 | - | `新工程文件` |
| `src/iag/applications/economy_governance/README.md` | 经济治理 Application 的规则、工具和提示词。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/applications/economy_governance/__init__.py` | 经济治理 Application 的规则、工具和提示词。 | 殖民地经济治理 Application。 | - | `新工程文件` |
| `src/iag/applications/economy_governance/agent_tools.py` | 经济治理 Application 的规则、工具和提示词。 | 向模型暴露受控工具，并维护准备、执行与事实账本。 | - | `tools/agent_runtime/agent_tools.py` |
| `src/iag/applications/economy_governance/application.toml` | 经济治理 Application 的规则、工具和提示词。 | 版本化 manifest 或 Python 构建配置。 | - | `新工程文件` |
| `src/iag/applications/economy_governance/conversation_agent.py` | 经济治理 Application 的规则、工具和提示词。 | 运行带工具调用的持续战役会话与自主巡检。 | - | `tools/agent_runtime/conversation_agent.py` |
| `src/iag/applications/economy_governance/iag_agent.py` | 经济治理 Application 的规则、工具和提示词。 | Create one auditable IAG plan and execution manifest. | `optional_text`, `configured_path`, `run_cycle`, `parse_args`, `main` | `tools/agent_runtime/iag_agent.py` |
| `src/iag/applications/economy_governance/planner.py` | 经济治理 Application 的规则、工具和提示词。 | 把存档事实与 Content Pack 映射转换为合法建设候选。 | - | `tools/agent_runtime/planner.py` |
| `src/iag/applications/economy_governance/prompts/grey_tempest_conversation_zh.md` | 经济治理 Application 的规则、工具和提示词。 | 项目说明、设计依据或历史参考文档。 | - | `tools/agent_runtime/strategy/grey_tempest_conversation_zh.md` |
| `src/iag/applications/economy_governance/prompts/grey_tempest_governor_zh.md` | 经济治理 Application 的规则、工具和提示词。 | 项目说明、设计依据或历史参考文档。 | - | `tools/agent_runtime/strategy/grey_tempest_governor_zh.md` |
| `src/iag/applications/economy_governance/schemas/agent_plan.schema.json` | 经济治理 Application 的规则、工具和提示词。 | 结构化配置、schema 或声明式映射；运行时按 JSON 校验。 | - | `tools/agent_runtime/schemas/agent_plan.schema.json` |
| `src/iag/applications/economy_governance/tests/README.md` | 经济治理 Application 的规则、工具和提示词。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/applications/economy_governance/tests/test_agent_tool_accounting.py` | 经济治理 Application 的规则、工具和提示词。 | Python 模块；详细职责见邻接 README。 | `AgentToolAccountingTests` | `新工程文件` |
| `src/iag/applications/economy_governance/tests/test_visible_conversation_events.py` | 经济治理 Application 的规则、工具和提示词。 | Ensure the governor exposes only player and model-visible conversation data. | `FakeRuntimeConfig`, `FakeToolbox`, `StreamingCompletion`, `TestConversationAgent`, `CompletedConstructionToolbox`, `FallbackTrackingAgent`, `VisibleConversationEventTests` | `新工程文件` |
| `src/iag/applications/fleet_operations/README.md` | 实验性舰队、民用船、扩张、设计与编制工具。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/applications/fleet_operations/__init__.py` | 实验性舰队、民用船、扩张、设计与编制工具。 | Experimental fleet observation and order Application. | - | `新工程文件` |
| `src/iag/applications/fleet_operations/agent_tools.py` | 实验性舰队、民用船、扩张、设计与编制工具。 | 向模型暴露存档候选约束的舰队、民用船、殖民、恒星基地、舰船设计与编制工具。 | - | `新工程文件` |
| `src/iag/applications/fleet_operations/application.toml` | 实验性舰队、民用船、扩张、设计与编制工具。 | 版本化 manifest 或 Python 构建配置。 | - | `新工程文件` |
| `src/iag/applications/fleet_operations/conversation_agent.py` | 实验性舰队、民用船、扩张、设计与编制工具。 | Independent persistent model agent for fleet and ship operations. | `FleetConversationAgent` | `新工程文件` |
| `src/iag/applications/fleet_operations/prompts/fleet_operator_zh.md` | 实验性舰队、民用船、扩张、设计与编制工具。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `src/iag/applications/fleet_operations/tests/test_agent_tools.py` | 实验性舰队、民用船、扩张、设计与编制工具。 | Python 模块；详细职责见邻接 README。 | `fleet_profile`, `expansion_profile`, `invasion_profile`, `StaticCampaignPlanner`, `FleetToolboxTests` | `新工程文件` |
| `src/iag/applications/fleet_operations/tests/test_conversation_agent.py` | 实验性舰队、民用船、扩张、设计与编制工具。 | Python 模块；详细职责见邻接 README。 | `FakeRuntimeConfig`, `ConfirmedFleetToolbox`, `FleetCompletion`, `FleetConversationAgentTests` | `新工程文件` |
| `src/iag/applications/fleet_operations/tests/test_save_continuations.py` | 实验性舰队、民用船、扩张、设计与编制工具。 | Python 模块；详细职责见邻接 README。 | `SaveContinuationTests` | `新工程文件` |
| `src/iag/applications/joint_plan_review.py` | 平台原生 Application 注册、跨域调度与确定性续接。 | 按游戏月份运行压缩的跨领域计划审查，并把建议投递给计划所有者。 | - | `新工程文件` |
| `src/iag/applications/plan_advisor.py` | 平台原生 Application 注册、跨域调度与确定性续接。 | 使用可选快速模型处理局部计划异常，并限制补丁只能修改受影响分支。 | - | `新工程文件` |
| `src/iag/applications/registry.py` | 平台原生 Application 注册、跨域调度与确定性续接。 | 平台内置 Application 的唯一登记入口。 | - | `新工程文件` |
| `src/iag/applications/research_strategy/README.md` | 实验性科研状态、合法候选与科技选择工具。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/applications/research_strategy/__init__.py` | 实验性科研状态、合法候选与科技选择工具。 | Save-backed, experimental research selection Application. | - | `新工程文件` |
| `src/iag/applications/research_strategy/agent_tools.py` | 实验性科研状态、合法候选与科技选择工具。 | 向模型暴露存档约束的三系科研状态、准备与串行确认工具。 | - | `新工程文件` |
| `src/iag/applications/research_strategy/application.toml` | 实验性科研状态、合法候选与科技选择工具。 | 版本化 manifest 或 Python 构建配置。 | - | `新工程文件` |
| `src/iag/applications/research_strategy/conversation_agent.py` | 实验性科研状态、合法候选与科技选择工具。 | Independent persistent model agent for research selection. | `ResearchConversationAgent` | `新工程文件` |
| `src/iag/applications/research_strategy/prompts/research_director_zh.md` | 实验性科研状态、合法候选与科技选择工具。 | 项目说明、设计依据或历史参考文档。 | - | `新工程文件` |
| `src/iag/applications/research_strategy/tests/README.md` | 实验性科研状态、合法候选与科技选择工具。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/applications/research_strategy/tests/test_agent_tools.py` | 实验性科研状态、合法候选与科技选择工具。 | Python 模块；详细职责见邻接 README。 | `research_profile`, `TechnologyToolboxTests` | `新工程文件` |
| `src/iag/applications/research_strategy/tests/test_conversation_agent.py` | 实验性科研状态、合法候选与科技选择工具。 | Python 模块；详细职责见邻接 README。 | `FakeRuntimeConfig`, `ResearchConversationAgentTests` | `新工程文件` |
| `src/iag/applications/save_continuations.py` | 平台原生 Application 注册、跨域调度与确定性续接。 | 在新存档到达后续接已授权的确定性跨存档工作流，不重新调用模型。 | - | `新工程文件` |
| `src/iag/applications/specialist_conversation_agent.py` | 平台原生 Application 注册、跨域调度与确定性续接。 | 运行舰队与科研的隔离会话、计划优先自主循环和局部异常升级。 | - | `新工程文件` |
| `src/iag/applications/tests/test_joint_plan_review.py` | 平台原生 Application 注册、跨域调度与确定性续接。 | Tests for compressed annual coordination without cross-plan mutation. | `runtime_config`, `JointPlanReviewerTests` | `新工程文件` |
| `src/iag/applications/tests/test_plan_advisor.py` | 平台原生 Application 注册、跨域调度与确定性续接。 | Tests for tightly scoped fast-adviser plan repair. | `PlanExceptionAdvisorTests` | `新工程文件` |
| `src/iag/core/README.md` | 平台无关核心：政策、会话、上下文和稳定契约。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/core/__init__.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | 与具体游戏动作无关的 Agent 核心。 | - | `新工程文件` |
| `src/iag/core/application_plan.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | 维护分层可执行计划、稳定事实条件、局部失效传播、存档确认、归档和审计。 | - | `新工程文件` |
| `src/iag/core/application_registry.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | 显式 Application 注册表；阻止目录扫描加载未知代码。 | - | `新工程文件` |
| `src/iag/core/autonomy.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | Save-driven scheduling helpers for unattended IAG reviews. | `save_identity`, `coalesce_next_review_after_turn`, `autonomy_probe` | `tools/agent_runtime/autonomy.py` |
| `src/iag/core/campaign_strategy.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | Persistent ten-year planning and player-controlled emergency state. | `game_date_parts`, `game_month_index`, `synchronize_decade_state`, `save_decade_plan`, `emergency_state`, `activate_emergency`, `end_emergency`, `public_strategy_state` | `tools/agent_runtime/campaign_strategy.py` |
| `src/iag/core/context_window.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | Token-budgeted conversation replay with loss-aware rolling summaries. | `estimate_text_tokens`, `estimate_message_tokens`, `estimate_messages_tokens`, `build_context_messages` | `tools/agent_runtime/context_window.py` |
| `src/iag/core/contracts.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | 定义跨 Agent、Mandate 与执行审计边界；未知字段会被拒绝。 | - | `新工程文件` |
| `src/iag/core/conversation_store.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | Persistent campaign conversation storage for the IAG agent. | `now_iso`, `ConversationStore` | `tools/agent_runtime/conversation_store.py` |
| `src/iag/core/paths.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | 集中解析开发工作区中的平台资源路径。 | `project_root`, `vanilla_content_pack_root`, `application_root`, `economy_governance_root`, `fleet_operations_root`, `research_strategy_root` | `新工程文件` |
| `src/iag/core/read_tasks.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | Bounded, recoverable thread workers for pure state reads. | `ReadTaskTimeoutError`, `ReadTask`, `ReadTaskPool`, `tool_is_read_only` | `新工程文件` |
| `src/iag/core/resource_ledger.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | 按战役与存档哈希原子预留资源，阻止多个 Application 重复消费同一份陈旧库存。 | - | `新工程文件` |
| `src/iag/core/tests/README.md` | 平台无关核心：政策、会话、上下文和稳定契约。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/core/tests/test_application_plan.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | Tests for model-authored plans and deterministic partial evaluation. | `FakeToolbox`, `ApplicationPlanBookTests` | `新工程文件` |
| `src/iag/core/tests/test_conversation_store.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | Persistence tests for provider-specific private protocol metadata. | `ConversationStoreTests` | `新工程文件` |
| `src/iag/core/tests/test_read_tasks.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | Python 模块；详细职责见邻接 README。 | `ReadTaskPoolTests` | `新工程文件` |
| `src/iag/core/tests/test_resource_ledger.py` | 平台无关核心：政策、会话、上下文和稳定契约。 | Python 模块；详细职责见邻接 README。 | `ResourceReservationLedgerTests` | `新工程文件` |
| `src/iag/infrastructure/README.md` | 仓库级配置、许可证或治理文件。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/infrastructure/__init__.py` | 仓库级配置、许可证或治理文件。 | 模型供应商、网页检索和其他可替换的基础设施实现。 | - | `新工程文件` |
| `src/iag/infrastructure/llm/README.md` | 模型供应商协议和请求模板适配。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/infrastructure/llm/__init__.py` | 模型供应商协议和请求模板适配。 | 兼容 OpenAI 与 Anthropic 协议的模型客户端和玩家模板。 | - | `新工程文件` |
| `src/iag/infrastructure/llm/anthropic_adapter.py` | 模型供应商协议和请求模板适配。 | Translate IAG's provider-neutral tool loop to Anthropic Messages. | `anthropic_messages`, `anthropic_tools`, `anthropic_message_body`, `anthropic_message_to_assistant` | `新工程文件` |
| `src/iag/infrastructure/llm/application_model_profile.py` | 模型供应商协议和请求模板适配。 | Named model selections owned by a first-party Application. | `ApplicationModelProfile` | `新工程文件` |
| `src/iag/infrastructure/llm/chat_stream.py` | 模型供应商协议和请求模板适配。 | 重组流式正文、私有推理和工具参数，只向叠加层发布普通正文。 | - | `新工程文件` |
| `src/iag/infrastructure/llm/endpoint_probe.py` | 模型供应商协议和请求模板适配。 | Read-only reachability checks for configured model endpoints. | `EndpointProbeResult`, `probe_endpoint` | `新工程文件` |
| `src/iag/infrastructure/llm/model_client.py` | 模型供应商协议和请求模板适配。 | 保持规划器同步接口，并编排异步模型协议调用与 JSON 解析。 | - | `tools/agent_runtime/model_client.py` |
| `src/iag/infrastructure/llm/model_pool.py` | 模型供应商协议和请求模板适配。 | Python 模块；详细职责见邻接 README。 | `ModelEndpoint`, `ModelPool` | `新工程文件` |
| `src/iag/infrastructure/llm/model_pool_runtime.py` | 模型供应商协议和请求模板适配。 | Runtime selection, health memory, and conservative endpoint failover. | `RequestFailure`, `EndpointHealth`, `ModelPoolExhaustedError`, `classify_request_error`, `ModelPoolRuntime` | `新工程文件` |
| `src/iag/infrastructure/llm/model_templates.json` | 模型供应商协议和请求模板适配。 | 结构化配置、schema 或声明式映射；运行时按 JSON 校验。 | - | `tools/agent_runtime/model_templates.json` |
| `src/iag/infrastructure/llm/model_templates.py` | 模型供应商协议和请求模板适配。 | Built-in model configuration templates. | `load_model_templates`, `public_model_templates`, `apply_model_template` | `tools/agent_runtime/model_templates.py` |
| `src/iag/infrastructure/llm/providers.py` | 模型供应商协议和请求模板适配。 | 使用 AsyncOpenAI 与 AsyncAnthropic 官方传输，并保留显式 raw HTTP 兼容实现。 | - | `新工程文件` |
| `src/iag/infrastructure/llm/runtime_config.py` | 模型供应商协议和请求模板适配。 | In-memory runtime configuration with explicit JSON persistence. | `RuntimeSnapshot`, `RuntimeConfig` | `新工程文件` |
| `src/iag/infrastructure/llm/tests/README.md` | 模型供应商协议和请求模板适配。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/infrastructure/llm/tests/test_chat_stream.py` | 模型供应商协议和请求模板适配。 | Tests for visible-content streaming and protocol reconstruction. | `ChatCompletionAccumulatorTests` | `新工程文件` |
| `src/iag/infrastructure/llm/tests/test_endpoint_probe.py` | 模型供应商协议和请求模板适配。 | Tests for no-completion endpoint reachability probes. | `endpoint`, `FakeResponse`, `EndpointProbeTests` | `新工程文件` |
| `src/iag/infrastructure/llm/tests/test_model_client.py` | 模型供应商协议和请求模板适配。 | Contract tests for OpenAI, Anthropic, and raw HTTP LLM transports. | `base_endpoint`, `base_options`, `anthropic_endpoint`, `ApiKeyResolutionTests`, `StubTransport`, `ModelClientTests` | `新工程文件` |
| `src/iag/infrastructure/llm/tests/test_model_pool_runtime.py` | 模型供应商协议和请求模板适配。 | Routing tests for cheap-first model endpoints and conservative replay. | `endpoint`, `ModelPoolRuntimeTests` | `新工程文件` |
| `src/iag/infrastructure/llm/tests/test_runtime_config.py` | 模型供应商协议和请求模板适配。 | Contract tests for model-pool configuration and explicit persistence. | `endpoint_document`, `legacy_config_document`, `flat_legacy_config_document`, `RuntimeConfigTests` | `新工程文件` |
| `src/iag/infrastructure/research/README.md` | 不可信网页资料的检索与正文提取适配。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/infrastructure/research/__init__.py` | 不可信网页资料的检索与正文提取适配。 | 受域名、长度和权限约束的不可信网页研究工具。 | - | `新工程文件` |
| `src/iag/infrastructure/research/research_tools.py` | 不可信网页资料的检索与正文提取适配。 | Read-only, bounded web research tools for the persistent governor. | `ResearchToolError`, `VisibleTextParser`, `ResearchClient` | `tools/agent_runtime/research_tools.py` |
| `src/iag/infrastructure/research/tests/README.md` | 不可信网页资料的检索与正文提取适配。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/infrastructure/research/web_research.py` | 不可信网页资料的检索与正文提取适配。 | Bounded, cached web research for Stellaris planning context. | `now_iso`, `DuckDuckGoResultParser`, `canonical_result_url`, `domain_allowed`, `source_tier`, `parse_search_html`, `fixed_queries`, `search_query` | `tools/agent_runtime/web_research.py` |
| `src/iag/stellaris/README.md` | 仓库级配置、许可证或治理文件。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/stellaris/__init__.py` | 仓库级配置、许可证或治理文件。 | Stellaris 领域层。 | - | `新工程文件` |
| `src/iag/stellaris/execution/README.md` | 游戏侧点击、网络发现、执行监督和确认。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/stellaris/execution/__init__.py` | 游戏侧点击、网络发现、执行监督和确认。 | Stellaris 副作用执行边界。 | - | `新工程文件` |
| `src/iag/stellaris/execution/fixed_click.py` | 游戏侧点击、网络发现、执行监督和确认。 | Platform facade for guarded fixed-coordinate Stellaris clicks. | - | `tools/agent_runtime/fixed_click.py` |
| `src/iag/stellaris/execution/host_bridge_pairing.py` | 游戏侧点击、网络发现、执行监督和确认。 | Create a runtime-paired Host Bridge archive from the public release archive. | `HostBridgePairingError`, `certificate_sha256`, `pairing_server_url`, `build_paired_host_bridge_archive` | `tools/agent_runtime/host_bridge_pairing.py` |
| `src/iag/stellaris/execution/host_executor_protocol.py` | 游戏侧点击、网络发现、执行监督和确认。 | File-backed coordination for the Windows host-side packet interceptor. | `HostExecutorProtocolError`, `now_iso`, `runtime_path`, `protocol_root`, `save_client_status_path`, `optional_json`, `atomic_write_json`, `request_directory` | `tools/agent_runtime/host_executor_protocol.py` |
| `src/iag/stellaris/execution/iag_linux_interceptor.py` | 游戏侧点击、网络发现、执行监督和确认。 | One-shot Linux NFQUEUE interceptor for the non-host Stellaris client. | `RuntimeState`, `now_iso`, `has_effective_capability`, `interceptor_lock_path`, `append_event`, `atomic_write_json`, `telemetry_document`, `publish_telemetry` | `tools/agent_runtime/iag_linux_interceptor.py` |
| `src/iag/stellaris/execution/iag_supervisor.py` | 游戏侧点击、网络发现、执行监督和确认。 | 在载体点击与会话代理之间编排准备、执行、确认和失败保护。 | - | `tools/agent_runtime/iag_supervisor.py` |
| `src/iag/stellaris/execution/linux_packet.py` | 游戏侧点击、网络发现、执行监督和确认。 | Pure IPv4/UDP and Stellaris payload helpers for the Linux interceptor. | `UdpView`, `internet_checksum`, `parse_ipv4_udp`, `replace_udp_payload`, `validate_host_ip`, `rewrite_stellaris_payload`, `expected_authoritative_needle` | `tools/agent_runtime/linux_packet.py` |
| `src/iag/stellaris/execution/packet/README.md` | 协作命令的离线解析、精确构造与受约束改写。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/stellaris/execution/packet/__init__.py` | 协作命令的离线解析、精确构造与受约束改写。 | 已由联机实验验证的建设命令解析与等长改写原语。 | - | `新工程文件` |
| `src/iag/stellaris/execution/packet/autonomous_commands.py` | 协作命令的离线解析、精确构造与受约束改写。 | Verified Clausewitz command builders used by the session proxy. | `now_iso`, `sha256_hex`, `append_jsonl`, `add_vendor_runtime`, `BuildingTarget`, `build_building_record`, `InjectionResult`, `BoundaryInjectionResult` | `新工程文件` |
| `src/iag/stellaris/execution/packet/building_mutation_commands.py` | 协作命令的离线解析、精确构造与受约束改写。 | 精确解析并构造建筑升级与槽位级建筑替换记录。 | - | `新工程文件` |
| `src/iag/stellaris/execution/packet/expansion_commands.py` | 协作命令的离线解析、精确构造与受约束改写。 | 精确解析并构造殖民、恒星基地升级、模块和建筑设置记录。 | - | `新工程文件` |
| `src/iag/stellaris/execution/packet/fleet_operation_commands.py` | 协作命令的离线解析、精确构造与受约束改写。 | 精确解析并构造舰队攻击、舰船自动化和工程船建造恒星基地记录。 | - | `新工程文件` |
| `src/iag/stellaris/execution/packet/fleet_reinforcement_commands.py` | 协作命令的离线解析、精确构造与受约束改写。 | 精确解析并构造 Fleet Manager 编制增减与两阶段增援记录。 | - | `新工程文件` |
| `src/iag/stellaris/execution/packet/ground_warfare_commands.py` | 协作命令的离线解析、精确构造与受约束改写。 | 精确解析并构造轰炸姿态、陆军登陆和空间站陆军招募记录。 | - | `新工程文件` |
| `src/iag/stellaris/execution/packet/iag_building_replacement_rewriter.py` | 协作命令的离线解析、精确构造与受约束改写。 | Length-preserving rewrite for Stellaris 4.4.6 building replacements. | `rewrite_building_replacement_carrier` | `tools/packet_interceptor/iag_building_replacement_rewriter.py` |
| `src/iag/stellaris/execution/packet/iag_building_to_zone_replacer.py` | 协作命令的离线解析、精确构造与受约束改写。 | Replace one legal building construction command with a zone command. | `is_building_carrier_command`, `build_zone_core`, `build_zone_record_from_building_carrier`, `replace_building_command_with_zone`, `inspect_payload`, `sha256_hex`, `now_iso`, `append_jsonl` | `tools/packet_interceptor/iag_building_to_zone_replacer.py` |
| `src/iag/stellaris/execution/packet/iag_building_upgrade_rewriter.py` | 协作命令的离线解析、精确构造与受约束改写。 | Length-preserving rewrite for Stellaris 4.4.6 building upgrades. | `rewrite_building_upgrade_carrier` | `tools/packet_interceptor/iag_building_upgrade_rewriter.py` |
| `src/iag/stellaris/execution/packet/iag_command_replacement_injector.py` | 协作命令的离线解析、精确构造与受约束改写。 | Replace one naturally emitted Stellaris command with a build command. | `is_building_command`, `replace_one_command`, `sha256_hex`, `now_iso`, `append_jsonl`, `add_vendor_runtime`, `parse_args`, `run` | `tools/packet_interceptor/iag_command_replacement_injector.py` |
| `src/iag/stellaris/execution/packet/iag_packet_interceptor.py` | 协作命令的离线解析、精确构造与受约束改写。 | WinDivert 一次性捕获框架、方向过滤和审计日志。 | - | `tools/packet_interceptor/iag_packet_interceptor.py` |
| `src/iag/stellaris/execution/packet/iag_same_family_construction_rewriter.py` | 协作命令的离线解析、精确构造与受约束改写。 | District and zone construction-record rewrites. | `rewrite_district_carrier`, `rewrite_zone_carrier` | `tools/packet_interceptor/iag_same_family_construction_rewriter.py` |
| `src/iag/stellaris/execution/packet/iag_stream_command_injector.py` | 协作命令的离线解析、精确构造与受约束改写。 | Inject one Stellaris construction command into the live reliable stream. | `read_uint24_be`, `write_uint24_be`, `forward_distance_uint24`, `is_at_or_after_uint24`, `is_reliable_packet`, `build_construction_command`, `StreamTranslator`, `CommandRecord` | `tools/packet_interceptor/iag_stream_command_injector.py` |
| `src/iag/stellaris/execution/packet/ship_commands.py` | 协作命令的离线解析、精确构造与受约束改写。 | 精确解析并构造舰船设计与直接船坞记录。 | - | `新工程文件` |
| `src/iag/stellaris/execution/packet/tests/README.md` | 协作命令的离线解析、精确构造与受约束改写。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/stellaris/execution/passive_network_observer.py` | 游戏侧点击、网络发现、执行监督和确认。 | Passively identify the active Stellaris UDP transport without changing packets. | `SockFilter`, `SockFprog`, `DatagramView`, `is_reliable_transport_payload`, `FlowEvent`, `PeerFlow`, `FlowTracker`, `now_iso` | `tools/agent_runtime/passive_network_observer.py` |
| `src/iag/stellaris/execution/port_discovery.py` | 游戏侧点击、网络发现、执行监督和确认。 | Discover the Stellaris process, transport sockets, and live network telemetry. | `ProcessInfo`, `UdpSocket`, `now_iso`, `read_text`, `find_processes`, `normalize_process_name`, `process_identity_matches`, `process_socket_inodes` | `tools/agent_runtime/port_discovery.py` |
| `src/iag/stellaris/execution/protocol_compatibility.py` | 游戏侧点击、网络发现、执行监督和确认。 | 登记全部会话代理命令，并生成版本兼容性计划、结果分类和基线差异报告。 | - | `新工程文件` |
| `src/iag/stellaris/execution/protocol_compatibility_control.py` | 游戏侧点击、网络发现、执行监督和确认。 | 持久化网页协议验收计划，以无模型调用的逐项状态机驱动离线检查和会话代理。 | - | `新工程文件` |
| `src/iag/stellaris/execution/session_proxy.py` | 游戏侧点击、网络发现、执行监督和确认。 | 在整局可靠流中插入已验证命令并持续映射 offset、ACK 与 actor serial。 | - | `新工程文件` |
| `src/iag/stellaris/execution/session_proxy_controller.py` | 游戏侧点击、网络发现、执行监督和确认。 | 管理 Windows 会话代理的进房前启动、串行动作提交和离房后停止。 | - | `新工程文件` |
| `src/iag/stellaris/execution/tests/README.md` | 游戏侧点击、网络发现、执行监督和确认。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/stellaris/execution/tests/test_extended_protocol_commands.py` | 游戏侧点击、网络发现、执行监督和确认。 | Python 模块；详细职责见邻接 README。 | `reliable_header`, `ExtendedProtocolCommandTests` | `新工程文件` |
| `src/iag/stellaris/execution/tests/test_fleet_reinforcement_commands.py` | 游戏侧点击、网络发现、执行监督和确认。 | Python 模块；详细职责见邻接 README。 | `FleetReinforcementCommandTests` | `新工程文件` |
| `src/iag/stellaris/execution/tests/test_host_bridge_pairing.py` | 游戏侧点击、网络发现、执行监督和确认。 | Tests for runtime pairing of Host Bridge and game-overlay credentials. | `HostBridgePairingTests` | `新工程文件` |
| `src/iag/stellaris/execution/tests/test_protocol_compatibility.py` | 游戏侧点击、网络发现、执行监督和确认。 | Python 模块；详细职责见邻接 README。 | `ProtocolCompatibilityTests` | `新工程文件` |
| `src/iag/stellaris/execution/tests/test_protocol_compatibility_control.py` | 游戏侧点击、网络发现、执行监督和确认。 | Python 模块；详细职责见邻接 README。 | `FakeSessionProxyController`, `ProtocolCompatibilityControlTests` | `新工程文件` |
| `src/iag/stellaris/execution/tests/test_session_proxy.py` | 游戏侧点击、网络发现、执行监督和确认。 | Python 模块；详细职责见邻接 README。 | `header`, `SessionProxyTests` | `新工程文件` |
| `src/iag/stellaris/execution/tests/test_session_proxy_controller.py` | 游戏侧点击、网络发现、执行监督和确认。 | Python 模块；详细职责见邻接 README。 | `SessionProxyControllerTests` | `新工程文件` |
| `src/iag/stellaris/execution/tests/test_ship_design_packets.py` | 游戏侧点击、网络发现、执行监督和确认。 | Python 模块；详细职责见邻接 README。 | `section`, `battleship_blueprint`, `cruiser_blueprint`, `ShipDesignPacketTests` | `新工程文件` |
| `src/iag/stellaris/execution/tests/test_supervisor_execution_modes.py` | 游戏侧点击、网络发现、执行监督和确认。 | Python 模块；详细职责见邻接 README。 | `SupervisorExecutionModeTests` | `新工程文件` |
| `src/iag/stellaris/execution/windows_fixed_click.py` | 游戏侧点击、网络发现、执行监督和确认。 | Guarded fixed-coordinate Stellaris capture and click implementation for Windows. | `WindowsControlError`, `WindowGeometry`, `MOUSEINPUT`, `INPUTUNION`, `INPUT`, `BITMAPINFOHEADER`, `RGBQUAD`, `BITMAPINFO` | `tools/agent_runtime/windows_fixed_click.py` |
| `src/iag/stellaris/execution/x11_fixed_click.py` | 游戏侧点击、网络发现、执行监督和确认。 | Calibrate and execute one fixed, guarded click in an X11 Stellaris window. | `X11ControlError`, `WindowGeometry`, `now_iso`, `atomic_write_json`, `read_json`, `x11_environment`, `run_checked`, `read_process_name` | `tools/agent_runtime/x11_fixed_click.py` |
| `src/iag/stellaris/game_knowledge.py` | 仓库级配置、许可证或治理文件。 | Extract version-locked Stellaris rule evidence from the local installation. | `file_sha256`, `normalized_version`, `detect_game_root`, `install_identity`, `scripted_variables`, `variables_for_source`, `strip_clausewitz_comments`, `extract_named_block` | `tools/agent_runtime/game_knowledge.py` |
| `src/iag/stellaris/state/README.md` | Stellaris 存档事实读取与规范化。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/stellaris/state/__init__.py` | Stellaris 存档事实读取与规范化。 | 权威存档的接收、身份校验、解析与状态归一化。 | - | `新工程文件` |
| `src/iag/stellaris/state/campaign_routes.py` | Stellaris 存档事实读取与规范化。 | 搜索多目标 Pareto 战役路线，汇总阻断代价，并只为达到军力门槛的原子任务编组分配进攻。 | - | `新工程文件` |
| `src/iag/stellaris/state/expansion_profiles.py` | Stellaris 存档事实读取与规范化。 | 解析物种宜居度、殖民来源、可殖民行星与恒星基地操作候选。 | - | `新工程文件` |
| `src/iag/stellaris/state/extract_game_state.py` | Stellaris 存档事实读取与规范化。 | 解包 Stellaris 存档并生成标准化帝国状态。 | - | `tools/agent_runtime/extract_game_state.py` |
| `src/iag/stellaris/state/fleet_profiles.py` | Stellaris 存档事实读取与规范化。 | 解析玩家舰队、军力、聚合耐久、位置、模板编制、增援队列和已验证动作目标。 | - | `新工程文件` |
| `src/iag/stellaris/state/invasion_profiles.py` | Stellaris 存档事实读取与规范化。 | 解析敌对殖民地、轰炸进度、守军、运输军团和行星超空间抑制器。 | - | `新工程文件` |
| `src/iag/stellaris/state/planet_profiles.py` | Stellaris 存档事实读取与规范化。 | 解析殖民地、区域、槽位、容量、拥有者和建设队列。 | - | `tools/save_state/extract_planet_profiles.py` |
| `src/iag/stellaris/state/research_profiles.py` | Stellaris 存档事实读取与规范化。 | 分离已完成科技、当前研究、合法候选和储存研究点。 | - | `新工程文件` |
| `src/iag/stellaris/state/save_ingest.py` | Stellaris 存档事实读取与规范化。 | Receive authenticated host saves and expose one verified current save. | `SaveIngestError`, `now_iso`, `configured_path`, `upload_root`, `manifest_path`, `campaign_manifest_path`, `upload_token_path`, `review_interval_months` | `tools/agent_runtime/save_ingest.py` |
| `src/iag/stellaris/state/ship_profiles.py` | Stellaris 存档事实读取与规范化。 | 解析玩家舰船设计、部件槽和直接船坞协议证据。 | - | `新工程文件` |
| `src/iag/stellaris/state/state_index.py` | Stellaris 存档事实读取与规范化。 | Thread-safe, snapshot-local indexes over one Stellaris gamestate string. | `WorldStateIndex` | `新工程文件` |
| `src/iag/stellaris/state/tests/README.md` | Stellaris 存档事实读取与规范化。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/stellaris/state/tests/test_campaign_routes.py` | Stellaris 存档事实读取与规范化。 | Python 模块；详细职责见邻接 README。 | `CampaignRoutePlannerTests` | `新工程文件` |
| `src/iag/stellaris/state/tests/test_expansion_profiles.py` | Stellaris 存档事实读取与规范化。 | Python 模块；详细职责见邻接 README。 | `ExpansionProfileTests` | `新工程文件` |
| `src/iag/stellaris/state/tests/test_fleet_profiles.py` | Stellaris 存档事实读取与规范化。 | Python 模块；详细职责见邻接 README。 | `ExtractFleetProfilesTests` | `新工程文件` |
| `src/iag/stellaris/state/tests/test_invasion_profiles.py` | Stellaris 存档事实读取与规范化。 | Python 模块；详细职责见邻接 README。 | `profile`, `InvasionProfileSelectionTests` | `新工程文件` |
| `src/iag/stellaris/state/tests/test_research_profiles.py` | Stellaris 存档事实读取与规范化。 | Python 模块；详细职责见邻接 README。 | `ResearchProfileTests` | `新工程文件` |
| `src/iag/stellaris/state/tests/test_ship_profiles.py` | Stellaris 存档事实读取与规范化。 | Python 模块；详细职责见邻接 README。 | `multisection_profile`, `ShipProfileTests` | `新工程文件` |
| `src/iag/stellaris/state/tests/test_world_snapshot.py` | Stellaris 存档事实读取与规范化。 | Python 模块；详细职责见邻接 README。 | `WorldStateIndexTests`, `WorldSnapshotTests` | `新工程文件` |
| `src/iag/stellaris/state/world_snapshot.py` | Stellaris 存档事实读取与规范化。 | Pinned save snapshots and shared, lazy world-state derivations. | `StaleWorldSnapshotError`, `SnapshotIdentity`, `WorldSnapshot`, `WorldStateService` | `新工程文件` |
| `src/iag/stellaris/tests/README.md` | 仓库级配置、许可证或治理文件。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `src/iag/stellaris/tests/test_game_knowledge_technology.py` | 仓库级配置、许可证或治理文件。 | Python 模块；详细职责见邻接 README。 | `TechnologyRuleTests` | `新工程文件` |
| `stellaris_mod/README.md` | 建设载体 Clausewitz Mod 源码。 | 该目录的职责、边界、主要文件和开发注意事项。 | - | `新工程文件` |
| `stellaris_mod/common/edicts/iag_edicts.txt` | 建设载体 Clausewitz Mod 源码。 | Stellaris Clausewitz 定义；只影响建设载体 Mod。 | - | `common/edicts/iag_edicts.txt` |
| `stellaris_mod/common/on_actions/iag_on_actions.txt` | 建设载体 Clausewitz Mod 源码。 | Stellaris Clausewitz 定义；只影响建设载体 Mod。 | - | `common/on_actions/iag_on_actions.txt` |
| `stellaris_mod/common/scripted_effects/iag_carrier_effects.txt` | 建设载体 Clausewitz Mod 源码。 | 创建和维护建设载体星系、殖民地及其安全状态。 | - | `common/scripted_effects/iag_carrier_effects.txt` |
| `stellaris_mod/common/solar_system_initializers/iag_carrier_initializers.txt` | 建设载体 Clausewitz Mod 源码。 | Stellaris Clausewitz 定义；只影响建设载体 Mod。 | - | `common/solar_system_initializers/iag_carrier_initializers.txt` |
| `stellaris_mod/common/static_modifiers/iag_static_modifiers.txt` | 建设载体 Clausewitz Mod 源码。 | Stellaris Clausewitz 定义；只影响建设载体 Mod。 | - | `common/static_modifiers/iag_static_modifiers.txt` |
| `stellaris_mod/descriptor.mod` | 建设载体 Clausewitz Mod 源码。 | Stellaris Clausewitz 定义；只影响建设载体 Mod。 | - | `descriptor.mod` |
| `stellaris_mod/events/iag_carrier_events.txt` | 建设载体 Clausewitz Mod 源码。 | Stellaris Clausewitz 定义；只影响建设载体 Mod。 | - | `events/iag_carrier_events.txt` |
| `stellaris_mod/localisation/english/iag_l_english.yml` | 建设载体 Clausewitz Mod 源码。 | 声明式本地化或服务配置。 | - | `localisation/english/iag_l_english.yml` |
| `stellaris_mod/localisation/simp_chinese/iag_l_simp_chinese.yml` | 建设载体 Clausewitz Mod 源码。 | 声明式本地化或服务配置。 | - | `localisation/simp_chinese/iag_l_simp_chinese.yml` |
