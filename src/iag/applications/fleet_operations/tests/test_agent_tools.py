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
        "shipyards": [
            {
                "starbase_index": 12,
                "system_id": 20,
                "system_name_key": "NAME_Alpha_Centauri",
                "system_display_name_hint": "Alpha Centauri",
                "shipyard_build_queue_id": 46,
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
                "attack_verified_family": True,
                "repair_verified_family": True,
                "upgrade_verified_family": True,
                "maintenance_callable_now": True,
                "needs_repair": True,
                "upgradeable_ship_count": 1,
                "durability_summary": {
                    "hull": {"mean_percent": 80.0, "median_percent": 80.0},
                    "armor": {"mean_percent": 100.0, "median_percent": 100.0},
                    "shield": {"mean_percent": 100.0, "median_percent": 100.0},
                },
                "ai_callable_now": True,
                "availability": "AVAILABLE",
                "movement": {
                    "current_system_id": 10,
                    "current_coordinate": {"x": 0.0, "y": 0.0, "origin": 10},
                },
            },
            {
                "fleet_id": 8,
                "name_key": "SCIENCE_SHIP",
                "display_name_hint": "Science Ship",
                "player_controllable": True,
                "ship_class": "shipclass_science_ship",
                "military_power": 0,
                "automation_verified_family": True,
                "verified_automation_options": [
                    "AUTOMATION_EXPLORE",
                    "AUTOMATION_SURVEY",
                    "AUTOMATION_ASTRAL_RIFTS",
                    "AUTOMATION_SEND_GRAVITY_SNARES",
                ],
                "has_council_leader": False,
                "build_starbase_verified_family": False,
                "civilian_callable_now": True,
                "civilian_availability": "AVAILABLE",
                "ai_callable_now": False,
                "availability": "UNAVAILABLE",
                "movement": {"current_system_id": 10},
            },
            {
                "fleet_id": 9,
                "name_key": "CONSTRUCTION_SHIP",
                "display_name_hint": "Construction Ship",
                "player_controllable": True,
                "ship_class": "shipclass_constructor",
                "military_power": 0,
                "automation_verified_family": True,
                "build_starbase_verified_family": True,
                "civilian_callable_now": True,
                "civilian_availability": "AVAILABLE",
                "ai_callable_now": False,
                "availability": "UNAVAILABLE",
                "movement": {"current_system_id": 10},
            },
        ],
        "hostile_targets": [
            {
                "fleet_id": 99,
                "owner_country_id": 2,
                "name_key": "HOSTILE_FLEET",
                "display_name_hint": "Hostile Fleet",
                "military_power": 250,
                "system_id": 20,
                "target_authority": "player_hostile_intel_exact_live_match",
            }
        ],
        "systems": [
            {
                "system_id": 10,
                "name_key": "NAME_Sol",
                "hyperlane_neighbors": [20, 30],
                "move_destination": {
                    "destination_tag_hex": "132a01001400",
                    "destination_object": 0,
                },
            },
            {
                "system_id": 20,
                "name_key": "NAME_Alpha_Centauri",
                "hyperlane_neighbors": [10],
                "fully_surveyed_by_owner": True,
                "construction_target": {"target_system_object": 20},
                "move_destination": {
                    "destination_tag_hex": "0c3a01001400",
                    "destination_object": 118,
                },
            },
            {
                "system_id": 30,
                "name_key": "NAME_Barnards_Star",
                "hyperlane_neighbors": [10],
                "fully_surveyed_by_owner": True,
                "construction_target": {"target_system_object": 30},
                "move_destination": {
                    "destination_tag_hex": "132a01001400",
                    "destination_object": 300,
                },
            },
        ],
    }


def expansion_profile() -> dict[str, object]:
    return {
        "schema": "iag.stellaris_expansion_state.v1",
        "game_date": "2204.09.15",
        "colonization_candidates": [
            {
                "candidate_id": "colonize:200:from:shipyard-queue:46:design:100",
                "action": "order_colony_ship_and_colonize",
                "target_planet": {
                    "planet_id": 200,
                    "display_name_hint": "New Terra",
                    "system_id": 20,
                    "habitability": {"habitability": 0.8},
                },
                "source_shipyard": {
                    "starbase_index": 12,
                    "system_id": 20,
                    "shipyard_build_queue_id": 46,
                },
                "species_id": 1,
                "colonizer_design_id": 100,
                "verified_designations": ["col_city", "col_mining"],
                "target_template": {
                    "context_822c": 0,
                    "species_id": 1,
                    "design_id": 100,
                    "upgrade_id": 4294967295,
                    "growth_stage": 0,
                    "target_planet_id": 200,
                    "source_shipyard_build_queue_id": 46,
                    "system_name_key": "NAME_Alpha_Centauri",
                },
            }
        ],
        "starbase_operation_candidates": [
            {
                "candidate_id": "starbase:12:upgrade:starbase_level_starport",
                "action": "upgrade_starbase",
                "requires_replacement_permission": False,
                "target": {
                    "context_822c": 0,
                    "build_queue_id": 44,
                    "target_level": "starbase_level_starport",
                    "starbase_object": 12,
                },
            },
            {
                "candidate_id": "starbase:12:module:0:anchorage",
                "action": "set_starbase_module",
                "requires_replacement_permission": True,
                "target": {
                    "context_822c": 0,
                    "build_queue_id": 44,
                    "component_id": "anchorage",
                    "slot_index": 0,
                    "starbase_object": 12,
                },
            },
        ],
        "starbases": [{"starbase_index": 12, "system_id": 20}],
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
            "experimental_fleet_maintenance_tools_enabled": True,
            "experimental_fleet_coordinate_tools_enabled": True,
            "fleet_coordinate_max_abs": 1000,
            "experimental_fleet_reinforcement_tools_enabled": True,
            "maximum_fleet_reinforcement_increase": 5,
            "experimental_new_fleet_tools_enabled": True,
            "maximum_new_fleet_initial_ships": 5,
            "experimental_civilian_ship_tools_enabled": True,
            "experimental_colonization_tools_enabled": True,
            "minimum_colonization_habitability": 0.30,
            "experimental_starbase_tools_enabled": True,
            "experimental_starbase_replacement_enabled": False,
        }

    def toolbox(self) -> FleetToolbox:
        value = FleetToolbox(self.config, self.store, allow_execute=True)
        value._profile = lambda: (self.save, fleet_profile())  # type: ignore[method-assign]
        value._expansion_profile = lambda: (  # type: ignore[method-assign]
            self.save,
            expansion_profile(),
        )
        return value

    def test_enabled_capabilities_are_exposed_to_the_agent(self) -> None:
        schema_names = {
            item["function"]["name"] for item in self.toolbox().schemas()
        }
        self.assertTrue(
            {
                "inspect_fleet_state",
                "prepare_fleet_attack",
                "prepare_fleet_repair",
                "prepare_fleet_upgrade",
                "prepare_ship_automation",
                "prepare_construction_ship_starbase",
                "inspect_expansion_state",
                "prepare_colonization",
                "prepare_starbase_operation",
                "execute_prepared_fleet_order",
            }.issubset(schema_names)
        )
        self.assertEqual(
            len(schema_names),
            len(self.toolbox().schemas()),
            "Tool schemas must not contain duplicate function names.",
        )

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

    def test_inspect_summary_reports_actual_permission_state(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_move": True, "allow_attack": False}},
        )

        _result, summary = self.toolbox().dispatch("inspect_fleet_state", {})

        self.assertIn("1 支已获至少一项 AI 权限", summary)
        self.assertIn("1 支当前可调用", summary)
        self.assertNotIn("新舰队默认不允许", summary)

    def test_inspect_exposes_aggregate_durability_without_ship_ids(self) -> None:
        profile = fleet_profile()
        fleet = profile["fleets"][0]  # type: ignore[index]
        fleet["ship_ids"] = [1000, 1001, 1002]
        fleet["durability_summary"] = {
            "basis": "per_ship_current_divided_by_max_percent",
            "hull": {
                "mean_percent": 58.33,
                "median_percent": 50.0,
                "sample_count": 3,
            },
            "armor": {
                "mean_percent": 50.0,
                "median_percent": 50.0,
                "sample_count": 3,
            },
            "shield": {
                "mean_percent": 41.67,
                "median_percent": 25.0,
                "sample_count": 3,
            },
        }
        toolbox = self.toolbox()
        toolbox._profile = lambda: (self.save, profile)  # type: ignore[method-assign]

        result = toolbox.inspect()

        inspected = next(item for item in result["fleets"] if item["fleet_id"] == 7)
        self.assertNotIn("ship_ids", inspected)
        self.assertEqual(
            inspected["durability_summary"]["hull"]["median_percent"],
            50.0,
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
                "destination_system_id": 30,
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

    def test_confirmed_attack_uses_save_backed_hostile_target(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_move": True, "allow_attack": True}},
        )
        toolbox = self.toolbox()
        prepared = toolbox.prepare_attack(
            {"fleet_id": 7, "target_fleet_id": 99, "reason": "Intercept."}
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertEqual(result["action"], "attack_fleet")
        call = controller_type.return_value.arm_and_wait.call_args
        self.assertEqual(call.kwargs["action"], "attack_fleet")
        self.assertEqual(
            call.kwargs["target"],
            {"source_fleet_object": 7, "target_fleet_object": 99},
        )

    def test_repair_and_upgrade_use_save_backed_fleet_and_shipyard(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_repair": True, "allow_upgrade": True}},
        )
        toolbox = self.toolbox()
        prepared = toolbox.prepare_fleet_repair(
            {"fleet_id": 7, "reason": "Repair the damaged fleet."}
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertEqual(result["action"], "repair_fleet")
        self.assertEqual(
            controller_type.return_value.arm_and_wait.call_args.kwargs["target"],
            {"context_822c": 0, "source_fleet_object": 7},
        )

        prepared = toolbox.prepare_fleet_upgrade(
            {
                "fleet_id": 7,
                "shipyard_build_queue_id": 46,
                "reason": "Upgrade at the selected shipyard.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertEqual(result["action"], "upgrade_fleet")
        self.assertEqual(
            controller_type.return_value.arm_and_wait.call_args.kwargs["target"],
            {
                "context_822c": 0,
                "source_fleet_object": 7,
                "shipyard_build_queue_id": 46,
            },
        )

    def test_attack_rejects_target_absent_from_player_intel(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_attack": True}},
        )
        profile = fleet_profile()
        profile["hostile_targets"] = []
        toolbox = self.toolbox()
        toolbox._profile = lambda: (self.save, profile)  # type: ignore[method-assign]
        with self.assertRaisesRegex(FleetToolError, "uniquely resolved"):
            toolbox.prepare_attack(
                {"fleet_id": 7, "target_fleet_id": 99, "reason": "Intercept."}
            )

    def test_civilian_automation_and_starbase_orders_are_authorized(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {
                "8": {"allow_automation": True},
                "9": {
                    "allow_automation": True,
                    "allow_build_starbase": True,
                },
            },
        )
        toolbox = self.toolbox()
        prepared = toolbox.prepare_ship_automation(
            {
                "fleet_id": 8,
                "options": ["AUTOMATION_EXPLORE", "AUTOMATION_SURVEY"],
                "reason": "Survey the frontier.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertEqual(result["action"], "configure_ship_automation")
        self.assertEqual(
            controller_type.return_value.arm_and_wait.call_args.kwargs["target"][
                "options"
            ],
            ["AUTOMATION_EXPLORE", "AUTOMATION_SURVEY"],
        )

        prepared = toolbox.prepare_construction_starbase(
            {
                "fleet_id": 9,
                "destination_system_id": 30,
                "reason": "Claim the surveyed frontier.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertEqual(result["action"], "build_starbase")
        self.assertEqual(
            controller_type.return_value.arm_and_wait.call_args.kwargs["target"],
            {"source_fleet_object": 9, "target_system_object": 30},
        )

    def test_colonization_and_starbase_operations_execute_candidates(self) -> None:
        toolbox = self.toolbox()
        prepared = toolbox.prepare_colonization(
            {
                "candidate_id": "colonize:200:from:shipyard-queue:46:design:100",
                "designation": "col_city",
                "reason": "Settle the high-habitability world.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertEqual(result["action"], "order_colony_ship_and_colonize")
        self.assertEqual(
            controller_type.return_value.arm_and_wait.call_args.kwargs["target"][
                "colony_designation"
            ],
            "col_city",
        )

        prepared = toolbox.prepare_starbase_operation(
            {
                "candidate_id": "starbase:12:upgrade:starbase_level_starport",
                "reason": "Upgrade the frontier base.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertEqual(result["action"], "upgrade_starbase")
        self.assertEqual(
            self.store.get_state("last_expansion_execution")["action"],
            "upgrade_starbase",
        )

    def test_starbase_replacement_requires_separate_switch(self) -> None:
        with self.assertRaisesRegex(FleetToolError, "replacement"):
            self.toolbox().prepare_starbase_operation(
                {
                    "candidate_id": "starbase:12:module:0:anchorage",
                    "reason": "Replace the module.",
                }
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
                "reinforce_selected_fleet",
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
                "reinforce_selected_fleet",
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
