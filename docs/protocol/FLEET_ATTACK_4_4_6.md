# Stellaris 4.4.6 舰队攻击

`6b3301000300` 是已验证的舰队攻击命令族。2026-09-05 的非房主合作端样本零丢包地
捕获了客户端请求与房主权威广播，关闭了此前只存在房主主动样本的证据缺口。

## 完整配对

| 项目 | 客户端请求 | 房主权威广播 |
|---|---:|---:|
| actor | 2 | 2 |
| origin | 0 | 0 |
| serial | 128 | 59052 |
| 来源 fleet | 3 | 3 |
| 目标 fleet | 379 | 379 |
| `4c01` | 1 | 1 |
| 记录长度 | 101 | 101 |

请求与广播清零四字节 command serial 后逐字节相同。单条命令由 3 字节 `000000`
前缀和 101 字节记录组成，共推进客户端可靠流 104 字节；房主 ACK 的增量也精确为 104。

## 字段布局

| 记录偏移 | 标签或值 | 含义 |
|---|---|---|
| 0:2 | `6400` | declared length + 1 = 101 |
| 6:12 | `6b3301000300` | 攻击命令族 |
| 18:24 | `400201000c00` | actor 标签 |
| 24:28 | u32 | actor |
| 45:51 | `130401000e00` | origin 标签 |
| 51 | u8 | origin |
| 52:58 | `db0001001400` | command serial 标签 |
| 58:62 | u32 | command serial |
| 70:76 | `502c01001400` | 来源 fleet 标签 |
| 76:80 | u32 | 来源 fleet 对象 |
| 80:86 | `d62e01001400` | 目标 fleet 标签 |
| 86:90 | u32 | 目标 fleet 对象 |
| 90:96 | `4c0101000e00` | `CFollowFleetCommand.attack` 标签 |
| 96 | `01` | `attack=true` |
| 97:101 | `04000400` | 稳定尾部 |

## 跨场景证据

| 场景 | actor | 来源 | 目标 | 结果 |
|---|---:|---:|---:|---|
| 早期接触攻击科研船 | 1 | 3 | fleet 5 | 房主主动样本 |
| 正式战争攻击空间站 | 1 | 3 | fleet 1168 | 房主主动样本 |
| 攻击敌对野怪舰队 | 2 | 3 | fleet 379 | 非房主完整配对 |
| Linux 原生 UI 攻击可攻击中立目标 | - | 888 | fleet 220 | `session_post` / `execute_sync` 成对样本 |
| Linux production runtime 攻击同一目标 | - | 888 | fleet 220 | 精确订单后置条件 + 探针 3303/3304 |

清零 actor、serial 和目标后，三条记录逐字节相同。外交阶段与目标类别没有为 `6b33`
增加额外字段；是否合法攻击必须由当前游戏状态判断。空间站目标使用其可战斗的 station
fleet 对象，而不是星系、主恒星、station ship 或 `starbase_mgr` 索引。

Linux 4.4.6 的符号与 writer 已将这条 wire family 映射到
`CFollowFleetCommand`：command token 为 `0x336b`，source/target/attack token
依次为 `0x2c50`、`0x2ed6`、`0x014c`，恰好对应 wire 中的小端标签
`6b33`、`502c`、`d62e`、`4c01`。因此 `4c01=1` 现已确认是
`attack=true`，不再是未知字段。它仍是 backend 私有表示，模型只选择语义动作
`attack_fleet`。

Linux focused probe 序号 3299/3300 进一步确认自然 UI 路径构造
`CFollowFleetCommand(source=888, target=220, attack=true,
cancelled=false, queue=false, queue_to_front=false)`。提交和同步执行阶段的六个业务字节
保持一致，玩家界面也接受了该攻击命令。此前友方跟随样本使用同一 native class 且
`attack=false`，因此攻击不是另一条隐藏命令族。

production runtime 请求 `iag-live-attack-20260924-01` 随后通过同一个
`PostCommandToSession` 入口提交了 `attack_fleet(888, 220)`。运行时仅在
`CFleet::GetExecutingOrder()` 返回目标为 220、`attack=true` 且
`cancelled=false` 的 `CFollowFleetOrder` 后返回 `confirmed`。探针序号
3303/3304 分别记录提交与同步执行；两阶段业务字段一致，执行对象由游戏分配 serial
4044。这证明 native backend 没有直接调用业务 `Execute()` 或旁路修改舰队状态。

同一 native command 的可选 `queue`、`queue_to_front` 与 `cancelled` token
分别为 `0x4063`、`0x35de` 与 `0x2eb8`（wire 标签 `6340`、`de35`、
`b82e`）。现有三条攻击样本均为 false，writer 因而省略这些默认字段。

## 自动化约束

参数化构造器应只接收确定性层已经解析并验证的来源和目标对象：

1. 从同一份最新同步存档读取 actor、玩家舰队与目标 fleet；
2. 验证来源舰队归属、存活、可用、非 MIA，以及玩家逐舰队授权；
3. 验证目标存在、`valid_for_combat` 和相对当前玩家的敌对／可攻击关系；
4. 写入当前可靠流 serial、`502c` 来源、`d62e` 目标，并保持 `4c01=1`；
5. 以房主权威广播或后续同步存档确认成立，并对可靠流重传去重；
6. 版本、命令长度或固定标签不匹配时失败关闭。

