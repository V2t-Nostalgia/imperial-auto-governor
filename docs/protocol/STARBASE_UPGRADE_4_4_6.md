# Stellaris 4.4.6 恒星基地升级命令

本文记录一次完整的非房主请求与房主权威广播配对。测试动作是把普通前哨站升级为
星港；捕获过程只观察网络，没有运行数据包改写器。

## 已确认结构

| 项目 | 值 |
|---|---|
| 命令族 | `b43d01000300` |
| 应用前缀 | `000000` |
| 记录长度 | 143 字节 |
| 应用总长度 | 146 字节 |
| 客户端 command serial | 86 |
| 房主 command serial | 38581 |
| 请求与房主回包 | 除 serial 两个有效字节外逐字节相同 |

客户端 sender offset 从 2080186 增至 2080332，增量恰好为 146 字节。请求发出后
261.564 ms 收到匹配的房主广播。

## 字段

| 标签 | 样本值 | 含义 |
|---|---:|---|
| `822c01001400` | 0 | 当前玩家／命令上下文 |
| `634001001400` | 8371 | 恒星基地建设队列 ID |
| `1c3a01000f00` | `starbase_level_starport` | 目标等级动态字符串 |
| `0c3a01001400` | 104 | 目标 `starbase_mgr.starbases` 条目 |

`0c3a` 的对象类型已经由舰队目的地与船坞建造样本独立映射；它不是银河系对象 ID，也
不是恒星舰队实体。`b43d` 是共享的建设命令族，不能只凭命令族判断这是恒星基地升级，
必须同时核对嵌套标记、等级字符串和目标对象。

## 普通等级链

Stellaris 4.4.6 的 `common/starbase_levels/00_starbase_levels.txt` 通过 `next_level`
声明普通等级链：

```text
starbase_level_outpost      -> starbase_level_starport
starbase_level_starport     -> starbase_level_starhold
starbase_level_starhold     -> starbase_level_starfortress
starbase_level_starfortress -> starbase_level_citadel
```

协议记录显式携带目标等级字符串，因此可以实现参数化构造器：从最新存档读取当前
`level`，从匹配版本的规则或 Content Pack 取得合法 `next_level`，编码进 `1c3a`，并按
字符串长度重算 framing。不同等级名称长度不同，禁止使用固定 143 字节模板或在字符串
内部补零。

只有 `outpost -> starport` 已完成本版本真实联机验证。其余普通等级是有原版规则支撑的
结构推导，可以作为实验能力进入兼容性验收，但在取得更高等级实机报告前不得标为逐级
实测。特殊、事件或 Mod 恒星基地必须由各自规则／Content Pack 提供升级链，不能沿用
普通等级列表。

## 执行约束

确定性规则层必须校验所有权、当前等级、唯一下一等级、科技、资源、容量、队列状态和
重复待确认动作。房主广播匹配键至少包括建设队列、恒星基地条目和目标等级。升级完成
后的新存档才是最终状态事实。
