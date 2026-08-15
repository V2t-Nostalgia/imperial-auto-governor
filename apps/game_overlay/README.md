# 游戏内对话叠加层

`IAGOverlay` 是房主侧的独立透明窗口，不会修改 Stellaris 原版 UI。它只显示玩家实际
输入和模型可见回复；工具审计、系统消息与 `reasoning_content` 不会进入叠加层接口。

## 使用

1. 从 Agent 网页下载已配对的 Windows 房主执行桥。
2. 打开房主执行桥并保存连接设置。
3. 点击“启动游戏叠加层”。
4. 默认按 `Ctrl+Shift+Space` 进入输入模式；再次按同一热键锁定并把焦点还给游戏。

锁定时窗口会保持置顶但完全鼠标穿透。输入模式下可拖动标题、拖拽右下角改变大小，
并通过 `CFG` 修改全局热键、透明度、字号和分析文案切换间隔。

输入框使用绿色块状闪烁光标。灰风的流式草稿和写入历史后的最终回复均使用同一套
Fusion Pixel 斜体，不会在流式传输结束时切换字形。

分析占位文字来自房主桥目录下的 `overlay/analysis_phrases_zh.txt`。每行是一条文案；
空行和以 `#` 开头的行会被忽略，修改后在 `CFG` 中保存一次即可重新载入。
提示文案会逐字显示；`CFG` 可分别调整每个字符的间隔和完整文案的停留时间。

界面统一使用 Fusion Pixel Font 的 12px 等宽简体中文字形，避免中英文回退造成视觉
割裂。字体按 SIL Open Font License 1.1 再分发，字体本体与完整上游许可证位于
`resources/fonts/fusion_pixel/`。

## 无 Agent 显示测试

`IAGOverlayDisplayTest.exe` 是单独构建的本地测试程序。它不会连接 Agent，也不读取
Agent 地址、TLS 指纹或访问令牌；输入任意文字后只会在本机模拟分析文案与流式回复。

```powershell
powershell -ExecutionPolicy Bypass -File `
  .\scripts\build\Build-IAGOverlayDisplayTest.ps1 `
  -Python .\.venv\Scripts\python.exe
```

构建结果位于 `build/overlay-display-test/`。该程序不是正式客户端的无认证网络模式。
