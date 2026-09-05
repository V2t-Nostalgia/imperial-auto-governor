# Execution

执行层负责把已验证的动作意图落实到 Stellaris，并返回机器可审计事实。

- `fixed_click.py` 选择 Windows/X11 点击后端并管理校准。
- `windows_fixed_click.py`、`x11_fixed_click.py` 实现平台专属 UI 守卫与点击。
- `port_discovery.py` 从进程和 UDP 连接发现本局对等端。
- `host_bridge_pairing.py`、`host_executor_protocol.py` 定义 Agent 与房主桥协议。
- `iag_supervisor.py` 在 `carrier_click` 与 `session_proxy` 之间选择执行链，并记录机器确认事实。
- `session_proxy.py` 在整局可靠流中插入受约束命令并维护偏移、ACK 和 actor serial。它登记经济建设/变更、舰队移动/攻击、科研、舰船自动化、殖民、恒星基地、舰船设计以及 Fleet Manager 编制/增援等 24 个动作。
- `session_proxy_controller.py` 管理代理的进房前启动、动作提交和离房后停止。
- `protocol_compatibility.py` 是会话代理命令目录、版本验收计划与差异报告的唯一登记层。
- `protocol_compatibility_control.py` 把同一目录接入网页控制端，持久化逐项验收状态，且不调用 LLM。
- `passive_network_observer.py` 只读观测 Linux 端流量。
- `packet/` 包含已验证的命令解析、精确构造和兼容点击模式的改写器。

未来的统一 Execution Broker 应从这里演进。它必须串行化游戏副作用，但不能让主模型成为每个动作的审批瓶颈。`session_proxy` 当前仅支持 Windows，并且必须由管理员进程在合作端加入房间前启动。新增命令族只有在成对样本、离线 fixture 和当前会话实机闭环都通过后，才能从实验状态升级为稳定能力。

游戏版本更新后的完整回归入口是
`scripts/diagnostics/run_protocol_compatibility_suite.py`。它默认只执行仓库测试与离线构包；
显式联机模式会复用现有会话代理，并把房主 ACK、权威回包匹配和玩家观察分阶段记录。
