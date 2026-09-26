# Native Research Hook: Stellaris 4.4.6

## Scope and identity

This document records the first production-shaped, live-verified native
execution chain. It supports only semantic action `stop_research.v1`.

| Item | Verified value |
| --- | --- |
| Game | Stellaris `Pegasus v4.4.6 (fdde)` |
| Platform | Linux x86-64, System V AMD64 ABI |
| ELF Build ID | `c6969e60fd81d738948222a94c0b5a0841abbffc` |
| Executable SHA-256 | `293946bce2ef423476fd725c5433bd54cfa860fc360c3ddaca85b2f5066dca64` |
| Verification date | 2026-09-24 |

The ELF is `ET_EXEC` and was loaded at base zero in the observed process. The
runtime still resolves every address as `main-module base + offset`, requires
the exact Build ID, and checks multiple instruction prefixes before installing
the hook. An address from another build is never accepted on name alone.

## Verified chain

```text
research_strategy
  -> ExecutionBroker.execute(PreparedAction[stop_research.v1])
  -> NativeRuntimeBackend
  -> private Unix socket (semantic technology key only)
  -> CGameIdler::Idle(true) main-thread hook
  -> CGameState::GetLocalObserved()
  -> CTechnologyDatabase::AccessTechnology("tech_shields_2")
  -> CTechnologyStatus::IsResearching(technology) == true
  -> CCancelResearchTechnologyCommand constructor + IsValid
  -> PostCommandToSession(command, false)
  -> CSession::Post(command)
       assigns command serial and player/session fields
  -> CSession::Update()
  -> CSession::ProcessSynchronousCommands()
       -> multiplayer/network path when applicable:
          CNetworkServer::DispatchOrder(command)
          -> CCancelResearchTechnologyCommand::WriteCommandMembers(writer)
  -> CCancelResearchTechnologyCommand::Execute()
  -> CTechnologyStatus::CancelTechnologyResearch(technology)
  -> CTechnologyStatus::IsResearching(technology) == false
  -> NativeRuntimeBackend evidence
  -> ExecutionResult(status="confirmed_by_packet")
```

The runtime never calls `Execute()` or
`CancelTechnologyResearch()` directly. The command enters Stellaris through
the same `PostCommandToSession` and session queue used by the UI.
In single-player captures `CNetworkServer::DispatchOrder` can remain quiet;
the session still owns queuing and execution. Network dispatch is therefore a
conditional downstream branch, not part of the public native-runtime contract.

## Anchors and signatures

All values below are module offsets for the exact Build ID above.

| Offset | Symbol / purpose | Stable evidence |
| --- | --- | --- |
| `0x17bd4a0` | `CGameIdler::Idle(bool)` | Main-thread task pump; 17-byte prologue `55 41 57 41 56 41 55 41 54 53 48 81 ec 88 01 00 00` |
| `0x17c2290` | `PostCommandToSession(CCommand*, bool)` | Common native submit; prefix `55 41 57 41 56 53 50 89 f5 49 89 ff` |
| `0x38250d0` | `CSession::Post(CCommand*)` | Assigns transport identity and takes ownership |
| `0x3825160` | `CSession::Update()` | Calls synchronous command processing |
| `0x3825b80` | `CSession::ProcessSynchronousCommands()` | Validates, dispatches and executes queued commands |
| `0x38c8c80` | `CNetworkServer::DispatchOrder(CCommand*)` | Serializer/network-server boundary |
| `0x1f34680` | cancel-research constructor | Prefix `b8 ff ff ff ff 48 89 47 08 c7 47 10` |
| `0x1f34790` | cancel-research `IsValid(CString*) const` | Pre-submit legality check only |
| `0x1f34700` | cancel-research `WriteCommandMembers(CWriter&)` | Native command serializer |
| `0x1f346b0` | cancel-research `Execute()` | Resolves country and tail-calls the technology subsystem |
| `0x1d3e810` | `CTechnologyStatus::CancelTechnologyResearch(...)` | Business mutation reached only from native `Execute()` |
| `0x1d3adb0` | `CTechnologyStatus::IsResearching(...) const` | Exact pre/postcondition; prefix `48 89 f8 8b 4f 5c 85 c9 7e 16 48 8b` |
| `0x1d284b0` | `CTechnologyDatabase::AccessTechnology(CString const&)` | Resolves semantic technology key |
| `0x180be30` | `CGameState::GetLocalObserved()` | Reacquires current authority object on every request |

`CGameIdler::Idle(true)` is hooked instead of the business function. Its first
17 bytes end on an instruction boundary and contain no PC-relative operands,
so the runtime can copy them into a trampoline without relocation. Pending
work is submitted before the original idle/update call and verified after it
returns. Socket threads never call game functions.

## Parameters and object layout

System V AMD64 observations:

- cancel constructor: `rdi = this`, `esi = TPdxRef<CCountry>` value,
  `rdx = CTechnology const*`;
- `PostCommandToSession`: `rdi = CCommand*`, `sil = force`;
- `CTechnologyStatus::IsResearching`: `rdi = CTechnologyStatus const*`,
  `rsi = CTechnology const*`;
- `CGameIdler::Idle`: `rdi = CGameIdler*`, `sil = run_game_update`.

The cancel command is `0x28` bytes:

| Offset | Meaning |
| --- | --- |
| `+0x00` | vtable, observed `0x42ecb28` |
| `+0x08` | command serial, constructed as `0xffffffff` |
| `+0x12` | player/session field, constructed as `0xffff` |
| `+0x1c` | `TPdxRef<CCountry>` value |
| `+0x20` | `CTechnology const*` |

The local country ID is at `CCountry + 0x20`; its `CTechnologyStatus` subobject
is at `CCountry + 0x1830`. Serial and player/session identity are deliberately
not supplied by IAG. `CSession::Post` fills them from current session state.

The runtime allocates the validated `0x28`-byte command with the process C++
allocator. Before submit, the runtime owns it. After
`PostCommandToSession`, ownership is considered transferred and the runtime
never reads or frees the command. Stellaris destroys rejected or completed
submitted commands through their virtual lifecycle.

## Confirmation correction

`CCancelResearchTechnologyCommand::IsValid()` remains true after a successful
cancel. Static disassembly shows that it checks technology/country legality;
it does not ask whether that technology is currently active. It must not be
used as a postcondition.

The authoritative live predicate is
`CTechnologyStatus::IsResearching(technology)`. The experiment observed:

```text
before Broker cancellation: IsResearching(tech_shields_2) = 1
after Broker cancellation:  IsResearching(tech_shields_2) = 0
```

This correction is why the production runtime uses both checks for different
purposes: native `IsValid` for legality before submit, and `IsResearching` for
semantic pre/postconditions.

## Live evidence

Two visible runs were completed after the corrected predicate was installed.
The final player-observed run used request ID
`native-stop-research-live-20260924-4` and returned:

```text
backend_id     = native_runtime
status         = confirmed_by_packet
action         = stop_research
country_id     = 0
technology_id  = tech_shields_2 (Simplified Chinese: 改良偏射盾)
runtime detail = native_postcondition_not_researching
```

The native runtime log contained correlated `posted` and
`confirmed postcondition` records. An independent debugger read after the
Broker result returned `IsResearching = 0`. During setup, a normal native
research command was allowed to reach `CResearchTechnologyCommand::Execute`,
then the state was held at `IsResearching = 1` until the player confirmed the
UI selection before the final cancellation.

Earlier UI and debugger traces independently established the queue stack:

```text
CCancelResearchTechnologyCommand::WriteCommandMembers
CCommand::WriteMembers
CPersistent::Write
CNetworkServer::DispatchOrder
CSession::ProcessSynchronousCommands
CSession::Update
CGameApplication::Update
CGameIdler::Idle
```

and the execution stack:

```text
CCancelResearchTechnologyCommand::Execute
CSession::ProcessSynchronousCommands
CSession::Update
CGameApplication::Update
CGameIdler::Idle
```

## Confidence and remaining work

### A. Confirmed statically and dynamically

- function identities, offsets, calling convention and command layout above;
- main-thread construction/submission requirement;
- serial/player fields are session-populated;
- submission ownership transfer;
- serializer and `Execute` occur downstream of the native session queue;
- Broker-to-runtime `stop_research.v1` state change and exact postcondition;
- exact build/signature fail-closed behavior.

### B. High confidence, further multiplayer validation required

- `CNetworkServer::DispatchOrder` is the multiplayer serialization boundary;
- host and client share the command class and submit path;
- a client-submitted native command follows the same authoritative host
  processing as its UI equivalent.

Validate B in a disposable two-machine session by submitting one native
cancel from the non-host, breaking on `DispatchOrder` on both peers, recording
serial/player fields, and confirming the host save plus both peer UIs converge
without OOS.

### C. Not claimed

- compatibility with Stellaris 4.5 or any other Build ID;
- safe hot unload of the injected library;
- restart-persistent request idempotency;
- a generic allocator/factory rule for every other command type;
- production support for any other native semantic action.

## `human_ai` discovery path

Vanilla logs do not record native command names or dispatcher calls; in the
observed run `ai.log` was empty. The ELF nevertheless exposes 622 command
RTTI/vtable symbol entries, so `human_ai` can drive an automated discovery
probe:

1. hook `CSession::Post` and `CNetworkServer::DispatchOrder` in a research-only
   library;
2. map command vtable pointers to demangled RTTI names;
3. record thread, callsite/stack fingerprint, serial/player fields and bounded
   serializer evidence;
4. correlate clusters with periodic save-state deltas and visible AI orders;
5. statically recover constructor fields for high-frequency clusters;
6. promote only manually confirmed semantics into the Action Registry.

This can enumerate and cluster commands without manual button-by-button
capture. It cannot safely infer every field's meaning from a class name alone.
The discovery probe must remain separate from the production runtime, and an
unknown command must never become executable merely because it was observed.
