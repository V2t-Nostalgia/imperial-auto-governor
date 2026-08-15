<!-- SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Windows 代理端快速开始

`IAGWindowsAgent` 是运行在非房主合作玩家电脑上的灰风代理端。它与房主电脑上的 `IAGHostBridgeGUI` 配合：代理读取房主上传的存档、调用模型规划、点击合法载体；房主桥在命令进入房主前进行受约束的等长改写。

## 启动

1. 解压整个目录。不要只移动 `IAGWindowsAgent.exe`，它依赖同目录的 `_internal`。
2. 双击 `IAGWindowsAgent.exe`。
3. 确认运行数据目录与 Stellaris 安装目录。需要从另一台电脑访问时保留监听地址 `0.0.0.0`，并选择一个未占用端口。
4. 点击“保存设置”。程序会在运行数据目录生成自签名 HTTPS 证书、前端密码、存档上传令牌和房主桥令牌。
5. 把 GUI 显示的房主桥令牌与证书 SHA-256 指纹填入房主电脑的 `IAGHostBridgeGUI`，再启动房主桥。

代理控制台中的“下载已配对 Windows 房主执行桥”会以 Agent 包内经过发布校验的公开 Host Bridge ZIP 为基础，在通过前端登录认证后生成仅供当前 Agent 使用的临时配对包。该包包含当前访问地址、TLS 指纹和桥接令牌；无需另外寻找旧的 `hostbridge7/8` 临时包，也不要把配对包作为公开附件再次分发。
6. 点击“启动控制台”，然后点击“打开控制台”。首次访问自签名 HTTPS 页面时，浏览器会显示证书警告；应先核对 GUI 中的证书指纹。

## 首次配置

在网页中配置模型 Base URL、模型名、API Key、超时、`temperature`、最大上下文、输出预留、压缩触发百分比和压缩后目标百分比。附加请求参数可写为 Raw JSON，例如 `{"top_p":0.9,"max_tokens":8192}`；消息、工具、模型等协议结构字段不能被覆盖。

每局游戏新建一条战役会话，并由玩家明确把当前上传存档绑定到该会话。填写本期十年计划后，选择读取周期和运行策略。紧急事件需由玩家在前端激活和结束；激活期间十年计划会暂停。

联网检索为可选功能。SearXNG、Crawl4AI 与 MediaWiki 返回的内容一律是不可信参考，不能创建本地不存在的建设候选，也不能绕过执行器校验。

正式包内的 `research_services/` 提供 Windows 一键部署脚本。在该目录运行
`Manage-IAGResearchServices.ps1 -Action Install` 后，可在模型设置区使用“允许灰风联网检索”开关；
关闭开关会从下一轮模型请求中移除全部联网工具，但不会停止 Docker 容器。完整的两机部署、房主桥
配对、模组准备和首次实局验收见 [WINDOWS_FULL_DEPLOYMENT.md](WINDOWS_FULL_DEPLOYMENT.md)；在发布包中
该文件位于 `docs/WINDOWS_FULL_DEPLOYMENT.md`。

## 停止与数据

关闭 GUI 会停止本机代理，不会建立计划任务或开机自启。完整会话、概况、建设审计和配置保存在所选运行数据目录；API Key 与桥接令牌不得上传到 GitHub。

遇到问题时先查看 GUI 日志，以及运行数据目录中的 `logs/windows_agent_server.log`。游戏内是否完成建设最终以房主权威回包或后续新存档中的精确证据为准；“已改写但回包不可识别”只能记为待存档确认，不能写成已经完成。
