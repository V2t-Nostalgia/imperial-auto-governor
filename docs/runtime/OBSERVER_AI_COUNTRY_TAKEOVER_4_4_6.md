# Observer-Mode AI Country Takeover: Stellaris 4.4.6

## Goal

This note defines the intended single-process control model for AI-versus-AI
and player-versus-IAG campaigns:

> Only the vanilla commands produced by explicitly managed AI countries are
> intercepted. Human commands, unmanaged AI countries, scripted/system work,
> and the common session pipeline remain untouched.

The design is a per-country takeover gate, not a global replacement of every
call to `PostCommandToSession`.

No production takeover implementation is claimed by this document yet. It
records the static evidence and the first successful, deliberately bounded
runtime replacement experiment.

## Developer lookup

Use this map to move from the evidence in this note to the corresponding code
without reconstructing the investigation from chat history:

| Concern | Entry point |
| --- | --- |
| Exact-build research hook and one-shot replacement | [`observer_takeover_probe.cpp`](../../services/stellaris_native_runtime/research/observer_takeover_probe.cpp) |
| Local control/status client | [`observer_takeover_client.py`](../../services/stellaris_native_runtime/research/observer_takeover_client.py) |
| Research-only build target | [`research/CMakeLists.txt`](../../services/stellaris_native_runtime/research/CMakeLists.txt) |
| Probe operating instructions and safety boundary | [`research/README.md`](../../services/stellaris_native_runtime/research/README.md) |
| Reused production semantic action | [`actions/move_fleet.cpp`](../../services/stellaris_native_runtime/actions/move_fleet.cpp) |
| Native object access and postcondition verification | [`game_api.cpp`](../../services/stellaris_native_runtime/game_api.cpp) |
| Linux 4.4.6 version profile | [`versions/stellaris_4_4_6.cpp`](../../services/stellaris_native_runtime/versions/stellaris_4_4_6.cpp) |
| Upstream `human_ai` discovery evidence | [`HUMAN_AI_COMMAND_DISCOVERY_4_4_6.md`](HUMAN_AI_COMMAND_DISCOVERY_4_4_6.md) |
| Production execution boundary | [`EXECUTION_BROKER.md`](../EXECUTION_BROKER.md) |

The probe and client are discovery/validation tools. They are not a production
backend and must not be advertised through the native action manifest. A
production implementation belongs behind the Execution Broker and needs an
explicit observer-managed authority contract rather than a relaxation of the
existing local-player check.

## Why the architecture is feasible

The live `human_ai` capture already established the native AI pipeline:

```text
CGameState::ProcessAI
  -> parallel CCountryAI updates
  -> CCountryAI::PostAICommandsToSession, once per processed country
  -> PostCommandToSession(command, true)
  -> CSession::Post
  -> CSession::ProcessSynchronousCommands
  -> concrete CCommand::Execute
```

Sixty-one concrete AI-origin command types reached both `CSession::Post` and
the synchronous Execute dispatch during the retained run. In single player,
`CNetworkServer::DispatchOrder` may remain quiet; the session queue and native
command lifecycle still run normally.

This means an external controller does not need a second state-mutation path.
It can replace one country's pending AI command batch and let Stellaris submit
the replacement through its existing `PostCommandToSession` choke point.

## Exact-build country gate

The following values are for Linux Stellaris `Pegasus v4.4.6 (fdde)`, ELF
Build ID `c6969e60fd81d738948222a94c0b5a0841abbffc`.

| Offset / layout | Verified meaning |
| --- | --- |
| `0x17d7f00` | `CGameState::ProcessAI()` |
| `0x17d8154` | sequential call to `CCountryAI::PostAICommandsToSession()` after parallel updates join |
| `0x238eb90` | `CCountryAI::PostAICommandsToSession()` |
| `0x237d480` | `CCountryAI::AddAICommandForPosting(CCommand*)` |
| `0x237bdf0` | `CCountryAI::GetCountry() const` |
| `CCountryAI + 0x3a0` | generation-bearing `TPdxRef<CCountry>` value |
| `CCountryAI + 0x518` | pending `CPdxArray<CCommand*>` object |
| `CCountryAI + 0x520` | pending command pointer storage |
| `CCountryAI + 0x52c` | pending command count |
| `CCountryAI + 0x5b0` | mutex used while appending commands |
| `CCommand + 0x16` | set to `1` by the AI append path before validation |
| `0x17c2290` | `PostCommandToSession(CCommand*, bool)` |

`CCountryAI::PostAICommandsToSession()` has a particularly useful and simple
shape:

```text
for command in this->pending_commands:
    PostCommandToSession(command, true)
this->pending_count = 0
```

The country identity is available before touching a command. Therefore the
gate can decide from `CCountryAI + 0x3a0` whether the current country is in the
explicit takeover set.

`CCountryAI::AddAICommandForPosting()` also establishes the correct rejected
command lifecycle. It marks the command as AI-origin, invokes the native
validation virtual, appends under the AI mutex when valid, and invokes the
command's virtual deleting destructor when invalid. A takeover implementation
must reuse these native ownership semantics; it must not leak suppressed
commands or apply raw `operator delete` to an unknown concrete type.

## Proposed command replacement point

The preferred first implementation hooks
`CCountryAI::PostAICommandsToSession()`, not the global Post function:

```text
PostAICommandsToSession(country_ai)
  -> read generation-bearing country_id
  -> country not managed:
       call original unchanged
  -> country managed:
       destroy and clear only this country's vanilla pending commands
       materialize validated IAG commands for this country
       append them through the native AI-owned queue path
       call original PostAICommandsToSession
         -> PostCommandToSession(command, true)
         -> normal CSession lifecycle
```

Advantages:

- other countries never enter the replacement branch;
- player and system commands do not pass through this country-specific method;
- `force=true`, AI command flags, session serial assignment, ownership
  transfer, synchronous processing, and conditional network behavior stay
  game-owned;
- one hook supports every reviewed semantic action without action-specific
  submission code;
- `CGameState::ProcessAI` invokes country flushes sequentially on the game
  thread after parallel AI updates have joined, providing a natural safe
  materialization point.

Hooking `AddAICommandForPosting()` can later avoid constructing vanilla work
for a managed country, but it is not the first correctness boundary. The
country flush is the point that guarantees no queued command escapes even if a
specialized AI helper bypasses the common append function.

## Runtime and Broker changes required

The current production runtime deliberately accepts only the local observed
country:

```text
GameApi::LocalObservedCountry()
country_id == request.country_id
```

That rule must remain the default player-control mode. Observer takeover needs
a separate, explicit authority context rather than weakening it globally:

```text
AuthorityScope::LocalPlayer
AuthorityScope::ObserverManagedCountry
```

An observer-managed request is valid only when all of the following hold:

1. the session is positively identified as local single player;
2. observer takeover is explicitly enabled by the player;
3. `country_id` belongs to the runtime-owned managed-country set;
4. the generation-bearing country reference still resolves;
5. the country still has a live `CCountryAI` instance;
6. the semantic action's targets are owned or legally controllable by that
   exact country;
7. the action passes its native validator and resource/reservation checks.

The model must not gain an arbitrary `country_id` escape hatch. Country scope
is selected by the orchestrator/Broker from configured controller identity,
then bound into the prepared action and runtime request.

Each managed country needs independent:

- Application/model contexts and strategic plans;
- snapshot/country revision binding;
- resource ledger and reservations;
- ordered side-effect queue and request-id deduplication;
- pending native command batch;
- result evidence and postconditions.

The process still has one game thread. Planning and state derivation may run in
parallel, but final command materialization and submission remain serialized
at the per-country flush points.

## Full takeover versus staged takeover

The gate can technically discard every vanilla command for one country before
IAG supports every domain. Doing so would leave unsupported behavior idle, not
magically controlled. Rollout should therefore expose explicit modes:

| Mode | Behavior |
| --- | --- |
| Observe | Record the country's native batches; replace nothing |
| Domain takeover | Replace only reviewed domains and leave other vanilla commands unchanged |
| Full takeover | Suppress the country's complete vanilla batch and submit only IAG commands |

Full takeover is appropriate only after the desired controller has enough
economic, research, construction, diplomacy, fleet, and maintenance coverage
to keep the country viable. The architecture supports it now; semantic action
coverage determines when it becomes useful.

## First live experiment

Use a disposable single-player observer save and one non-player country.

1. Inject a read-only country-gate probe.
2. Select one generation-bearing country ID as the managed set.
3. For several turns, log that country's `CCountryAI*`, queue count, concrete
   command RTTI, and downstream `CSession::Post` events.
4. Enable suppression for only that country, destroy its queued commands via
   the native virtual lifecycle, and verify that:
   - no command from the managed batch reaches `CSession::Post`;
   - unmanaged AI countries continue posting;
   - player/system commands continue posting;
   - the game clock and save remain valid.
5. Queue one reviewed `move_fleet.v1` request for a fleet owned by the managed
   country.
6. At that country's next flush, append the IAG command through the native AI
   queue and call the original flush.
7. Verify exactly one replacement command reaches
   `PostCommandToSession(..., true)`, the target fleet receives the intended
   order, and the next save records the movement.
8. Repeat with one human country, one IAG-managed AI country, and one vanilla
   AI country in the same process.

Abort on an ownership mismatch, an unclassified command destructor, queue
corruption, stalled tick, invalid save, or any downstream command from the
managed vanilla batch.

## First live result: successful one-shot replacement

The focused experiment above was completed on 2026-09-28 against the exact
Linux build recorded in this document.

| Item | Observed value |
| --- | --- |
| Save | `ob.sav`, game date `2200.01.04` |
| Session | local single player, console observer mode |
| Managed country | generation-bearing country ID `1`, Techarus Interstellar Commissariat |
| Replacement action | `move_fleet.v1` |
| Fleet | ID `7`, `MACHINE1_FLEET_Solution` |
| Route | system `29` (`SPEC_Techarus_system`) to adjacent system `114` (`Elgerot`) |
| Vanilla batch | 3 commands |
| Vanilla vtable offsets | `0x42f78c0`, `0x42f77e8`, `0x42f8640` |
| Result | `confirmed:native_postcondition_player_movement_order_present` |

The probe began in read-only observation mode. It was then armed once for the
exact tuple `(country=1, fleet=7, destination=114)`. On country 1's next
non-empty flush it:

1. validated the semantic move through the existing native-runtime
   `move_fleet` implementation;
2. validated every pending command's module-owned vtable and executable
   deleting-destructor slot;
3. invoked those native deleting destructors and reset only country 1's
   pending count;
4. appended the replacement through
   `CCountryAI::AddAICommandForPosting()`;
5. called the original `CCountryAI::PostAICommandsToSession()`;
6. changed to verification-only mode and confirmed the native fleet movement
   postcondition.

The runtime recorded:

```text
batch_replaced generation=1 country_id=1 suppressed_count=3
               action_type=move_fleet fleet_id=7 destination_system_id=114
replacement_confirmed generation=1
               confirmed:native_postcondition_player_movement_order_present
```

The player independently observed the fleet begin moving. After the managed
flush, countries `16777222`, `16777223`, `16777224`, and `16` continued to
post non-empty AI batches and the Stellaris process remained alive. This is
direct evidence that the gate replaced one selected country's batch rather
than globally suppressing AI or session submission.

The probe automatically stopped replacement after that one batch. It did not
call a concrete command's `Execute()` directly, hook the global Post function,
or alter the authority behavior of the production runtime.

## Expected long-term topology

```text
one Stellaris process in observer or human-player mode
    |
    +-- human country commands ------------------------------+
    |                                                        |
    +-- unmanaged vanilla AI countries ----------------------+--> native session
    |                                                        |    pipeline
    +-- managed country A -> controller A -> semantic batch -+
    +-- managed country B -> controller B -> semantic batch -+
                                       |
                                       +-> per-country takeover gate
                                           -> native AI queue
                                           -> PostCommandToSession
```

This supports AI-versus-AI and player-versus-IAG on one computer without
creating fake multiplayer peers. Multiplayer behavior is a separate authority
problem and is not implied by this single-player design.

## Confidence

### A. Confirmed statically and dynamically

- AI commands from multiple countries converge on
  `CCountryAI::PostAICommandsToSession()` and then the common native submit;
- the country flush runs sequentially after parallel AI updates;
- the per-country pending array, count, country reference, AI flag, and forced
  Post call are recovered for this exact build;
- single-player execution does not require a network dispatch;
- reviewed native commands can already be submitted through the normal session
  path and verified by state postconditions.
- one selected AI country's three-command batch can be disposed through the
  native virtual lifecycle and replaced with one reviewed semantic action;
- the replacement can be appended through the native AI queue, submitted by
  the original forced Post path, and confirmed by the existing action
  postcondition;
- unmanaged AI countries continue submitting after the selected-country
  replacement.

### B. High confidence, needs broader live coverage

- observer mode can manage several countries in one process when each is bound
  to an explicit runtime controller;
- the same gate can replace repeated batches over a long campaign without
  accumulating lifecycle or scheduling defects;
- domain takeover can retain selected vanilla commands while replacing only
  reviewed semantic domains.

### C. Not yet established

- production hardening and soak testing for disposing every concrete command
  class that can appear in a managed batch;
- that every AI command producer uses the same append helper before the flush
  (the flush gate still prevents an alternate producer from escaping);
- complete semantic action coverage for a viable full-country replacement;
- Windows offsets/signatures for the country gate;
- any multiplayer authority or synchronization claim for observer takeover.
