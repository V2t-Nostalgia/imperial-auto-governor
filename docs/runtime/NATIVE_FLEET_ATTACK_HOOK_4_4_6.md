# Native Fleet Attack Hook: Stellaris 4.4.6

This note records the production-runtime evidence for semantic action
`attack_fleet.v1`. It applies only to Stellaris `4.4.6 (fdde)` with ELF build
ID `c6969e60fd81d738948222a94c0b5a0841abbffc`.

## Semantic contract

The Broker-facing target is:

```text
fleet_id: non-negative integer
target_fleet_id: non-negative integer
```

The Application does not supply command objects, native pointers, serials,
wire tags or queue flags. The runtime resolves both fleet IDs through the
generation-checked fleet database and rejects the request unless the source
fleet belongs to the authorized country.

## Native construction and submission

The guarded 4.4.6 profile uses these module-relative anchors:

| Symbol / object | Stellaris offset | Purpose |
| --- | ---: | --- |
| `CFollowFleetCommand::CFollowFleetCommand(...)` | `0x1f73000` | construct the native command |
| `CFollowFleetCommand::IsValid(...)` | `0x1f73140` | game-owned legality check before submission |
| `CFleet::GetExecutingOrder()` | `0x25ae690` | exact postcondition lookup |
| `CFollowFleetOrder` vtable | `0x43cfc00` | verify the executing order type |

The constructor is invoked with System V AMD64 C++ calling convention and the
semantic values:

```text
source_fleet, target_fleet,
attack=true, queue=false, queue_to_front=false, cancelled=false
```

Recovered command layout:

| Offset | Field |
| ---: | --- |
| `+0x1c` | source fleet ID |
| `+0x20` | target fleet ID |
| `+0x24` | attack |
| `+0x25` | cancelled |
| `+0x26` | queue |
| `+0x27` | queue-to-front |

The socket thread only parses and queues the semantic request. The shared
`CGameIdler::Idle(true)` trampoline executes it on the game thread. After
native validation, the runtime transfers the normally allocated command to
the shared `PostCommandToSession` submission path. It does not call
`CFollowFleetCommand::Execute()` directly.

## Postcondition

After the native update, the runtime asks the source fleet for its executing
order and requires all of the following:

- order vptr equals the guarded `CFollowFleetOrder` vptr;
- target field at `+0x24` equals `target_fleet_id`;
- attack field at `+0x50` is true;
- cancelled field at `+0x51` is false.

Only then does it return:

```text
native_postcondition_attack_order_matches_target
```

## Live verification

On 2026-09-24, fleet `888` had its earlier orders cancelled first. The runtime
then accepted this correlated request:

```text
IAG1  iag-live-attack-20260924-01  attack_fleet  0  888  220
```

It returned:

```text
confirmed  0  888:220  native_postcondition_attack_order_matches_target
```

The focused command probe independently recorded:

| Sequence | Phase | Command | Business fields |
| ---: | --- | --- | --- |
| 3303 | `session_post` | `CFollowFleetCommand` | source 888, target 220, attack 1, remaining flags 0 |
| 3304 | `execute_sync` | `CFollowFleetCommand` | same fields, game-assigned serial 4044 |

The bytes match the preceding natural UI sample at sequences 3299/3300. This
closes the chain:

```text
Broker semantic action
  -> NativeRuntimeBackend
  -> game-thread action handler
  -> CFollowFleetCommand constructor and IsValid
  -> PostCommandToSession
  -> Stellaris synchronized Execute
  -> CFollowFleetOrder exact-target postcondition
```

## Stability and limits

Every offset is guarded by the exact ELF build ID and instruction-prefix
checks. ASLR is handled by resolving offsets from the loaded Stellaris module
base. A version mismatch fails closed. Fleet pointers are resolved only while
the action runs on the game thread and are not retained across ticks.

This proves the Linux single-player native path for the guarded build and the
common session submission pipeline. It does not by itself promote the legacy
session-proxy backend or prove a new Stellaris build. Multiplayer equivalence
must retain its own authoritative synchronization evidence.
