from __future__ import annotations

import json
import threading
import time
import unittest
import uuid

from iag.stellaris.execution.action_registry import (
    ActionRisk,
    AttackFleetTarget,
    BackendId,
    MoveFleetTarget,
    ResearchTarget,
    builtin_action_registry,
)
from iag.stellaris.execution.broker import (
    BackendExecutionResult,
    BackendSequenceResult,
    CandidateIdentity,
    ExecutionAuthority,
    ExecutionBroker,
    IdempotencyConflict,
    NativeRuntimeBackend,
    OrderedActionSequence,
    PreparedAction,
    SessionProxyBackend,
    SourceSnapshot,
    _merge_runtime_assignments,
    _native_registry_overlay,
)
from iag.stellaris.execution.native_runtime import (
    NATIVE_RUNTIME_BUILD_ID,
    NativeRuntimeManifest,
    NativeRuntimeOutcome,
    NativeRuntimeResponse,
)


class RecordingBackend:
    backend_id = BackendId.SESSION_PROXY

    def __init__(self) -> None:
        self.calls = 0
        self.active = 0
        self.maximum_active = 0
        self._lock = threading.Lock()

    def capabilities(self) -> tuple[str, ...]:
        return ("stellaris.execution.v1",)

    def supports(self, spec: object) -> bool:
        return getattr(spec, "action_type", None) in {
            "start_research",
            "stop_research",
        }

    def execute(self, action: PreparedAction) -> BackendExecutionResult:
        with self._lock:
            self.calls += 1
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        time.sleep(0.02)
        with self._lock:
            self.active -= 1
        return BackendExecutionResult(
            backend_id=self.backend_id,
            status="confirmed_by_packet",
            evidence={"machine_confirmation": True},
        )

    def execute_ordered_sequence(
        self,
        sequence: OrderedActionSequence,
    ) -> BackendSequenceResult:
        return BackendSequenceResult(
            backend_id=self.backend_id,
            requested_steps=len(sequence.actions),
            results=tuple(self.execute(action) for action in sequence.actions),
        )


class FakeProxyController:
    def __init__(self, _config: dict[str, object]) -> None:
        self.steps: list[dict[str, object]] = []
        self.request_id: str | None = None

    def arm_and_wait(self, **_kwargs: object) -> dict[str, object]:
        return {"outcome": "confirmed", "response_retagged": True}

    def arm_sequence_and_wait(
        self,
        *,
        steps: list[dict[str, object]],
        request_id: str,
    ) -> dict[str, object]:
        self.steps = steps
        self.request_id = request_id
        return {
            "outcome": "confirmed",
            "results": [
                {"outcome": "confirmed", "response_retagged": True} for _step in steps
            ],
        }


class FakeNativeClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.calls: list[dict[str, object]] = []
        self._manifest = NativeRuntimeManifest.model_validate_json(
            json.dumps(
                {
                "schema_version": "iag.native_tool_manifest.v1",
                "platform": "linux-x86_64",
                "game_version": "Stellaris 4.4.6 (fdde)",
                "build_id": NATIVE_RUNTIME_BUILD_ID,
                "tools": [
                    {
                        "action_type": "move_fleet",
                        "action_version": 1,
                        "application_id": "fleet_operations",
                        "description": "Move one fleet.",
                        "risk_class": "state_change",
                        "verification_state": "live_verified",
                        "parameters": [
                            {
                                "name": "fleet_id",
                                "type": "uint32",
                                "required": True,
                                "description": "Fleet id.",
                                "minimum": 0,
                                "maximum": 0xFFFFFFFF,
                            },
                            {
                                "name": "destination_system_id",
                                "type": "uint32",
                                "required": True,
                                "description": "System id.",
                                "minimum": 0,
                                "maximum": 0xFFFFFFFF,
                            },
                        ],
                    },
                    {
                        "action_type": "attack_fleet",
                        "action_version": 1,
                        "application_id": "fleet_operations",
                        "description": "Attack one fleet.",
                        "risk_class": "destructive_state_change",
                        "verification_state": "live_verified",
                        "parameters": [
                            {
                                "name": "fleet_id",
                                "type": "uint32",
                                "required": True,
                                "description": "Fleet id.",
                                "minimum": 0,
                                "maximum": 0xFFFFFFFF,
                            },
                            {
                                "name": "target_fleet_id",
                                "type": "uint32",
                                "required": True,
                                "description": "Target fleet id.",
                                "minimum": 0,
                                "maximum": 0xFFFFFFFF,
                            },
                        ],
                    },
                    {
                        "action_type": "stop_research",
                        "action_version": 1,
                        "application_id": "research_strategy",
                        "description": "Stop research.",
                        "risk_class": "destructive_state_change",
                        "verification_state": "live_verified",
                        "parameters": [
                            {
                                "name": "technology_id",
                                "type": "string",
                                "required": True,
                                "description": "Technology id.",
                                "maximum_length": 160,
                            }
                        ],
                    },
                ],
                }
            )
        )

    def available(self) -> bool:
        return True

    def ensure_available(self) -> bool:
        return True

    def describe_tools(self, *, refresh: bool = False) -> NativeRuntimeManifest:
        del refresh
        return self._manifest

    def execute(self, **kwargs: object) -> NativeRuntimeResponse:
        self.calls.append(dict(kwargs))
        action = str(kwargs["action_type"])
        target = dict(kwargs["target"])  # type: ignore[arg-type]
        if action == "move_fleet":
            target_echo = (
                f"{int(target['fleet_id'])}:"
                f"{int(target['destination_system_id'])}"
            )
            detail = "native_postcondition_player_movement_order_present"
        elif action == "attack_fleet":
            target_echo = (
                f"{int(target['fleet_id'])}:{int(target['target_fleet_id'])}"
            )
            detail = "native_postcondition_attack_order_matches_target"
        else:
            target_echo = str(target["technology_id"])
            detail = "native_postcondition_not_researching"
        return NativeRuntimeResponse(
            request_id=str(kwargs["request_id"]),
            action=action,
            outcome=NativeRuntimeOutcome.CONFIRMED,
            build_id=NATIVE_RUNTIME_BUILD_ID,
            country_id=int(kwargs["country_id"]),
            target_echo=target_echo,
            detail=detail,
        )



class ExecutionBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = RecordingBackend()
        self.broker = ExecutionBroker(
            backends=(self.backend,),
            application_action_types={
                "research_strategy": frozenset({"start_research", "stop_research"})
            },
            snapshot_validator=lambda _snapshot: None,
            candidate_validator=lambda _action: None,
        )
        self.prefix = uuid.uuid4().hex

    def action(
        self,
        suffix: str,
        *,
        technology_id: str = "tech_shields_2",
        application_id: str = "research_strategy",
    ) -> PreparedAction:
        target = ResearchTarget(
            area="physics",
            technology_id=technology_id,
        )
        return PreparedAction(
            request_id=f"{self.prefix}:{suffix}",
            action_intent_id=f"intent:{self.prefix}",
            application_id=application_id,
            action_type="start_research",
            target=target,
            candidate=CandidateIdentity.for_target(
                f"candidate:{technology_id}",
                target,
            ),
            source_snapshot=SourceSnapshot(
                campaign_id="campaign-test",
                revision=7,
                save_sha256="a" * 64,
                game_date="2204.09.15",
            ),
            authority=ExecutionAuthority(
                application_id=application_id,
                execution_authorized=True,
                granted_action_types=("start_research",),
                allowed_risk_classes=(ActionRisk.STATE_CHANGE,),
                actor_country_id=0,
            ),
        )

    def test_normalizes_backend_result_and_suppresses_identical_replay(self) -> None:
        action = self.action("same")
        first = self.broker.execute(action)
        second = self.broker.execute(action)

        self.assertEqual(first.status, "confirmed_by_packet")
        self.assertEqual(first, second)
        self.assertEqual(self.backend.calls, 1)
        self.assertEqual(first.evidence["backend_id"], "session_proxy")

    def test_reusing_request_id_for_different_target_is_rejected(self) -> None:
        self.broker.execute(self.action("collision"))
        with self.assertRaises(IdempotencyConflict):
            self.broker.execute(self.action("collision", technology_id="tech_lasers_2"))

    def test_ordered_sequence_replay_returns_cached_result(self) -> None:
        sequence = OrderedActionSequence(
            request_id=f"{self.prefix}:ordered",
            actions=(self.action("ordered:1"),),
        )
        first = self.broker.execute_sequence(sequence)
        second = self.broker.execute_sequence(sequence)

        self.assertEqual(first, second)
        self.assertEqual(first.status, "confirmed_by_packet")
        self.assertEqual(self.backend.calls, 1)

    def test_application_permission_and_snapshot_guard_fail_closed(self) -> None:
        denied = self.broker.execute(
            self.action("denied", application_id="fleet_operations")
        )
        self.assertEqual(denied.status, "rejected")
        self.assertEqual(self.backend.calls, 0)

        stale = ExecutionBroker(
            backends=(self.backend,),
            application_action_types={
                "research_strategy": frozenset({"start_research"})
            },
            snapshot_validator=lambda _snapshot: (_ for _ in ()).throw(
                RuntimeError("stale snapshot")
            ),
            candidate_validator=lambda _action: None,
        ).execute(self.action("stale"))
        self.assertEqual(stale.status, "rejected")
        self.assertIn("stale snapshot", stale.evidence["error"])
        self.assertEqual(self.backend.calls, 0)

        invalid_candidate = ExecutionBroker(
            backends=(self.backend,),
            application_action_types={
                "research_strategy": frozenset({"start_research"})
            },
            snapshot_validator=lambda _snapshot: None,
            candidate_validator=lambda _action: (_ for _ in ()).throw(
                RuntimeError("candidate disappeared")
            ),
        ).execute(self.action("candidate"))
        self.assertEqual(invalid_candidate.status, "rejected")
        self.assertIn("candidate disappeared", invalid_candidate.evidence["error"])
        self.assertEqual(self.backend.calls, 0)

    def test_side_effects_are_serialized_across_broker_instances(self) -> None:
        second_broker = ExecutionBroker(
            backends=(self.backend,),
            application_action_types={
                "research_strategy": frozenset({"start_research"})
            },
            snapshot_validator=lambda _snapshot: None,
            candidate_validator=lambda _action: None,
        )
        threads = [
            threading.Thread(target=broker.execute, args=(self.action(suffix),))
            for broker, suffix in (
                (self.broker, "thread-a"),
                (second_broker, "thread-b"),
            )
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(self.backend.maximum_active, 1)
        self.assertEqual(self.backend.calls, 2)

    def test_session_proxy_backend_resolves_private_research_fields(self) -> None:
        controller = FakeProxyController({})
        backend = SessionProxyBackend(
            {},
            controller_factory=lambda _config: controller,  # type: ignore[arg-type]
        )
        broker = ExecutionBroker(
            backends=(backend,),
            application_action_types={
                "research_strategy": frozenset({"start_research"})
            },
            snapshot_validator=lambda _snapshot: None,
            candidate_validator=lambda _action: None,
        )
        action = self.action("proxy")
        result = broker.execute_sequence(
            OrderedActionSequence(
                request_id=f"{self.prefix}:proxy-sequence",
                actions=(action,),
            )
        )

        self.assertEqual(result.status, "confirmed_by_packet")
        self.assertEqual(controller.request_id, f"{self.prefix}:proxy-sequence")
        self.assertEqual(
            controller.steps,
            [
                {
                    "action": "start_research",
                    "target": {
                        "context_822c": 0,
                        "technology_id": "tech_shields_2",
                    },
                }
            ],
        )
        self.assertNotIn(
            "context_822c",
            action.target.model_dump(mode="json"),
        )

    def test_native_runtime_backend_executes_verified_stop_research(self) -> None:
        client = FakeNativeClient()
        backend = NativeRuntimeBackend(
            {"native_runtime_enabled": True},
            client_factory=lambda *_args, **_kwargs: client,
        )
        target = ResearchTarget(
            area="physics",
            technology_id="tech_shields_2",
            expected_current_technology_id="tech_shields_2",
        )
        action = PreparedAction(
            request_id=f"{self.prefix}:native-stop",
            action_intent_id=f"intent:{self.prefix}",
            application_id="research_strategy",
            action_type="stop_research",
            target=target,
            candidate=CandidateIdentity.for_target(
                "research:physics:tech_shields_2", target
            ),
            source_snapshot=SourceSnapshot(
                campaign_id="campaign-test",
                revision=7,
                save_sha256="a" * 64,
            ),
            authority=ExecutionAuthority(
                application_id="research_strategy",
                execution_authorized=True,
                granted_action_types=("stop_research",),
                allowed_risk_classes=(ActionRisk.DESTRUCTIVE_STATE_CHANGE,),
                actor_country_id=7,
            ),
        )
        broker = ExecutionBroker(
            backends=(backend,),
            application_action_types={
                "research_strategy": frozenset({"stop_research"})
            },
            snapshot_validator=lambda _snapshot: None,
            candidate_validator=lambda _action: None,
        )

        result = broker.execute(action)

        self.assertEqual(result.status, "confirmed_by_packet")
        self.assertEqual(result.evidence["backend_id"], "native_runtime")
        self.assertEqual(
            result.evidence["backend_evidence"]["confirmation_kind"],
            "native_runtime_postcondition",
        )
        self.assertEqual(
            client.calls,
            [
                {
                    "request_id": f"{self.prefix}:native-stop",
                    "action_type": "stop_research",
                    "action_version": 1,
                    "country_id": 7,
                    "target": {
                        "area": "physics",
                        "technology_id": "tech_shields_2",
                        "expected_current_technology_id": "tech_shields_2",
                    },
                }
            ],
        )

    def test_native_runtime_backend_executes_verified_fleet_move(self) -> None:
        client = FakeNativeClient()
        backend = NativeRuntimeBackend(
            {"native_runtime_enabled": True},
            client_factory=lambda *_args, **_kwargs: client,
        )
        target = MoveFleetTarget(fleet_id=888, destination_system_id=47)
        action = PreparedAction(
            request_id=f"{self.prefix}:native-move",
            action_intent_id=f"intent:{self.prefix}",
            application_id="fleet_operations",
            action_type="move_fleet",
            target=target,
            candidate=CandidateIdentity.for_target("fleet:888:move:47", target),
            source_snapshot=SourceSnapshot(
                campaign_id="campaign-test",
                revision=7,
                save_sha256="a" * 64,
            ),
            authority=ExecutionAuthority(
                application_id="fleet_operations",
                execution_authorized=True,
                granted_action_types=("move_fleet",),
                allowed_risk_classes=(ActionRisk.STATE_CHANGE,),
                actor_country_id=0,
            ),
        )
        broker = ExecutionBroker(
            backends=(backend,),
            application_action_types={"fleet_operations": frozenset({"move_fleet"})},
            snapshot_validator=lambda _snapshot: None,
            candidate_validator=lambda _action: None,
        )

        result = broker.execute(action)

        self.assertEqual(result.status, "confirmed_by_packet")
        self.assertEqual(result.evidence["backend_id"], "native_runtime")
        self.assertEqual(
            client.calls,
            [
                {
                    "request_id": f"{self.prefix}:native-move",
                    "action_type": "move_fleet",
                    "action_version": 1,
                    "country_id": 0,
                    "target": {
                        "fleet_id": 888,
                        "destination_system_id": 47,
                    },
                }
            ],
        )
        catalog = broker.capabilities().tools
        move_tool = next(item for item in catalog if item.action_type == "move_fleet")
        self.assertTrue(move_tool.execution_ready)
        self.assertEqual(move_tool.application_id, "fleet_operations")
        self.assertEqual(
            tuple(item.name for item in move_tool.parameters),
            ("fleet_id", "destination_system_id"),
        )

    def test_unknown_runtime_tool_gets_strict_etc_semantic_contract(self) -> None:
        manifest = NativeRuntimeManifest.model_validate_json(
            json.dumps(
                {
                    "schema_version": "iag.native_tool_manifest.v1",
                    "platform": "linux-x86_64",
                    "game_version": "Stellaris 4.4.6",
                    "build_id": NATIVE_RUNTIME_BUILD_ID,
                    "tools": [
                        {
                            "action_type": "experimental_toggle",
                            "action_version": 1,
                            "application_id": "etc",
                            "description": "Contributor example action.",
                            "risk_class": "state_change",
                            "verification_state": "live_verified",
                            "parameters": [
                                {
                                    "name": "object_id",
                                    "type": "uint32",
                                    "required": True,
                                    "description": "Snapshot object id.",
                                    "minimum": 0,
                                    "maximum": 0xFFFFFFFF,
                                },
                                {
                                    "name": "enabled",
                                    "type": "boolean",
                                    "required": True,
                                    "description": "Desired state.",
                                },
                            ],
                        }
                    ],
                }
            )
        )

        registry = _native_registry_overlay(builtin_action_registry(), manifest)
        spec = registry.get("experimental_toggle")
        target = spec.validate_target({"object_id": 42, "enabled": True})

        self.assertEqual(spec.risk_class, ActionRisk.STATE_CHANGE)
        self.assertEqual(
            spec.broker_enabled_backends,
            (BackendId.NATIVE_RUNTIME,),
        )
        self.assertEqual(target.model_dump(mode="json"), {"object_id": 42, "enabled": True})
        with self.assertRaises(ValueError):
            spec.validate_target({"object_id": -1, "enabled": True})

    def test_runtime_cannot_reassign_reviewed_action_to_etc(self) -> None:
        manifest = NativeRuntimeManifest.model_validate_json(
            json.dumps(
                {
                    "schema_version": "iag.native_tool_manifest.v1",
                    "platform": "linux-x86_64",
                    "game_version": "Stellaris 4.4.6",
                    "build_id": NATIVE_RUNTIME_BUILD_ID,
                    "tools": [
                        {
                            "action_type": "move_fleet",
                            "action_version": 1,
                            "application_id": "etc",
                            "description": "Mismatched lower-layer owner.",
                            "risk_class": "state_change",
                            "verification_state": "live_verified",
                            "parameters": [
                                {
                                    "name": "fleet_id",
                                    "type": "uint32",
                                    "required": True,
                                    "description": "Fleet id.",
                                    "minimum": 0,
                                    "maximum": 0xFFFFFFFF,
                                },
                                {
                                    "name": "destination_system_id",
                                    "type": "uint32",
                                    "required": True,
                                    "description": "System id.",
                                    "minimum": 0,
                                    "maximum": 0xFFFFFFFF,
                                },
                            ],
                        }
                    ],
                }
            )
        )
        assignments = {
            "fleet_operations": {"move_fleet"},
            "etc": set(),
        }

        _merge_runtime_assignments(
            builtin_action_registry(), assignments, manifest
        )

        self.assertEqual(assignments["etc"], set())
        self.assertEqual(assignments["fleet_operations"], {"move_fleet"})

    def test_native_runtime_backend_resolves_semantic_fleet_attack(self) -> None:
        client = FakeNativeClient()
        backend = NativeRuntimeBackend(
            {"native_runtime_enabled": True},
            client_factory=lambda *_args, **_kwargs: client,
        )
        target = AttackFleetTarget(fleet_id=888, target_fleet_id=220)
        action = PreparedAction(
            request_id=f"{self.prefix}:native-attack",
            action_intent_id=f"intent:{self.prefix}",
            application_id="fleet_operations",
            action_type="attack_fleet",
            target=target,
            candidate=CandidateIdentity.for_target("fleet:888:attack:220", target),
            source_snapshot=SourceSnapshot(
                campaign_id="campaign-test",
                revision=7,
                save_sha256="a" * 64,
            ),
            authority=ExecutionAuthority(
                application_id="fleet_operations",
                execution_authorized=True,
                granted_action_types=("attack_fleet",),
                allowed_risk_classes=(ActionRisk.DESTRUCTIVE_STATE_CHANGE,),
                actor_country_id=0,
            ),
        )
        broker = ExecutionBroker(
            backends=(backend,),
            application_action_types={
                "fleet_operations": frozenset({"attack_fleet"})
            },
            snapshot_validator=lambda _snapshot: None,
            candidate_validator=lambda _action: None,
        )

        result = broker.execute(action)

        self.assertEqual(result.status, "confirmed_by_packet")
        self.assertEqual(result.evidence["backend_id"], "native_runtime")
        self.assertEqual(
            client.calls,
            [
                {
                    "request_id": f"{self.prefix}:native-attack",
                    "action_type": "attack_fleet",
                    "action_version": 1,
                    "country_id": 0,
                    "target": {
                        "fleet_id": 888,
                        "target_fleet_id": 220,
                    },
                }
            ],
        )

    def test_mixed_research_sequence_falls_back_to_common_proxy_backend(self) -> None:
        client = FakeNativeClient()
        native = NativeRuntimeBackend(
            {"native_runtime_enabled": True},
            client_factory=lambda *_args, **_kwargs: client,
        )
        proxy = RecordingBackend()
        broker = ExecutionBroker(
            backends=(native, proxy),
            application_action_types={
                "research_strategy": frozenset({"start_research", "stop_research"})
            },
            snapshot_validator=lambda _snapshot: None,
            candidate_validator=lambda _action: None,
        )
        snapshot = SourceSnapshot(
            campaign_id="campaign-test",
            revision=7,
            save_sha256="a" * 64,
        )
        authority = ExecutionAuthority(
            application_id="research_strategy",
            execution_authorized=True,
            granted_action_types=("start_research", "stop_research"),
            allowed_risk_classes=(
                ActionRisk.STATE_CHANGE,
                ActionRisk.DESTRUCTIVE_STATE_CHANGE,
            ),
            actor_country_id=7,
        )

        def action(action_type: str, technology_id: str) -> PreparedAction:
            target = ResearchTarget(
                area="physics",
                technology_id=technology_id,
                expected_current_technology_id="tech_shields_2",
            )
            return PreparedAction(
                request_id=f"{self.prefix}:{action_type}",
                action_intent_id=f"intent:{self.prefix}",
                application_id="research_strategy",
                action_type=action_type,
                target=target,
                candidate=CandidateIdentity.for_target(
                    f"research:physics:{technology_id}", target
                ),
                source_snapshot=snapshot,
                authority=authority,
            )

        result = broker.execute_sequence(
            OrderedActionSequence(
                request_id=f"{self.prefix}:mixed-sequence",
                actions=(
                    action("stop_research", "tech_shields_2"),
                    action("start_research", "tech_lasers_2"),
                ),
            )
        )

        self.assertEqual(result.status, "confirmed_by_packet")
        self.assertEqual(proxy.calls, 2)
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
