<!-- SPDX-FileCopyrightText: 2026 Imperial Auto Governor contributors -->
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 联网检索服务部署

灰风运行时提供三个只读工具：`search_web` 使用 SearXNG，`fetch_page` 优先使用 Crawl4AI，`search_stellaris_wiki` 直接使用 MediaWiki Action API。网页始终是不可信参考，不能创建合法建设候选或绕过执行校验。

## SearXNG

按 [SearXNG 官方容器安装文档](https://docs.searxng.org/admin/installation-docker) 获取当前 Compose 模板，不在本项目中固定复制一份会过期的上游模板。官方流程会创建 `core-config/settings.yml`；至少确认搜索输出允许 JSON：

```yaml
use_default_settings: true

search:
  formats:
    - html
    - json
```

通过 `SEARXNG_SECRET` 提供随机密钥，不要把密钥提交到仓库。仅供本机代理使用时把端口限制到 loopback，并在灰风前端把 SearXNG URL 设为 `http://127.0.0.1:8080`。可用下面的请求验证：

```bash
curl --fail 'http://127.0.0.1:8080/search?q=Stellaris&format=json'
```

## Crawl4AI

按 [Crawl4AI 官方自托管文档](https://docs.crawl4ai.com/core/self-hosting/) 部署当前稳定容器。新版本默认鉴权并收紧了请求边界；先用其 `/health`、`/schema` 和 `/playground` 核对当前镜像的接口，再把服务 URL 与 Bearer Token 写入灰风前端。Token 只写入运行数据目录的 `secrets`，不进入配置导出或日志。

灰风当前使用 `POST /crawl` 并发送单个已由本轮搜索发现的 URL。若上游版本变更了该结构，应保持 `fetch_page` 关闭，或临时启用前端中的“受限直接抓取回退”，不要为兼容而开放任意 URL、内网地址、`file://` 或远程脚本执行。

## MediaWiki

Stellaris Wiki 搜索默认连接：

```text
https://stellaris.paradoxwikis.com/api.php
```

它不依赖 SearXNG 或 Crawl4AI。网络不可用、Wiki 拒绝请求或返回字段变化时，工具应报告失败，模型不得用记忆补造具体数值。

## 暴露边界

SearXNG 与 Crawl4AI 不需要对局域网公开；让它们只监听运行代理的同一台电脑最简单。若必须跨主机部署，应使用防火墙、TLS 和服务自身鉴权，并只允许代理端 IP。不要把 Crawl4AI 的执行脚本、任意 hook、文件 URL 或私网抓取能力开放给模型。
