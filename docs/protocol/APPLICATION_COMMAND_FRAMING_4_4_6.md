# Stellaris 4.4.6 应用命令长度分帧

本文件记录 2026-09-05 通过工程船全自动托管请求及房主权威回包确认的应用命令
分帧规则。它不包含会话地址、端口、存档、完整抓包或个人路径。

## 已确认结构

一条位于可靠字节流边界的 Clausewitz 应用命令由三字节前缀和一条记录组成：

```text
00 00 PP | LL 00 04 00 00 00 ...
```

- `PP` 是声明长度的高 8 位；
- `LL`（记录偏移 0）是声明长度的低 8 位；
- `declared_length = (PP << 8) | LL`；
- `record_length = declared_length + 1`；
- `application_length = 3 + record_length`。

长度小于 256 字节的既有记录使用 `00 00 00`。长度跨过 256 字节后，不能只读取
记录首字节；否则会把长记录截断为低字节所表示的伪长度。

## 实验证据

工程船全自动托管记录包含四个自动化标识符，实际记录长度为 339 字节：

```text
PP = 0x01
LL = 0x52
declared_length = 0x0152 = 338
record_length = 338 + 1 = 339
application_length = 3 + 339 = 342
```

客户端可靠流 sender offset 在该请求后恰好增加 342。房主返回同长度权威记录，除
四字节 command serial 被房主重编号外，其余记录字节完全相同。

## 边界

这三个字节是应用命令记录的 framing 前缀，不是 UDP 包头，也不是 25 字节可靠传输
头的一部分。一条命令可以：

- 独占某个可靠载荷；
- 位于其他应用数据之后；
- 被可靠流分片跨多个 UDP 数据报传输。

因此解析器应先按 sender offset 重组可靠字节流，再识别应用前缀和记录；不能假定每个
UDP 数据报的应用载荷都从命令边界开始。

目前只能确认前缀第三字节 `PP` 的长度高页语义。前两个固定为 `00 00` 的字节应继续
作为已验证 framing 标记保留，不应在没有新对照样本时赋予更具体含义。

## 实现约束

构造器必须依据最终记录长度生成前缀，并核对低长度字节：

```python
declared = len(record) - 1
prefix = b"\x00\x00" + bytes((declared >> 8,))
assert record[0] == declared & 0xFF
```

当前实现入口为
`iag.stellaris.execution.packet.ship_commands.application_prefix_for_record`；新增长命令
应复用该函数并增加跨 255/256 字节边界的回归测试。

