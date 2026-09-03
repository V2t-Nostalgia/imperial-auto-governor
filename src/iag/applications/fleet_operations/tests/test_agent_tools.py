from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iag.applications.fleet_operations.agent_tools import (
    FLEET_PERMISSIONS_KEY,
    PENDING_NEW_FLEET_KEY,
    FleetToolbox,
    FleetToolError,
)
from iag.core.conversation_store import ConversationStore


def fleet_profile() -> dict[str, object]:
    return {
        "schema": "iag.stellaris_fleet_state.v1",
        "schema_version": 1,
        "game_date": "2204.09.15",
        "owner_country_id": 0,
        "player_country_ids": [0],
        "player_ship_design_ids": [100],
        "fleet_templates": [
            {
                "fleet_template_id": 17,
                "fleet_id": 7,
                "design_targets": [
                    {
                        "design_id": 100,
                        "upgrade_id": 4294967295,
                        "growth_stage": 0,
                        "target_count": 1,
                    }
                ],
                "queued_item_handles": [],
            }
        ],
        "fleets": [
            {
                "fleet_id": 7,
                "name_key": "HUMAN1_FLEET_1",
                "display_name_hint": "HUMAN1_FLEET 1",
                "player_controllable": True,
                "ship_class": "shipclass_military",
                "fleet_template_id": 17,
                "fleet_composition": [
                    {
                        "design_id": 100,
                        "upgrade_id": 4294967295,
                        "growth_stage": 0,
                        "target_count": 1,
                        "current_count": 1,
                        "missing_count": 0,
                    }
                ],
                "reinforcement_queue_item_handles": [],
                "d32c_move_verified_family": True,
                "coordinate_move_verified_family": True,
                "ai_callable_now": True,
                "availability": "AVAILABLE",
                "movement": {
                    "current_system_id": 10,
                    "current_coordinate": {"x": 0.0, "y": 0.0, "origin": 10},
                },
            }
        ],
        "systems": [
            {
                "system_id": 20,
                "name_key": "NAME_Alpha_Centauri",
                "move_destination": {
                    "destination_tag_hex": "0c3a01001400",
                    "destination_object": 118,
                },
            }
        ],
    }


class FleetToolboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.save = self.root / "test.sav"
        self.save.write_bytes(b"test")
        self.store = ConversationStore(self.root / "conversation.sqlite3")
        self.config = {
            "runtime_root": str(self.root),
            "execution_mode": "session_proxy",
            "experimental_fleet_tools_enabled": True,
            "experimental_fleet_attack_enabled": True,
            "experimental_fleet_coordinate_tools_enabled": True,
            "fleet_coordinate_max_abs": 1000,
            "experimental_fleet_reinforcement_tools_enabled": True,
            "maximum_fleet_reinforcement_increase": 5,
            "experimental_new_fleet_tools_enabled": True,
            "maximum_new_fleet_initial_ships": 5,
        }

    def toolbox(self) -> FleetToolbox:
        value = FleetToolbox(self.config, self.store, allow_execute=True)
        value._profile = lambda: (self.save, fleet_profile())  # type: ignore[method-assign]
        return value

    def test_new_fleet_is_denied_until_player_grants_permission(self) -> None:
        toolbox = self.toolbox()
        with self.assertRaisesRegex(FleetToolError, "没有授权"):
            toolbox.prepare_move(
                {
                    "fleet_id": 7,
                    "destination_system_id": 20,
                    "reason": "Move to the frontier.",
                }
            )

    def test_prepare_and_execute_revalidate_permission(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_move": True, "allow_attack": False}},
        )
        toolbox = self.toolbox()
        prepared = toolbox.prepare_move(
            {
                "fleet_id": 7,
                "destination_system_id": 20,
                "reason": "Move to the frontier.",
            }
        )
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_move": False, "allow_attack": False}},
        )
        with self.assertRaisesRegex(FleetToolError, "撤销"):
            toolbox.execute({"run_id": prepared["run_id"]})

    def test_confirmed_move_records_machine_fact(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_move": True, "allow_attack": False}},
        )
        toolbox = self.toolbox()
        prepared = toolbox.prepare_move(
            {
                "fleet_id": 7,
                "destination_system_id": 20,
                "reason": "Move to the frontier.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "request_id": prepared["run_id"],
                "outcome": "confirmed",
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertTrue(result["success"])
        self.assertEqual(
            self.store.get_state("last_fleet_execution")["fleet_id"],
            7,
        )

    def test_attack_permission_does_not_bypass_missing_protocol_pair(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_move": True, "allow_attack": True}},
        )
        with self.assertRaisesRegex(FleetToolError, "成对样本"):
            self.toolbox().prepare_attack(
                {"fleet_id": 7, "target_fleet_id": 99, "reason": "Intercept."}
            )

    def test_confirmed_coordinate_move_uses_4f2c_action(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_move": True, "allow_attack": False}},
        )
        toolbox = self.toolbox()
        prepared = toolbox.prepare_coordinate_move(
            {
                "fleet_id": 7,
                "x": 23.4744,
                "y": -299.04174,
                "reason": "Take position inside the system.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertEqual(result["action"], "move_fleet_to_coordinate")
        call = controller_type.return_value.arm_and_wait.call_args
        self.assertEqual(call.kwargs["action"], "move_fleet_to_coordinate")
        self.assertEqual(call.kwargs["target"]["system_origin"], 10)

    def test_reinforcement_updates_template_then_requests_reinforcement(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {
                "7": {
                    "allow_move": False,
                    "allow_attack": False,
                    "allow_reinforce": True,
                }
            },
        )
        toolbox = self.toolbox()
        prepared = toolbox.prepare_fleet_reinforcement(
            {
                "fleet_id": 7,
                "design_id": 100,
                "target_count": 4,
                "reason": "Increase First Fleet by three corvettes.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute_ship_action({"run_id": prepared["run_id"]})

        actions = [
            call.kwargs["action"]
            for call in controller_type.return_value.arm_and_wait.call_args_list
        ]
        self.assertEqual(
            actions,
            [
                "add_fleet_template_ship",
                "add_fleet_template_ship",
                "add_fleet_template_ship",
                "reinforce_fleet_stage_1",
                "reinforce_fleet_stage_2",
            ],
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["confirmed_target_increase"], 3)
        self.assertTrue(result["reinforcement_request_required"])
        self.assertTrue(result["reinforcement_request_confirmed"])
        self.assertEqual(result["ships_completed"], 0)
        self.assertTrue(result["awaiting_fresh_save_confirmation"])

    def test_template_increase_skips_reinforcement_when_fleet_meets_target(
        self,
    ) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_reinforce": True}},
        )
        profile = fleet_profile()
        profile["fleets"][0]["fleet_composition"][0]["current_count"] = 2
        toolbox = self.toolbox()
        toolbox._profile = lambda: (self.save, profile)  # type: ignore[method-assign]
        prepared = toolbox.prepare_fleet_reinforcement(
            {
                "fleet_id": 7,
                "design_id": 100,
                "target_count": 2,
                "reason": "Align the template with the existing fleet.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute_ship_action({"run_id": prepared["run_id"]})

        actions = [
            call.kwargs["action"]
            for call in controller_type.return_value.arm_and_wait.call_args_list
        ]
        self.assertEqual(actions, ["add_fleet_template_ship"])
        self.assertTrue(result["success"])
        self.assertFalse(result["reinforcement_request_required"])
        self.assertFalse(result["reinforcement_request_confirmed"])

    def test_new_fleet_waits_for_save_id_then_configures_template(self) -> None:
        initial = fleet_profile()
        toolbox = self.toolbox()
        toolbox._profile = lambda: (self.save, initial)  # type: ignore[method-assign]
        prepared = toolbox.prepare_new_fleet(
            {
                "design_id": 100,
                "target_count": 3,
                "reason": "Create a three-corvette patrol fleet.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            created = toolbox.execute_ship_action({"run_id": prepared["run_id"]})
        self.assertTrue(created["awaiting_fresh_save_template_id"])
        create_call = controller_type.return_value.arm_and_wait.call_args
        self.assertEqual(create_call.kwargs["action"], "create_fleet_template")
        self.assertEqual(create_call.kwargs["target"], {"context_822c": 0})
        self.assertEqual(
            self.store.get_state(PENDING_NEW_FLEET_KEY)["phase"],
            "awaiting_template_save",
        )

        fresh = fleet_profile()
        fresh["fleet_templates"].append(
            {
                "fleet_template_id": 177,
                "fleet_id": None,
                "design_targets": [],
                "queued_item_handles": [],
            }
        )
        self.save.write_bytes(b"fresh-save")
        continuation = FleetToolbox(
            self.config,
            self.store,
            allow_execute=True,
        )
        continuation._profile = lambda: (self.save, fresh)  # type: ignore[method-assign]
        schema_names = {
            item["function"]["name"] for item in continuation.schemas()
        }
        self.assertNotIn("prepare_new_fleet_configuration", schema_names)

        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = continuation.continue_pending_new_fleet_from_save()
        actions = [
            call.kwargs["action"]
            for call in controller_type.return_value.arm_and_wait.call_args_list
        ]
        self.assertEqual(
            actions,
            [
                "add_fleet_template_ship",
                "add_fleet_template_ship",
                "add_fleet_template_ship",
                "reinforce_fleet_stage_1",
                "reinforce_fleet_stage_2",
            ],
        )
        self.assertEqual(result["state"], "executed")
        self.assertTrue(result["mutated_game"])
        self.assertEqual(result["fleet_template_id"], 177)
        self.assertEqual(
            self.store.get_state(PENDING_NEW_FLEET_KEY)["phase"],
            "awaiting_fleet_save",
        )

        fresh["fleet_templates"][-1]["design_targets"] = [
            {
                "design_id": 100,
                "upgrade_id": 4294967295,
                "growth_stage": 0,
                "target_count": 3,
            }
        ]
        self.save.write_bytes(b"confirmation-save")
        completion = continuation._pending_new_fleet_status()
        assert completion is not None
        self.assertEqual(completion["phase"], "confirmed_in_save")
        self.assertIsNone(self.store.get_state(PENDING_NEW_FLEET_KEY))
        self.assertEqual(completion["ships_completed"], 0)


if __name__ == "__main__":
    unittest.main()
