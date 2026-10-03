# Windows Native Move Probe

This directory is a deliberately narrow research harness for the stripped
Windows Stellaris 4.4.6 executable. It is not a production backend.

The Linux ELF provides semantic names and class relationships. The matching
Windows PE profile is recovered from command token `0x2c4f`, the command
vtable shape, common callsites, fixed-point coordinate logic, and the native
submission path. Every absolute value in the probe is an RVA and is guarded by
the exact PE timestamp/image size plus instruction and vtable anchors.

The only supported request is:

```text
move_fleet(fleet_id, destination_system_id)
```

It runs on the game thread through `CGameIdler::Idle`, resolves typed PDX
objects, asks the game's coordinate implementation for the destination FTL
point, calls the native command `IsValid`, clones the command with the game's
allocator, and submits it through `PostCommandToSession`. It never calls
`Execute` directly.

Build with the x64 Visual Studio generator:

```powershell
cmake -S . -B build -A x64
cmake --build build --config RelWithDebInfo
```

Then load and query it with:

```powershell
iag_stellaris_windows_probe_tool.exe inject iag_stellaris_windows_native_probe.dll
iag_stellaris_windows_probe_tool.exe status
iag_stellaris_windows_probe_tool.exe move test-1 123 456
```

The DLL writes diagnostics to
`%TEMP%\iag-stellaris-native-windows.log`. A successful live validation is a
prerequisite for moving this profile into the production runtime backend.

## Verified 4.4.6 run

The exact profile above was live-verified on 2026-09-26 with save `TestMove`:

```text
request:  win44-testmove-move-001
target:   fleet 3, Sol (11) -> Barnard's Star (448)
response: confirmed / native_postcondition_move_order_2cde_present
```

The game remained responsive and the fleet acquired native executing order
type `0x2cde`. Repeating the same request ID returned the cached response while
the matching `posted` log count remained one.

This is still a research harness, not the Windows production backend. In
particular, promotion requires the production authority check, an
exact-destination postcondition, a stable launcher/lifecycle, and Broker client
support for the private Windows transport.
