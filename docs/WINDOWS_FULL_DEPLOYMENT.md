<!-- SPDX-FileCopyrightText: 2026 Nostalgia and Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 两台 Windows 完整部署教程

本教程对应当前经过验证的生产路线：房主电脑接收并权威执行联机命令，合作端电脑运行
Stellaris、Windows Agent、LLM 和可选的联网研究服务。模型只从本地规则引擎选择合法候选；
实际建设仍通过合作玩家在载体星球产生的合法点击，由房主执行桥在命令进入房主前作受约束改写。

## 1. 拓扑与前提

```text
房主电脑 <HOST_IP>
  Stellaris 房主 + IAGHostBridgeGUI + 房主存档上传

合作端电脑 <AGENT_IP>
  Stellaris 合作玩家 + IAGWindowsAgent + LLM API
  可选：Docker Desktop + SearXNG + Crawl4AI
```

准备事项：

1. 两台电脑使用相同的 Stellaris 版本、DLC、播放集和游戏端模组版本。
2. 房主电脑必须是 Windows；Host Bridge 需要管理员权限加载 WinDivert。
3. 合作端安装当前 Windows x64 Agent。启用联网检索时还需安装使用 WSL2 后端的
   [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)。
4. 为 Agent 准备支持 OpenAI Responses 或 Chat Completions 工具调用协议的模型服务。
5. 不要把 API Key、配对包、运行配置、日志或存档提交到 GitHub。

## 2. 启动合作端 Agent

1. 完整解压 `IAGWindowsAgent-0.5.5-windows-x64.zip`。不要只移动 EXE。
2. 双击 `IAGWindowsAgent.exe`。
3. 在 GUI 中确认运行数据目录、Stellaris 安装目录、控制台监听地址和端口。需要让房主电脑访问时，
   监听地址使用 `0.0.0.0`；默认端口为 `8765`。
4. 保存设置并启动控制台。首次保存会在运行数据目录的 `secrets/` 下生成前端密码、上传令牌、
   TLS 证书和私钥。
5. 点击“打开控制台”。浏览器访问 `https://<AGENT_IP>:8765/` 时会看到自签名证书提示；
   先核对 GUI 显示的 SHA-256 指纹，再继续并使用 GUI 提供的前端凭据登录。
6. Windows 防火墙只需允许房主电脑访问 Agent 的控制台端口。不要对公网开放该端口。

## 3. 启用可选联网检索

联网检索不是建设执行的前置条件。需要它时，在 Agent 解压目录打开
`research_services/`，运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Manage-IAGResearchServices.ps1 -Action Install
```

首次安装会拉取固定版本和摘要的 SearXNG、Crawl4AI 镜像，生成本机秘密，启动容器，执行搜索、
鉴权、正文提取与 Wiki 查询测试，并把服务地址写入当前 Agent 配置。两个容器只映射到
`127.0.0.1:8080` 和 `127.0.0.1:11235`，不需要新增局域网防火墙规则。

在网页模型设置区勾选“允许灰风联网检索”，然后点击“保存模型配置”。关闭该开关后，下一轮对话
和自主巡检不会获得 `search_web`、`fetch_page`、`search_stellaris_wiki`；容器仍会待命。要释放
Docker 资源，应另行运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Manage-IAGResearchServices.ps1 -Action Stop
```

详细管理命令和安全边界见 [RESEARCH_SERVICES.md](RESEARCH_SERVICES.md)。

## 4. 部署房主执行桥

1. 在合作端网页登录后，点击“下载已配对 Windows 房主执行桥”。
2. 将下载的 `IAGHostBridge-paired-windows-x64.zip` 传到房主电脑并完整解压。配对包包含当前
   Agent URL、TLS 指纹和桥接令牌，属于本局部署秘密，不得公开分发。
3. 在房主电脑启动 `IAGHostBridgeGUI.exe`，接受管理员权限提示。
4. 核对 Agent 地址为 `https://<AGENT_IP>:8765`，确认指纹与合作端 GUI 一致，并选择房主的
   Stellaris 存档目录。
5. 点击“开始上传”。GUI 应显示 Agent 已连接、房主入站改写可用和最近成功上传时间。
6. Host Bridge 窗口关闭即停止，不会创建开机任务。

## 5. 准备游戏与载体

1. 房主和合作端同时启用 `LLM Construction Carrier / LLM 建设载体` 与
   `Carrier Construction Console / 载体统一建设控制台`。
2. 房主创建合作模式游戏，合作玩家加入同一帝国。
3. 在游戏内使用政府法令创建灰风载体飞地，并让游戏时间推进到载体控制台初始化完成。
4. 川陀用于建筑载体点击；端点星用于区划和区域特化载体点击。不要把载体星球当作正常经济殖民地。
5. 房主开启周期性自动存档。Host Bridge 只上传房主实际生成的新存档，不会把旧档自动绑定到战役。

## 6. 配置模型与战役

1. 在网页配置模型协议、Base URL、模型名、API Key、`temperature`、超时、上下文上限、
   输出预留和压缩策略。Raw JSON 只能补充允许的请求参数。
2. 新建一条战役会话。每局游戏使用独立会话，不要依赖文件名自动绑定。
3. 在战役卡片中选择当前上传存档并明确绑定。
4. 填写当前十年计划；突发战争或经济崩溃时可单独开启紧急状态，结束后由玩家手动关闭。
5. 选择存档读取周期和运行策略。首次实局先使用“自主分析，只准备规划”。

## 7. 校准和首次执行

1. 按前端向导校准建筑、区划、区域特化和建筑升级所需的窗口相对坐标。
2. 每个校准点先运行“仅移动鼠标”，确认指针落在正确载体入口，不执行点击。
3. 运行一次立即巡检，检查读取到的游戏日期、殖民地数量、合法候选和规划理由。
4. 确认 Host Bridge 已连接、合作端端口候选已确认、载体窗口位置正确后，再切换为
   “自主分析并执行建设”。
5. 第一次建设后同时核对游戏队列、房主权威确认或下一份房主存档。仅有“已改写”而没有回包证据时，
   动作只能记为待存档确认，不能当作已经建成。

## 8. 部署验收清单

```powershell
# 在 Agent 的 research_services 目录
powershell -ExecutionPolicy Bypass -File .\Manage-IAGResearchServices.ps1 -Action Status
powershell -ExecutionPolicy Bypass -File .\Manage-IAGResearchServices.ps1 -Action Test
```

同时确认：

- Agent 网页能登录，模型配置能保存；
- 联网开关关闭后状态显示“联网研究已关闭”，重新开启后恢复；
- SearXNG、Crawl4AI 健康，未鉴权访问 Crawl4AI `/schema` 被拒绝；
- 房主桥使用已配对包连接，最新房主存档能上传；
- 当前战役明确绑定到该存档；
- Stellaris 双向 UDP 流量候选出现并最终确认载体命令；
- 第一次目标建设由房主权威回包或后续存档确认，游戏时间继续推进且没有 OOS。

## 9. 停止与恢复

结束游戏时关闭 Host Bridge 和 Windows Agent GUI。研究容器可以独立停止；下次使用时运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Manage-IAGResearchServices.ps1 -Action Start
```

`Start` 会复用原有秘密并重新执行健康检查，不会强制打开网页联网开关。`Remove` 删除容器但保留
Agent 配置、秘密和 SearXNG 缓存卷。

## 10. 常见问题

- **浏览器显示证书警告**：核对 Agent GUI 中的 SHA-256 指纹。不要在无法核对时继续。
- **网页返回 401**：使用 Agent GUI 生成的前端用户名和密码，不要使用存档上传令牌。
- **Crawl4AI 启动慢**：首次创建无头浏览器需要等待；以 `Action Test` 的最终结果为准。
- **Wiki API 返回客户端挑战**：工具会退回受白名单限制的 SearXNG Wiki 站内搜索，并在结果中标明来源。
- **关闭联网后仍占用内存**：网页开关只撤销模型工具；运行 `Action Stop` 停止容器。
- **房主桥未确认**：重新下载当前 Agent 生成的配对包，检查地址、TLS 指纹、令牌和 Windows 防火墙。
- **有存档但没有执行**：确认该存档已明确绑定到当前战役，并检查殖民阶段、合法候选和待确认账本。
