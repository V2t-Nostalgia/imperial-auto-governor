# 协议兼容性验收套件

`run_protocol_compatibility_suite.py` 用于在 Stellaris 更新后系统检查 IAG 已登记的
会话代理命令。它不会另写一套抓包器：联机阶段复用 `SessionProxyController` 与
`session_proxy`，因此端口发现、Steam 中继识别、actor/serial、可靠流 offset 和 ACK
映射仍只有一个实现。

同一套流程也已接入网页控制端“执行与确认 → 协议兼容性验收”。网页协调器不调用
LLM：一键开始会保存玩家填写的计划、为全部已登记命令运行内置离线 fixture，并在通过
后于进房前启动代理。真实联机动作仍逐项展示、逐项点击和逐项由玩家核对，不会把多条
副作用命令盲目连发。

## 验收范围

命令目录位于 `src/iag/stellaris/execution/protocol_compatibility.py`。当前覆盖：

- 建筑、基础区划和区域特化；
- 建筑升级与替换；
- 跨对象舰队移动与星系内坐标移动；
- 舰队攻击、轨道轰炸、返港维修与前往指定船坞升级；
- 运输舰队登陆敌方殖民地与机器人进攻部队招募；
- 科研船/工程船自动化与工程船建造恒星基地；
- 订购殖民船并殖民，以及现成殖民船发起殖民；
- 恒星基地升级、模块建造/替换和建筑建造/替换；
- 科研开始与停止；
- 船坞直接建造与舰船设计；
- Fleet Manager 模板创建、目标数量增减和单条选中舰队增援。

当前共登记 28 个动作。每次增加新命令时，必须同时增加目录项和离线 fixture；仓库测试
会检查目录与代理动作是否一一对应。套件直接测试协议执行器，不等同于允许模型填写
任意字段；攻击、自动殖民、民用舰船自动化、工程船建站和恒星基地操作现已接入存档
候选与 Application 权限层。现成殖民船 `e62c` 仍仅在本套件中验收；自动化取消 `6836`
因字段语义未定而不在目录中。

## 安全边界

默认行为只有离线构包和仓库测试。`live` 模式必须满足：

1. 当前存档和房间可丢弃；
2. 所有参与者明确授权；
3. Windows 进程以管理员身份运行；
4. 计划中的游戏版本与 `game_root`/Steam 库检测到的安装版本一致；
5. 代理在合作端进房前启动；
6. 测试计划中的每个启用动作单独确认副作用；
7. 出现未知回包、动作不生效、不同步或时间停滞后立即停止后续命令；
8. 代理插入过字节后，必须先退出多人房间再停止代理。

计划模板里的动作全部默认关闭。不要把示例占位 ID 当成当前对局 ID，也不要跨会话
重放旧目标。

网页联机验收期间，控制中心会冻结模型聊天、后台巡检、常规规划/执行、执行设置修改和
手动代理启停，避免另一个任务扰动同一条可靠流。结束验收前必须先退出多人房间；关闭
浏览器弹窗不会停止正在运行的代理。

## 一次版本验收

### 网页控制端

1. 在“执行与确认”里选择并保存“会话代理”，配置房主地址/actor，并暂停自主巡检；
2. 打开“协议兼容性验收”，新建计划；
3. 逐项用当前可丢弃存档的真实 ID 替换示例占位值，勾选要测试的动作与副作用确认；
4. 可先点“离线全量检查”，也可直接点“一键开始验收（进房前）”；
5. 一键开始会先保存计划并重新执行全部内置 fixture，只有通过后才启动代理；
6. 代理就绪后进入测试房间，产生自然双向流量，再点“已进房，确认锁流”；
7. 每次只点一次“执行当前一项”，随后根据游戏内结果选择玩家判定；
8. 全部完成或首次异常后先退出房间，再点“已退出房间，结束验收”；
9. 下载 JSON 或 Markdown 报告，作为下一版本的比较依据。

“一键”只合并计划保存、离线构包和进房前启动，不会跳过逐条副作用确认。网页内置
检查面向冻结版 Agent，覆盖全部发布版命令构造器；源码仓库级 Python 测试仍由下述 CLI
流程或发布验证执行。

### 命令行

先生成计划：

```powershell
python scripts/diagnostics/run_protocol_compatibility_suite.py init-plan `
  --game-version 4.4.6 `
  --game-build <GAME_BUILD> `
  --output runtime/protocol-plan.json
```

从当前测试存档填写每项 `target`。只启用已经准备好前置条件的场景，并把相应的
`acknowledge_side_effects` 改为 `true`。然后执行离线验收：

```powershell
python scripts/diagnostics/run_protocol_compatibility_suite.py check `
  --plan runtime/protocol-plan.json
```

`check` 会先运行协议、执行监督、舰队/科研存档解析和工具状态机相关测试；其中一条
测试会为目录内每个命令构造脱敏 fixture。它不会因为本机未安装透明叠加层等无关可选
GUI 依赖而误判协议失败。随后脚本使用计划中的真实目标再次构包，检查命令族、记录长度
和应用层插入长度。任一失败都不会启动代理。

联机验收必须在管理员 PowerShell 中执行：

```powershell
python scripts/diagnostics/run_protocol_compatibility_suite.py live `
  --plan runtime/protocol-plan.json `
  --config runtime/agent_config.json
```

脚本会按以下顺序运行：

1. 要求输入 `DISPOSABLE`；
2. 在进房前启动 WinDivert 会话代理；
3. 玩家进入房间，自动 actor 模式下先从合作端自然执行一条无害命令；
4. 输入 `START` 后等待唯一双向可靠流；
5. 逐项展示命令、目标和准备条件，由玩家按 Enter 执行或跳过；
6. 每项等待房主 ACK 和精确匹配的权威广播；
7. 玩家核对游戏 UI 中的实际结果；
8. 每一步立即更新报告；
9. 全部完成或首次异常后，玩家先退出房间，再输入 `ROOM EXITED` 停止代理。

脚本被中断且代理已经插入过命令时，不会强制关闭代理。退出房间后可执行：

```powershell
python scripts/diagnostics/run_protocol_compatibility_suite.py stop `
  --config runtime/agent_config.json `
  --room-exited
```

## 报告与基线

联机报告默认写入：

```text
<runtime_root>/artifacts/protocol_compatibility/<run_id>/
```

其中包括：

- `report.json`：机器可读结果；
- `report.md`：人工审阅表；
- `plan.snapshot.json`：本次计划快照，不含 Agent 配置和 API Key；
- `tests.log`：仅 CLI 源码验收流程生成的仓库测试输出；网页端离线检查不运行仓库测试。

报告还会保存脱敏的游戏安装身份：版本、Mod 兼容版本、发行平台和
`launcher-settings.json` 的 SHA-256，但不会保存游戏安装绝对路径。

报告不会写入实际 IP 或 UDP 端口。每项命令会区分以下失败阶段：

| 阶段 | 含义 |
|---|---|
| `build_failed` | 当前代码无法按结构化目标构包 |
| `wire_family_mismatch` | 构造结果与登记命令族不一致 |
| `proxy_preflight_failed` | 未锁定流或仍有旧请求待处理 |
| `host_ack_or_authoritative_response_missing` | 房主既未确认插入字节，也未出现匹配广播 |
| `authoritative_response_not_matched` | 房主已 ACK，但现有回包解析器没有匹配到权威命令 |
| `network_confirmed` | 精确目标回包已匹配并重标记 |
| `desync_or_stalled_clock` | 玩家观察到不同步或时间停滞 |

后一种“房主 ACK 但回包不匹配”尤其适合定位版本更新造成的字段或记录结构变化。
使用上一版本已通过的报告作为基线：

```powershell
python scripts/diagnostics/run_protocol_compatibility_suite.py check `
  --plan runtime/protocol-plan.json `
  --baseline runtime/known-good/report.json
```

只有目标指纹相同的场景才比较完整记录长度与 SHA-256；不同目标仍会比较命令族、网络
阶段和玩家观察，避免把正常的对象 ID 变化误报成协议变化。

## 增加新命令族

新增命令的最小完成定义是：

1. 先取得当前版本的成对请求/权威广播样本；
2. 实现受约束解析、构造和精确目标回包匹配；
3. 把动作加入 `COMMAND_SPECS`；
4. 在 `protocol_compatibility.py` 的 `OFFLINE_FIXTURE_TARGETS` 增加脱敏 fixture；
5. 更新协议证据文档和准备条件；
6. 在可丢弃房间中只启用该动作完成一次联机闭环；
7. 通过后保存报告，作为后续版本的已知良好基线。

套件证明的是“当前构造器、传输链、权威回包和玩家观察在本次目标上闭环”，不替代新
存档对最终对象 ID、舰船完工或长期游戏状态的确认。
