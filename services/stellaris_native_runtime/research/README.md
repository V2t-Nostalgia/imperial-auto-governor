# Stellaris command discovery probe

This directory contains a **research-only, read-only** runtime probe. It is not
an execution backend and must never be registered with the Execution Broker.

For the exact Stellaris 4.4.6 Linux build recorded in `command_probe.cpp`, the
probe observes two already-mapped points in the native command pipeline:

```text
CSession::Post(CCommand*)
    -> network path, when applicable: CNetworkServer::DispatchOrder(CCommand*)
    -> CSession::ProcessSynchronousCommands() virtual Execute dispatch
```

It writes newline-delimited JSON to
`/tmp/iag-stellaris-command-probe.jsonl` (override with
`IAG_COMMAND_PROBE_LOG`). Every event includes the concrete RTTI class, common
`CCommand` header, module-relative caller, thread ID, and a first-seen stack for
each command vtable. `execute_sync` records the common dispatch immediately
before the original virtual `Execute` call. The game-thread hooks only copy a
fixed-size event into a nonblocking pipe; symbol resolution, demangling, and
file I/O happen on a background writer thread.
The exact-build `CTurnTickCommand` vtable is filtered before enqueueing because
tick traffic carries no action semantics and otherwise dominates the capture.

Schema `iag.stellaris.command_probe.v2` additionally copies bounded layouts for
the fleet command and nested `CFleetOrder` types that have been recovered for
this exact executable. The record exposes `known_command_hex`,
`known_nested_hex`, nested RTTI and bounded related fleet IDs. Unknown RTTI
continues to expose only the common `CCommand` header. This is deliberately an
exact-build research allowlist: the probe never guesses an object size or
follows an unclassified payload pointer.

`payload_probe.cpp` is a companion observer for polymorphic payloads hidden by
generic command wrappers. It records nested `CFleetOrder` objects written by
`CWriter::Write(int, CPersistent const*)` and `CBuildableBase` objects written
by `CItemFactory<CBuildableBase>::Write`. Its separate JSONL stream is
`/tmp/iag-stellaris-payload-probe.jsonl`. This distinguishes, for example,
attack orders from movement orders and ship construction from building queues
without adding any execution capability.

The intended discovery loop is:

1. inject the probe while Stellaris is stopped in a debugger;
2. verify the probe status and a known command pair;
3. enable `human_ai` in the game console;
4. correlate JSONL command events with observed AI activity;
5. recover layouts and serializers statically from each confirmed RTTI type.

The probe deliberately has no socket, request parser, command constructor, or
write API. Unknown commands remain discovery evidence and cannot become a
production action until their semantics and execution chain are independently
verified.

Analyze a completed or live capture with:

```bash
python3 analyze_capture.py \
  --commands /tmp/iag-stellaris-command-probe.jsonl \
  --payloads /tmp/iag-stellaris-payload-probe.jsonl \
  --require-zero-drops
```

Decode the guarded fleet layouts into comparison-friendly semantic evidence
with:

```bash
python3 decode_fleet_movement.py \
  /tmp/iag-stellaris-command-probe.jsonl \
  --phase session_post
```

The decoder distinguishes replace, append and prepend queue modes and retains
unknown booleans under neutral `flag_*` names. Its output is evidence for
analysis and fixture creation, not a production action request format.

The analyzer treats `CCountryAI::PostAICommandsToSession()` in a first-seen
stack as an AI-origin fingerprint. Generic command payloads are considered
final only when their serialization occurs on the inferred game thread after
the matching `CSession::Post`. Objects serialized only by AI worker threads
are reported separately as candidates, never as executed commands.

Do not unload the shared object from a live process. The inline trampolines are
process-lifetime instrumentation.

## Windows structure matching

The Windows 4.4.6 executable is stripped. Use the Linux symbols as semantic
anchors, then recover and validate a separate Windows ABI/profile:

- `analyze_windows_command.py` locates a command token method, its vtable and
  construction callsites;
- `find_windows_game_idler.py` ranks the Windows game-loop marshal point;
- `find_windows_calc_ftl.py` ranks the fixed-point FTL coordinate helper;
- `windows/` contains the exact-build, one-action live research harness.

Do not copy Linux offsets or object sizes into Windows code. The live-verified
`move_fleet` mapping and PE identity are recorded in
`docs/runtime/NATIVE_FLEET_MOVE_HOOK_4_4_6.md`.
