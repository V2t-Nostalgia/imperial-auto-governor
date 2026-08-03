<!-- SPDX-FileCopyrightText: 2026 Nostalgia and Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Imperial Auto Governor v0.5.8

v0.5.8 是面向 Steam 公网/中继合作会话的房主执行桥热修复。

## 修复

- 房主桥不再把 Agent 的私网地址误当作 Stellaris 游戏 UDP 的唯一对端。
- 每次执行前重新枚举房主 `stellaris.exe` 拥有的 UDP 端口，同时保留局域网直连路径。
- 公网或 Steam 中继地址变化时，进入 Stellaris 端口的合法载体仍可进入既有的一次性等长改写链。
- 无法发现 Stellaris UDP 端口时在 READY 前关闭失败，不再点击并把原始载体留在川陀。
- READY 与执行遥测新增路由范围、端口清单和载体命中路径，便于区分直连与中继。
- Windows Host Bridge 正式包含 `psutil` 运行依赖；Agent 强制要求 `hostbridge10`，避免新旧组件静默混用。

## 未改变

- 不构造新数据包，不重放旧 payload。
- 仍只处理真实合作端 UI 产生的同命令族载体。
- UDP payload 长度、命令记录长度、命令数量和 serial 的安全约束不变。
- v0.5.7 的建筑替换、紧急停止恢复开关、有限存档 revision 容差和批次串行执行保持不变。

详细故障证据与边界见 `docs/STEAM_RELAY_HOTFIX.md`。
