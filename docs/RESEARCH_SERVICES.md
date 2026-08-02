<!-- SPDX-FileCopyrightText: 2026 Nostalgia and Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 联网检索服务部署

灰风提供三个只读研究工具：`search_web` 使用 SearXNG，`fetch_page` 使用 Crawl4AI，
`search_stellaris_wiki` 优先使用 MediaWiki Action API。网页内容始终是不可信参考，不能创建合法建设
候选、提供存档对象 ID、绕过执行校验或直接控制游戏。

## Windows 一键部署

Windows Agent 正式发布包内含 `research_services/`。先安装并启动使用 WSL2 后端的
[Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)，然后在该目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Manage-IAGResearchServices.ps1 -Action Install
```

`Install` 会：

1. 生成 SearXNG、Crawl4AI 和 Redis 所需的随机秘密；
2. 拉取 Compose 文件中固定标签和 SHA-256 摘要的镜像；
3. 只在 `127.0.0.1:8080` 与 `127.0.0.1:11235` 映射服务；
4. 为 Crawl4AI 启用 Bearer 鉴权、禁用 hooks 和私网目标，并使用只读根文件系统；
5. 执行真实搜索、未鉴权拒绝、鉴权 schema、正文抓取和 Wiki 查询测试；
6. 把本地端点和 Token 文件路径写入 Agent 配置，并初次开启联网检索。

运行时秘密位于 `%LOCALAPPDATA%\ImperialAutoGovernor\secrets`，Compose 环境文件位于运行数据目录，
不会写入项目源码或发布包。

## 网页开关

模型设置区的“允许灰风联网检索”是总开关。修改后点击“保存模型配置”：

- 开启：下一轮对话或自主巡检向模型提供三个研究工具；
- 关闭：下一轮完全不提供这些工具，直接调用也会被本地拒绝；
- 开关不会启动或停止 Docker 容器，也不会删除任何秘密。

要释放容器资源，使用 `Stop`；要恢复服务，使用 `Start`。即使容器正在运行，玩家仍可保持网页开关
关闭。

## 管理命令

```powershell
# 启动并复用现有秘密
powershell -ExecutionPolicy Bypass -File .\Manage-IAGResearchServices.ps1 -Action Start

# 执行端到端健康检查
powershell -ExecutionPolicy Bypass -File .\Manage-IAGResearchServices.ps1 -Action Test

# 查看容器状态
powershell -ExecutionPolicy Bypass -File .\Manage-IAGResearchServices.ps1 -Action Status

# 停止但保留容器
powershell -ExecutionPolicy Bypass -File .\Manage-IAGResearchServices.ps1 -Action Stop

# 删除容器；保留 Agent 配置、秘密和缓存卷
powershell -ExecutionPolicy Bypass -File .\Manage-IAGResearchServices.ps1 -Action Remove
```

## 固定服务与安全边界

Compose 当前固定：

- `searxng/searxng:2026.7.26-b060c780d` 及其镜像摘要；
- `unclecode/crawl4ai:0.9.0` 及其镜像摘要。

升级前先阅读 [SearXNG 容器安装文档](https://docs.searxng.org/admin/installation-docker) 与
[Crawl4AI 自托管文档](https://docs.crawl4ai.com/core/self-hosting/)，再更新标签、摘要、接口适配和测试。
不要只改标签后跳过端到端验收。

SearXNG 开启 HTML 与 JSON 输出。Crawl4AI 仅抓取本轮搜索实际返回、位于玩家白名单且解析为公网地址
的 URL；请求体大小、返回大小、正文长度和超时均受限。受限直接 HTML 回退默认关闭。

## Stellaris Wiki

默认 MediaWiki API 为：

```text
https://stellaris.paradoxwikis.com/api.php
```

若 API 返回客户端挑战页、非 JSON 或网络错误，工具会使用 SearXNG 执行
`site:stellaris.paradoxwikis.com` 站内检索。回退结果仍受域名白名单和“只能抓取本轮发现 URL”的限制，
并明确标记 `source: searxng_site_fallback`，不会伪装成 MediaWiki API 结果。

## 跨主机部署

默认方案不需要把 8080 或 11235 暴露给局域网。若自行改为跨主机部署，必须额外配置防火墙、TLS、
服务鉴权和来源限制；不要开放任意 hooks、脚本执行、`file://`、回环或私网抓取能力。该拓扑不属于
当前发布包的自动化测试范围。
