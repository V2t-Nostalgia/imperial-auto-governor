"""Imperial Auto Governor 群星多 Agent 平台。

顶层包只公开稳定的平台版本。领域逻辑、执行桥和具体 Application 必须通过
各自子包访问，避免重新形成旧版运行目录中的隐式耦合。
"""

__version__ = "0.5.9"
