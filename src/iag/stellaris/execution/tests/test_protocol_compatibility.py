from __future__ import annotations

import unittest

from iag.stellaris.execution.protocol_compatibility import (
    COMMAND_SPECS,
    OFFLINE_FIXTURE_TARGETS,
    SUPPORTED_SESSION_PROXY_ACTIONS,
    classify_network_result,
    new_plan_document,
    validate_plan_document,
)
from iag.stellaris.execution.session_proxy import build_action_probe


class ProtocolCompatibilityTests(unittest.TestCase):
    def test_every_supported_action_has_one_offline_fixture(self) -> None:
        self.assertEqual(
            set(OFFLINE_FIXTURE_TARGETS),
            set(SUPPORTED_SESSION_PROXY_ACTIONS),
        )
        for spec in COMMAND_SPECS:
            with self.subTest(action=spec.action):
                result = build_action_probe(
                    action=spec.action,
                    target=OFFLINE_FIXTURE_TARGETS[spec.action],
                )
                self.assertEqual(result["wire_family_hex"], spec.wire_family_hex)
                self.assertGreater(result["record_length"], 0)
                self.assertGreater(result["inserted_length"], result["record_length"])

    def test_new_plan_disables_every_mutating_action(self) -> None:
        plan = new_plan_document(game_version="4.4.6")
        validated = validate_plan_document(plan)
        self.assertEqual(
            [item["action"] for item in validated["scenarios"]],
            list(SUPPORTED_SESSION_PROXY_ACTIONS),
        )
        self.assertFalse(any(item["enabled"] for item in validated["scenarios"]))

    def test_live_plan_requires_per_scenario_side_effect_acknowledgement(self) -> None:
        plan = new_plan_document(game_version="4.4.6")
        plan["scenarios"][0]["enabled"] = True
        with self.assertRaisesRegex(ValueError, "side effects"):
            validate_plan_document(plan, require_live_acknowledgements=True)
        plan["scenarios"][0]["acknowledge_side_effects"] = True
        validate_plan_document(plan, require_live_acknowledgements=True)

    def test_timeout_stage_distinguishes_ack_from_response_match(self) -> None:
        self.assertEqual(
            classify_network_result(
                {
                    "outcome": "response_timeout",
                    "host_acknowledged_inserted_bytes": True,
                    "response_retagged": False,
                }
            ),
            "authoritative_response_not_matched",
        )
        self.assertEqual(
            classify_network_result(
                {
                    "outcome": "response_timeout",
                    "host_acknowledged_inserted_bytes": False,
                    "response_retagged": False,
                }
            ),
            "host_ack_or_authoritative_response_missing",
        )

    def test_selected_fleet_reinforcement_is_one_catalog_action(self) -> None:
        self.assertIn("reinforce_selected_fleet", SUPPORTED_SESSION_PROXY_ACTIONS)
        self.assertNotIn("reinforce_fleet_stage_1", SUPPORTED_SESSION_PROXY_ACTIONS)
        self.assertNotIn("reinforce_fleet_stage_2", SUPPORTED_SESSION_PROXY_ACTIONS)


if __name__ == "__main__":
    unittest.main()
