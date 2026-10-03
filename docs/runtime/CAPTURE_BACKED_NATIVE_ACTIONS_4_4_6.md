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

For live promotion, each action still needs:

1. a disposable-save target whose IDs are fresh;
2. observed native factory success and `IsValid == true`;
3. exactly one call through the common post path;
4. an action-specific game-state or new-save postcondition;
5. invalid-ID and wrong-authority rejection evidence;
6. a semantic resolver that eliminates captured queue/context fields before
   enabling the native backend in the Broker.

## Confidence

### A. Confirmed statically and offline

- the native byte factory, its consumers, body boundary and virtual validator;
- the common `PostCommandToSession` handoff and game-owned command lifecycle;
- exact generated-body equality against all 25 existing packet builders;
- strict C++ registration/manifest contracts;
- Windows MSVC `/W4 /WX` and Linux GCC `-Werror` builds and contract tests;
- unsupported Windows profiles do not advertise capture-backed actions.

### B. High confidence, requires the planned live batch

- every generated body is accepted by the live factory for a fresh legal
  target;
- the native validator agrees with the prior packet-side candidate validator;
- submitted commands reach the expected concrete game behavior.

### C. Not yet established

- save-backed postconditions for every generated action;
- production semantic resolution for queue IDs and other private captured
  fields;
- Windows factory signature, offsets and ABI;
- multiplayer behavior for this new native reconstruction path;
- promotion of any generated descriptor from `paired_capture` to
  `live_verified`.
