# Native Fleet Move Hook: Stellaris 4.4.6

## Scope and identity

This document records the first live-verified native fleet action exposed by
the production runtime: semantic action `move_fleet.v1`.

The production implementation below is the Linux profile. A separately
validated Windows 4.4.6 profile is recorded later in this document because the
semantic command pipeline is shared while the ABI, object layout and offsets
are not.

| Item | Verified value |
| --- | --- |
| Game | Stellaris `Pegasus v4.4.6 (fdde)` |
| Platform | Linux x86-64, System V AMD64 ABI |
| ELF Build ID | `c6969e60fd81d738948222a94c0b5a0841abbffc` |
| Verification date | 2026-09-24 |
| Live request | country `0`, fleet `888`, system `47` |

Every address below is a main-module offset for that exact Build ID. The
runtime rejects another build or a mismatched instruction prefix before it
installs its main-thread hook.

## Verified chain

```text
MoveFleetTarget(fleet_id, destination_system_id)
  -> ExecutionBroker / NativeRuntimeBackend contract
  -> private Unix socket (semantic IDs only)
  -> CGameIdler::Idle(true) main-thread hook
  -> reacquire local observed country
  -> resolve TPdxRef<CFleet> and TPdxRef<CGalacticObject>
  -> verify fleet controller == local country
  -> destination.CalcFTLPointWith(source system)
  -> CFleetFlyToCoordinatesCommand(..., false, false)
  -> CFleetFlyToCoordinatesCommand::IsValid(nullptr)
  -> PostCommandToSession(command, false)
  -> CSession::Post assigns serial/session fields
  -> CSession::ProcessSynchronousCommands
  -> CFleetFlyToCoordinatesCommand::Execute
  -> CMoveToSystemPointFleetOrder is installed on the fleet
  -> runtime observes a new player-issued movement order
  -> normalized ExecutionResult
```

The runtime never calls `Execute()`, `CFleet::ClearOrders()` or
`CFleet::AddOrder()` directly. It allocates the same native command type used
by game callers and transfers ownership through `PostCommandToSession`.

## Anchors and calling convention

| Offset | Symbol / purpose |
| --- | --- |
| `0x17bd4a0` | `CGameIdler::Idle(bool)` main-thread marshal point |
| `0x17c2290` | `PostCommandToSession(CCommand*, bool)` common submit |
| `0x1f64c30` | `CFleetFlyToCoordinatesCommand` constructor |
| `0x1f64c80` | fleet-move command `Execute()` |
| `0x1f64e60` | fleet-move command `WriteCommandMembers()` |
| `0x1f64f50` | fleet-move command `IsValid(CString*) const` |
| `0x229b7a0` | `CGalacticObject::CalcFTLPointWith(...) const` |
| `0x2595480` | `CFleet::GetControllerRef() const` |
| `0x25bd960` | `CFleet::GetCoordinateOrigin() const` |
| `0x25ae480` | `CFleet::CountOrders() const` |
| `0x25ae8f0` | `CFleet::HasPlayerIssuedMovementOrder() const` |
| `0x548bc10` | `TPdxRef<CFleet>::_pDatabase` |
| `0x548bc18` | null `CFleet` instance |
| `0x548aec0` | `TPdxRef<CGalacticObject>::_pDatabase` |
| `0x548aec8` | null `CGalacticObject` instance |

System V AMD64 parameters are:

- move constructor: `rdi=this`, `esi=fleet ref`, `rdx=coordinate`,
  `cl=queue/append flag`, `r8b=secondary order flag`;
- `CalcFTLPointWith`: `rdi=hidden return buffer`, `rsi=destination system`,
  `rdx=source system`;
- `PostCommandToSession`: `rdi=command`, `sil=force`.

All examined UI/AI callers that create a normal replacing movement order pass
both constructor booleans as `false` and call `IsValid` before enqueueing.

## Object layout and resolution

The fleet-move command is `0x50` bytes:

| Offset | Meaning |
| --- | --- |
| `+0x00..+0x1b` | common `CCommand` header |
| `+0x1c` | `TPdxRef<CFleet>` |
| `+0x20..+0x47` | embedded `CCelestialCoordinate` |
| `+0x48` | first constructor boolean |
| `+0x49` | second constructor boolean |

The runtime resolves IDs through the native TPdx databases: index is
`id & 0x00ffffff`, database count is at `+0x20`, the entries pointer is at
`+0x18`, entries have a `0x10` stride, and the object pointer is at entry
`+0x08`. It then compares the object's complete generation-bearing ID
(`CFleet+0x30`, `CGalacticObject+0x08`) with the requested value. A stale ID,
null object, wrong controller or invalid route fails before allocation/submission.

The destination coordinate is not guessed as `(0, 0)`. The runtime asks the
destination system to calculate its FTL point relative to the fleet's current
system, matching native AI callers and preserving route/pathfinding semantics.

## Live evidence

The live request `iag-live-move-20260924-01` moved fleet `888` from Sol toward
adjacent owned system `47` (Barnard's Star). Evidence was collected from two
independent hooks:

1. runtime precondition: fleet existed, belonged to country `0`, destination
   existed and native `IsValid` returned true;
2. runtime log: order count changed from `0` to `1` and
   `HasPlayerIssuedMovementOrder()` became true;
3. command probe `session_post`: type was exactly
   `CFleetFlyToCoordinatesCommand`, serial was `0xffffffff` before session
   ownership;
4. command probe `execute_sync`: the same command object reappeared with serial
   `1` on the game thread;
5. Stellaris remained alive and continued updating.

Re-sending the identical request ID returned the cached confirmed response.
The probe's command-record count remained `2 -> 2`, proving the retry did not
post a second movement command.

## Windows 4.4.6 structural match and live evidence

The Windows executable is stripped, so Linux names are semantic anchors rather
than addresses to copy. The matching Windows functions were recovered from the
move command token `0x2c4f`, vtable shape, native UI callsites, fixed-point FTL
coordinate logic and the common submission path.

| Item | Verified value |
| --- | --- |
| Platform | Windows x86-64, Microsoft x64 ABI |
| Steam build | `24109497` |
| PE timestamp | `0x6a4e461d` |
| PE image size | `0x03950000` |
| EXE SHA-256 | `bc451c72d9654c8901f1bb0bee1dd78d76f415465c2fbf746e9f98ade333173a` |
| Verification date | 2026-09-26 |
| Test save | `TestMove`, game date `2200.03.04` |
| Live request | fleet `3`, Sol `11` -> Barnard's Star `448` |

All values below are RVAs for that exact PE identity. ASLR module base is
resolved at runtime and is never persisted as an interface.

| RVA | Purpose / structural anchor |
| --- | --- |
| `0x00337530` | `CGameIdler::Idle(bool)` main-thread marshal point |
| `0x00648970` | `PostCommandToSession(CCommand*, bool)` common submit |
| `0x0097c2f0` | move command token method, `mov eax, 0x2c4f; ret` |
| `0x02544180` | move command vtable |
| `0x00ac15b0` | move command `IsValid` |
| `0x0097c220` | game-allocator clone/copy method |
| `0x008c3b10` | `CGalacticObject::CalcFTLPointWith` coordinate overload |
| `0x024d6c10` | `CCelestialCoordinate` vtable |
| `0x03285688` / `0x03286190` | fleet database / null fleet globals |
| `0x03287348` / `0x03283fc8` | galactic-object database / null object globals |

The live Windows research chain was:

```text
typed move_fleet(fleet_id=3, destination_system_id=448)
  -> named-pipe research transport
  -> CGameIdler::Idle(true) hook
  -> generation-checked fleet/system database lookup
  -> fleet coordinate provider
  -> destination.CalcFTLPointWith(source coordinate)
  -> exact 0x58-byte move command layout
  -> native IsValid(nullptr)
  -> game-native clone / ownership allocation
  -> PostCommandToSession(command, false)
  -> native game update
  -> executing order type 0x2cde observed on fleet 3
```

The probe response was `confirmed` with detail
`native_postcondition_move_order_2cde_present`. The Stellaris process remained
responsive. Repeating the identical request ID returned the cached response;
the matching `posted` log count remained `1 -> 1`, proving that the retry did
not enqueue a second order.

The Windows command layout differs from Linux and was therefore recovered
independently:

| Offset | Meaning |
| --- | --- |
| `+0x00` | move command vptr |
| `+0x08..+0x1b` | common command/session header |
| `+0x20` | fleet ID |
| `+0x28..+0x4f` | embedded `CCelestialCoordinate` |
| `+0x50` | append/queue flag (`false` for this test) |
| `+0x51` | secondary order flag (`false` for this test) |

This confirms the cross-platform premise: the unstripped Linux build is an
effective semantic map for the stripped Windows build, but Windows still
requires its own structure matching, ABI profile and live validation. The
research harness is intentionally not yet promoted to the production backend;
it lacks the production runtime's full authority check and exact-destination
postcondition, so it must remain under `research/` until those controls are
ported and tested.

## Confidence and remaining validation

### A. Confirmed statically and dynamically

- constructor, size, core fields, parameter registers and normal boolean values;
- ID database lookup and generation check;
- authority check, native legality check and game-thread requirement;
- ownership transfer through `PostCommandToSession`;
- session serial assignment and synchronous execution;
- semantic postcondition and process-local idempotent retry;
- Broker/backend contract and strict semantic target tests.

### B. High confidence, multiplayer test still required

- a non-host native move should pass through the same serializer/host authority
  path as a UI move;
- host and client should converge without IAG handling reliable-stream framing.

Validate this in a disposable two-peer game by issuing one adjacent-system move
from the non-host, recording `session_post`, serializer/network dispatch and
host execution on both peers, then checking both saves and OOS state.

### C. Not claimed

- compatibility with another Stellaris build;
- hot unload of either hook library;
- restart-persistent exactly-once behavior;
- native support for attack, bombardment or any other fleet action merely
  because it shares the common session dispatcher.
