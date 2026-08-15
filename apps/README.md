# Applications And Operator Surfaces

`apps/` 保存可直接启动或打包的进程入口，不放领域核心逻辑。

- `control_center/`：Agent 控制台、网页前端和 Windows 启动器。
- `host_bridge/`：房主侧存档上传与入站命令改写桥。
- `game_overlay/`：房主侧透明会话叠加层，只消费脱敏后的可见事件流。

两者可以部署在不同电脑上，通过明确协议通信。
