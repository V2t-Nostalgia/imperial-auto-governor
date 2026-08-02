<!-- SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Windows 房主执行桥 GUI

该程序运行在 Stellaris 多人游戏的 Windows 房主电脑上，同时负责上传 autosave 和执行已经过本地校验的一次性入站包改写。它不修改存档；只有 Ubuntu 端先发布建设清单、Windows 端以管理员权限打开 WinDivert 并回报 READY 后，合作端才会执行载体点击。

## 生命周期

- 不安装系统服务。
- 不创建计划任务。
- 不开机自启。
- 打开 GUI 后仍不会上传，必须点击“开始上传”。
- 点击“停止上传”会停止扫描、心跳、后续上传和当前房主拦截器。
- 关闭 GUI 窗口等同于停止上传。
- 游戏结束但 GUI 仍开启时，客户端只维持轻量心跳并等待新存档；没有新 `.sav` 就不会上传文件。

## 使用方法

1. 解压完整目录，不要只单独复制 EXE。`iag_save_uploader.json`、`secrets`、`logs` 和 `state` 会在首次保存设置后写入用户选择的运行目录，不会随公开包分发。
2. 双击 `IAGHostBridgeGUI.exe`，接受一次 Windows 管理员权限提示。
3. 推荐从正在运行的 Agent 控制台下载“已配对 Windows 房主执行桥”；该临时下载包已写入当前 Agent 地址、TLS 指纹和桥接令牌。直接使用 GitHub Release 的通用包时，才需要手工填写这些信息。
4. 确认 Stellaris 存档目录；默认会定位当前 Windows 用户的 `Documents\Paradox Interactive\Stellaris\save games`。
5. 点击“保存设置”。
6. 点击“开始上传”。
7. GUI 的“房主入站改写”必须显示“WinDivert 已提权，等待建设清单”；LLM 前端也应显示房主桥能力就绪。
8. 本局结束时点击“停止上传”或直接关闭窗口。

只填写 IP（例如 `<AGENT_IP>`）时，程序自动使用：

```text
https://<AGENT_IP>:8765
```

## 工作方式

1. GUI 启动后用独立令牌和固定 TLS 证书指纹连接 LLM 端。
2. 每 5 秒发送一次鉴权心跳，使前端能判断客户端是否真的在线。
3. 等待 `.sav` 的大小和修改时间至少稳定 5 秒。
4. 验证存档 ZIP 中同时存在 `meta` 与 `gamestate`。
5. 锁定产生最新存档的战役目录。
6. 计算 SHA-256，并通过 HTTPS 上传到 `/api/save/upload`。
7. 新战役目录出现更新的稳定存档时自动切换。
8. 心跳收到一次性建设清单后，在房主机打开只匹配合作端 IP 的 WinDivert handle，再向 Ubuntu 回报 READY。
9. Ubuntu 收到 READY 后才点击与动作同族的载体：川陀上的建设指令中继、发电区划、工程学研究特化和升级指令中继分别用于建筑、主区划、区划特化和建筑升级。房主桥只改写该入站记录，并保持 UDP payload 长度、命令记录长度、命令数和 serial 不变。Host Bridge 8 在升级过渡期仍接受旧研究实验室的建造与升级载体。
10. 房主广播目标建筑、主区划或区划特化后回报权威确认；失败不会自动重试。

旧版仅上传存档的 GUI 不具备 `host_inbound_rewrite_v1` 能力。服务器会在点击前拒绝旧客户端，不能继续用于自主建设。

## 高级设置

“高级设置”中可以修改服务器 TLS 证书 SHA-256 指纹。更改同一台 LLM 主机的局域网 IP 通常不需要修改指纹；更换服务器时，必须同时换用新服务器的指纹和上传令牌。

程序不会在 GUI 中显示令牌明文。令牌默认位于：

```text
secrets/save_upload_token
```

## 本地状态

- `state/uploader_state.json`：客户端 ID、当前战役锁定及最后成功上传。
- `logs/save_uploader.jsonl`：心跳、扫描、上传与错误日志。
- `iag_save_uploader.json`：LLM 地址、证书指纹、存档目录和轮询参数。

“重新选择战役”只清除本地战役锁定，不删除游戏存档，也不修改 Ubuntu 已接收的历史文件。

若配置文件被误删，GUI 会在首次启动时自动生成一份空白基础配置并正常打开；服务器地址和 TLS 指纹仍需在界面中填写。GitHub Release 的公开包永远不携带服务器地址、证书指纹或桥接令牌；只有通过已登录的 Agent 控制台生成的“已配对”临时下载包会包含当前 Agent 的配对数据。

## 源码运行

源码运行还需要项目内附带的 PyDivert/WinDivert 运行库，并应从管理员 PowerShell 启动：

```powershell
py -3 .\iag_save_uploader_gui.py
```

正式压缩包携带独立 Windows x64 运行时，不要求房主安装 Python。
