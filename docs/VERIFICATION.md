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

## v0.5.9 Draft 源码验收（2026-08-15）

- 仓库递归测试入口发现 20 个模块、83 项测试：全部通过。
- `python -m compileall -q src apps scripts`：通过。
- 非测试 Python 模块导入：78/78 通过。
- JSON/TOML/PyInstaller spec 解析：9/5/4 通过。
- PowerShell AST 与浏览器 JavaScript 语法：2/2 通过。
- Ruff `E9,F` 运行错误级检查：通过。
- 新增代理回归覆盖动态长度建筑标识符、采矿区划、跨科研特化、两种舰队目的地字段、精确权威回包关联、多次插入坐标组合和 session ID 隔离。
- 舰队工具回归覆盖总开关、逐舰队默认拒绝、准备/执行双重权限校验、机器事实账本和未验证攻击硬拒绝。

真实联机实验已经闭环验证建设命令、区域特化与 `d32c` 舰队移动。`6b33` 攻击仍缺少非房主请求和房主权威回包的成对样本，因此 v0.5.9 只提供攻击授权预检，不发送猜测命令。

各公开附件的二进制健康检查、隐私扫描、解压复验和包内 SHA-256 结果由 `scripts/release/build_release.py` 在源码提交后执行，并写入各包的 `RELEASE_MANIFEST.json`；本文件不提前宣称尚未构建的附件已经通过。

## OpenAI SDK 传输适配（2026-08-09）

- 使用 Python 3.11.9 新建仓库内隔离环境，并以 editable 模式从 `pyproject.toml` 完整安装依赖。
- 在 OpenAI Python SDK 2.53.0 上执行 LLM 传输契约测试：8/8 通过。
- 无网络 Mock 已覆盖 Chat Completions、Responses、DeepSeek `reasoning_content`、工具调用、`extra_body`、同步兼容桥和显式 raw HTTP 路径。
- Python 3.11 全量编译通过；当前可发现的 `iag.*` / `apps.*` 模块 55/55 导入通过。
- JSON、TOML、PowerShell AST 与 `app_v2.js` 语法检查通过。
- 使用干净 Python 3.11 环境重新构建 Windows Agent；打包目录中的 `IAGWindowsAgent.exe --health-check` 退出码为 0。
- PyInstaller 只报告 OpenAI SDK 可选能力（Realtime WebSocket、Trio、Pandas、Bedrock 等）的缺失依赖；当前 Chat Completions / Responses 非流式链路不使用这些可选模块，且没有缺失 `iag.*` 模块。
- 验证没有调用真实模型服务、没有读取真实 API Key，也没有产生计费请求。
