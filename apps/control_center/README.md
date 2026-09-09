# Control Center

控制中心承载战役会话、模型配置、周期巡检、联网检索开关、执行状态和前端 API。

- `web_console.py` 是 HTTP/HTTPS 服务入口。
- `windows_agent_gui.py` 是 Windows 一键启动与本地证书配置界面。
- `web/` 是当前灰风风格前端。
- `agent_config.*.example.json` 是无秘密的系统示例配置。
- `IAGWindowsAgent.spec` 从 monorepo 构建独立 Windows Agent。

主页面的 Application 模型配置区只负责选择“哪个 Application 使用哪份玩家命名配置”。配置可以自行命名，并绑定到某个模型池中的具体逻辑模型；`temperature`、思考模式、上下文压缩和联网研究开关随该配置保存。

`/api/overlay/*` 是与网页前端分离的游戏叠加层接口，使用独立 Bearer 令牌。它只提供
玩家 `operator_message`、模型可见正文和正文增量；工具结果、系统审计与
`reasoning_content` 不会进入该接口。网页控制台继续保留完整的审计展示方式。

“模型配置面板”使用左侧模型池目录和右侧配置区。模型池名称由玩家决定；池内可以添加 OpenAI-compatible 或 Anthropic Messages 端点，分别设置供应商模型名、Base URL、API Key、优先级、协议和传输。点击任何现有端点卡片即可重新编辑这些参数，`OpenAI Compatible` 和 `Anthropic API` 两个模板只提供可继续修改的初始值。多个端点只要具有相同 `model_id`，就会为同一个逻辑模型提供按成本顺序排列的回退来源；数字较小的端点先用。

“保存并检测端点/检测此池端点”只调用配置的模型列表路径，不发送聊天消息。健康状态属于当前进程；持久化的启停和优先级只由玩家修改。API Key 会写入被 Git 忽略的运行配置，但公开状态只返回 `api_key_configured`。

运行配置写入 `runtime/agent_config.json`，不应回写示例文件或提交版本库。
