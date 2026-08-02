# 帝国内政代理运行时

本目录是 `LLM Construction Carrier / LLM 建设载体` 的外部代理。它运行在**非房主合作玩家的 Windows 或 Ubuntu 客户端**。由于联机合作端不会产生可供读取的 autosave，房主 Windows Host Bridge 会把稳定、完整的新存档送到代理端；代理验证存档后调用可配置的 LLM 形成建设规划，再用合法载体点击触发命令，并在该命令进入房主前做受约束的等长改写。

当前版本的目标是先稳定完成“分析一次、执行一次、等待房主确认”的实局闭环。它不会伪造新数据包，不会重放旧包，也不会在失败后自动补点。

## 运行链路

```text
房主 Windows autosave
  -> HTTPS 上传、证书指纹校验与 Bearer Token 鉴权
  -> Ubuntu 校验 SHA-256、ZIP 结构和战役身份并原子发布
  -> 完整状态提取
  -> 安全规则生成合法候选
  -> LLM 结合玩家指令做一次选择
  -> 生成带存档哈希的执行清单
  -> Windows 房主入站一发式拦截器 READY
  -> xdotool 从川陀统一控制台执行对应的一步或两步固定坐标序列
  -> 房主桥保留可靠头、命令数、serial 和 UDP 长度并改写目标命令
  -> 房主处理并广播权威命令
  -> 捕获房主明文回包，或由下一份房主存档补做结果确认
```

## 已实现

- 由房主 Windows 上传器自动发现当前战役的新 autosave，等待文件写入稳定后上传；Ubuntu 只读取经过哈希和 Stellaris ZIP 结构验证的当前存档。
- 读取 Stellaris 4.x 房主存档中的玩家国家、资源库存、月度收支、战争、舰队容量、科技、殖民地、岗位、住房、舒适度、稳定度、犯罪、区划、zone、建筑与建设队列。
- 排除川陀等带载体标记的专用星球，以及特殊星球和已有建设队列的星球。
- 只向模型提供规则引擎生成的合法候选，模型不能自行编造对象 ID。
- 支持 OpenAI Responses 兼容接口和 Chat Completions 兼容接口；`Base URL`、模型和 API Key 可在前端配置。持久工具会话目前使用 Chat Completions 兼容协议。
- 模型请求支持 `temperature`、超时、最大上下文、输出预留和受保护的 Raw JSON 参数；结构字段、工具和消息不能被 Raw JSON 覆盖。
- 每局维护十年计划和玩家控制的紧急状态；紧急状态暂停十年计划，直到玩家在前端明确结束。
- 上下文达到玩家设置的比例后自动生成概况版上文，并压缩到目标比例；SQLite 中的完整原始会话不会删除。
- 可选提供 SearXNG、Crawl4AI 和 MediaWiki API 只读工具；网页内容始终被标为不可信参考。
- “本局指令”已经升级为多战役持久会话。每局游戏拥有独立的玩家消息、灰风回复、自主巡检、长期规划、工具调用摘要和执行结果，统一保存在 `conversation/campaign.sqlite3`。
- 前端可新建、切换、重命名、归档和恢复战役会话。上传器只更新“当前上传存档”；玩家在前端下拉框中手动决定该 `campaign_id` 绑定哪条会话，也可保持未绑定。
- 内置“DeepSeek 思考模式 + 建设工具”模板，可一键设置官方 Base URL、`deepseek-v4-pro`、思考模式和 `max` 推理强度，也可随时切回自定义模板。
- DeepSeek 工具回合会完整重放对应 assistant 消息的 `reasoning_content`、`tool_calls` 与本地 tool 结果；隐藏思维不会显示在网页中。
- 前端可编辑长期战略 Prompt，并保留 Prompt 历史版本。稳定规则放在 Prompt，阶段要求和问答放在持久会话。
- 自动巡检有三种策略：暂停、只分析并准备规划、分析并执行建设。默认始终为暂停。
- 五步固定坐标校准：建筑入口、建筑载体、主区划载体、特化入口、特化载体分别保存窗口相对坐标与局部 OpenCV 防误点模板。
- 端口发现：读取 `/proc` 中 Stellaris 进程实际持有的 UDP 套接字，并与拦截器遥测交叉验证。
- Steam 代理识别：常驻观察器只读检查由 `steam` 托管的双向 UDP 流，并用可靠流标记筛选候选；只有载体命令或房主权威回包才能把候选升级为已确认。
- Windows 房主入站一发式 WinDivert 改写、包长度检查、权威回包确认、审计日志和紧急停止。

## 端口判定含义

前端不会把任意 UDP 数字冒充为本局端口。

| 状态 | 含义 |
| --- | --- |
| 未发现 | Stellaris 尚未运行。 |
| 等待套接字 | 发现进程，但尚未找到联机 UDP 套接字。 |
| 端口候选 | 端口属于 Stellaris 进程，但尚未匹配载体命令。 |
| Steam 代理流候选 | 已在 Steam 持有的套接字上观察到唯一双向流，且至少一个方向含可靠传输标记；显示当前端口，但仍等待载体命令确认。 |
| 双向流量匹配 | 拦截器流量与 Stellaris 进程套接字一致，仍等待载体命令。 |
| 载体命令锁定 | 已识别本局真实载体命令，当前客户端端口已锁定。 |
| 房主权威确认 | 已在锁定端口上看到目标命令的房主明文回包。 |
| 等待存档确认 | 载体已安全改写，但回包未提供可识别的完整目标字符串；暂停后续点击，等待新存档检查队列或建成状态。 |
| 端口冲突 | 遥测端口不属于 Stellaris 进程；前端会红色警告，禁止把它当成已确认端口。 |

房主发送端口和接收端口可以不同，也可能在重开房间后变化。运行时不把旧端口写死，每局重新发现并在载体命令出现时锁定。

## Ubuntu 依赖

在 Ubuntu 合作端执行：

```bash
cd ~/iag-agent
bash agent_runtime/install_ubuntu.sh
```

安装脚本在解压目录创建独立环境：

```text
.venv
```

Python 环境内只安装固定版本的 `NetfilterQueue`、`opencv-python-headless` 和其 NumPy 依赖，不向系统 Python 或项目目录安装包。系统层只准备运行所需的原生组件：

- `ffmpeg`
- `xdotool`
- `nftables`
- `libnetfilter-queue-dev`
- 编译 `NetfilterQueue` 所需的基础工具

要求 Python 3.11 或更高版本；Ubuntu 24.04 的系统 Python 可直接使用。可用 `IAG_PYTHON` 和 `IAG_VENV_ROOT` 覆盖解释器与虚拟环境路径。`xdotool` 通过 X11/XTEST 执行确定性点击；第一版不使用 OCR，也不让视觉模型识别按钮。独立 Release 的完整步骤见 [../../docs/UBUNTU_AGENT_README.md](../../docs/UBUNTU_AGENT_README.md)。

## Windows 代理端

Windows 代理端不要求 X11。源码方式安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r .\requirements-windows.txt
.\.venv\Scripts\python .\windows_agent_gui.py
```

也可以运行 `Build-IAGWindowsAgent.ps1` 生成 `windows_dist\IAGWindowsAgent\IAGWindowsAgent.exe`。GUI 不创建计划任务，也不开机自启；关闭 GUI 会停止本机代理控制台。首次保存设置时自动生成本地 HTTPS 证书、前端密码和房主桥接令牌，并显示可复制的证书 SHA-256 指纹。

两台 Windows 电脑的典型分工：合作端运行 `IAGWindowsAgent.exe` 和 Stellaris，房主端运行 `IAGHostBridgeGUI.exe`。房主桥地址填写合作端局域网 IP，令牌与证书指纹从 Windows 代理 GUI 复制。

## 配置

首次安装后编辑：

```text
~/iag-agent/agent_config.json
```

至少检查：

- `save_source_mode`：生产环境保持 `host_upload`；只有排查旧环境时才显式使用 `legacy_local`。
- `uploaded_save_root`：房主存档在 Ubuntu 上的受控存放目录，默认 `state/uploaded_saves`。
- `save_manifest_path`：当前已验证存档的原子清单，默认 `state/current_save.json`。
- `save_review_interval_months`：自主巡检周期，只接受 `1`、`3`、`6`、`12`。
- `runtime_root`：默认使用解压目录（`.`）；也可以改为当前普通用户可写的独立目录。
- `display`：当前已知为 `:1`。
- `xauthority`：当前已知为 `/run/user/1000/gdm/Xauthority`。
- `host_ip`：可留空，由 Stellaris 进程套接字发现；存在多个候选时应手动填写房主局域网 IP。
- `network_interface`：可留空监听全部接口；固定局域网环境建议填写游戏流量所在接口，例如 `ens34`。
- `passive_telemetry_path`：只读网络观察器状态文件，默认 `state/passive_flow_status.json`。
- `host_discovery_timeout_seconds`：执行前自动等待端点恢复的最长秒数，默认 `20`。
- `host_discovery_poll_seconds`：端点恢复轮询间隔，默认 `0.5` 秒。
- execution_transport：生产环境必须使用 windows_host_bridge，即经过实机验证的房主端入站改写。linux_client_nfqueue 只保留为研究回退，不得用于自动建设。
- host_executor_ready_timeout_seconds：等待 Windows 房主桥证明管理员 WinDivert handle 已打开的最长时间，默认 30 秒；超时绝不点击载体。
- interceptor_privilege_mode：只影响显式启用的 Linux 研究回退路径。
- `base_url` 和 `model`：也可直接在前端修改。

API Key 不会返回给浏览器。前端保存后写入：

```text
~/iag-agent/secrets/llm_api_key
```

权限为 `0600`。也可不写文件，改用 `IAG_LLM_API_KEY` 环境变量。

房主存档上传使用独立随机令牌，不与网页密码或模型 API Key 共用。服务首次启动时生成：

```text
~/iag-agent/secrets/save_upload_token
```

上传入口只接受带该 Bearer Token 的原始 `.sav` 请求，并校验请求长度、SHA-256、文件名、战役标识及 ZIP 内的 `meta`、`gamestate`。验证失败的文件不会成为当前存档。

## 房主 Windows 上传器

GUI 上传器位于：

```text
tools/windows_save_uploader/
```

正式包携带独立 Windows x64 运行时、PyDivert 和 WinDivert 驱动，不要求安装 Python，也不创建计划任务或开机自启。玩家双击 `IAGHostBridgeGUI.exe` 并接受管理员权限提示，在窗口内填写 LLM 端 IP、确认存档目录，再点击“开始上传”。点击“停止上传”或关闭窗口会停止心跳、扫描、上传和当前房主拦截器。

GUI 每 5 秒向 `/api/save/client-heartbeat` 发送鉴权心跳。Ubuntu 前端据此显示房主客户端是否在线、真实来源 IP、Windows 主机名和最近心跳，而不是把“服务器接收器已启动”误报成“房主已连接”。

新版还通过心跳声明 host_inbound_rewrite_v1。每次执行时，Windows 先从心跳领取一次性清单、在房主端打开入站 WinDivert handle、回报 READY；Ubuntu 只有收到 READY 才点击对应载体星球。旧 GUI、未提权进程或未打开的 handle 都会在点击前被拒绝。

上传器只处理大小和修改时间已经稳定、且 ZIP 元数据可读的新 `.sav`。每份存档计算 SHA-256，成功状态持久化到本地，网络失败时保持运行并重试。更改同一台 LLM 主机的局域网 IP 可直接在 GUI 修改；TLS 指纹与独立上传令牌仍用于确认目标服务器身份。

Windows 端在 GUI 运行期间上传每一份稳定的新存档。网页中的“按月 / 按季 / 每半年 / 每年”只控制灰风多久读取一次新状态并发起自主巡检，不改变上传频率。
## 安装常驻服务

运行时默认由 systemd 系统服务启动，并监听所有 IPv4 接口的 `0.0.0.0:8765`。因为控制台可以修改模型密钥并触发真实建设，局域网模式强制同时启用 HTTPS 和 Basic Auth；缺少证书或密码时程序会拒绝绑定非回环地址。

在 Ubuntu 执行：

```bash
cd ~/iag-agent
bash agent_runtime/install_systemd_service.sh
```

安装脚本会：

- 生成随机前端密码，保存为 `~/iag-agent/secrets/frontend_password`，权限 `0600`。
- 生成包含本机局域网 IP 的自签名 TLS 证书。
- 把服务安装到 `/etc/systemd/system/iag-agent-console.service`。
- 同时安装 `/etc/systemd/system/iag-agent-network-observer.service`；该服务只拥有 `CAP_NET_RAW`，不拥有改包权限。
- 控制台服务只获得启动 NFQUEUE 子进程所需的 `CAP_NET_ADMIN`，不获得 root 身份。
- 执行 `systemctl enable --now iag-agent-console.service`。
- 把现有 `agent_config.json` 的前端配置更新为 `0.0.0.0:8765`。

Windows 浏览器访问：

```text
https://<AGENT_IP>:8765/
```

首次访问会遇到自签名证书警告。登录用户名为 `iag`；密码在 Ubuntu 上查看：

```bash
cat ~/iag-agent/secrets/frontend_password
```

证书位于 `~/iag-agent/secrets/frontend_tls.crt`，可以导入 Windows 的受信任证书存储以消除警告。不要把密码、私钥或模型 API Key 提交进 mod 或 Git。

常用维护命令：

```bash
sudo systemctl status iag-agent-console.service
sudo systemctl restart iag-agent-console.service
sudo systemctl stop iag-agent-console.service
sudo journalctl -u iag-agent-console.service -f
sudo systemctl status iag-agent-network-observer.service
sudo journalctl -u iag-agent-network-observer.service -f
```

服务主进程始终以执行安装脚本的普通用户运行。开机自启负责前端、存档分析和规划；生产路径使用 Windows 房主桥，不把网页服务提升为 root。

网络观察器同样以该普通用户运行，只额外获得读取原始包所需的 `CAP_NET_RAW`。它不创建 nftables 规则，不进入 NFQUEUE，也不修改、丢弃或延迟游戏流量。

需要临时前台运行时，可以停止 systemd 服务后执行：

```bash
cd ~/iag-agent
bash agent_runtime/start_console.sh
```
## 持久会话与自动巡检

网页中的“灰风 · 本局上下文”是一条正常的多轮会话，不再是覆盖式的单个文本框。左侧“战役会话”栏负责管理不同游戏：

- 新开游戏时先建立新的战役会话，再在“当前存档 · 手动绑定”下拉框中选择它并应用。
- 继续旧档时，上传器只更新当前存档信息。若该 `campaign_id` 已有绑定，前端会显示对应会话，但不会自动切换；点击“打开已绑定会话”才会进入原有上下文。
- 同一 `campaign_id` 只能绑定一条会话，避免同一局历史分叉。
- 运行时同时核对 `campaign_id` 与上传器提供的存档目录标签；若内部 ID 相同但标签不同，会阻止巡检与建设并要求玩家重新确认绑定。
- 每条会话独立保存自动巡检模式、巡检周期、下次复查时间和最新规划。
- Prompt、模型网关与载体校准仍是全局配置，不会为每局复制一份。
- 当前上传存档与所选会话不一致时，聊天历史仍可查看；读档、规划、巡检和实际建设会被本地代码拒绝。
- 代理任务运行期间不能切换、归档或重新绑定会话，防止后台结果写入另一局。
- `maximum_pending_construction_items_per_planet` 默认是 `2`：允许在已有一个可识别建设项目时追加一个不同目标；达到上限、目标重复或队列项无法解析时停止追加。

首次升级会保留旧版 `campaign` 会话及全部消息，但不会根据当前上传存档自动建立绑定。升级后由玩家在前端明确选择对应关系。

每条战役会话内：

- 玩家可以随时补充战略要求或询问当前状态。
- 需要当前数值时，模型调用 `inspect_empire_state` 读取房主最近上传且已验证的存档。
- 需要建设时，模型先调用 `list_legal_construction_candidates`，再用 `prepare_construction` 线性选择候选；一轮可在玩家设定上限内连续执行多项，但每项都先完成本地校验和事实记账。
- 无需建设时，模型调用 `record_noop_review` 记录判断；下次复查月份由网页配置的固定周期决定，模型不能自行缩短或延长。
- 只有运行策略为“自主分析并执行建设”时，模型才会看到 `execute_prepared_construction` 工具。
- 执行工具只接受刚生成且尚未执行的 `run_id`；存档、端口、载体点击、等长改写与房主确认仍由本地代码重新验证。严格模式下，明文回包未命中会暂停到新存档；玩家也可启用“允许安全改写后暂记并继续”，此时动作标为 `provisional_pending_save`，占用虚拟槽位和资源，绝不会被宣称为已完成。

自动巡检由**房主新上传存档和游戏月份**驱动，不按现实时间盲目重复调用 API。服务发现一份尚未审计且达到前端所设复查月份的新存档后，才启动下一轮。即使服务重启，默认策略仍是 `paused`，不会自行点击游戏。

DeepSeek 与其他 Chat Completions 接口本身不保存会话状态。运行时会从 SQLite 重放历史，并按前端设置的 token 上限预算输入与输出。达到压缩触发比例后，较早的完整工具协议段会被模型总结为概况，近期协议段原样保留；assistant 工具调用与对应 tool 结果不会被拆开。数据库仍保留完整记录，因此压缩只改变 API 重放上下文，不改写历史审计。

### 十年计划与紧急状态

十年计划的起止日期由同步存档中的游戏日期维护。当前十年接近结束时，前端开放下一期草案；跨期后自动晋升。紧急状态包含标题和独立指令，激活后在每轮提示中优先于十年计划，并且不会由模型自行结束。玩家处理完突发事件后必须在前端点击结束紧急状态。

### 联网检索

启用后，模型获得 `search_web`、`fetch_page` 和 `search_stellaris_wiki`。通用搜索连接 SearXNG，正文提取连接 Crawl4AI，Wiki 查询使用 MediaWiki Action API。`fetch_page` 只能读取本轮检索实际返回的 URL，且受域名白名单、私网地址拒绝、正文长度和超时限制。论坛攻略可以影响判断，但不能创建候选、提供对象 ID 或直接触发建设。

自托管服务的当前官方模板、JSON 输出要求、鉴权和连通性检查见 [../../docs/RESEARCH_SERVICES.md](../../docs/RESEARCH_SERVICES.md)。

## DeepSeek 模板

在“模型连接”中选择：

```text
DeepSeek 思考模式 + 建设工具
```

模板会设置：

```text
协议: Chat Completions Compatible
Base URL: https://api.deepseek.com
模型: deepseek-v4-pro
thinking.type: enabled
reasoning_effort: max
```

填写 API Key 并保存即可。模型只生成工具调用；真正的存档解析、候选校验和建设执行都在本地完成。切换回“自定义 OpenAI 兼容接口”后，可自行修改 Base URL 和模型名称。

## 每局固定坐标校准

1. 在 Ubuntu 合作端进入本局，并把 Stellaris 调整到约定窗口位置与大小。
2. 在前端依次校准川陀的“建筑入口”“建设指令中继”“发电区划载体”“特化入口”“工程学研究特化载体”“升级指令中继槽”和“升级按钮”。
3. 每一步都把川陀恢复到提示要求的基础画面，然后点击“捕获当前窗口”。
4. 在前端截图中单击对应入口或载体按钮，再保存点击位置。
5. 点击“仅移动鼠标测试”。它只测试当前下拉框选中的载体，不会点击。
6. 七步校准分别写入 `carrier_building_open.json`、`carrier_click.json`、`carrier_district_click.json`、`carrier_zone_open.json`、`carrier_zone_click.json`、`carrier_upgrade_open.json` 和 `carrier_upgrade_click.json`。
7. 若窗口尺寸或局部按钮画面不再匹配，执行器会在点击前拒绝执行并要求重新校准。

这里保存的是**窗口相对坐标**，不是整个桌面的绝对坐标，因此小范围移动窗口不会改变目标；窗口尺寸变化仍会触发保护。

统一建设目录会把模型本轮选择的真实目标置顶；游戏内列表不随目标变化，而是始终把已验证载体放在第一项（建筑和特化为唯一项）。执行时先点击合法载体，再由房主入站桥等长改写。完整差分实验记录只保留在原始工作仓库，不进入公开运行包。

## 一次实局循环

1. 在房主 Windows 电脑启动 GUI 上传器，确认地址与存档目录后点击“开始上传”。前端必须显示 GUI 客户端在线、已验证存档和当前战役。
2. 在前端保存模型模板、Base URL、模型名称与 API Key，并保存战略 Prompt。
3. 让 Ubuntu 合作端进入同一局，保持约定的 Stellaris 窗口尺寸，并完成本局载体点击校准。
4. 在“灰风 · 本局上下文”中说明当前大战略，例如备战期限、资源偏好和禁止改动的星球。
5. 第一次先选择“自主分析，只准备规划”和读取周期，点击“应用策略”，再点击“立即巡检”。
6. 检查灰风回复、工具审计摘要、帝国快照和最新规划，并确认端口状态没有“端口冲突”。第一次执行前显示“等待载体命令最终确认”是正常状态；真实载体点击会完成确认。
7. 验收无误后，选择“自主分析并执行建设”，再次点击“应用策略”。这一步就是正式启动自动建设。
8. 后续房主新存档达到网页配置的复查月份时，灰风会自动审计；默认每轮最多执行三项，但模型应按资源和紧迫性提前停止。
9. 前端显示房主明文权威回包确认，或后续房主存档明确显示目标进入队列/已经建成，建设才算成立；失败后不会自动重试。

要暂停模型巡检与自动建设，选择“暂停自动巡检”并点击“应用策略”。房主 GUI 的“停止上传”只停止存档同步；关闭 GUI 会同时停止上传和心跳。紧急停止按钮用于终止正在进行或即将进入执行阶段的本轮操作。

顶部“本轮决策”仍保留旧的一次性规划与人工执行按钮，作为兼容和故障排查入口。

## 审计文件

每轮输出位于：

```text
~/iag-agent/runs/YYYYMMDD_HHMMSS/
```

关键文件：

- `snapshot.json`：完整解析后的本局状态。
- `candidates.json`：规则引擎允许的候选。
- `decision_request.json`：发送给模型的结构化请求。
- `plan.json`：模型决策。
- `execution_manifest.json`：不可越过的执行约束与目标参数。
- `interceptor.jsonl`：载体识别、改写和房主确认日志。
- `execution_result.json`：最终结果。
- `interceptor.stdout.log` / `interceptor.stderr.log`：故障诊断。

## 安全边界

- 只处理非房主合作客户端发往房主的真实载体命令。
- 每条命令仍只准备、点击和确认一项建设；同一巡检允许有界串行批次，禁止并发。
- 保持 UDP payload 长度、可靠传输头、命令数量和 carrier serial 不变。
- 不插包、不重放、不凭空构造命令、不自动重试。
- 必须看到房主明文权威回包，或在后续新存档中看到精确目标进入队列/已经建成，才算成功。
- 执行期间存档变化、上传清单失效、端口冲突、窗口尺寸变化、模板不匹配或未知包结构时全部失败关闭。

## 当前限制

- 自动巡检已经实现，但默认暂停；只有用户明确切换到 `execute` 才允许模型调用真实建设工具。
- 默认每轮最多三项建设。顺序固定为 `prepare A → execute A → 房主明文确认或新存档确认 A → prepare B`；任一失败或等待确认都会封锁本轮后续动作。
- 已确认动作会先登记为本轮虚拟队列并扣减能够从 4.4.6 本地规则中确定的成本，再重新生成候选，防止存档尚未刷新时重复下单。
- `NEW_COLONY_NAME_1` 是可在不同战役重复出现的本地化模板键，不是显示名或唯一标识。机器执行使用 `planet_id`；工具日志优先使用存档中的 `display_name_hint`，例如 `Trappist-I`。
- 完整会话保存在本地 SQLite；发送给模型的历史按前端设置的最大上下文、输出预留、压缩触发占比和压缩目标占比滚动整理。概况不会删除或覆盖原始审计记录。
- 联网检索默认关闭，可由玩家启用。启用后仅提供受域名、URL 来源、正文长度和超时约束的只读参考工具；网页内容不能绕过本地合法候选或直接触发建设。
- 城市区划中的八项选定特化已进入生产能力表：`zone_foundry`、`zone_factory`、`zone_research`、三种学科研究特化、`zone_unity` 和 `zone_trade`。它们都已取得 Stellaris 4.4.6 的合作端请求与房主权威回传配对样本；固定源载体为最长的 `zone_research_engineering`。四种基础主区划已根据存档占用和本地定义生成容量候选；存在条件成本分支时保持“成本未知”，不编造精确值。
- 度假星球、环世界、栖息地、蜂巢、机械、企业等特殊分支默认转人工审查。
