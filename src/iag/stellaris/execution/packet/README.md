# Packet Rewrite

本目录保存 Stellaris 协作模式建设命令的已验证解析与受约束改写实现。

- `iag_packet_interceptor.py`：WinDivert 捕获、方向过滤、日志与一次性拦截框架。
- `iag_stream_command_injector.py`：流内载体命令识别与等长替换基础。
- `iag_same_family_construction_rewriter.py`：同命令族建设替换。
- `iag_command_replacement_injector.py`：已知命令记录替换。
- `iag_building_to_zone_replacer.py`：建筑载体到区域/区划命令转换。
- `iag_building_upgrade_rewriter.py`：建筑升级命令改写。
- `iag_building_replacement_rewriter.py`：已有建筑替换命令改写。

生产链依赖“非房主协作端发出合法载体、房主入站前改写、房主成为最终权威”。未知包、长度变化或无法确认方向时必须放行或失败，不得尝试猜测性注入。
