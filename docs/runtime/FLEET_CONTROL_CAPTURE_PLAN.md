# Fleet Control Native Capture Plan

This is the handoff checklist for completing fleet movement discovery before a
training environment consumes IAG actions. Existing `move_fleet.v1` proves one
normal replacing inter-system movement order. It does not yet prove every
movement mode.

## Capture matrix

Each row needs a native command type, constructor ABI, serialized members,
submission call stack, exact postcondition and invalid-case sample.

| Semantic behavior | Native form | Current state |
| --- | --- | --- |
| Replace orders and move to an adjacent system | `CFleetFlyToCoordinatesCommand` | Live verified end to end |
| Replace orders and route across multiple systems | Same direct command, destination FTL point | Live player sample verified |
| Append a waypoint | direct queue flags (`queue=true`) | Live Shift sample verified |
| Prepend a waypoint | direct queue flags (`queue=true`, `queue_to_front=true`) | Live Ctrl+Shift sample verified |
| Move multiple selected fleets | one direct command per selected fleet | Live player sample emitted two separate commands; expose as a Broker-locked ordered sequence, not an atomic transaction |
| Move to an intra-system coordinate | embedded `CCelestialCoordinate` | Live player sample verified; native and paired `4f2c` evidence agree |
| Cancel all fleet orders | `CFleetCancelOrdersCommand` | Live samples verified for two fleets; multi-selection emitted one command per fleet |
| Cancel one special current order | `CFleetCancelOrderCommand` | Internal anomaly/archaeology/rift/automation UI path; not a generic player waypoint editor |
| Follow/escort another fleet | `CFollowFleetCommand(attack=false)` | Live player sample verified |
| Attack a hostile/attackable fleet | `CFollowFleetCommand(attack=true)` / wire `6b33` | Live player and production-runtime samples verified for fleet 888 attacking fleet 220; runtime exact-order postcondition and probe sequences 3303/3304 close the native path |
| Orbit a planet or starbase | `CFleetOrbitPlanetCommand` / wire `d32c` | Live planet (`kind=1`) and starbase (`kind=2`) samples plus old paired packets close the target differential |
| Jump-drive movement | one `CQueueFleetOrderCommand` + `CJumpDriveFleetOrder` per fleet | Live normal-click sample verified for fleet 888, destination system 232 and replace mode |
| Return for repair | `CQueueFleetOrderCommand` + `CRepairFleetOrder` / wire `8f32` | Old paired packet now maps exactly to native token, writer tags and defaults; no duplicate capture required |
| Fleet upgrade routing | `CFleetUpgradeDesignCommand` / wire `8f2f` | Old paired packet maps exactly to country, fleet, construction queue and queue flags; no duplicate capture required |
| Return home (non-repair) | nested `CReturnFleetOrder` | Normal and Ctrl player samples verified; home-base and own-starbase-only branches named |
| Merge selected fleets | direct `CMergeFleetsCommand` / wire token `ef31` | Live player submit/execute pair 4555/4556 verified. UI passes the complete selected-fleet array with both exceptional flags false; the next probe build records the concrete IDs. AI-only `CMergeFleetOrder` is a different type |
| Emergency FTL / retreat | `CFleetCombatEmergencyFTLCommand` / wire token `072f` | Static layout and full validator recovered. Live fleet 888 reached combat but hit the native 14-day `EMERGENCY_FTL_DAYS_LEFT` gate; wait for one legal natural submit/execute pair and postcondition before promotion |

Attack, bombardment and landing may cause movement, but remain distinct
semantic actions. They must not be mislabeled as generic movement simply
because they share the command dispatcher.

## Evidence reuse rule

Do not repeat a multiplayer packet capture merely to reproduce an action that
already has a paired client request, host-authoritative response and save
cross-check. A prior packet fixture can close the field layout when all of the
following also hold on the guarded Linux build:

1. the native command `_Token` equals the wire family after little-endian
   encoding;
2. `WriteCommandMembers()` uses the same field tokens and types;
3. the native object layout or constructor accounts for every serialized
   business field; and
4. no unexplained non-default member remains in the focused `session_post`
   sample.

Under that rule, `4f2c`, `d32c`, `6b33`, `8f32` repair and `8f2f` upgrade reuse
the retained packet evidence. New live captures are reserved for commands that
have no retained fixture, flags whose semantic branch remains unnamed, or a
production runtime postcondition test after implementing a new handler.

## Evidence collection

Run the v2 `human_ai` command probe and decode it with
`services/stellaris_native_runtime/research/decode_fleet_movement.py`. Group
records by command RTTI, nested-order RTTI, caller stack and decoded fields.
For each candidate, retain `session_post` and `execute_sync` records from the
same command object and compare at least two targets. Use an explicit player
action only when AI behavior cannot isolate a constructor flag or target
field. Each focused sample should record the pre-action save identity, visible
UI intent, decoded command, and post-action order/route state.

The production implementation should continue using one `CGameIdler::Idle`
marshal hook and one `PostCommandToSession` submission point. New movement
variants become separate action handlers or a versioned extension of an
existing semantic target only after their semantics are proven.

## Training-facing state requirements

The fleet controller should receive stable, maskable entity records rather
than native pointers or save-parser implementation details. At minimum, retain:

- full generation-bearing fleet/system/entity IDs and owner;
- system and physical coordinates, hyperlane adjacency and reachability mask;
- current order type, target, route, queue mode, FTL windup/cooldown and MIA;
- fleet power plus aggregate hull, armor and shield condition;
- hostile/neutral/allied relation and target visibility confidence;
- inhibitors, closed borders and other reasons an edge is unavailable;
- the snapshot revision used to create observations and legal-action masks.

The current Hive network has a fixed-width `action_head` while environment
entities are variable-length. A training adapter must therefore define a
stable candidate-slot/action-mask convention or add a separate target-scoring
head. An unconstrained action index must never be interpreted directly as a
Stellaris object ID.

The recurrent Hive latent is model memory, not authoritative game state. Every
runtime request must still bind to a current snapshot and pass Broker/runtime
validation even when the model proposes the action.

For training, queue behavior should be an explicit categorical action
(`replace`, `append`, `prepend`) with an action mask. Do not encode keyboard
modifiers in the model contract. Shift and Ctrl+Shift are UI evidence for the
native queue mode, not semantic runtime parameters.
