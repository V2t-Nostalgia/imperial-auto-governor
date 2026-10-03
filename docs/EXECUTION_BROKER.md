# Execution Broker

## 目的

Execution Broker 是 Application 与 Stellaris 副作用实现之间唯一稳定边界。
Application 只提交经过候选校验的语义动作；session proxy、carrier click 和未来
native runtime 分别实现同一个 backend contract。`packet/`、可靠流坐标、actor
serial、wire tag 和 hook 地址都不是公共 API。

## v0.5.10 基线依赖图

```text
economy_governance
  -> iag_supervisor.execute_run()
       -> SessionProxyController              [session_proxy]
       -> host bridge + carrier click          [carrier_click]
  -> SessionProxyController.status()           [状态面板]

fleet_operations
  -> SessionProxyController.arm_and_wait()
  -> SessionProxyController.arm_sequence_and_wait()

research_strategy
  -> build_execution_broker()                  [通用平台组合入口]
       -> ExecutionBroker                      [本轮已迁移]
       -> NativeRuntimeBackend                 [runtime-owned tool catalog]
       -> SessionProxyBackend
            -> SessionProxyController
                 -> existing proxy worker / packet builders
```

控制台的全局执行锁、经济资源预留、存档续接和 Application 自己的候选生成仍保留。
Broker 又提供进程级副作用锁，从而保证不同 Broker 实例也不能同时触碰游戏。

## 已确认的重复与耦合

- `protocol_compatibility.py` 登记动作名、风险、验证状态、必需字段、wire family 和
  离线 fixture。
- `session_proxy.py` 另行定义 `ArmRequest`、transport target、解析和 record 构造。
- Application 曾直接构造上述 transport target，并检查 `outcome == "confirmed"`。
- Application manifest 曾要求 `session_proxy_v1` 或
  `host_inbound_rewrite_v1`，把业务能力绑定到传输方式。
- `iag_supervisor.execute_run()`、各工具自己的审计结果和
  `iag.core.contracts.ExecutionResult` 形成了多种结果形状。

本轮新增的 `action_registry.py` 是 semantic action 的登记来源。
`protocol_compatibility.py` 暂时仍是 session-proxy 验收目录；测试强制它的动作风险与
验证状态和 semantic registry 一致。wire family、fixture 与 operator check 继续只属于
该低层目录，后续不应搬进公共 registry。

## 公共契约

```python
target = ResearchTarget(
    area="physics",
    technology_id="tech_shields_2",
)

prepared = PreparedAction(
    request_id="research_...:1",
    action_intent_id="research_...",
    application_id="research_strategy",
    action_type="start_research",
    action_version=1,
    target=target,
    candidate=CandidateIdentity.for_target("research:physics:...", target),
    source_snapshot=source_snapshot,
    authority=authority,
)

result = broker.execute(prepared)
```

`PreparedAction` 绑定：

- request / intent ID；
- Application 与版本化 semantic action；
- strict、frozen 的 semantic target；
- candidate ID 与 target 指纹；
- campaign、save hash 与 upload revision；
- 玩家产生的 authority context。

Broker 在持有全局副作用锁时依次检查 registry、target 类型、Application 白名单、
authority、risk class、prepare 时冻结的 snapshot freshness、candidate identity 和 backend 支持，再进行 dispatch。返回值复用
`iag.core.contracts.ExecutionResult`；Application 不读取 raw proxy outcome。

## Ordered sequence

`OrderedActionSequence` 保证步骤有序，并在整个调用期间阻止其他 Agent 插入动作。
它不是 transaction：游戏动作通常不可回滚，失败时只返回已尝试的前缀和每一步的机器
证据，不承诺 atomic rollback。

## Backend contract

Backend 实现：

```text
backend_id
capabilities()
supports(ActionSpec)
execute(PreparedAction)
execute_ordered_sequence(OrderedActionSequence)
```

Backend 负责把 semantic target 解析成自己的私有表示，并返回
`BackendExecutionResult`。Broker 再把它标准化成公共 `ExecutionResult`。raw ACK、serial
translation、wire family 或 native callsite 可以留在 evidence 中，但不能成为
Application 分支判断依据。

当前 `SessionProxyBackend` 只启用科研动作。它把 authority 中由存档确认的
country ID 解析为私有 `context_822c`，并原样调用已验证的
`SessionProxyController.arm_*`。registry 中其他动作已经有 semantic target，但在对应
save-backed resolver 迁移前不会被 Broker 接管。

`NativeRuntimeBackend` 不维护另一份动作名称或 dispatch switch。运行中的 DLL/SO 通过
`describe_tools` 声明 action/version、所属 Application、风险、验证等级和严格参数；
Broker 将这份下层能力与 semantic registry、Application 权限及玩家授权求交集。Linux
4.4.6 当前公布 `stop_research.v1`、`move_fleet.v1`、`attack_fleet.v1`；Windows 4.4.6
当前只公布独立实机验证过的 `move_fleet.v1`。未归类的新贡献显式进入 `etc` 暂存域。
runtime 在主线程解析原生对象并走 Stellaris 自己的 command/session pipeline。没有启用
runtime、Build ID 不符、IPC 不存在或动作不受支持时，Broker 保持现有 backend 选择与
旧路径行为。

Application 默认调用不含 transport 名称的 `build_execution_broker()` 组合入口。以后
加入 `NativeRuntimeBackend` 时修改平台组合层即可，不需要修改 research 的业务代码。

## Idempotency

同一进程中：

- 相同 `request_id` 与相同 semantic payload 返回第一次的缓存事实，不再次执行；
- 相同 `request_id` 对应不同 payload 时抛出 `IdempotencyConflict`；
- ordered sequence 的 request ID 与每个 step request ID 都参与冲突检查；
- 失败和拒绝也会缓存，调用者不能用同一 ID 静默重放。

这是第一阶段的进程内保证。生产 native backend 接入前，应把 request fingerprint、最终
结果和明确的“尚未确定是否提交”状态持久化到战役账本，使服务重启后的超时重试也不能
造成重复游戏动作。

## Native runtime 插入点

```text
Application
  -> semantic target / PreparedAction
  -> ExecutionBroker
       -> SessionProxyBackend
       -> CarrierClickBackend       [经济迁移阶段]
       -> NativeRuntimeBackend      [self-described exact-build actions]
```

`NativeRuntimeBackend` 实现同一 contract，不向 Application 暴露 command class、
函数地址、allocator、logic-thread queue 或 opcode。当前实机链路与证据见
`docs/runtime/NATIVE_RESEARCH_HOOK_4_4_6.md` 和
`docs/runtime/NATIVE_FLEET_MOVE_HOOK_4_4_6.md`。

发现与生产必须分离：

```text
runtime/research/probes
  允许记录 stack、callsite、未知 command 和 human_ai 观察结果

runtime backend
  只登记已经确认语义、线程、ownership、serializer 和多人同步行为的 command
```

逆向时优先用现有 move、attack、research、construction、colonization、starbase、ship
construction 和 automation 动作作为锚点。让 vanilla AI / `human_ai` 产生大量真实动作，
按 callsite、stack 与 command type 聚类；若多个动作汇入公共 dispatcher，优先验证并
hook 公共 submit/queue 路径。直接调用业务 `Execute()` 或本地改内存不能成为正式多人
backend。

## 迁移顺序

1. **已完成**：semantic registry、Broker facade、mock/backend contract tests。
2. **已完成**：`research_strategy` 通过 Broker 调用现有 session proxy。
3. **下一步**：逐项给舰队动作补 save-backed semantic resolver，再移除 fleet import
   allowlist。
4. 迁移 economy 的 manifest/run adapter，同时保持 resource reservation、stale-save
   refresh 和 carrier click confirmation 完全不变。
5. 实现真正的 `CarrierClickBackend`；在此之前 `execute_run()` 仍是兼容路径。
6. 当所有 Application 只依赖 Broker 后，把 controller/supervisor 标记为 internal。
7. **已完成原生目录与三条 Linux 链路**：同一 backend 从 runtime manifest 自动发现
   `stop_research.v1`、`move_fleet.v1`、`attack_fleet.v1`；Windows 已接入独立验证的
   `move_fleet.v1`。其它动作逐条验证后由各自 `.cpp` 自注册，不修改 Application 代码。

## 必须保持的行为

- 现有 packet builders、record 长度与字段顺序；
- reliable-stream framing、ACK/response correlation 和 actor serial 维护；
- session proxy 的进房前启动、流锁定与离房后停止规则；
- carrier click 的合法候选、Host Bridge 和存档确认语义；
- prepare 后的 stale-save / candidate revalidation；
- 玩家权限、高风险开关与资源预留；
- 全局副作用串行化；
- provisional 与 packet/save confirmed 的严格区分；
- Save Continuation 每份存档最多一个变更动作的约束。

## Architecture tests

`test_execution_architecture.py`：

- 禁止新增 Application 直接 import controller、supervisor、`packet/` 或 future runtime；
- 用精确 symbol allowlist 冻结尚未迁移的 economy/fleet 债务；
- 要求 manifest 只声明 semantic execution capability。

`test_action_registry.py` 要求 protocol catalog 与 semantic registry 的风险及
session-proxy 专属验证状态不漂移，并单独保留每个 backend 的证据等级；同时要求所有
Application action 都已登记。`test_execution_broker.py` 覆盖权限、
snapshot fail-closed、结果标准化、ordered sequence、全局串行和 request-id 去重。

## 本阶段不修改

- 不移动或重写 `packet/`；
- 不改变 session proxy worker、flow discovery、serial 或回包匹配；
- 不把舰队/经济候选生成放入 Broker；
- 不把 ordered sequence 宣称为 transaction；
- 不为尚未确认的 native command 建立生产 registry 项；
- 不在没有持久化协议前假装跨服务重启已经 exactly-once。
