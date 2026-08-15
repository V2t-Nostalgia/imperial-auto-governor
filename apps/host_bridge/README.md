# Windows Host Bridge

Host Bridge 运行在 Stellaris 房主 Windows 电脑上，负责上传最新存档、发现房主进程与本局对等端、拦截协作端进入房主的载体命令并回报执行证据。

- `iag_save_uploader.py`：存档发现、稳定化、上传和控制 API 客户端。
- `iag_host_interceptor.py`：房主入站 WinDivert 拦截与改写。
- `iag_save_uploader_gui.py`：玩家可见 GUI、配对和运行状态。
- `IAGSaveUploaderGUI.spec`：独立 Windows Host Bridge 构建描述。

完整发布构建还会在桥包的 `overlay/` 目录内附带 `IAGOverlay.exe`。GUI 的“启动游戏
叠加层”按钮负责启动它；关闭 Host Bridge 时也会终止本次叠加层进程。配对包分别注入
存档/执行令牌与叠加层只读会话令牌，二者不能互相替代。

桥接器不保存模型密钥，也不负责战略决策。
