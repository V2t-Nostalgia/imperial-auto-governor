# Stellaris 4.4 内政策略资料库

更新日期：2026-07-28

这份文件只提供可追溯的决策背景，不是固定建造表。LLM 每轮仍须结合当前存档中的战争状态、资源收支、库存、殖民地岗位、住房、稳定度、区划、zone、现有建筑和建设队列独立判断。

## 版本事实

- Paradox 的 4.0 说明确认了当前经济结构：区划提供基础岗位，区划特化为对应区划增加岗位，特化还会解锁建筑槽位。
  - https://www.paradoxinteractive.com/games/stellaris/news/stellaris-update-4-0
- 2026-07-28 查询时，正式分支最新公开热修为 4.4.6；本地运行器必须以实际存档版本为准，不得把 4.5 测试分支规则混入 4.4 存档。
  - https://store.steampowered.com/news/posts/?appids=281990

## 可借鉴的自动化原则

Better Colony Automation 4.4 的公开说明与开源文档提供了有价值的保守门槛：

- 有建设队列时不再追加建设。
- 只有空闲岗位低于阈值时才扩充纯岗位建筑或区划。
- 保留矿物安全库存。
- 先考虑提高现有岗位效率的建筑，再考虑单纯增加岗位。
- 只有相关帝国月收入低于目标时，才继续扩张对应资源产能。
- 行星定位用于指导 zone 与建筑选择。
- 特殊行星需要额外分支；未知情况宁可跳过。
- 本项目只参考规则思想，不复制其源代码。

来源：

- https://steamcommunity.com/sharedfiles/filedetails/?id=3673829479
- https://github.com/StellarWarp/better_colony_automation/blob/master/README_EN.md

## 社区策略信号

近期 4.4 社区讨论中反复出现的共识：

- 先稳住能源、矿物、食物、消费品等基础经济，再扩大合金、科研和凝聚力。
- 不要为尚不存在的人口提前制造大量岗位；空岗位只产生维护负担。
- 行星定位、天然修正和帝国短板共同决定特化，不采用固定行星比例。
- 原料星优先考虑提高现有原料岗位效率；都市特化星在有人口可填充时再增加岗位。
- 稀有资源紧张时避免新增高维护建筑或升级。

来源：

- https://www.reddit.com/r/Stellaris/comments/1v7gxws/universal_specialized_planet_guide_for_44/
- https://www.reddit.com/r/Stellaris/comments/1uyc1zg/planet_specialization_ratios/

## IAG 首轮实战约束

1. 每轮允许有界串行批次；每个命令必须逐项等待房主权威确认，任何失败立即停止。
2. 目标行星必须没有进行中的建设。
3. 不拆除、不升级、不替换现有建筑，不更改政策、物种权利、市场、舰队或外交。
4. 特殊行星、度假星球和无法识别的结构默认跳过。
5. 空闲岗位明显过多时，不增加纯岗位建筑。
6. 能源、矿物、消费品或食物出现高风险时，不进行科研等高维护扩张。
7. 模型只能从执行器提供的已验证 building/zone 白名单中选择。
8. 点击前再次核对最新存档哈希与游戏日期；改写后必须看到目标命令的房主权威回传或新存档结果，否则立即停机。
