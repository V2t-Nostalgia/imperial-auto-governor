# 开发约定

## 环境

- Python 3.11+
- Pydantic v2 用于跨 Agent 和执行边界的结构化契约
- `asyncio` 作为后续并发调度基础
- OpenAI 与 Anthropic Python SDK 分别作为 OpenAI-compatible 和 Anthropic Messages 模型的原生传输实现
- Application 使用显式静态注册表
- Content Pack 使用 TOML/JSON/YAML 等声明数据，不加载任意 Python

运行依赖集中在根目录 `requirements.txt`，Python 包元数据位于 `pyproject.toml`。Windows 和 Linux 专属依赖使用环境标记隔离。

模型适配代码位于 `src/iag/infrastructure/llm/`。新增供应商参数应优先经 `request_body_overrides` / SDK `extra_body` 传递；只有服务端路径或鉴权确实不兼容 SDK 时才扩展 `raw_http`。不要在 Application 中直接引入供应商客户端。

同一逻辑模型的不同供应来源应添加为 `ModelPool` 内的 `ModelEndpoint`，不要为每个中转站复制客户端。新增可自动回退的错误类型前，必须证明第一次请求未产生不可重复的服务端结果；否则只记录健康状态并把错误交给上层。

## 添加 Application

1. 在 `src/iag/applications/<application_id>/` 建立独立包。
2. 定义职责、输入视图、消息类型、动作类型、升级条件和严格限域的 `agent_tools.py`。
3. 提供领域提示词与 `conversation_agent.py`；可复用 `SpecialistConversationAgent`，但不能把其他 Application 的工具聚合进来。
4. 在 `src/iag/applications/registry.py` 显式登记，并在控制台注册独立的 Agent 工厂与模型路由。
5. 把玩家可调整政策放入配置，不写死最低储备或巡检频率。
6. 在模块自己的 `tests/` 下验证工具隔离、模型绑定、私有历史和执行事实。
7. 只有确定性验证器通过的 `ActionIntent` 才能进入执行层；多个 Application 的游戏副作用必须经共享执行锁串行化。
8. Application 通过 `PreparedAction` / `ExecutionBroker` 执行，不得导入 session proxy、
   packet builder、carrier click 或 native runtime client。manifest 声明 semantic action
   capability，不声明 transport。

## 添加 Semantic Action

1. 在 `src/iag/stellaris/execution/action_registry.py` 登记稳定动作名、版本、strict/frozen
   target、风险、验证状态与 semantic capability。
2. target 只表达游戏语义；wire tag、actor serial、queue object 和 hook 地址属于 backend。
3. Application/state 层生成并校验候选，Broker 只验证候选身份、权限、snapshot 和 dispatch。
4. 为每个正式 backend 增加明确 resolver 与机器确认测试。未知 native command 不得进入
   production registry。
5. 若动作需要多步，使用 ordered sequence；不要称为 transaction，也不要承诺回滚。

## 添加 Execution Backend

实现 `ExecutionBackend` 的 capability、support、单动作和 ordered sequence contract。
Backend 返回 typed evidence，公共状态由 Broker 标准化为 `ExecutionResult`。新增
扩展 `NativeRuntimeBackend` 不得要求 Application 修改业务代码；runtime probe 与
production backend 必须位于不同目录并采用不同准入标准。当前实机验证动作是
Linux 4.4.6 的 `stop_research.v1`、`move_fleet.v1`、`attack_fleet.v1`，以及 Windows
4.4.6 的 `move_fleet.v1`。runtime 自描述的未归类 action 默认进入 `etc`，其中不运行
自主 Agent。详见
[`EXECUTION_BROKER.md`](EXECUTION_BROKER.md)。

新增原生动作不得复制 game-loop Hook 或直接调用业务 `Execute()`。动作实现、版本
binding、Application 归属、严格参数、准入证据与测试清单见
[`runtime/NATIVE_RUNTIME_ACTION_CONTRIBUTING.md`](runtime/NATIVE_RUNTIME_ACTION_CONTRIBUTING.md)。

## 添加 Content Pack

1. 在 `content_packs/<pack_id>/` 创建 `pack.toml`。
2. 声明支持的游戏版本、所需 Mod、已验证版本和平台能力。
3. 只添加对象映射、定义来源和提示词补充。
4. 若需要新动作语义，先扩展平台或对应 Application。
5. 禁止在 Content Pack 中放可执行 Python、动态导入路径或安装脚本。

## 测试布局

测试与所属模块相邻，例如：

```text
src/iag/core/tests/
src/iag/stellaris/state/tests/
src/iag/stellaris/execution/tests/
src/iag/applications/economy_governance/tests/
apps/control_center/tests/
apps/host_bridge/tests/
```

测试源码应进入 Git；只有测试输出、临时目录和私密 fixture 被 `.gitignore` 排除。公开发行脚本应排除整个测试目录。

仓库级验证命令与 CI 相同：

```bash
python scripts/validation/run_tests.py --quiet
```

## 修改执行链

包格式、方向、槽位、对象 ID、可靠流坐标和房主确认都属于高风险边界。修改前应阅读 `docs/reference/v0_5_8/PACKET_INTERCEPTOR.md`、`docs/SESSION_PROXY_MODE.md` 和对应代码地图，保留样本证据并针对回归场景增加测试。不要凭字段名称猜测协议语义。

等长载体改写与动态代理构包是两条不同路径。`rewrite_*_carrier` 默认必须保持 UDP 长度，只有会话代理构建新记录时才可显式使用动态长度。新增代理动作还必须同时提供：离线构建测试、精确权威回包关联、存档目标校验、玩家权限边界和失败关闭策略。

## 代码注释

公共模块应有职责级 docstring。复杂协议块应解释不变量和失败条件，不逐行复述语法。每个文件的快速入口由 `docs/code-map/FILE_INDEX.md` 统一生成。
