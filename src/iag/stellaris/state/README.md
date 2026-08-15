# Save State

- `save_ingest.py` 接收、选择并稳定化最新上传存档。
- `extract_game_state.py` 把 `.sav` 解包并生成 Agent 使用的帝国状态。
- `planet_profiles.py` 从 Clausewitz 存档结构提取殖民地、区域、建筑槽、队列和拥有者等事实。
- `fleet_profiles.py` 提取玩家舰队、舰船数、军力、当前位置、移动、交战、MIA 与可调用状态，并把已验证目标映射成 `d32c` 目的地字段。

解析器只报告证据，不做战略推断。字段缺失应显式标记未知；不能用 UI 猜测值填充存档。
