<!-- SPDX-FileCopyrightText: 2026 Nostalgia and Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# IAG 联网检索服务

该目录提供 Windows Agent 的本机 SearXNG 与 Crawl4AI 部署。完整说明见
[`docs/RESEARCH_SERVICES.md`](../../docs/RESEARCH_SERVICES.md)。

管理员 PowerShell 不是必需条件。首次安装：

```powershell
powershell -ExecutionPolicy Bypass -File .\Manage-IAGResearchServices.ps1 -Action Install
```

管理动作：`Start`、`Stop`、`Test`、`Status`、`Remove`。服务仅映射到
`127.0.0.1`；运行时秘密写入 `%LOCALAPPDATA%\ImperialAutoGovernor\secrets`，
不得发布由本机生成的 `compose.env` 或秘密文件。

网页中的“允许灰风联网检索”只决定下一轮是否向模型提供研究工具。关闭开关不会停止容器；
需要释放资源时运行 `-Action Stop`。
