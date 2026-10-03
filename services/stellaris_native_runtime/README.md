# Stellaris Native Runtime

This directory contains the production-side in-process runtime for verified
Stellaris native commands. It is deliberately separate from packet execution
and from reverse-engineering probes.

The supported build/action set remains exact-build and is described by the
loaded runtime itself:

- Linux Stellaris `4.4.6 (fdde)`, ELF build ID
  `c6969e60fd81d738948222a94c0b5a0841abbffc`: live-verified
  `stop_research.v1`, `move_fleet.v1`, and `attack_fleet.v1`, plus 25
  generated `paired_capture` actions reconstructed through the game's native
  binary-persistence command factory;
- Windows Stellaris `4.4.6 (fdde)`, Steam build `24109497`:
  `move_fleet.v1`.

The Windows move chain was structure-matched from the Linux symbols and then
verified independently in a live `TestMove` save. Linux offsets and ABI
assumptions are never reused on Windows: each platform selects its own exact
build profile and only advertises actions whose required bindings are present.

The library fails closed unless the build ID and key function prefixes match.
Its socket thread only validates and queues semantic requests. A guarded
trampoline on `CGameIdler::Idle(true)` transfers pending work onto the main
game thread immediately before `CGameApplication::Update()` and the session
update. The runtime resolves the technology key, verifies the local country,
checks both the command's `IsValid` and
`CTechnologyStatus::IsResearching`, then submits it through
`PostCommandToSession`. After the native update it confirms that the exact
technology is no longer being researched. Fleet movement resolves generation-
checked fleet/system objects, verifies local control, asks Stellaris for the
correct FTL point, calls the native command validator, and confirms that a
player movement order appeared. Fleet attack resolves both generation-checked
fleet objects, verifies source ownership and native command validity, then
confirms that the executing order is a non-cancelled attack against the exact
target fleet. The runtime never calls a command's business `Execute()` method
directly.

At startup each `actions/*.cpp` translation unit self-registers an
`ActionDescriptor`. `describe_tools` returns one strict manifest containing the
action name/version, owning Application, risk and verification state, plus the
ordered parameter names, types and bounds. The Python Broker intersects that
runtime-owned catalog with its semantic policy. An action whose permanent
domain has not been reviewed uses the explicit `etc` Application.

The production runtime is split by responsibility:

```text
runtime*.cpp                       platform hook/IPC, idempotency, submit
game_api.cpp                        shared access to the verified game ABI
versions/*.cpp                      build IDs, offsets and instruction anchors
actions/action_registry.cpp         self-registration and manifest machinery
actions/<semantic_action>.cpp       metadata, target parser, builder, verification
include/iag_native_runtime/*.h      contributor-facing native contracts
research/                           probes only; never a production action source
```

Adding an action does not add another game-loop hook and must not call a
business `Execute()` method. Follow
[`docs/runtime/NATIVE_RUNTIME_ACTION_CONTRIBUTING.md`](../../docs/runtime/NATIVE_RUNTIME_ACTION_CONTRIBUTING.md).

The live chain, offsets, signatures, object layout and validation evidence are
recorded in
[`docs/runtime/NATIVE_RESEARCH_HOOK_4_4_6.md`](../../docs/runtime/NATIVE_RESEARCH_HOOK_4_4_6.md).
Fleet movement evidence is recorded in
[`docs/runtime/NATIVE_FLEET_MOVE_HOOK_4_4_6.md`](../../docs/runtime/NATIVE_FLEET_MOVE_HOOK_4_4_6.md),
fleet attack evidence in
[`docs/runtime/NATIVE_FLEET_ATTACK_HOOK_4_4_6.md`](../../docs/runtime/NATIVE_FLEET_ATTACK_HOOK_4_4_6.md),
the shared capture-backed factory and its staged action catalog in
[`docs/runtime/CAPTURE_BACKED_NATIVE_ACTIONS_4_4_6.md`](../../docs/runtime/CAPTURE_BACKED_NATIVE_ACTIONS_4_4_6.md),
with the wider command/order family map in
[`docs/runtime/FLEET_MOVEMENT_NATIVE_4_4_6.md`](../../docs/runtime/FLEET_MOVEMENT_NATIVE_4_4_6.md).
The separate single-player observer takeover proposal and its recovered
per-country AI queue boundary are recorded in
[`docs/runtime/OBSERVER_AI_COUNTRY_TAKEOVER_4_4_6.md`](../../docs/runtime/OBSERVER_AI_COUNTRY_TAKEOVER_4_4_6.md).

Build on the Linux game host:

```bash
cmake -S services/stellaris_native_runtime \
  -B build/stellaris_native_runtime \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build/stellaris_native_runtime --parallel
```

Build on Windows with Visual Studio Build Tools (this also runs the native
contract test):

```powershell
./scripts/build/Build-StellarisNativeRuntime.ps1
```

Compile and run the native contract tests with:

```bash
cmake -S services/stellaris_native_runtime \
  -B build/stellaris_native_runtime-tests \
  -DIAG_NATIVE_RUNTIME_BUILD_TESTS=ON
cmake --build build/stellaris_native_runtime-tests --parallel
ctest --test-dir build/stellaris_native_runtime-tests --output-on-failure
```

The Linux endpoint is `/tmp/iag-stellaris-native.sock`; Windows uses
`\\.\pipe\iag-stellaris-native`. Set
`IAG_NATIVE_RUNTIME_SOCKET` before the game starts to choose another absolute
Unix-socket path. The packaged Windows Agent contains the exact-build DLL and
loader and only loads them when native runtime execution is enabled. Linux
loading remains an explicit operator/deployment concern.

Discovery scripts and raw runtime traces belong under `research/`; unknown
commands must not be added to this production runtime.
