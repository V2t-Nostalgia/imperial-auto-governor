# LLM Construction Carrier / LLM 建设载体

这个 Stellaris mod 现在只做一件事：在游戏内创建一个专用、低干扰的建设命令载体飞地。

它不再承担帝国内政总管、月度扫描、季度报告、殖民地自动化候选或游戏内 LLM 配置。真正的规划与执行链路交给外部工具完成：读取游戏状态，生成建设计划，使用合作玩家客户端发出合法载体点击，再在命令进入房主前改写为真实目标建设。

## 当前功能

- 通过政府法令 `创建 LLM 建设载体飞地` 创建一个名为 `灰风`、与当前首都星系直接相连的专用星系。
- 在该星系内生成两颗 25 格盖亚载体殖民地：`川陀`固定承载建筑选择面板，`端点星`固定承载主区划与区划特化面板。
- 为飞地创建玩家哨站并把殖民地归属当前玩家。
- 给载体星球添加保护修正，避免住房、舒适度、稳定度、犯罪、岗位/建筑/区划维护费干扰。
- 对进入 `灰风` 的非星系拥有者舰队触发反应式防护事件 `第四面墙`，随后让舰队进入 MIA 并在 42 天后返回。
- 如果飞地已存在，再次使用法令会执行维护：恢复两颗载体星球的名称和保护、补建旧档缺失的 `端点星`，并确保灰风与当前首都直接相连。
- 不扫描殖民地，不弹季度报告，不自动建设，不触发 LLM 决策。

## 舰队进入边界

`spawn_system` 使用 `hyperlane = yes`，从当前首都星系创建一条直达灰风的超空间航道；脚本还使用原版 `add_hyperlane` 作为显式兜底，并为旧存档自动补建同一条航道。initializer 不创建虫洞、网关或 L-Gate，灰风仍由玩家哨站控制。

Stellaris 当前 vanilla 脚本里没有确认到“按星系在进入前完全拒绝非我方舰队”的稳定 effect/trigger。原版有 `on_entering_system_fleet`，但那是舰队已经进入系统后的反应式事件，不是进入前门禁。

因此现在采用“首都直连 + 反应式门禁”：我方舰队可以通过首都航道正常往返；任何非星系拥有者控制的舰队进入 `灰风` 后，会看到 `第四面墙` 事件，清空当前命令，执行 `set_mia = mia_return_home`，并通过 `set_mia_return_delay = 42` 设置 42 天后返回。

## 失落帝国安全性

该飞地不调用失落帝国初始化器，不设置 `holy_planet` 修正，也不设置 `holy_world_1` 到 `holy_world_4` 标记。当前脚本还会在生成和维护时显式移除这些标记。

名字像“隐居地”不等于圣地。原版失落帝国惩罚检查的是 `holy_planet` 修正；辅助圣地判定还会看 `holy_world_1` 到 `holy_world_4` carrier flags。

旧存档只有川陀时，重新使用政府法令并选择维护现有飞地，即可在原灰风星系中补建端点星并修复两颗载体星球的角色标记。

## 游戏内使用

1. 进入正常玩家帝国。
2. 打开政府/法令界面。
3. 使用 `创建 LLM 建设载体飞地`。
4. 如果飞地不存在，选择创建；如果飞地已存在，选择维护现有载体飞地。
5. 合作玩家客户端让 `川陀`保持建筑选择面板。
6. 让 `端点星`保持主区划或区划特化选择面板。
7. 外部执行器按动作类型使用对应星球上的固定点击作为合法命令载体。

## 保留的游戏脚本文件

```text
common/edicts/iag_edicts.txt
common/on_actions/iag_on_actions.txt
common/scripted_effects/iag_carrier_effects.txt
common/solar_system_initializers/iag_carrier_initializers.txt
common/static_modifiers/iag_static_modifiers.txt
events/iag_carrier_events.txt
localisation/english/iag_l_english.yml
localisation/simp_chinese/iag_l_simp_chinese.yml
```

## 不再包含

- 殖民地内政月度扫描。
- 季度报告事件。
- 总管模式配置面板。
- 行星托管策略决议。
- 原生殖民地自动化类别。
- 游戏内 LLM 建议弹窗或 runtime bridge 事件。

## 日志

创建飞地、维护飞地和第四面墙防护触发时，`game.log` 中会出现 `[LLM Carrier]` 前缀的调试行。Stellaris 日志目录通常为：

```text
Documents\Paradox Interactive\Stellaris\logs\
```

重点查看：

```text
game.log
error.log
setup.log
```

## 外部代理控制台

外部规划、三类固定坐标校准、Stellaris 端口发现、Windows 房主入站一发式改写、房主权威确认，以及可编辑 Prompt / Base URL / API Key 的本地前端位于：

```text
tools/agent_runtime/
```

中文安装、启动、校准、端口状态与实局测试说明见 [tools/agent_runtime/README.md](tools/agent_runtime/README.md)。Ubuntu 独立发行包的全新环境安装步骤见 [docs/UBUNTU_AGENT_README.md](docs/UBUNTU_AGENT_README.md)；Windows 可以直接运行带 GUI 的 `IAGWindowsAgent.exe`。局域网监听必须使用 HTTPS 和前端鉴权。

代理前端现在支持每局独立会话、十年计划、玩家手动启停的紧急状态、DeepSeek 思考模式模板、模型 `temperature` 与 Raw JSON 参数、最大上下文及按比例自动压缩，以及受约束的 SearXNG、Crawl4AI、MediaWiki 只读检索工具。

## 许可与公开发布

- 程序代码：`GPL-3.0-only`。
- 文档：`CC BY-SA 4.0`。
- 原创者、项目起源与保留声明： [AUTHORS.md](AUTHORS.md)、[ORIGIN.md](ORIGIN.md) 与 [NOTICE](NOTICE)。
- 引用、贡献、DCO 与商标边界： [CITATION.cff](CITATION.cff)、[CONTRIBUTING.md](CONTRIBUTING.md)、[DCO](DCO)、[TRADEMARKS.md](TRADEMARKS.md)。
- 首发签名、GitHub Release、Zenodo DOI 与 Software Heritage 归档流程： [docs/PUBLICATION.md](docs/PUBLICATION.md)。尚未由外部服务实际签发的 DOI 与 SWHID 不会被预填。

v0.5.5 正式发布目录只包含源码包、Windows Agent、Windows Host Bridge、Ubuntu Agent 和顶层 SHA-256 清单。`public_release.zip`、展开目录与旧临时包不属于 GitHub Release 附件。
## 重要边界

这个 mod 本身不负责抓包、改包、点击、发包或调用 API。它只是为外部 LLM 执行链准备一个可重复、可观察、低干扰的游戏内 carrier 目标。

完整工作仓库仍保留抓包和命令改写研究记录；公开源码包只包含可审计、可测试、无运行数据的工具源码：

```text
tools/packet_interceptor/
tools/save_state/
```
