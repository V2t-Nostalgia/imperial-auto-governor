# Human AI Command Discovery: Stellaris 4.4.6

## Scope

This report records a read-only `human_ai` discovery run against Stellaris
`Pegasus v4.4.6 (fdde)` on Linux. It identifies native commands that vanilla AI
actually submits and separates them from temporary orders serialized by AI
worker threads while evaluating candidates.

The run did not construct commands, call `Execute()` or modify game state. The
research DSOs only observed existing game calls.

| Item | Captured value |
| --- | --- |
| ELF Build ID | `c6969e60fd81d738948222a94c0b5a0841abbffc` |
| Process | `727813` |
| UTC window | `2026-09-24T08:16:47.180325Z` to `2026-09-24T09:08:35.688554Z` |
| Command events | 204,054 |
| Payload events | 77,708 |
| Malformed lines | 0 |
| Dropped events | 0 |
| Inferred game thread | `727813` |

The frozen capture prefix remains on the research host as:

```text
/tmp/iag-stellaris-command-probe.human-ai-20260924T090818Z.jsonl
/tmp/iag-stellaris-payload-probe.human-ai-20260924T090818Z.jsonl
/tmp/iag-human-ai-command-symbols-20260924T090818Z.txt
```

The symbol catalog contains 778 matching ELF symbols across all 61 captured
types; none of the types lacked constructor/lifecycle/RTTI evidence. Raw logs
and the generated catalog are intentionally not committed to the repository.

## Native AI submission chain

The first-seen stack for AI-origin commands resolves to:

```text
CGameState::HandleTurnTick
  -> CGameState::ProcessAI
  -> CCountryAI::PostAICommandsToSession
  -> PostCommandToSession
  -> CSession::Post
  -> CSession::ProcessSynchronousCommands
  -> concrete CCommand::Execute
```

Relevant module offsets in this build are:

| Offset | Symbol |
| --- | --- |
| `0x17c2290` | `PostCommandToSession(CCommand*, bool)` |
| `0x238eb90` | `CCountryAI::PostAICommandsToSession()` |
| `0x17d8159` | call site in `CGameState::ProcessAI` |
| `0x17d54f5` | call site in `CGameState::HandleTurnTick` |
| `0x38250d0` | `CSession::Post(CCommand*)` |
| `0x3825b80` | `CSession::ProcessSynchronousCommands()` |
| `0x3826178` | common virtual `Execute` dispatch instruction |
| `0x38c8c80` | `CNetworkServer::DispatchOrder(CCommand*)` |

`CNetworkServer::DispatchOrder` was quiet in this single-player run. This is
expected and is evidence that network serialization is a conditional branch,
not a prerequisite for local native command execution. The production entry
must remain `PostCommandToSession` / `CSession::Post`; calling a business
`Execute()` function directly would bypass the game-owned command lifecycle.

## Confirmed native command families

The AI fingerprint was the first-seen frame
`CCountryAI::PostAICommandsToSession + 0x35` (`stellaris+0x238ebc5`). Sixty-one
concrete command types reached both `session_post` and `execute_sync`. Selected
families are:

| Domain | Native commands observed |
| --- | --- |
| Fleet orders | `CQueueFleetOrderCommand`, `CQueueFleetsOrderCommand`, `CMergeFleetsCommand`, `CFleetFlyToCoordinatesCommand`, `CMoveShipsToFleetCommand`, `CFollowFleetCommand`, `CSwitchFleetStanceCommand`, `CSetHomeBaseCommand` |
| Civilian fleets | `CFleetSurveyDepositHolderCommand`, `CFleetResearchAnomalyCommand`, `CFleetBuildOrbitalStationCommand`, `CFleetColonizePlanetCommand`, `CExcavateArchaeologicalSiteFleetOrderCommand` |
| Construction | `CAddBuildableToQueueCommand`, `CBuildMegaStructureCommand`, `CFleetUpgradeDesignCommand` |
| Economy | `CMarketBuyResourceCommand`, `CMarketSellResourceCommand`, `CResettlePopCommand`, `CChangeJobWorkforceLimitCommand`, `CSetColonyTypeCommand` |
| Research and policy | `CResearchTechnologyCommand`, `CResearchSpecialProjectCommand`, `CSetPolicyCommand`, `CAddEdictCommand`, `CRemoveEdictCommand`, `CSetCouncilAgendaCommand` |
| Diplomacy and strategy | `CDiploActionProposeCommand`, `CDiploActionRespondCommand`, `CAIUpdateStrategicWarData`, `CAIAddStrategyCommand`, `CAIRemoveDiploAction` |
| Leaders and traditions | `CAssignLeaderCommand`, `CHireAndAssignLeaderCommand`, `CCountryActivateTraditionCommand`, `CCountryActivateAscensionPerkCommand` |
| Ship design | `CAutoGenerateShipDesignCommand`, `COverwriteFleetTemplateDesignCommand`, `CCreateOrUpdateShipDesignCommand` |

The most frequent final posts in the frozen run included 13,279 fleet-template
overwrites, 11,206 multi-fleet order wrappers, 5,032 single-fleet order
wrappers, 717 build-queue commands, 542 orbital-station commands, 203 direct
coordinate moves, 189 research selections and 25 colonization commands.

## Wrapper payload correlation

`CQueueFleetOrderCommand`, `CQueueFleetsOrderCommand` and
`CAddBuildableToQueueCommand` hide a polymorphic payload. The payload probe
observes the writer boundary. A payload is classified as final only if it is
serialized on the game thread after the corresponding `CSession::Post` and
before the next post.

The frozen run produced these exact correlations with no unmatched wrapper:

| Count | Outer command | Final nested payload |
| ---: | --- | --- |
| 8,361 | `CQueueFleetsOrderCommand` | `CReturnFleetOrder` |
| 2,652 | `CQueueFleetOrderCommand` | `CReturnFleetOrder` |
| 247 | `CQueueFleetOrderCommand` | `CFollowFleetOrder` |
| 126 | `CQueueFleetOrderCommand` | `CMergeFleetOrder` |
| 103 | `CQueueFleetOrderCommand` | `CScriptedFleetOrder` |
| 11 | `CQueueFleetOrderCommand` | `CMoveToSystemPointFleetOrder` |
| 112 | `CAddBuildableToQueueCommand` | `CBuildableShip` |
| 83 | `CAddBuildableToQueueCommand` | `CBuildableDistrict` |
| 53 | `CAddBuildableToQueueCommand` | `CBuildableBuilding` |
| 46 | `CAddBuildableToQueueCommand` | `CBuildableStarbaseModule` |
| 22 | `CAddBuildableToQueueCommand` | `CBuildableColonyShip` |
| 20 | `CAddBuildableToQueueCommand` | `CBuildableArmy` |
| 20 | `CAddBuildableToQueueCommand` | `CBuildableStarbaseBuilding` |
| 17 | `CAddBuildableToQueueCommand` | `CBuildableStarbaseUpgrade` |
| 7 | `CAddBuildableToQueueCommand` | `CBuildableZone` |
| 4 | `CAddBuildableToQueueCommand` | `CBuildableFederationShip` |
| 4 | `CAddBuildableToQueueCommand` | `CBuildableClearDepositBlocker` |
| 2 | `CAddBuildableToQueueCommand` | `CBuildableUpgradeBuilding` |

This reveals a reusable native design: a stable semantic command wrapper owns
a typed polymorphic order/buildable and the game serializes both through its
normal registry.

## Candidate versus executed order

The most important negative result concerns attack discovery. AI worker threads
serialized 10,632 `CAggressiveStanceFleetOrder` candidates, but the
game thread serialized zero of them as a final posted payload during the
frozen interval. The same distinction applies to candidate-only survey,
repeat, wait, orbit, station-building and target-selection objects.

Therefore this run confirms that `CAggressiveStanceFleetOrder` exists and is
used during AI planning; it does not prove that posting this order represents
a direct attack command. Promoting it to the production action registry on
class name alone would be unsafe.

The 103 final `CScriptedFleetOrder` posts serialized only the scripted order
object at the observed writer boundary. No nested `CAttackFleetAction` reached
either probe during this interval, although the ELF exports its lifecycle and
serializer functions at `0x25ec500` through `0x25ec720`. That is a useful
static anchor, not dynamic proof of an attack submission.

Static symbols clarify its role: it has a default constructor at `0x262eda0`
and methods such as `FindAttackTarget`, `FindInvadeTarget`, `FindFollowTarget`
and `UpdateSubOrder`. It appears to be a persistent autonomous behavior that
chooses suborders, rather than a semantic `attack target_id` request. A direct
attack still needs a main-thread final-post sample and state-delta validation.

## Constructor anchors

Linux symbols provide high-value anchors for future native actions and Windows
matching:

```text
0x1f344e0 CResearchTechnologyCommand(TPdxRef<CCountry>, CTechnology const*)
0x1f6f780 CQueueFleetOrderCommand(CCountry const*, CFleet const*,
                                  CFleetOrder*, EQueueMode)
0x1f6feb0 CQueueFleetsOrderCommand(CPdxArray<TPdxRef<CFleet>, int> const&,
                                   CCountry const*, CFleetOrder*, EQueueMode)
0x2360940 CAddBuildableToQueueCommand(TPdxRef<CCountry>,
                                      TPdxRef<CConstructionQueue>,
                                      CPdxScopedPtrImpl<CBuildableBase, false>)
0x1f6c6e0 CMergeFleetsCommand(CCountry const*, CFleet const*,
                              CFleet const*, bool)
0x1f64c30 CFleetFlyToCoordinatesCommand(TPdxRef<CFleet>,
                                        CCelestialCoordinate const&, bool, bool)
0x1f669c0 CFleetBuildOrbitalStationCommand(TPdxRef<CFleet>,
                                           CMetaRef<CDepositHolder, ...> const&,
                                           EShipClass, bool, bool)
0x1f67b50 CFleetColonizePlanetCommand(CFleet const*, CPlanet const*, bool, bool)
0x1f73860 CBuildMegaStructureCommand(TPdxRef<CFleet>,
                                     CMegaStructureType const*, CPlanet const*,
                                     EQueueMode)
0x3f287d0 CMarketBuyResourceCommand(STradeData const&, int)
0x3f28a60 CMarketSellResourceCommand(STradeData const&, int)
```

These constructors accept semantic game objects. Transport identity, serials
and wire framing are not constructor inputs and remain owned by the submit
layer, matching the existing Execution Broker boundary.

## Confidence

### A. Confirmed by static and dynamic evidence

- the native AI submission call chain and game-thread execution boundary;
- 61 AI-origin command types reached both post and execute in this run;
- the listed final wrapper-to-payload correlations;
- worker candidate serialization is distinct from final game-thread posts;
- single-player native commands do not require a network-dispatch observation;
- selected constructor, writer, validator and execute symbols are available in
  the Linux ELF.

### B. High probability, requires focused live validation

- generic fleet-order and buildable wrappers can support several future Broker
  actions through one shared construction/submission implementation;
- `CScriptedFleetOrder` or one of its nested actions may carry direct attack
  intent;
- the same `PostCommandToSession` path will enter multiplayer synchronization
  automatically when a session requires it.

Validate each B item by correlating one final game-thread post with its complete
serializer fields, a visible before/after game predicate and, for multiplayer,
host/client convergence without OOS.

### C. Not established by this run

- a production-safe direct attack constructor and target layout;
- field semantics inferred solely from object-head bytes;
- cross-version signatures, especially Stellaris 4.5;
- multiplayer host authority behavior for every captured command;
- safety of executing any candidate-only order.

## Reproduction

After loading both research probes and enabling `human_ai`, run:

```bash
python3 services/stellaris_native_runtime/research/analyze_capture.py \
  --commands /tmp/iag-stellaris-command-probe.jsonl \
  --payloads /tmp/iag-stellaris-payload-probe.jsonl \
  --require-zero-drops \
  --top 0
```

The analyzer infers the game thread, reports probe health, identifies commands
whose first-seen stack contains the AI submission frame, correlates final
wrapper payloads and lists worker-only candidates separately.
