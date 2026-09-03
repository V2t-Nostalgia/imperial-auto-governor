# Applications

Application 是平台原生的专业能力模块。每个 Application 拥有受严格限制的角色、状态视图、动作类型、规则验证器和模型提示词。

当前内置注册表明确登记：

- `economy_governance`：殖民地建设与经济治理。
- `fleet_operations`：默认关闭的实验性舰队观察、移动、舰船设计与逐舰队编制增援。
- `research_strategy`：默认关闭的实验性科研候选读取和科技选择。

新增能力必须显式注册并声明所需平台能力，不能靠目录扫描自动加载代码。

跨存档动作通过 `save_continuations.py` 注册固定续接器。模型只记录一次持久意图；新存档
到达后由续接器解析字段并推进状态机，不把纯机械的 ID 解析重新交给模型。
