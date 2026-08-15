# Economy Governance

这是从 v0.5.8 迁移来的首个 Application，负责殖民地建设、就业住房、资源平衡与长期经济规划。

- `planner.py` 从存档事实和 Content Pack 生成合法候选。
- `agent_tools.py` 向模型暴露读取状态、列举候选、准备和执行等受控工具。
- `conversation_agent.py` 管理带工具调用的持续战役对话。
- `iag_agent.py` 提供单轮/命令行执行入口。
- `application.toml` 声明角色、动作和平台能力。
- `prompts/` 保存灰风角色与经济治理提示词。
- `schemas/` 保存模型计划输出结构。

模型不能编造候选，也不能凭自然语言宣布执行成功。候选合法性来自规则引擎；执行事实来自 Host Bridge、网络确认或后续存档。
