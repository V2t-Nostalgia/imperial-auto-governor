# Fleet Movement Native Map: Stellaris 4.4.6 Linux

This document records the current fleet-movement evidence for Stellaris 4.4.6
(`fdde`), Linux ELF Build ID
`c6969e60fd81d738948222a94c0b5a0841abbffc`. Addresses are module-relative and
must not be reused for another executable without a new profile and anchors.

It is a reverse-engineering record, not a promise that every command below is
already exposed through the production runtime.

## Shared pipeline

```text
UI / AI action
  -> concrete CCommand or queue-wrapper construction
  -> CSession::Post(CCommand*)                         stellaris+0x38250d0
  -> optional multiplayer dispatch
  -> CSession::ProcessSynchronousCommands()
  -> virtual CCommand::IsValid / Execute
  -> CFleet order-list mutation and simulation ticks
```

The production native runtime does not call `Execute` directly. Its verified
submission choke point is `PostCommandToSession` at
`stellaris+0x17c2290`, reached from the single `CGameIdler::Idle` game-thread
marshal. Command ownership transfers at submission.

## Direct movement command

`CFleetFlyToCoordinatesCommand` is the first production-verified movement
family.

| Property | Evidence |
| --- | --- |
| constructor | `stellaris+0x1f64c30` |
| `IsValid` | `stellaris+0x1f64f50` |
| writer | `stellaris+0x1f64e60` |
| object size | `0x50` |
| constructor ABI | SysV: `this, fleet-id/ref, CCelestialCoordinate const*, queue, queue_to_front` |
| fleet ID | `+0x1c`, `uint32` |
| coordinate | embedded at `+0x20`, size `0x28` |
| append flag | `+0x48`, serialized as `queue` |
| prepend flag | `+0x49`, serialized as `queue_to_front` |

The command `_Token` is `0x2c4f`, serialized as wire family `4f2c`. Its writer
uses tokens `0x2c50`, `0x006b`, `0x4063` and `0x35de` for the source fleet,
coordinate container, queue and queue-to-front fields. These are the existing
wire tags `502c`, `6b00`, `6340` and `de35`; the retained paired coordinate
packet therefore describes this same native command rather than a parallel
movement format.

One live runtime request moved fleet `888` to system `47`; the command changed
from unassigned serial `0xffffffff` at construction to serial `1` in native
execution and the fleet order count changed from zero to one. This proves the
construction, main-thread submission and normal session execution chain for a
replacing inter-system move.

`CSendFleetToLocationCommand` (`stellaris+0x1f66790`) has the same recovered
size and field shape. Its `Execute` path constructs a move-to-system-point
order but was not observed in the retained `human_ai` sample, so it is not a
production alternative.

## Coordinate value

`CCelestialCoordinate` is `0x28` bytes:

| Offset | Field |
| --- | --- |
| `+0x00` | vptr |
| `+0x08` | signed 48.15 fixed-point X |
| `+0x10` | signed 48.15 fixed-point Y |
| `+0x18` | signed 48.15 display height |
| `+0x20` | generation-bearing origin `CGalacticObject` ID |
| `+0x25` | randomize-display-height flag |

The research decoder exposes both raw integers and values divided by `32768`.
For normal inter-system movement, the destination is obtained through
`CGalacticObject::CalcFTLPointWith(CGalacticObject const*)` at
`stellaris+0x229b7a0`; the runtime should continue resolving semantic system IDs
and must not accept raw coordinates from an Application unless a separately
verified intra-system action needs them.

## Orbitable target command and `d32c`

`CFleetOrbitPlanetCommand` is the native form of the retained `d32c` movement
family. Despite its historical class name, it accepts either a planet or a
starbase through `CRefObjectOrbitableRef<CFleetOrbitableEnumType>`.

| Property | Evidence |
| --- | --- |
| command token | `0x2cd3` -> wire bytes `d32c` |
| starbase constructor | `stellaris+0x1f65710` |
| generic orbitable constructor | `stellaris+0x1f65750` |
| planet constructor | `stellaris+0x1f657b0` |
| writer | `stellaris+0x1f65a50` |
| fleet | `+0x1c`, token `0x2c50` -> `502c` |
| target reference | `+0x20`, kind at `+0x24` |
| queue flags | `+0x38/+0x39`, tokens `0x4063/0x35de` -> `6340/de35` |
| target container | token `0x3d8b` -> `8b3d` |

Focused player samples used the same source fleet and replacement queue mode.
A starbase target decoded as ID `218`, kind `2`; a planet target decoded as ID
`3`, kind `1`. The retained `d32c` packets use nested tag `0c3a` for starbases
and `132a` for planets. Together, native token/writer evidence, the live target
differential and the paired packet fixtures account for every business field;
another packet capture of ordinary planet/starbase movement would add no new
information.

## Queue wrappers and queue mode

Many UI and AI actions submit a general wrapper containing an owned
`CFleetOrder`:

| Type | Size | Recovered fields |
| --- | --- | --- |
| `CQueueFleetOrderCommand` | `0x30` | mode `+0x1c`, country `+0x20`, fleet `+0x24`, owned order pointer `+0x28` |
| `CQueueFleetsOrderCommand` | `0x48` | mode `+0x1c`, fleet-array object `+0x20`, country `+0x38`, owned order pointer `+0x40` |

`EQueueMode` is recovered from the native enum-to-token path and
`CFleet::AddOrder` behavior:

| Native value | Token | Semantic contract |
| --- | --- | --- |
| `0` | `first` | `prepend` |
| `1` | `last` | `append` |
| `2` | `clear` | `replace` |

The UI modifier path reads Shift and Control through `CPdxKeyBoard`. No
modifier selects replace, Shift selects append, and Ctrl+Shift selects
prepend. Keyboard state is not part of the public action contract.

Focused player captures confirmed all three modes on
`CFleetOrbitPlanetCommand`: `(false,false)` replaced the queue,
`(true,false)` appended, and `(true,true)` prepended. A two-fleet selection
submitted two ordinary commands with one common target, rather than one
`CQueueFleetsOrderCommand`. Consequently a public group move is a validated,
locked ordered sequence of per-fleet actions; it is not advertised as an
atomic native transaction.

Both wrappers own their nested order and destroy it if ownership has not been
transferred. A future runtime handler must use the native constructor and
allocator evidence for the nested order and wrapper; it must not place a
stack object or a foreign smart pointer in these fields.

## Recovered nested orders

| Nested order | Size | Confirmed semantic fields |
| --- | --- | --- |
| `CMoveToSystemPointFleetOrder` | `0x50` | triggered-message flag `+0x21`; coordinate `+0x28` |
| `COrbitPlanetFleetOrder` | `0x60` | target ID `+0x28`; kind `+0x2c` (`1` planet, `2` starbase); origin system `+0x40`; merge option `+0x58` |
| `CFollowFleetOrder` | `0x58` | target fleet `+0x24`; cached coordinate `+0x28`; unnamed flags `+0x50/+0x51` |
| `CReturnFleetOrder` | `0x28` | base flag `+0x20`; own-starbase-only `+0x21`; try-home-base `+0x22` |
| `CMergeFleetOrder` | `0x30` | target fleet `+0x24`; country `+0x28`; unnamed flag `+0x2c` |
| `CJumpDriveFleetOrder` | `0x28` | destination system `+0x24` |

The large `human_ai` capture observed queue wrappers and all of the common
move, orbit, follow, return and merge nested families. Observation proves that
the objects flow through the shared serializer; it does not by itself prove
which player intent sets an unnamed flag.

Static UI callers further separate three superficially similar paths:

- `CJumpDriveFleetOrderButton::OnClick` constructs one
  `CJumpDriveFleetOrder` per selected fleet, then submits a
  `CQueueFleetOrderCommand`; the order token is `0x3bbb` (`bb3b` on wire) and
  destination token is `0x308d` (`8d30` on wire). Focused probe sequence 3115
  captured a normal player jump for fleet `888` to system `232` in replace
  mode; correlated execution sequence 3128 retained the same destination.
  The visible jump completed, closing the wrapper, destination and
  postcondition chain without any additional target or policy field.
- `CReturnFleetOrderButton::OnClick` constructs one shared
  `CReturnFleetOrder` and a `CQueueFleetsOrderCommand`. Its constructor is
  `CReturnFleetOrder(own_starbases_only, try_home_base)`: the second argument
  is hard-coded true and Control supplies the first argument.
- the ordinary merge button does not submit the nested `CMergeFleetOrder` seen
  in AI activity. It constructs `CMergeFleetsCommand` directly, validates it,
  and calls `PostCommandToSession`. Its command token is `0x31ef` (wire
  `ef31`), with country at `+0x1c`, fleet array at `+0x20`, and booleans at
  `+0x38/+0x39`; the normal button path passes both booleans false. Player
  probe sequences 4555/4556 confirm the natural submit/execute pair and game-
  assigned serial 5584. That running process had an older probe build which
  copied only the common header for this type, so the particular selected IDs
  are not claimed from this sample; the updated probe copies the full `0x40`
  object and the ID array on subsequent runs.

`CMergeFleetsCommand::Execute()` resolves the generation-bearing IDs and calls
`MergeFleets(CFleet* const*, int, CCountry const*, bool, bool)`. The helper
orders fleets by `CFleet::CalcNaturalFleetPriority()`, retains the first fleet
as the merge target, clears every incoming fleet's orders, and gives each one
a `CMergeFleetOrder` targeting the survivor. The `+0x38` exceptional flag also
makes that survivor go MIA with type 2; `+0x39` additionally clears the
survivor's existing orders. Both remain false for the ordinary player button,
so neither behavior belongs in the normal semantic action.

## Cancellation and direct target commands

| Command | Recovered layout / behavior |
| --- | --- |
| `CFleetCancelOrdersCommand` | country `+0x1c`, fleet-ID array at `+0x20`; clears the order list, cancels movement and disables automove |
| `CFleetCancelOrderCommand` | country `+0x1c`, fleet `+0x20`, order index `+0x24`; calls the indexed player-cancel path |
| `CFleetOrbitPlanetCommand` | fleet `+0x1c`, target `+0x20`, target kind `+0x24`, append/prepend flags `+0x38/+0x39` |
| `CFollowFleetCommand` | source fleet `+0x1c`, target fleet `+0x20`, `attack` `+0x24`, `cancelled` `+0x25`, append/prepend `+0x26/+0x27` |

The native token for `CFollowFleetCommand` is `0x336b`, which is the existing
paired network family `6b33`. Its writer maps source `0x2c50`, target `0x2ed6`
and attack `0x014c` to wire tags `502c`, `d62e` and `4c01`. The retained packet
sample `(source=3, target=379, 4c01=1)` therefore proves
`CFollowFleetCommand(source=3, target=379, attack=true, cancelled=false,
queue=false, queue_to_front=false)`. A live friendly follow sample separately
proved the same native class with `attack=false`. Focused player probe
sequences 3299/3300 then recorded fleet `888` attacking fleet `220` with
`attack=true`, `cancelled=false` and replace mode. The game accepted the
visible attack order. This closes the natural player differential: follow and
attack share one native type and differ in the `attack` member rather than in
their dispatcher or target representation.

The production runtime then issued request
`iag-live-attack-20260924-01` with semantic target `(source=888, target=220)`.
It returned `confirmed` only after `CFleet::GetExecutingOrder()` produced a
`CFollowFleetOrder` whose target was fleet `220`, whose attack flag was true
and whose cancelled flag was false. Focused probe sequences 3303/3304 recorded
the resulting `session_post` and `execute_sync` pair. Their six business bytes
were identical to sequences 3299/3300; Stellaris assigned command serial 4044
on the synchronized execution object. This verifies the full production path
instead of only the natural UI constructor layout.

## Combat state and emergency FTL

Combat participation and emergency-FTL eligibility are separate states. A
fleet may have an active `CFleetCombatManager` combat and still be temporarily
unable to retreat. The save-facing `in_combat_with` list is therefore a
deterministic `BUSY` reason for ordinary move, attack and maintenance actions;
it is not an `UNCERTAIN` parser state and does not imply that emergency FTL is
already legal.

`CFleetCombatEmergencyFTLCommand` is a direct command with no nested order:

| Property | Evidence |
| --- | --- |
| command token | `0x2f07` -> wire bytes `072f` |
| constructor | `stellaris+0x1f6a8f0` |
| `Execute` | `stellaris+0x1f6a920` |
| `IsValid` | `stellaris+0x1f6a970` |
| writer | `stellaris+0x1f6aa00` |
| object size | `0x20` |
| fleet ID | `+0x1c`, writer token `0x2c50` -> wire `502c` |

`Execute` resolves the fleet and calls
`CFleetMovementManager::DoEmergencyFTL()` at `stellaris+0x260bf60`.
`IsValid` first rejects the incompatible idler state and an invalid fleet,
then delegates to `CFleetMovementManager::CanEmergencyFTL(CString*)` at
`stellaris+0x260b590`. The latter requires all of the following:

- elapsed combat days are at least the maximum value returned by
  `CShip::CalcMinDaysBeforeEmergencyFTL()` for any ship in the fleet, falling
  back to `COMBAT_BASE_DAYS_UNTIL_EMERGENCY_FTL` for an empty list;
- the fleet is not MIA and is not already in FTL;
- `CFleetCombatManager::HasCombat()` is true;
- the fleet has either ordinary FTL or a jump drive;
- the controller-country retreat-permission bit associated with
  `NO_RETREAT_DESC` is set;
- every active leader trait passes the tested retreat-permission flag;
- `HasEscapeCoordinate()` succeeds, and any flagship behavior present allows
  that coordinate;
- the fleet is not held by a gravity snare.

The wait calculation reads the current date from
`g_CurrentGameState + 0xc0`, the fleet combat-start date from `fleet + 0x800`,
and emits `EMERGENCY_FTL_DAYS_LEFT` with the positive difference. Other native
diagnostics are `IS_MIA`, `IN_FTL`, `NO_COMBAT`,
`EMERGENCY_FTL_NOT_MOBILE`, `NO_RETREAT_DESC`, `NO_RETREAT` and
`GRAVITY_SNARE_NO_EMERGENCY_FTL`.

In the focused live check, fleet `888` had an active combat, was neither MIA
nor in FTL, had both FTL types, a valid escape coordinate, no gravity snare,
controller permission, leader permission and an allowed flagship behavior.
Its per-ship minimum was 14 days. A breakpoint in the game's own validator
hit the `EMERGENCY_FTL_DAYS_LEFT` branch, proving that the disabled button was
the combat-age gate rather than a failure to detect combat. No emergency-FTL
command was submitted in that sample, so production exposure still waits for
one natural `session_post`/`execute_sync` pair and a visible or saved
postcondition.

Two focused `CFleetCancelOrdersCommand` samples each carried one selected fleet
ID and cleared that fleet's complete order queue. The ordinary fleet UI does
not expose arbitrary waypoint-index cancellation. `CFleetCancelOrderCommand`
is retained for the specific anomaly, archaeology, astral-rift and automation
callers found statically, and is not part of the generic neural-controller
surface.

The old maintenance fixtures also close two native mappings without another
live packet capture:

| Wire family | Native type | Exact writer correspondence |
| --- | --- | --- |
| `8f32` repair | `CQueueFleetOrderCommand` wrapping `CRepairFleetOrder` | outer token `0x328f`; nested token `0x3020`; country `0x2c82`; fleet `0x2c50`; repair flags `0x3292/0x3b16`; default replace mode |
| `8f2f` upgrade | `CFleetUpgradeDesignCommand` | token `0x2f8f`; country `0x2c82`; fleet `0x2c50`; construction queue `0x3dc3`; queue flags `0x4063/0x35de` |

In wire byte order those tokens are exactly `8f32`, `2030`, `822c`, `502c`,
`9232`, `163b`, `8f2f`, `c33d`, `6340` and `de35`. The previously opaque
`context_822c` is therefore the country reference, while `c33d` is confirmed
by both the native writer type and save evidence as a `CConstructionQueue`
reference.

These types are now copied only under exact vtable allowlists by command-probe
schema v2 and decoded offline. They remain research-only until focused live
postconditions distinguish their player-facing variants.

## Return destination policy

`CReturnFleetOrder` does not serialize an explicit destination. Its two
constructor booleans at `+0x21/+0x22` are written under tokens `0x3292` and
`0x3b16` (wire `9232` and `163b`) and control the native return-point search:

| UI intent | `own_starbases_only` | `try_home_base` | Native behavior |
| --- | ---: | ---: | --- |
| ordinary Return click | `false` | `true` | try the assigned Home Base, otherwise the nearest allied starbase |
| Ctrl+Return | `true` | `true` | try the assigned Home Base, otherwise restrict fallback to an owned starbase |
| AI `ReturnToClosestStarbase()` | `false` | `false` | skip the Home Base attempt and choose the nearest allied starbase |

This is confirmed by the named AI callers, the implementation of
`NFleetRepairUpgrade::FindNearestReturnPoint`, the game's
`RETURN_ORDER_DESC`/`RETURN_TO_HOMEBASE_ORDER_DESC` localization, and focused
player probe sequences 33 and 35. The two player objects differed only at
`+0x21` (`0 -> 1`); `+0x22` remained `1`, and both outer wrappers used replace
mode. The byte at `+0x20` is inherited/base state rather than a constructor
policy argument and remains zero in these samples.

## Training-facing semantic surface

The neural controller should propose semantic decisions such as:

```text
move(fleet_candidate, destination_candidate, replace|append|prepend)
cancel_all(fleet_candidate)
follow(fleet_candidate, target_fleet_candidate)
orbit(fleet_candidate, planet_or_starbase_candidate)
jump(fleet_candidate, reachable_system_candidate)
```

Candidate indices are snapshot-local slots produced by the environment and
resolved back to generation-bearing IDs before Broker submission. A fixed
network output index is never a native object ID. Every head requires a legal
action mask for ownership, MIA/FTL state, reachability, queue capacity and
target visibility.

Group selection should remain an environment operation: one policy decision
may resolve to a locked ordered sequence or a verified multi-fleet wrapper,
but the production Broker still serializes the game side effect and binds it
to one source snapshot.

## Confidence and remaining live work

### A: statically confirmed or live verified

- common post/execute pipeline and direct move constructor/layout;
- coordinate layout and fixed-point representation;
- queue wrapper ownership shape and `EQueueMode` meanings;
- layouts listed above for known command/order types;
- adjacent and multi-hop replacement movement through the production runtime;
- append, prepend, intra-system coordinate, multi-selection and cancel-all
  player samples;
- friendly follow and planet/starbase orbit target differentials;
- player-issued attack from fleet `888` to attackable fleet `220`;
- production-runtime attack from fleet `888` to fleet `220`, including an
  exact native executing-order postcondition and probe sequences 3303/3304;
- jump-drive movement for fleet `888` to destination system `232`;
- normal and Ctrl return-destination policy differentials;
- player-issued merge submit/execute pair 4555/4556, the UI constructor path,
  selected-array layout, natural-priority survivor selection and both
  exceptional-flag effects;
- emergency-FTL command layout, serializer, execution target, full native
  validator and a live 14-day cooldown rejection for fleet `888`;
- packet/native mappings for attack, repair and upgrade.

### B: high probability, focused runtime confirmation required

- a successful natural emergency-FTL submit/execute pair and postcondition;

For each B item, capture before/after order state, one v2 `session_post` event,
the correlated `execute_sync` event and a post-action save or visible order
queue. Change only one target or modifier between paired samples.

### C: do not expose yet

- arbitrary intra-system coordinates supplied by an external caller;
- emergency FTL, return-for-repair and return-home flag variants;
- any command layout on a build other than the guarded 4.4.6 ELF;
- multiplayer equivalence for newly recovered wrapper actions.

The production `move_fleet.v1` action stays limited to its already verified
semantic target until the relevant B evidence is complete.
