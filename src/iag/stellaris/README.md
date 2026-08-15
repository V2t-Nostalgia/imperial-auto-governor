# Stellaris Adapter

这一层把平台抽象连接到 Stellaris。

- `state/` 读取并规范化存档事实。
- `execution/` 把已验证动作转成游戏侧操作并收集确认事实。
- `game_knowledge.py` 读取原版定义，为规则引擎提供建筑、区域和区划知识。

这里不负责决定帝国应该发展什么；战略判断属于 Application。Content Pack 可以映射 Mod 内容，但不能绕过本层执行边界。
