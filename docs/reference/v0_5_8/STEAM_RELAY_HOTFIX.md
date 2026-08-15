<!-- SPDX-FileCopyrightText: 2026 Nostalgia and Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Steam 公网/中继流房主筛选热修复

## 故障证据

2026-08-03 的同一公网合作会话中，主区划载体和建筑载体各失败一次。两次执行都满足：

- 固定坐标点击前后截图证明合法载体已进入川陀建设队列；
- Windows Host Bridge 已提权并成功打开 WinDivert；
- 旧过滤器只包含 `<AGENT_IP>`，且等待期内存在双向 UDP 流量；
- `carrier_seen`、`rewritten` 和 `authoritative_confirmation` 均为 `false`；
- 游戏保留了川陀上的原始载体建设，证明命令没有经过改写器。

失败点因此位于“载体点击已经发出”与“房主拦截器看到载体”之间，不是候选、坐标、载体名称或等长改写器本身。

## 根因

旧版把 Agent Web/存档上传连接的私网源地址同时当作 Stellaris 游戏 UDP 的固定对端。局域网直连时该假设成立；Steam 公网或中继会话中，进入房主的实际游戏包可以来自 Steam/平台中继地址，因此只按 `<AGENT_IP>` 筛选会漏掉真正的建设命令。

“过滤器看到了很多 UDP 包”只能证明两台机器之间存在其他 UDP 流量，不能证明建设命令也使用同一地址路径。

## v0.5.8 修复

每次一次性执行开始前，Host Bridge 通过 `psutil`：

1. 精确匹配房主本机的 `stellaris.exe` 进程；
2. 枚举该进程当前拥有的 UDP 本地端口；
3. 构造“合作端直连 IP 或 Stellaris 进程端口”的 WinDivert 过滤器；
4. 仅把进入这些端口的包视为入站游戏流，把从这些端口发出的包视为房主出站流；
5. 仍要求载体明文、完整命令记录和全部等长安全约束通过后才改写。

端口在每次执行前重新发现，不跨重连、重启或新房间复用。端口数量设有硬上限；发现异常时会在点击前失败关闭，而不是退化为全机宽泛 UDP 修改器。

READY 与执行遥测新增 `route_scope`、`stellaris_udp_ports` 和 `carrier_route`，用于区分私网直连路径与 Steam/平台中继路径。

## 回归边界

- 测试地址使用 RFC 5737 文档网段，不包含真实环境地址。
- 公网模拟使用不同于 `<AGENT_IP>` 的源地址，只有目标端口属于 Stellaris 时才匹配。
- 无关公网 UDP 不会被分类为游戏流，更不会触发改写。
- 此修复不改变任何建设命令的字段布局或等长规则。
