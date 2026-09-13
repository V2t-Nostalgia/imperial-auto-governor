# 迁移验收记录

验收日期：2026-08-09

## 源仓库保护

- 基线提交：`81bb66d2609c42136fdae35b6538dea28d4ba9d7`
- 验收时源仓库状态：clean
- 迁移源文件哈希：99/99 与基线文件一致
- 旧仓库未删除、未移动、未写入

## 新工程静态检查

- `python -m compileall`：通过
- Python 包全量导入：56/56 模块通过
- JSON、TOML 和 PyInstaller spec 解析：通过
- PowerShell AST 解析：4/4 脚本通过
- Pydantic Mandate 冻结与未知字段拒绝：通过
- 源码模式 Windows Agent 健康检查：通过
- Bash 语法检查：本机没有可用 Bash/WSL 发行版，未执行；应在 Ubuntu 首次部署前补跑 `bash -n scripts/deploy/*.sh`

## Windows 构建检查

- `Build-IAGWindowsAgent.ps1`：实际构建通过
- 打包后的 `IAGWindowsAgent.exe --health-check`：退出码 0
- `Build-IAGHostBridge.ps1`：修正 hidden import 后重新构建通过
- Host Bridge 归档所需改写模块：7/7 存在
- Host Bridge 内 WinDivert 文件：DLL 与 SYS 均存在
- Host Bridge 临时本地 HTTPS 健康检查：退出码 0
- PyInstaller 警告中没有缺失 `iag.*` 或 `apps.*` 模块

PyInstaller 会提示 WinDivert 驱动依赖的 `NDIS.SYS`、`fwpkclnt.sys`、`WDFLDR.SYS` 未被打包。这些是 Windows 系统组件，不应复制进发行包；WinDivert 自身的 DLL/SYS 已验证存在。

## 迁移边界

- 旧生产/文档文件纳入：99
- 旧测试文件纳入：0
- 新组件测试目录：10
- 运行数据、存档、日志、抓包、数据库、密钥和旧发行包：未纳入
- 维护文件隐私搜索：未发现个人绝对路径、旧工作目录或真实局域网地址

## 尚未执行

- 未在 Ubuntu 上安装依赖或启动 systemd 服务。
- 未在本次工程化迁移中连接真实 Stellaris 会话。
- 未重跑旧仓库测试；这些测试依赖旧扁平导入结构，后续应按新模块边界逐步重写。
- 未修改、提交或推送旧仓库与既有 GitHub 项目。

## v0.5.9 早期 Draft 源码验收（2026-08-15）

- 仓库递归测试入口发现 20 个模块、84 项测试：全部通过。
- `python -m compileall -q src apps scripts`：通过。
- 非测试 Python 模块导入：78/78 通过。
- JSON/TOML/PyInstaller spec 解析：9/5/4 通过。
- PowerShell AST 与浏览器 JavaScript 语法：5/2 通过。
- Ruff `E9,F` 运行错误级检查：通过。
- 新增代理回归覆盖动态长度建筑标识符、采矿区划、跨科研特化、两种舰队目的地字段、精确权威回包关联、多次插入坐标组合和 session ID 隔离。
- 舰队工具回归覆盖总开关、逐舰队默认拒绝、准备/执行双重权限校验、机器事实账本和未验证攻击硬拒绝。
- Windows 运行配置回归覆盖 v0.5.8 根级扁平模型字段、相对 API Key 文件、请求参数保留、只在显式保存时迁移，以及真实 `ConsoleService` 初始化链路。

该早期 Draft 当时已经闭环验证建设命令、区域特化与 `d32c` 舰队移动。`6b33` 攻击已
取得非房主请求和房主权威回包的零丢包配对，但在这次早期验收时，确定性敌对目标映射
尚未进入舰队 Application，因此当时只提供攻击授权预检。后续集成结果见下方验收记录。

## v0.5.9 协议执行器验收（2026-09-06）

- `python -m compileall -q src apps scripts`：通过。
- 仓库递归测试入口发现 34 个模块、179 项测试：全部通过。
- Ruff `E9,F` 运行错误级检查：通过。
- `node --check apps/control_center/web/app_v2.js`：通过。
- Windows/Linux 示例运行配置 JSON 解析：2/2 通过。
- 源码模式 Windows Agent `--health-check`：退出码 0。
- 会话代理目录中的 28 个动作均使用脱敏 fixture 完成结构化构包、应用层分帧、命令族和记录长度校验。
- CLI 离线协议兼容性验收状态：`passed`；安全模板中的真实副作用动作保持关闭，未启动 WinDivert 或连接游戏。
- 新增精确样本回归覆盖建筑升级/替换、舰队攻击、舰船自动化、工程船建造恒星基地、两类殖民、恒星基地升级/模块/建筑，以及超过 255 字节的应用记录前缀。

各公开附件的二进制健康检查、隐私扫描、解压复验和包内 SHA-256 结果由 `scripts/release/build_release.py` 在源码提交后执行，并写入各包的 `RELEASE_MANIFEST.json`；本文件不提前宣称尚未构建的附件已经通过。

## v0.5.9 舰队与扩张 Application 集成验收（2026-09-08）

- 仓库递归测试入口发现 35 个模块、191 项测试：全部通过。
- 舰队 Application 定向回归 19/19、Stellaris 状态解析回归 14/14、控制台执行模式回归 37/37：全部通过。
- `python -m compileall -q src apps scripts`：通过。
- Ruff `E9,F` 运行错误级检查：通过；新增模块的导入顺序与 Python 3.11 类型导入检查通过。
- `node --check apps/control_center/web/app.js` 与 `app_v2.js`：通过。
- 新增显式 schema 回归，确保攻击、民用船自动化、工程船建站、殖民、恒星基地操作及其统一执行工具确实暴露给舰队与扩张 Agent，而不只是存在于底层构包器。
- 新增舰队耐久聚合回归，覆盖不同船体/装甲/护盾比例、零值省略、平均数、中位数和 Agent 视图不展开 `ship_ids`。
- 工具准备阶段与执行阶段均从最新存档重建候选，并重新检查全局开关、逐舰队权限和高影响替换权限；只有会话代理返回 `confirmed` 才写入机器事实。
- 使用本机 Stellaris 4.4.6 原版规则和两份既有测试存档完成只读冒烟检查：物种宜居度、殖民船设计、船坞来源及恒星基地候选均可解析；本次验收没有连接游戏或发送实时命令。

本节仍不声明发布附件已经重新构建。最终压缩包与清单应在所有收尾功能合并后统一生成并复验。

## v0.5.9 战役路线与跨 Application 账本验收（2026-09-13）

- 仓库测试入口 `scripts/validation/run_tests.py --quiet`：加载 44 个测试模块，300 项通过；仅保留一个既有的测试类收集警告。
- Ruff `E9,F` 运行错误级检查：`src`、`apps`、`scripts` 全部通过。
- 定向回归覆盖多目标 Pareto 路径、走廊拥挤与探索排序、恒星基地与行星抑制器、守军生命池、轰炸／登陆状态、招募后新建或既有运输舰队差分、同存档单步续接和跨 Application 资源超支拒绝。
- 空间战力是探索与分流之前的硬约束：规划器汇总交战星系内全部已知敌对舰队和恒星基地，并按默认 `1.20` 安全系数组建互斥、原子派遣的任务群；敌军 600、三支友军各 300 的回归中，规划器会集中三支舰队，而不会派单舰送死。即使拥挤度或探索奖励很高，两支各 300 的舰队也不会被派往需要 720 战力的目标；敌方战力未知时同样停止执行并请求情报。
- 舰船设计归属回归覆盖集合级自动设计开关：`auto_gen_design=no` 时，仍残留在玩家国家 `ship_design_collection` 中的自动生成模板会标记为隐藏，不进入模型视图，也不能通过猜测 ID 展开或创建新设计；重新启用自动设计时对应模板仍可正常使用。真实绑定存档 `2408.03.01` 的 34 条国家设计记录被正确分为 7 条当前可见记录和 27 条隐藏自动生成模板。
- 普通存档移动目标只参与走廊拥挤估计，不会被误算为中途交战支援；只有已在目标星系、以目标为最终目的地或属于明确战役承诺的可用舰队才计入硬支援。
- 使用同步存档 `2407.09.30` 做只读冒烟检查：路线搜索展开 160 个标签并保留 2 个 Pareto 方案，单次路径规划约 0.15 秒、全局部署约 0.21 秒；未连接游戏或发送命令。
- CLI 协议兼容性验收再次返回 `passed`：11 个协议相关测试模块通过，28 项真实副作用保持关闭，未启动 WinDivert。
- 从当前未提交工作区重新构建 Windows Agent，并沿用未变更的 Host Bridge 生成独立的 `uncommitted-test` 附件；Agent ZIP 通过隐私扫描、包内 SHA-256、解压后二次健康检查，并确认舰队与科研专职提示词、WinDivert DLL/SYS 均已入包。Agent ZIP 的 SHA-256 为 `c3f9482ac7ea7efa16989ea72d6dc247ae3bcef12cd0494363e1be402ede8ffe`。
- 测试包清单把源版本显式标为 `d87a9fa8208c339f73980f2532fa56a7d331c3f4-dirty`，没有覆盖正式 `final` 目录，也没有提交或推送。正式可复现附件仍须在源码提交后由严格发布入口重新生成。

## OpenAI 与 Anthropic SDK 传输适配（2026-09-08）

- 使用 Python 3.11.9 新建仓库内隔离环境，并以 editable 模式从 `pyproject.toml` 完整安装依赖。
- 在 OpenAI Python SDK 2.53.0 与 Anthropic Python SDK 1.4.0 上执行 LLM 传输契约测试：34/34 通过。
- 无网络 Mock 已覆盖 Chat Completions、Responses、Anthropic Messages content blocks/`tool_use`/流式正文、DeepSeek `reasoning_content`、`extra_body`、同步兼容桥和显式 raw HTTP 路径。
- 全仓库 13 个测试目录共 202 项测试通过；Python 3.11 `compileall`、Ruff `E9/F`、`app_v2.js` 语法与 `git diff --check` 通过。
- 本轮没有重新构建 Windows Agent 或 Release 附件；添加 Anthropic SDK 后的冻结包健康检查留到最终发布构建。
- 验证没有调用真实模型服务、没有读取真实 API Key，也没有产生计费请求。
