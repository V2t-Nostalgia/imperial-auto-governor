# 灰风显示层测试程序

`IAGOverlayDisplayTest.exe` 是完全本地运行的显示测试程序。

- 不连接 Agent；
- 不读取 Agent 地址、TLS 指纹或访问令牌；
- 不会向游戏发送任何指令；
- 输入任意文字后，会在本机模拟“正在分析”和流式回复；
- 支持与正式版相同的拖动、缩放、透明度和全局快捷键设置。

默认使用 `Ctrl+Shift+Space` 进入或退出输入状态。设置保存在：

```text
%LOCALAPPDATA%\Imperial Auto Governor\overlay-display-test.json
```

分析文案位于同一目录下的 `overlay\analysis_phrases_zh.txt`，每行一条。

本程序只用于检查显示效果，不是无认证版本的 Agent 客户端。
