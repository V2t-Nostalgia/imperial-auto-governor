# Adding a Native Runtime Action

This document is the contribution boundary for a verified Stellaris native
action. An action PR extends the existing runtime; it does not install a second
hook or create an Application-specific native API.

## Stable pipeline

```text
Application semantic target
  -> ExecutionBroker
  -> NativeRuntimeBackend
  -> private IAG1 Unix-socket or Windows named-pipe request
  -> runtime action registry
  -> CGameIdler::Idle main-thread marshal
  -> action Prepare()
  -> common GameApi::PostCommand()
  -> Stellaris session/command processing
  -> action Verify()
  -> normalized ExecutionResult
```

`runtime*.cpp` owns request correlation, process-local idempotency, the single
active side-effect slot, local-country authority, timeouts and the only call to
`PostCommandToSession`. Action handlers cannot bypass those rules.

## Files changed by a normal action PR

1. Add `actions/<action>.h` and `actions/<action>.cpp` implementing one
   `ActionInvocation`, parser, support probe and self-registering
   `ActionDescriptor`. Both CMake and the Windows release builder discover
   `actions/*.cpp`; do not edit a central action list.
2. In that descriptor declare `application_id`, semantic version, description,
   risk, verification state and every ordered parameter with its exact type and
   bounds. Use `application_id = "etc"` until the permanent domain is reviewed.
3. Add typed bindings and required instruction anchors to
   `version_profile.h` and the exact supported version profile.
4. If the semantic action already exists in Python, its strict target fields
   must match the runtime parameters. If it is new, the Broker creates a strict
   staging target from the manifest under `etc`; promote it to a reviewed
   `SemanticTarget` before a permanent Application depends on it.
5. Add parser/contract tests, Broker tests and a live evidence document.
6. Only return true from the platform support probe after live verification.
   Unsupported actions stay compiled but are absent from `describe_tools`.

An action PR normally does **not** edit the hook/trampoline, socket lifecycle,
idempotency implementation, Python runtime client, Broker dispatch, or another
action handler. Start from
`services/stellaris_native_runtime/examples/example_action.cpp.example`.

## Runtime-owned tool declaration

The runtime manifest is the lower layer's complete offer, not an authority
grant. It tells the Broker exactly what this binary can translate:

```json
{
  "action_type": "example_action",
  "action_version": 1,
  "application_id": "etc",
  "risk_class": "state_change",
  "verification_state": "live_verified",
  "parameters": [
    {"name": "object_id", "type": "uint32", "required": true,
     "minimum": 0, "maximum": 4294967295}
  ]
}
```

The Broker still checks the semantic registry, Application grant, candidate,
snapshot and player authority. Applications never consume this private
manifest or construct positional native fields themselves.

## Action handler contract

`Prepare()` runs on the Stellaris game thread. It must:

- resolve generation-bearing semantic IDs through verified game databases;
- enforce action-specific ownership and native legality;
- construct the same command type used by a native UI or AI caller;
- return an allocated command without submitting or executing it;
- fail closed on unknown objects, stale IDs or uncertain semantics.

The common runtime changes the request to `posted` and transfers command
ownership through `GameApi::PostCommand()`. After this call, neither the action
handler nor the runtime may destroy or reuse the command pointer.

`Verify()` also runs on the game thread after the native update. It must check
a specific state transition, not merely the absence of a crash. If no exact
postcondition can be observed before timeout, the action remains pending and
must later be confirmed from a save.

## Example: building construction

A building contribution should accept the already-defined semantic fields
`colony_id`, `zone_id` and `building_id`. Its handler would resolve the colony
and building definition, validate local authority and placement, construct the
verified native construction command, and return it. The common runtime posts
the command. Its postcondition should identify the exact new construction
queue item or return pending for save confirmation.

It must not accept object pointers, packet tags, command serials or raw hex. It
must not call the colony construction business method directly.

## Evidence required

- exact game version and ELF Build ID or PE build identity;
- symbol/function purpose, module-relative offsets and instruction anchors;
- command size, constructor ABI, relevant object layouts and ownership;
- native `IsValid` or equivalent validation path;
- evidence that `PostCommandToSession` receives the command;
- evidence that normal session processing reaches command execution;
- a precise in-memory or save postcondition;
- invalid-ID, wrong-authority and duplicate-request tests;
- explicit list of multiplayer and version-compatibility claims not yet made.

Unknown or inferred commands remain under `research/` until all of these
conditions are met.

## Existing packet-grounded staging path

The 4.4.6 Linux profile also contains a generated staging adapter for command
families that already have paired packet fixtures. Do not hand-edit
`actions/generated_capture_actions.inc`. Its source is
`tools/generate_capture_actions.py`, which derives and validates every field
offset against the existing packet builders.

These descriptors intentionally remain `paired_capture` and are not a shortcut
around the requirements above. A contribution that promotes one of them should
replace private captured queue/context fields with a semantic resolver, add a
specific postcondition, attach live evidence, and only then enable the native
backend for that reviewed Broker action. See
[`CAPTURE_BACKED_NATIVE_ACTIONS_4_4_6.md`](CAPTURE_BACKED_NATIVE_ACTIONS_4_4_6.md).
