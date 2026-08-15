# Execution

执行层负责把已验证的动作意图落实到 Stellaris，并返回机器可审计事实。

- `fixed_click.py` 选择 Windows/X11 点击后端并管理校准。
- `windows_fixed_click.py`、`x11_fixed_click.py` 实现平台专属 UI 守卫与点击。
- `port_discovery.py` 从进程和 UDP 连接发现本局对等端。
- `host_bridge_pairing.py`、`host_executor_protocol.py` 定义 Agent 与房主桥协议。
- `iag_supervisor.py` 在 `carrier_click` 与 `session_proxy` 之间选择执行链，并记录机器确认事实。
- `session_proxy.py` 在整局可靠流中插入已验证命令并维护偏移、ACK 和 actor serial。
- `session_proxy_controller.py` 管理代理的进房前启动、动作提交和离房后停止。
- `passive_network_observer.py` 只读观测 Linux 端流量。
- `packet/` 包含已验证的命令解析和改写器。

未来的统一 Execution Broker 应从这里演进。它必须串行化游戏副作用，但不能让主模型成为每个动作的审批瓶颈。`session_proxy` 当前仅支持 Windows，并且必须由管理员进程在合作端加入房间前启动。
