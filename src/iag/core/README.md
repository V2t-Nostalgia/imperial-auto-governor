# Core

这里保存与 Stellaris 具体内容无关的平台核心。

- `contracts.py`：Mandate、状态视图、Agent 消息与执行结果的 Pydantic 契约。
- `application_registry.py`：只允许显式登记的 Application 注册表。
- `autonomy.py`：自主巡检周期、暂停和复查策略。
- `campaign_strategy.py`：十年计划与紧急状态等战役级战略数据。
- `context_window.py`：上下文预算、压缩阈值与摘要边界。
- `conversation_store.py`：每场战役独立会话及持久化。
- `paths.py`：源码、可编辑安装和 PyInstaller 的统一资源路径。

本目录不能依赖具体建筑 ID、WinDivert 或网页 UI。跨层数据必须先落成 `contracts.py` 中的明确契约。
