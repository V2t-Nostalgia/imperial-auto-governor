# Capture-Backed Native Commands: Stellaris 4.4.6

## Scope

This note records the first shared native reconstruction path for command
families that already have paired packet evidence. It does not claim that all
listed actions are live verified or ready for autonomous Application use.

The implementation restores a real Stellaris `CCommand` from its reviewed
binary-persistence body, invokes the command's native validator, and submits
the resulting object through the existing runtime's single game-thread post
path. It never calls a command's business `Execute()` method and never sends a
captured network packet.

## Recovered native path

Exact target: Linux Stellaris `Pegasus v4.4.6 (fdde)`, ELF Build ID
`c6969e60fd81d738948222a94c0b5a0841abbffc`.

| Symbol / use | Module offset | Evidence |
| --- | ---: | --- |
| `CreateCommand(unsigned char const*, unsigned int)` | `0x381c8d0` | symbol plus disassembly |
| `CNetworkServer::PackageCallback` factory call | `0x38c633c` | calls factory, then virtual `+0x40` |
| `CProxyServer::PackageCallback` factory calls | `0x38cb7cd`, `0x38cbc70` | same deserialization path |
| `CDummyServer::Post` factory call | `0x38ce6a4` | single-player/local path consumer |
| `PostCommandToSession(CCommand*, bool)` | `0x17c2290` | existing live-verified runtime choke point |
| `CSession::Post(CCommand*)` | `0x38250d0` | assigns session actor and serial before queueing |

`CreateCommand(bytes, size)` constructs `CMemoryFile`, `CBinLexer` and
`CReader`, reads the command token, dispatches through `CreateCommand(int)`,
then calls `CReader::Read(CPersistent&)` to populate the concrete object. The
network caller demonstrates the validator ABI:

```text
command = CreateCommand(body, body_size)
command->vtable[0x40 / sizeof(void*)](command)  // IsValid-like predicate
```

Only the CBin command-object body is passed to this factory. The six-byte
reliable-stream record envelope is excluded. Actor and command serial fields
inside the retained fixture are placeholders; the native session post path
owns final session identity and serial assignment.

The deleting destructor is the virtual at byte offset `+0x08`. The capture
adapter uses it only when deserialization succeeds but native validation
rejects the command. Successful submission transfers ownership to Stellaris.

## Generated action set

The generator uses the current `session_proxy` packet builders and
`OFFLINE_FIXTURE_TARGETS` as its source of truth. It never copies offsets from
this document. For every scalar action it:

1. builds the known-good command record;
2. changes one target field at a time and rebuilds it;
3. derives every affected byte offset from the exact diff;
4. changes all fields together, including string lengths;
5. reconstructs the body with the generated patches;
6. requires exact byte equality with a fresh builder result.

Run the reproducibility check with:

```bash
python services/stellaris_native_runtime/tools/generate_capture_actions.py --check
```

The 25 generated actions are:

| Domain | Actions |
| --- | --- |
| Economy | `build_building`, `upgrade_building`, `replace_building`, `build_district`, `build_zone` |
| Fleet orders | `move_fleet_to_coordinate`, `set_orbital_bombardment_stance`, `land_armies`, `repair_fleet`, `upgrade_fleet` |
| Army / expansion | `recruit_army`, `build_starbase`, `order_colony_ship_and_colonize`, `colonize_with_existing_ship` |
| Starbase | `upgrade_starbase`, `set_starbase_module`, `set_starbase_building` |
| Research | `start_research` |
| Ship / template | `build_ship`, `configure_ship_automation`, `create_ship_design`, `create_fleet_template`, `add_fleet_template_ship`, `remove_fleet_template_ship`, `reinforce_selected_fleet` |

Together with the separately implemented and live-verified
`move_fleet`, `attack_fleet` and `stop_research`, the source action families
now have one native-runtime registration point each.

Every generated descriptor remains `paired_capture`. On Linux it appears in
the runtime manifest so diagnostics can exercise it. The Broker overlays it as
a supported backend but does not add it to `broker_enabled_backends`; therefore
an Application cannot silently switch from its existing execution path.

## Complex records

Most commands expose typed captured fields such as queue, object, definition
and coordinate values. Two nested commands cannot yet be expressed as a small
fixed patch set:

- `configure_ship_automation`
- `create_ship_design`

For these, the private `_native_capture_adapter.py` reuses the reviewed Python
builder and sends a bounded hex encoding of the command body to the runtime.
The C++ side checks decoding and the exact expected command family before
calling the native factory. This parameter is transport-specific debt:

- it is diagnostic/backend-only;
- it is not a public semantic target;
- it is not Broker execution-ready;
- Applications and models never construct or receive it.

The long-term replacement is a typed backend resolver or a direct native
constructor whose fields match `CreateShipDesignTarget` and
`ConfigureShipAutomationTarget`.

## Runtime safety boundary

- The shared runtime keeps one active side-effect request and request-ID
  conflict detection.
- Preparation, native validation and submission run from the existing
  `CGameIdler::Idle(true)` game-thread marshal.
- The Linux build identity and factory instruction prefix are mandatory
  anchors. A mismatch prevents runtime startup.
- Generated commands must match their expected six-byte command family.
- Runtime IPC remains local and bounded to 64 KiB.
- A capture-backed command returns
  `native_posted_awaiting_save_verification`; absence of a crash is not a
  confirmation.
- Existing live-verified direct implementations are not replaced.
- Windows has no `CreateCommand(bytes)` profile yet, so generated actions are
  compiled but absent from the Windows manifest.

## Batch validation

`scripts/diagnostics/run_native_capture_batch.py` is the deliberately isolated
test entry point. Its plan contains explicit, already candidate-validated
targets and an `approved: true` flag per action. The default is dry-run. Sending
requires both `--execute` and the exact confirmation phrase printed by the
script help.

The runner queries the loaded runtime manifest, validates every ordered field,
uses the private nested-record adapter only where required, submits actions in
order, stops on the first rejection/failure, and writes a JSON evidence report.
It is not an Application API and does not bypass Broker authority in production.

## Live validation: 2026-10-03

A disposable Stellaris 4.4.6 single-player run exercised the generated path on
the exact Linux Build ID above. Every submitted request was reconstructed by
the live factory, accepted by the native validator, and posted through the
common game-thread path without terminating the game process. New saves were
then compared with the exact source snapshots; runtime acceptance by itself
was not treated as success.

The snapshot chain was:

| Snapshot | SHA-256 |
| --- | --- |
| `war.sav` | `0d1c55e08498098f43f11486b95d55781b9877e31a96a7f9295edd4cbe43157f` |
| `war_after.sav` | `d4f9a8f5bd9d30c39eb9015f70408dfea18c7478afd933469225284850f3995d` |
| `war_after2.sav` | `4bedac3962285de7a2b012a730c307347959cb49908df08c68f5ab08a7d223f4` |
| `war_after3.sav` | `dcb9a82e43595dfcf55840eb17dcc1e5d622bccaeb5896d8f6080ce95a37c2b8` |
| `war_after4.sav` | `d5497a1d067c785308cdf8e2efff128acb42b2d1dc9a07c4c4cf5b1c05ba65f5` |
| `war_after5_repair.sav` | `e87aa7fc1f0c7f493544dcaefd9288b92ff1098804450cc20100cce2b6776b11` |
| `war_after6_upgrade.sav` | `2ca26135d9e4e9ffc4ff75cb850100538f9c678f89a02a98a80020d25b8c3f3f8` |
| `war_after7_landing.sav` | `9e0f26b6f0fa6705cd17ec07d4a76c85f312da4f695cae7ba90a423c1e09a21d` |

Save-backed behavior was confirmed for 23 generated actions:

| Domain | Confirmed actions |
| --- | --- |
| Economy | `build_building`, `upgrade_building`, `replace_building`, `build_district`, `build_zone` |
| Fleet orders | `move_fleet_to_coordinate`, `set_orbital_bombardment_stance`, `land_armies`, `repair_fleet`, `upgrade_fleet` |
| Army / expansion | `recruit_army`, `order_colony_ship_and_colonize` |
| Starbase | `upgrade_starbase`, `set_starbase_module`, `set_starbase_building` |
| Research | `start_research` after the existing research was stopped |
| Ship / template | `build_ship`, `configure_ship_automation`, `create_ship_design`, `create_fleet_template`, `add_fleet_template_ship`, `remove_fleet_template_ship`, `reinforce_selected_fleet` |

The evidence includes concrete construction queue entries, research selection,
fleet order/stance changes, automation state, a colonization expansion-list
entry, a new ship design, and fleet-template mutations. `add` and `remove`
were retested in separate snapshots so their effects could not cancel each
other in the final state. The follow-up snapshots additionally contain:

- `repair_fleet_order` for fleet `251662165`, routing to the repair orbit in
  system `2`;
- `upgrade_design_at_orbitable_fleet_order` for that fleet, with five ships in
  `upgrade_waiting` state and shipyard queue `9167` in system `217`;
- `land_armies_order` for transport fleet `3103785054`, targeting colony `298`
  and planet `2983`.

`build_starbase` was then submitted as request
`war7-04-build-enigmas-end` for construction fleet `16777379` and system
`546` (`Enigmas_End`). The runtime accepted and posted the command, and the
player confirmed the generated travel/build order while the game remained
paused. No subsequent save was made, so this is live player-visible evidence,
not yet a save-backed postcondition.

`colonize_with_existing_ship` was intentionally not submitted. The preferred
automatic path, `order_colony_ship_and_colonize`, was already confirmed by
request `war-10-order_colony_ship_and_colonize`: target planet `6068` appears
in `standard_expansion_module.expansion_list` with queue item `3841982519`.
Repeating it without advancing time would create or attempt a duplicate order,
so the candidate layer now suppresses planets already present in that list.

The run also established an important negative result. Native deserialization
and the command's validator do not prove that all higher-level game rules allow
the action. An already installed unique starbase building, a district with no
remaining capacity, and an ineligible titan build were accepted by the native
post path but produced no save mutation. The candidate layer must therefore
continue to enforce freshness, ownership, uniqueness, capacity, resources and
other domain rules before execution. The follow-up used legal alternatives and
confirmed the same command families.

These results are live save-backed evidence, but the generated descriptors
remain `paired_capture`. Promotion to `live_verified` still requires the
production semantic resolver and the rejection/authority gates below.

For live promotion, each action still needs:

1. a disposable-save target whose IDs are fresh;
2. observed native factory success and `IsValid == true`;
3. exactly one call through the common post path;
4. an action-specific game-state or new-save postcondition;
5. invalid-ID and wrong-authority rejection evidence;
6. a semantic resolver that eliminates captured queue/context fields before
   enabling the native backend in the Broker.

Items 1-4 are now satisfied for the 23 save-backed actions listed above.
`build_starbase` has native-post and live-order evidence but still needs a
subsequent save postcondition. `colonize_with_existing_ship` still requires all
six gates.

## Confidence

### A. Confirmed

Static and offline evidence:

- the native byte factory, its consumers, body boundary and virtual validator;
- the common `PostCommandToSession` handoff and game-owned command lifecycle;
- exact generated-body equality against all 25 existing packet builders;
- strict C++ registration/manifest contracts;
- Windows MSVC `/W4 /WX` and Linux GCC `-Werror` builds and contract tests;
- unsupported Windows profiles do not advertise capture-backed actions.

Live 4.4.6 evidence:

- 23 generated actions produced action-specific changes in subsequent saves;
- `build_starbase` produced a player-confirmed native order while paused;
- all submitted command objects passed the live factory/validator/post path;
- the game process remained alive throughout the ordered batches and follow-up;
- native validation alone is not a substitute for semantic candidate checks.

### B. High confidence, still requires a legal live candidate

- `build_starbase` uses the recovered factory/post path and has a confirmed
  live order, but still needs a subsequent save postcondition.
- `colonize_with_existing_ship` passes exact offline body reconstruction but
  was intentionally not submitted in this run.

### C. Not yet established

- save-backed postconditions for `build_starbase` and
  `colonize_with_existing_ship`;
- invalid-ID and wrong-authority rejection behavior for generated actions;
- production semantic resolution for queue IDs and other private captured
  fields;
- Windows factory signature, offsets and ABI;
- multiplayer behavior for this new native reconstruction path;
- promotion of any generated descriptor from `paired_capture` to
  `live_verified`.
