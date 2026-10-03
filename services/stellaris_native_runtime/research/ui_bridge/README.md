# Native UI bridge probe

This directory contains an exact-build, research-only native Stellaris
conversation panel. It is deliberately separate from the production native
runtime and the Execution Broker.

The probe is live-verified against Stellaris `Pegasus v4.4.6 (fdde)` on Linux:

| Item | Verified value |
| --- | --- |
| ELF Build ID | `c6969e60fd81d738948222a94c0b5a0841abbffc` |
| ABI | Linux x86-64, System V AMD64 |
| Verification date | 2026-09-28 |
| Game mode | Running single-player campaign |

## Verified behavior

The native panel now provides:

- a persistent lower-right portrait launcher and an independently closable
  window;
- four Application contacts: fleet, economy, research, and `etc`;
- one in-memory conversation and unread counter per Application;
- a native `smoothListBoxType` message history with separate player and
  Application bubbles;
- real list clearing/reconstruction when contacts change, so histories do not
  become one mixed timeline;
- player-to-service requests and service-to-player unsolicited messages;
- response correlation to the Application selected at submission time, even
  when the player changes contacts before the response returns;
- native close/send buttons, `Enter` to send, `Esc` to close, and a movable
  panel.

The launcher remains visually minimal. That is presentation debt, not a
missing lifecycle or communication capability.

## Right-side assistant launcher

The first right-side revision was live-verified as an independent 260 x 96
native card docked immediately above the vanilla Outliner. The current source
revision keeps the same verified launcher lifecycle and `open_chat` button,
but removes the rectangular card: the transparent assistant portrait is now
anchored below the Outliner and above the lower-right toolbar. Its full visible
area is the click target, and the unread marker remains attached to it. This
portrait-only placement and its click path have now also passed a live game
run.

The launcher remains a top-level IAG container rather than becoming a child of
the Outliner. This is intentional: Stellaris rebuilds and switches the
normal/observer Outliner, so directly inserting an owned object into that tree
would couple its lifetime to unrelated game UI state. The new lower-right
placement uses Clausewitz's native `lower_right` anchor rather than a fixed
screen coordinate.

The previously verified rectangular card remains a deployment fallback at
`/tmp/iag-ui-v1-fallback-rectangular` on the research host.

Incoming Application responses and unsolicited pushes are also queued as a
transparent white thought bubble above and to the left of the portrait. Native
text controls place the originating Application and a bounded three-line UTF-8
preview inside the reusable bubble sprite. Clicking it selects that
Application and opens its private thread; dismissing it advances to the next
queued notification. The queue is capped at 16 entries and is cleared when the
campaign UI is left, so stale notifications cannot cross into the front end or
a later campaign. Player-authored messages do not create a duplicate
notification. This source revision uses the existing GUI-thread response drain
and performs no socket or model work on the GUI thread.

The thought-bubble sprite, unsolicited push, Application routing, open action,
and lower-right placement are live-verified. The current prototype text boxes
and dismiss button sit slightly above the bubble's ideal optical bounds; this
is a presentation-coordinate calibration item, not a protocol or lifecycle
failure.

## Runtime structure

The exact-build probe:

1. hooks `CGui::PerFrameUpdate(float)` so all game UI object access remains on
   the GUI thread;
2. requires both the in-game `g_CurrentGameState` started flag and an active
   `g_CurrentInGameIdler`, avoiding construction on the front end even when
   the started byte remains latched after leaving a campaign;
3. creates `iag_llm_chat_launcher` and `iag_llm_chat_window` through
   `CGui::CreateContainerWindow`;
4. resolves buttons, text boxes, the edit box, and the smooth list through
   native `CContainerWindow` accessors;
5. constructs each message row as a native `CSmoothListboxItem`;
6. exchanges UTF-8 messages with a local Unix socket on one background
   thread;
7. drains replies and unsolicited messages on the next GUI frame;
8. recreates invalidated windows without discarding per-Application
   conversation state.

The launcher is a separate native window. Closing the conversation window
therefore cannot remove the only way to reopen it.

## Native anchors

All offsets are module-relative and valid only for the Build ID above.

| Offset | Purpose |
| --- | --- |
| `0x3fa33a0` | `CGui::PerFrameUpdate(float)` GUI-thread marshal |
| `0x3f50730` | native button click dispatch |
| `0x3fa2190` | `CGui::CreateContainerWindow(...)` |
| `0x3fa22d0` | `CGui::GetGuiType(...)` |
| `0x3fa43c0` | `CGui::IsValid(...)` |
| `0x3f61f80` | `CContainerWindow::GetButton(...)` |
| `0x3f61fa0` | `CContainerWindow::GetInstantTextBox(...)` |
| `0x3f61fb0` | `CContainerWindow::GetEditBox(...)` |
| `0x3f62050` | `CContainerWindow::GetSmoothListbox(...)` |
| `0x3fd4b30` | `CSmoothListboxItem` constructor |
| `0x3fd70b0` | `CSmoothListbox::Add(...)` |
| `0x3fbd160` | list `DeleteAndRemoveAll()` ownership-safe reset |
| `0x3fd7e90` | `CSmoothListbox::SetScrollbarToMax()` |
| `0x3fd8550` | `CSmoothListbox::UpdateGraphics()` |
| `0x3fb1e50` | `CInstantTextBox::ChangeString(...)` |
| `0x5492098` | current game-state global |
| `0x5491900` | current idler global |
| `0x5491908` | current front-end idler global |
| `0x5491910` | current in-game idler global |

Important layout facts:

- `CGui::IsValid` receives the `CGuiObject` subobject at
  `CContainerWindow + 0x38`, not the container base;
- a `CSmoothListboxItem` is `0x78` bytes in this build;
- its native container pointer is at `CSmoothListboxItem + 0x68`;
- after `CSmoothListbox::Add`, the list owns the item;
- contact switching calls the game-owned `DeleteAndRemoveAll()` and rebuilds
  only the selected conversation. Calling item `Hide()` was tested and found
  insufficient because list refresh made unrelated entries visible again.
- launcher creation requires `g_CurrentIdler == g_CurrentInGameIdler` and a
  null `g_CurrentFrontEndIdler`. Existing panel windows are hidden as soon as
  that predicate becomes false and are never recreated on the front end.

## `IAGUI1` conversation protocol

Every record is one tab-delimited line. Backslash, tab, CR, and LF are escaped
inside fields.

```text
IAGUI1  conversation.submit  <application_id>  <message>
IAGUI1  conversation.response <application_id> <message>

IAGUI1  conversation.push    <application_id>  <message>
IAGUI1  conversation.accepted <application_id> <empty>

IAGUI1  conversation.poll    *                 <empty>
IAGUI1  conversation.none    *                 <empty>
```

The probe polls on its background thread every 400 ms. No socket operation
runs on the GUI thread. `echo_agent.py` is only a demonstrator, not an LLM
client, but it proves both directions of process-boundary communication.

Run the peer:

```bash
python3 echo_agent.py
```

Push a message without waiting for player input:

```bash
python3 echo_agent.py \
  --push-application research_strategy \
  --push-message 'External IAG message'
```

Unknown Application routing is intentionally not inferred by the bridge. The
demonstrator stages unsupported IDs under `etc`; a production IAG service must
derive contacts and routing from reviewed Application descriptors.

## Live evidence

Two successive live runs established the complete path.

The first verified native construction, close/reopen, contact selection, text
submission, and correlated response. The second added strict thread isolation
and unsolicited delivery. The retained log shape was:

```text
native_ui_bridge_ready ... application=etc
panel_created launcher=... window=... list=... input=... send=...
conversation_response bytes=115 application=fleet_operations
panel_opened
application_selected id=fleet_operations
conversation_response bytes=153 application=research_strategy
application_selected id=research_strategy
```

The fleet message was queued before the bridge was injected. It appeared after
the runtime began polling without any player message. A second message was
pushed to research while fleet was selected; it remained absent from the fleet
thread, raised research unread state, and appeared after selecting research.
The game remained alive after repeated list reconstruction.

## Build and stage

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build -j4
cp iag_llm_chat.gui iag_llm_chat.gfx \
  ~/.local/share/Steam/steamapps/common/Stellaris/interface/
mkdir -p \
  ~/.local/share/Steam/steamapps/common/Stellaris/gfx/interface/iag
cp gfx/interface/iag/assistant_portrait.dds \
  ~/.local/share/Steam/steamapps/common/Stellaris/gfx/interface/iag/
```

Load the resulting shared object before starting Stellaris or inject it once
into a clean process. The probe refuses to install hooks when the ELF Build ID
or guarded instruction prefixes differ. Logs are written to
`/tmp/iag-stellaris-ui-bridge.log`.

Do not unload or replace the shared object in a live process. Inline hooks and
native GUI pointers have process lifetime. A clean game restart is required
after rebuilding the library.

## Boundaries and remaining work

- Contacts are currently a four-entry exact-build prototype rather than a
  production manifest-driven list.
- Conversations are process-memory state and are not persisted across game
  restarts.
- Markdown, selectable text, rich attachments, localization keys, animated
  portraits, and richer activity state remain presentation work.
- The Unix socket is local and mode `0600`; production integration still needs
  authenticated service ownership, backpressure, persistence, and bounded
  reconnect behavior.
- This code must not become a second execution API. Native game actions remain
  behind the Execution Broker and production native-runtime contract.
