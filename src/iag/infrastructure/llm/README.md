# LLM Providers

该目录把“模型协议”和“网络传输”分开：

- `model_client.py` 保留上层使用的同步入口，同时提供异步入口，负责 Responses / Chat Completions 请求形状、JSON 解析和 DeepSeek 工具消息。
- `model_pool.py` 定义玩家命名的模型池和供应端点。端点通过 `model_id` 声明自己服务的逻辑模型，因此不同供应商可以使用不同的请求模型名、Base URL、密钥和传输，同时仍参与同一条回退链。
- `application_model_profile.py` 定义玩家命名的 Application 模型配置，将一个第一方 Application 绑定到模型池中的某个逻辑模型，并保存该 Application 的请求、上下文压缩和联网研究参数。
- `model_pool_runtime.py` 按玩家设置的 `priority` 从小到大选择端点，记录本进程健康与冷却状态，并只对明确可安全换源的错误执行回退。
- `endpoint_probe.py` 使用端点的 `GET /models` 做无聊天费用的可达性检查，同时兼容 DeepSeek 与 OpenRouter 的 `data[].id` 响应形状。
- `providers.py` 实现传输层。`openai_sdk` 默认使用官方 `AsyncOpenAI`；`raw_http` 保留旧版精确 URL 行为，只用于兼容特殊服务或诊断。
- `model_templates.py` 与 `model_templates.json` 提供玩家可切换的模型模板，不保存密钥。

`model_transport` 必须由玩家为每个端点显式选择。一个端点不会在 SDK 与原始 HTTP 之间偷偷切换。模型池只会在 401/403、404、429 或尚未取得 HTTP 响应的连接错误后尝试下一端点；5xx、超时及未知错误可能已经被服务端处理，因此不会自动重放。

429 冷却优先采用服务端 `Retry-After`，缺失时使用端点的 `rate_limit_cooldown_seconds`。这不是根据本地 token 数猜测供应商额度。显式 `/models` 探测失败也不会修改持久化的 `enabled`；它只更新当前进程的健康和冷却状态。

供应商特有参数通过受控 `request_body_overrides` 进入 SDK 的 `extra_body`。DeepSeek 返回的 `reasoning_content` 会原样保留给本地会话账本，但不会作为普通回复展示。

测试：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s src/iag/infrastructure/llm/tests -p "test_*.py" -v
```

## 运行时配置

`RuntimeConfig` 只在程序组合边界读取本地 JSON。运行代码使用相互隔离的一致快照；每份快照包含完整模型资产目录、Application 配置目录、当前 Application 绑定，以及只含所选逻辑模型端点的运行池。网页端修改会立即更新内存，只有显式调用 `save()` 才写回磁盘。

持久化配置将连接信息放在 `model_pools[].endpoints`，将玩家命名的运行方案放在 `application_model_profiles`，并通过 `application_model_bindings` 指定每个 Application 当前启用的方案。旧版 `endpoint`、`model_pool`、根级 `request_options`，以及 v0.5.8 的根级 `base_url`/`model`/API Key 文件配置仍可读取，并在下一次显式保存时迁移。API Key 保存在玩家本机未跟踪的 `agent_config.json` 中，进入内存后由 `SecretStr` 包装，且不会通过控制台公开接口返回。该文件必须保持在 Git 跟踪范围之外并限制为当前用户可读。
