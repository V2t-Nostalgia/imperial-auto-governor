# IAG 一次性联机命令替换器

该工具用于私有 `OLTEST` 合作房间中的受控实验。它拦截合作玩家发往房主的一条合法
TEST1 建造命令，将：

```text
building_research_lab_1
```

等长替换为：

```text
building_bureaucratic_1
```

两个 ID 都是 23 字节。工具不会生成会话序号、ACK、tick、行星 ID 或槽位信息，而是
保留游戏刚刚生成的真实字段。命中一次后自动退出。

## 安全边界

- 仅匹配当前已观察到的精确 UDP 四元组。
- 仅匹配 TEST1 的完整 85 字节研究实验所建造记录。
- 也接受已在实时流中确认的 83 字节包尾片段；该片段包含完整建筑 ID、
  TEST1 对象 ID 和槽位字段，缺失的两个尾随零字节位于下一 UDP 片段。
- 记录出现零次或多次时不修改。
- 替换后包长必须保持不变。
- 校验失败时原样转发。
- 一次命中后自动关闭。
- 不处理撤销命令。

## 离线验证

```powershell
cd tools\packet_interceptor
python -m unittest -v
```

## 观察模式

观察模式只确认 WinDivert 能在当前网络层看见目标命令，不修改数据包：

```powershell
.\Start-IAGPacketInterceptor.ps1 -ObserveOnly
```

窗口显示 `READY` 后，由合作玩家在 TEST1 排队研究实验所。精确命中后工具自动退出，
结果写入 `logs\interceptor_*.jsonl`。

如果精确四元组或稳定记录没有命中，先使用被动发现模式：

```powershell
.\Start-IAGPacketInterceptor.ps1 -Discover
```

该模式使用 WinDivert `SNIFF` 标志，只接收数据包副本，不阻断、修改或重新注入数据包。
它仅在 UDP payload 出现 `building_research_lab_1` 时记录实际地址、端口和邻近字节。

发现其他对象 ID：

```powershell
.\Start-IAGPacketInterceptor.ps1 -Discover `
  -DiscoverIDs building_foundry_1
```

一次收集多个对象：

```powershell
.\Start-IAGPacketInterceptor.ps1 -Discover `
  -DiscoverIDs building_research_lab_1,building_foundry_1 `
  -DiscoverCount 2
```

## 一次性替换模式

```powershell
.\Start-IAGPacketInterceptor.ps1
```

窗口显示 `ARMED` 后，由合作玩家在 TEST1 排队研究实验所。命中后房主应看到行政办公楼
进入建设队列。测试结束后同时检查双方状态和 JSONL 日志。

指定其他源建筑与目标建筑：

```powershell
.\Start-IAGPacketInterceptor.ps1 `
  -SourceID building_foundry_1 `
  -TargetID building_research_lab_1
```

工具会同步改写序列化字符串的 16 位小端长度字段。目标 ID 较长或较短时，UDP payload
长度会相应变化，IP/UDP 长度与校验和由 WinDivert 重新计算。

将地球发出的研究实验所命令重定向到 TEST1：

```powershell
.\Start-IAGPacketInterceptor.ps1 `
  -SourceID building_research_lab_1 `
  -TargetID building_research_lab_1 `
  -SourceEA3F 6 -SourceFC29 3 -SourcePlacement 0 `
  -TargetEA3F 616 -TargetFC29 308 -TargetPlacement 23
```

Zone 类型替换：

```powershell
.\Start-IAGPacketInterceptor.ps1 `
  -RecordKind zone `
  -SourceID zone_foundry `
  -TargetID zone_factory `
  -SourceEA3F 398 -SourceFC29 199 -SourcePlacement 11 `
  -TargetEA3F 398 -TargetFC29 199 -TargetPlacement 11
```

如地址或端口变化，可通过参数覆盖：

```powershell
.\Start-IAGPacketInterceptor.ps1 `
  -RemoteIP <HOST_IP> -RemotePort <HOST_PORT> `
  -LocalIP <AGENT_IP> -LocalPort <AGENT_PORT>
```

手动停止：

```powershell
.\Stop-IAGPacketInterceptor.ps1
```
