# Packet Rewrite

本目录保存 Stellaris 协作模式命令的已验证解析、精确构造与受约束改写实现。

- `iag_packet_interceptor.py`：WinDivert 捕获、方向过滤、日志与一次性拦截框架。
- `iag_stream_command_injector.py`：可靠流应用记录扫描、动态长度前缀和插入基础。
- `iag_same_family_construction_rewriter.py`：同命令族建设替换。
- `iag_command_replacement_injector.py`：已知命令记录替换。
- `iag_building_to_zone_replacer.py`：建筑载体到区域/区划命令转换。
- `iag_building_upgrade_rewriter.py`：建筑升级命令改写。
- `iag_building_replacement_rewriter.py`：已有建筑替换命令改写。
- `ship_commands.py`：`fb2d` 舰船设计和直接船坞记录的解析与精确构造；直接船坞记录不作为公开定量增援接口。
- `fleet_reinforcement_commands.py`：Fleet Manager 目标编制增减、模板创建和单条选中舰队增援请求的解析与精确构造；未解析的 `f23b` 全帝国路径不对 Agent 暴露。
- `building_mutation_commands.py`：建筑升级与槽位级建筑替换记录的解析与精确构造。
- `fleet_operation_commands.py`：舰队攻击、返港维修、指定船坞升级、科研船/工程船自动化和工程船建造恒星基地记录的解析与精确构造。
- `expansion_commands.py`：两类殖民、恒星基地升级、模块和建筑设置记录的解析与精确构造。

`carrier_click` 链依赖“非房主协作端发出合法载体、房主入站前改写、房主成为最终权威”。
`session_proxy` 链在已锁定的双向可靠流中插入完整的已验证记录，并持续维护 offset、ACK
和 actor serial 映射。未知包、未验证字段或无法确认方向时必须原样放行或失败，不得尝试
猜测性注入。
