from __future__ import annotations

import unittest

from pydantic import ValidationError

from iag.applications.registry import builtin_application_registry
from iag.stellaris.execution.action_registry import (
    BackendId,
    MoveFleetTarget,
    ResearchTarget,
    builtin_action_registry,
)
from iag.stellaris.execution.protocol_compatibility import COMMAND_SPECS


class ActionRegistryTests(unittest.TestCase):
    def test_every_protocol_and_application_action_is_registered(self) -> None:
        registry = builtin_action_registry()
        registered = {spec.action_type for spec in registry.all()}
        protocol_actions = {spec.action for spec in COMMAND_SPECS}
        application_actions = {
            action
            for manifest in builtin_application_registry().all()
            for action in manifest.action_types
        }
        self.assertTrue(protocol_actions <= registered)
        self.assertTrue(application_actions <= registered)

    def test_protocol_risk_and_verification_mirror_semantic_registry(self) -> None:
        registry = builtin_action_registry()
        for protocol_spec in COMMAND_SPECS:
            semantic = registry.get(protocol_spec.action)
            self.assertEqual(protocol_spec.risk, semantic.risk_class.value)
            self.assertEqual(
                protocol_spec.verification_state,
                semantic.verification_for(BackendId.SESSION_PROXY).value,
            )

    def test_verification_is_tracked_per_backend(self) -> None:
        stop = builtin_action_registry().get("stop_research")
        move = builtin_action_registry().get("move_fleet")
        attack = builtin_action_registry().get("attack_fleet")

        self.assertEqual(stop.verification_state.value, "live_verified")
        self.assertEqual(
            stop.verification_for(BackendId.SESSION_PROXY).value,
            "paired_capture",
        )
        self.assertEqual(
            stop.verification_for(BackendId.NATIVE_RUNTIME).value,
            "live_verified",
        )
        self.assertEqual(
            move.verification_for(BackendId.SESSION_PROXY).value,
            "live_verified",
        )
        self.assertEqual(
            move.verification_for(BackendId.NATIVE_RUNTIME).value,
            "live_verified",
        )
        self.assertEqual(attack.verification_state.value, "live_verified")
        self.assertEqual(
            attack.verification_for(BackendId.SESSION_PROXY).value,
            "paired_capture",
        )
        self.assertEqual(
            attack.verification_for(BackendId.NATIVE_RUNTIME).value,
            "live_verified",
        )

    def test_public_targets_are_strict_and_transport_independent(self) -> None:
        target = MoveFleetTarget(fleet_id=17, destination_system_id=42)
        self.assertEqual(
            target.model_dump(mode="json"),
            {"fleet_id": 17, "destination_system_id": 42},
        )
        with self.assertRaises(ValidationError):
            MoveFleetTarget.model_validate(
                {
                    "fleet_id": 17,
                    "destination_system_id": 42,
                    "destination_tag_hex": "0c3a01001400",
                }
            )
        with self.assertRaises(ValidationError):
            ResearchTarget.model_validate(
                {"area": "physics", "technology_id": "bad technology id"}
            )

        forbidden_fields = {
            "army_build_queue_id",
            "build_queue_id",
            "command_serial",
            "context_822c",
            "destination_object",
            "destination_tag_hex",
            "shipyard_build_queue_id",
            "source_fleet_object",
            "system_origin",
            "target_fleet_object",
            "wire_family",
            "x_fixed",
            "y_fixed",
        }
        for spec in builtin_action_registry().all():
            self.assertTrue(
                forbidden_fields.isdisjoint(spec.target_model.model_fields),
                msg=f"{spec.action_type} exposes a transport field",
            )


if __name__ == "__main__":
    unittest.main()
