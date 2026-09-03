# Save State

- `save_ingest.py` 接收、选择并稳定化最新上传存档。
- `extract_game_state.py` 把 `.sav` 解包并生成 Agent 使用的帝国状态。
- `planet_profiles.py` 从 Clausewitz 存档结构提取殖民地、区域、建筑槽、队列和拥有者等事实。
- `fleet_profiles.py` 提取玩家舰队、军力、位置、Fleet Manager 模板、实际/目标设计数量、增援队列、交战、MIA 与可调用状态，并把动作映射成受约束的移动或编制增援字段。
- `ship_profiles.py` 提取玩家舰船设计、部件槽和直接船坞证据；直接船坞数量命令只保留为协议研究事实，不是公开的定量增援接口。
- `research_profiles.py` 把已完成科技、三系当前研究、已滚出候选、常驻候选和储存研究点分开，避免把当前项目误判为已完成科技。

解析器只报告证据，不做战略推断。字段缺失应显式标记未知；不能用 UI 猜测值填充存档。
