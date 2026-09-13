from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iag.applications.fleet_operations.agent_tools import (
    CAMPAIGN_ROUTE_USAGE_KEY,
    FLEET_FULL_DELEGATION_KEY,
    FLEET_PERMISSIONS_KEY,
    PENDING_ARMY_RECRUITMENT_KEY,
    PENDING_CAMPAIGN_KEY,
    PENDING_NEW_FLEET_KEY,
    FleetToolbox,
    FleetToolError,
    sha256_file,
)
from iag.core.conversation_store import ConversationStore
from iag.core.resource_ledger import ResourceReservationLedger


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
                "military_power": 500,
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
                "move_callable_now": True,
                "attack_callable_now": True,
                "reinforcement_callable_now": True,
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


def invasion_profile() -> dict[str, object]:
    profile = fleet_profile()
    military = profile["fleets"][0]  # type: ignore[index]
    military["bombardment_verified_family"] = True
    military["ground_support_stance"] = "selective"
    transport = {
        "fleet_id": 18,
        "name_key": "TRANSPORT_FLEET",
        "display_name_hint": "Transport Fleet",
        "player_controllable": True,
        "ship_class": "shipclass_transport",
        "landing_verified_family": True,
        "landing_callable_now": True,
        "landing_availability": "AVAILABLE",
        "movement": {"current_system_id": 10},
    }
    profile["fleets"].append(transport)  # type: ignore[union-attr]
    colony = {
        "planet_id": 70,
        "colony_id": 4,
        "planet_display_name_hint": "Rodel Prime",
        "system_id": 20,
        "reachable_from_fleet_ids": [7, 18],
        "route_evidence": [],
    }
    return {
        "schema": "iag.stellaris_invasion_state.v1",
        "game_date": "2204.09.15",
        "owner_country_id": 0,
        "country_stockpile": {"minerals": 5000},
        "fleets": profile["fleets"],
        "hostile_colonies": [colony],
        "blocked_hostile_colonies": [],
        "verified_bombardment_stances": [
            "selective",
            "indiscriminate",
            "raiding",
        ],
        "army_recruitment_candidates": [
            {
                "candidate_id": "recruit:robotic_army:queue:1:starbase:0",
                "army_type": "robotic_army",
                "species_id": 182,
                "source_colony": {
                    "army_build_queue_id": 1,
                    "source_colony_id": 0,
                },
                "recruitment_starbase": {"starbase_index": 0, "system_id": 10},
                "maximum_count": 5,
                "target_template": {
                    "context_822c": 0,
                    "army_build_queue_id": 1,
                    "army_subtype": 0,
                    "army_type": "robotic_army",
                    "species_id": 182,
                    "source_colony_object": 0,
                    "recruitment_starbase_object": 0,
                },
            }
        ],
    }


class StaticCampaignPlanner:
    def __init__(
        self,
        result: dict[str, object],
        deployment: dict[str, object] | None = None,
    ) -> None:
        self.result = result
        self.deployment = deployment or {}

    def plan(self, *, fleet_id: int, target_system_id: int) -> dict[str, object]:
        return self.result

    def deployment_summary(
        self,
        *,
        authorized_fleet_ids: set[int] | None = None,
    ) -> dict[str, object]:
        return {
            **self.deployment,
            "authorized_fleet_ids": sorted(authorized_fleet_ids or set()),
        }


class FleetToolboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.save = self.root / "test.sav"
        self.save.write_bytes(b"test")
        self.store = ConversationStore(self.root / "conversation.sqlite3")
        self.store.bind_campaign(
            "campaign",
            "0" * 64,
            campaign_label="fleet-toolbox-test",
        )
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
            "experimental_invasion_tools_enabled": True,
            "maximum_army_recruitment_batch": 5,
        }

    def toolbox(self) -> FleetToolbox:
        value = FleetToolbox(self.config, self.store, allow_execute=True)
        value._profile = lambda: (self.save, fleet_profile())  # type: ignore[method-assign]
        value._expansion_profile = lambda: (  # type: ignore[method-assign]
            self.save,
            expansion_profile(),
        )
        value._invasion_profile = lambda: (  # type: ignore[method-assign]
            self.save,
            invasion_profile(),
        )
        return value

    def test_enabled_capabilities_are_exposed_to_the_agent(self) -> None:
        schema_names = {item["function"]["name"] for item in self.toolbox().schemas()}
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
                "inspect_invasion_state",
                "prepare_orbital_bombardment",
                "prepare_army_landing",
                "prepare_army_recruitment",
                "inspect_campaign_deployment",
                "inspect_campaign_routes",
                "prepare_campaign_route",
            }.issubset(schema_names)
        )
        self.assertEqual(
            len(schema_names),
            len(self.toolbox().schemas()),
            "Tool schemas must not contain duplicate function names.",
        )

    def test_ship_state_excludes_hidden_disabled_autodesign_templates(self) -> None:
        self.config["experimental_ship_design_tools_enabled"] = True
        toolbox = self.toolbox()
        toolbox._ship_profile = lambda: (  # type: ignore[method-assign]
            self.save,
            {
                "schema": "iag.ship_state.v1",
                "owner_country_id": 0,
                "automatic_design_enabled": False,
                "component_choice_index": [],
                "shipyards": [],
                "designs": [
                    {
                        "design_id": 100,
                        "name_key": "PLAYER_VISIBLE",
                        "player_visible": True,
                    },
                    {
                        "design_id": 101,
                        "name_key": "HIDDEN_AUTO",
                        "auto_generated": True,
                        "player_visible": False,
                    },
                ],
            },
        )

        result, summary = toolbox.dispatch("inspect_ship_state", {})

        self.assertEqual(
            [design["design_id"] for design in result["designs"]],
            [100],
        )
        self.assertEqual(result["country_design_record_count"], 2)
        self.assertEqual(result["hidden_autogenerated_design_count"], 1)
        self.assertIn("1 份自动设计关闭后遗留的隐藏模板", summary)

    def test_full_delegation_authorizes_existing_and_future_fleets(self) -> None:
        self.store.set_state(FLEET_FULL_DELEGATION_KEY, True)
        toolbox = self.toolbox()

        state = toolbox.inspect()
        prepared = toolbox.prepare_move(
            {
                "fleet_id": 7,
                "destination_system_id": 20,
                "reason": "Follow the active campaign plan.",
            }
        )

        self.assertTrue(state["full_delegation"])
        self.assertTrue(all(state["fleets"][0]["permission"].values()))
        self.assertEqual(prepared["action"], "move_fleet")

    def test_full_delegation_includes_all_fleets_in_campaign_roles(self) -> None:
        self.store.set_state(FLEET_FULL_DELEGATION_KEY, True)
        planner = StaticCampaignPlanner(
            {},
            {
                "schema": "iag.campaign_deployment_state.v1",
                "fronts": [],
                "garrison_candidates": [],
                "fleet_role_recommendations": [],
            },
        )
        toolbox = self.toolbox()
        toolbox._campaign_profiles = lambda: (  # type: ignore[method-assign]
            self.save,
            fleet_profile(),
            invasion_profile(),
            planner,
        )

        result = toolbox.inspect_campaign_deployment({})

        self.assertEqual(result["authorized_fleet_ids"], [7, 8, 9])

    def test_campaign_deployment_only_recommends_fully_authorized_fleets(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {
                "7": {"allow_move": True, "allow_attack": True},
                "8": {"allow_move": True, "allow_attack": False},
            },
        )
        planner = StaticCampaignPlanner(
            {},
            {
                "schema": "iag.campaign_deployment_state.v1",
                "fronts": [{"system_id": 20}],
                "garrison_candidates": [
                    {"system_id": 10, "coverage_state": "uncovered"}
                ],
                "fleet_role_recommendations": [],
            },
        )
        toolbox = self.toolbox()
        toolbox._campaign_profiles = lambda: (  # type: ignore[method-assign]
            self.save,
            fleet_profile(),
            invasion_profile(),
            planner,
        )

        result, summary = toolbox.dispatch("inspect_campaign_deployment", {})

        self.assertEqual(result["authorized_fleet_ids"], [7])
        self.assertIn("1 个尚无驻守舰队", summary)

    def test_campaign_profiles_are_cached_until_save_or_commitments_change(
        self,
    ) -> None:
        toolbox = self.toolbox()
        toolbox._save_path = lambda: self.save  # type: ignore[method-assign]
        fleet = fleet_profile()
        invasion = invasion_profile()
        planner = object()
        with (
            patch(
                "iag.applications.fleet_operations.agent_tools.load_gamestate",
                return_value="gamestate",
            ) as load,
            patch(
                "iag.applications.fleet_operations.agent_tools.extract_fleet_profiles",
                return_value=fleet,
            ) as extract_fleets,
            patch(
                "iag.applications.fleet_operations.agent_tools.extract_invasion_profiles",
                return_value=invasion,
            ) as extract_invasion,
            patch(
                "iag.applications.fleet_operations.agent_tools.CampaignRoutePlanner",
                return_value=planner,
            ) as planner_type,
        ):
            first = toolbox._campaign_profiles()
            second = toolbox._campaign_profiles()
            self.store.set_state(
                CAMPAIGN_ROUTE_USAGE_KEY,
                [
                    {
                        "fleet_id": 7,
                        "target_system_id": 30,
                        "path_system_ids": [10, 20, 30],
                        "status": "active",
                    }
                ],
            )
            third = toolbox._campaign_profiles()

        self.assertIs(first[3], planner)
        self.assertIs(second[3], planner)
        self.assertIs(third[3], planner)
        self.assertEqual(load.call_count, 2)
        self.assertEqual(extract_fleets.call_count, 2)
        self.assertEqual(extract_invasion.call_count, 2)
        self.assertEqual(planner_type.call_count, 2)

    def test_bombardment_executes_stance_and_planet_move_as_one_sequence(self) -> None:
        toolbox = self.toolbox()
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_bombardment": True}},
        )
        prepared = toolbox.prepare_orbital_bombardment(
            {
                "fleet_id": 7,
                "target_planet_id": 70,
                "stance": "indiscriminate",
                "reason": "Disable the inhibitor planet.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_sequence_and_wait.return_value = {
                "outcome": "confirmed",
                "confirmed_steps": 2,
                "requested_steps": 2,
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})

        self.assertTrue(result["success"])
        steps = controller_type.return_value.arm_sequence_and_wait.call_args.kwargs[
            "steps"
        ]
        self.assertEqual(
            [step["action"] for step in steps],
            ["set_orbital_bombardment_stance", "move_fleet"],
        )
        self.assertEqual(steps[1]["target"]["destination_object"], 70)

    def test_landing_and_ctrl_style_recruitment_use_verified_targets(self) -> None:
        toolbox = self.toolbox()
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"18": {"allow_land_armies": True}},
        )
        landing = toolbox.prepare_army_landing(
            {
                "transport_fleet_id": 18,
                "target_planet_id": 70,
                "reason": "Occupy the hostile colony.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_sequence_and_wait.return_value = {
                "outcome": "confirmed",
                "confirmed_steps": 1,
                "requested_steps": 1,
            }
            result = toolbox.execute({"run_id": landing["run_id"]})
        self.assertTrue(result["success"])
        landing_step = (
            controller_type.return_value.arm_sequence_and_wait.call_args.kwargs[
                "steps"
            ][0]
        )
        self.assertEqual(landing_step["target"]["target_colony_object"], 4)

        toolbox = self.toolbox()
        recruitment = toolbox.prepare_army_recruitment(
            {
                "candidate_id": "recruit:robotic_army:queue:1:starbase:0",
                "count": 5,
                "reason": "Raise an invasion force.",
            }
        )
        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_sequence_and_wait.return_value = {
                "outcome": "confirmed",
                "confirmed_steps": 5,
                "requested_steps": 5,
            }
            result = toolbox.execute({"run_id": recruitment["run_id"]})
        steps = controller_type.return_value.arm_sequence_and_wait.call_args.kwargs[
            "steps"
        ]
        self.assertTrue(result["success"])
        self.assertEqual(len(steps), 5)
        self.assertTrue(all(step["action"] == "recruit_army" for step in steps))

    def test_campaign_executes_only_the_first_reachable_blocker(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_attack": True}},
        )
        profile = fleet_profile()
        target = dict(profile["hostile_targets"][0])  # type: ignore[index]
        target["ship_class"] = "shipclass_starbase"
        route = {
            "route_id": "route:7:30:test",
            "route_type": "shortest_assault",
            "path_system_ids": [10, 20, 30],
            "blockers": [
                {
                    "system_id": 20,
                    "starbase_sources": [target],
                    "planetary_sources": [],
                }
            ],
        }
        route_profile = {
            "route_options": [route],
            "target_objectives": {
                "hostile_starbases": [],
                "hostile_mobile_fleets": [],
                "hostile_colonies": [],
            },
        }
        toolbox = self.toolbox()
        toolbox._campaign_profiles = lambda: (  # type: ignore[method-assign]
            self.save,
            profile,
            invasion_profile(),
            StaticCampaignPlanner(route_profile),
        )
        prepared = toolbox.prepare_campaign_route(
            {
                "fleet_id": 7,
                "target_system_id": 30,
                "route_id": route["route_id"],
                "ground_policy": "bombard_then_land",
                "bombardment_stance": "selective",
                "reason": "Clear the nearer inhibitor before advancing.",
            }
        )
        self.assertEqual(prepared["prepared_action"], "attack_fleet")

        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertTrue(result["success"])
        self.assertEqual(
            controller_type.return_value.arm_and_wait.call_args.kwargs["target"][
                "target_fleet_object"
            ],
            99,
        )
        pending = self.store.get_state(PENDING_CAMPAIGN_KEY)
        self.assertEqual(pending["status"], "awaiting_fresh_save")
        self.assertEqual(pending["last_action_save_sha256"], sha256_file(self.save))
        usage = self.store.get_state(CAMPAIGN_ROUTE_USAGE_KEY)
        self.assertEqual(usage[0]["status"], "active")
        self.assertEqual(usage[0]["path_system_ids"], [10, 20, 30])

        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type:
            repeated = toolbox.continue_pending_campaign_from_save()
        self.assertEqual(repeated["state"], "waiting_for_fresh_save")
        controller_type.assert_not_called()

    def test_campaign_does_not_prepare_an_understrength_attack(self) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_attack": True}},
        )
        profile = fleet_profile()
        target = dict(profile["hostile_targets"][0])  # type: ignore[index]
        target["ship_class"] = "shipclass_starbase"
        route = {
            "route_id": "route:7:20:understrength",
            "route_type": "shortest_assault",
            "path_system_ids": [10, 20],
            "blockers": [],
            "force_assessment": {
                "status": "reinforcement_required",
                "hard_gate_satisfied": False,
                "engagements": [
                    {
                        "system_id": 20,
                        "status": "reinforcement_required",
                        "hard_gate_satisfied": False,
                        "known_hostile_military_power": 1000.0,
                        "projected_friendly_military_power": 500.0,
                        "additional_military_power_required": 700.0,
                    }
                ],
            },
        }
        route_profile = {
            "route_options": [route],
            "target_objectives": {
                "hostile_starbases": [target],
                "hostile_mobile_fleets": [],
                "hostile_colonies": [],
            },
        }
        toolbox = self.toolbox()
        toolbox._campaign_profiles = lambda: (  # type: ignore[method-assign]
            self.save,
            profile,
            invasion_profile(),
            StaticCampaignPlanner(route_profile),
        )

        result = toolbox.prepare_campaign_route(
            {
                "fleet_id": 7,
                "target_system_id": 20,
                "route_id": route["route_id"],
                "ground_policy": "bombard_then_land",
                "bombardment_stance": "selective",
                "reason": "Do not attack until the task force is strong enough.",
            }
        )

        self.assertFalse(result["execution_required"])
        self.assertEqual(result["status"], "waiting_for_space_reinforcements")
        self.assertEqual(
            result["current_step"]["additional_military_power_required"],
            700.0,
        )
        self.assertIsNone(self.store.get_state(PENDING_CAMPAIGN_KEY))

    def test_campaign_ground_step_waits_for_strength_then_lands(self) -> None:
        profile = invasion_profile()
        target = profile["hostile_colonies"][0]  # type: ignore[index]
        target.update(
            {
                "bombardment_damage": 55.0,
                "defending_armies": {
                    "army_count": 1,
                    "current_health_total": 100.0,
                },
            }
        )
        transport = next(
            item
            for item in profile["fleets"]  # type: ignore[union-attr]
            if item["fleet_id"] == 18
        )
        transport["transport_armies"] = {
            "army_count": 1,
            "current_health_total": 100.0,
        }
        plan = {
            "fleet_id": 7,
            "transport_fleet_id": 18,
            "ground_policy": "bombard_then_land",
            "bombardment_stance": "selective",
        }
        toolbox = self.toolbox()
        permissions = {
            "7": {"allow_bombardment": True},
            "18": {"allow_land_armies": True},
        }
        waiting = toolbox._campaign_ground_step(
            plan=plan,
            target=target,
            invasion_profile=profile,
            permissions=permissions,
        )
        self.assertEqual(waiting["state"], "waiting_for_stronger_transport")

        transport["transport_armies"] = {
            "army_count": 2,
            "current_health_total": 150.0,
        }
        landing = toolbox._campaign_ground_step(
            plan=plan,
            target=target,
            invasion_profile=profile,
            permissions=permissions,
        )
        self.assertEqual(landing["action"], "land_armies")
        self.assertEqual(
            landing["landing_assessment"]["authority"],
            "save_army_health_pool_comparison_not_full_combat_simulation",
        )

    def test_recruited_transport_is_bound_and_optionally_authorized(self) -> None:
        self.config["auto_authorize_recruited_transport_fleets"] = True
        self.store.set_state(
            PENDING_ARMY_RECRUITMENT_KEY,
            {
                "schema": "iag.pending_army_recruitment.v1",
                "recruitment_run_id": "army-test",
                "expected_army_count": 3,
                "baseline_transport_fleet_ids": [18],
                "campaign_plan_id": "campaign-test",
                "last_action_save_sha256": "old-save",
            },
        )
        self.store.set_state(
            PENDING_CAMPAIGN_KEY,
            {
                "campaign_plan_id": "campaign-test",
                "status": "waiting_for_stronger_transport",
                "transport_fleet_id": 18,
            },
        )
        profile = invasion_profile()
        profile["fleets"].append(  # type: ignore[union-attr]
            {
                "fleet_id": 19,
                "ship_class": "shipclass_transport",
                "ship_count": 3,
                "transport_armies": {
                    "army_count": 3,
                    "current_health_total": 300.0,
                },
            }
        )
        toolbox = self.toolbox()
        toolbox._invasion_profile = lambda: (  # type: ignore[method-assign]
            self.save,
            profile,
        )

        result = toolbox.reconcile_pending_army_recruitment_from_save()

        self.assertEqual(result["state"], "confirmed_in_save")
        self.assertEqual(result["transport_fleet_id"], 19)
        self.assertIsNone(self.store.get_state(PENDING_ARMY_RECRUITMENT_KEY))
        self.assertTrue(
            self.store.get_state(FLEET_PERMISSIONS_KEY)["19"][
                "allow_land_armies"
            ]
        )
        self.assertEqual(
            self.store.get_state(PENDING_CAMPAIGN_KEY)["transport_fleet_id"],
            19,
        )

    def test_recruitment_can_merge_into_an_existing_transport(self) -> None:
        self.store.set_state(
            PENDING_ARMY_RECRUITMENT_KEY,
            {
                "schema": "iag.pending_army_recruitment.v1",
                "recruitment_run_id": "army-merge-test",
                "expected_army_count": 3,
                "baseline_transport_fleet_ids": [18],
                "baseline_transport_fleets": [
                    {"fleet_id": 18, "army_count": 1}
                ],
                "last_action_save_sha256": "old-save",
            },
        )
        profile = invasion_profile()
        transport = next(
            item
            for item in profile["fleets"]  # type: ignore[union-attr]
            if item["fleet_id"] == 18
        )
        transport["ship_count"] = 4
        transport["transport_armies"] = {
            "army_count": 4,
            "current_health_total": 400.0,
        }
        toolbox = self.toolbox()
        toolbox._invasion_profile = lambda: (  # type: ignore[method-assign]
            self.save,
            profile,
        )

        result = toolbox.reconcile_pending_army_recruitment_from_save()

        self.assertEqual(result["state"], "confirmed_in_save")
        self.assertEqual(result["transport_fleet_id"], 18)
        self.assertEqual(result["recruited_army_count"], 3)
        self.assertIsNone(self.store.get_state(PENDING_ARMY_RECRUITMENT_KEY))

    def test_cross_application_reservation_blocks_army_overspend(self) -> None:
        toolbox = self.toolbox()
        prepared = toolbox.prepare_army_recruitment(
            {
                "candidate_id": "recruit:robotic_army:queue:1:starbase:0",
                "count": 2,
                "reason": "Raise an invasion force.",
            }
        )
        ResourceReservationLedger(self.store).reserve(
            reservation_id="economy:test",
            application_id="economy_governance",
            action="build_building",
            source_save_sha256=sha256_file(self.save),
            source_game_date="2204.09.15",
            stockpile={"minerals": 5000.0},
            costs={"minerals": 4800.0},
        )

        with patch(
            "iag.applications.fleet_operations.agent_tools.SessionProxyController"
        ) as controller_type, self.assertRaisesRegex(FleetToolError, "不足以预留"):
            toolbox.execute({"run_id": prepared["run_id"]})
        controller_type.assert_not_called()

    def test_full_ship_design_arguments_are_revalidated_at_execution(self) -> None:
        self.config["experimental_ship_design_tools_enabled"] = True
        toolbox = self.toolbox()
        toolbox._ship_profile = lambda: (  # type: ignore[method-assign]
            self.save,
            {"owner_country_id": 0, "designs": []},
        )
        arguments = {
            "source_design_id": 200,
            "new_name": "IAG_TEST_DESIGN",
            "section_replacements": [
                {
                    "section_slot": "stern",
                    "section_template": "BATTLESHIP_STERN_M2",
                    "components": [
                        {
                            "component_slot": "MEDIUM_GUN_01",
                            "component_id": "MEDIUM_PLASMA_3",
                        }
                    ],
                }
            ],
            "component_replacements": [
                {
                    "section_slot": "bow",
                    "component_slot": "EXTRA_LARGE_01",
                    "component_id": "ARC_EMITTER_2",
                }
            ],
            "required_component_replacements": [
                {
                    "component_set": "combat_computers",
                    "component_id": "COMBAT_COMPUTER_CARRIER_SAPIENT",
                }
            ],
            "upgrade_components_automatically": True,
            "reason": "Create a carrier design.",
        }
        blueprint = {
            "name": "IAG_TEST_DESIGN",
            "context_822c": 0,
            "growth_stages": [],
        }
        with (
            patch(
                "iag.applications.fleet_operations.agent_tools.detect_game_root",
                return_value=self.root,
            ),
            patch(
                "iag.applications.fleet_operations.agent_tools.customize_ship_design",
                return_value=blueprint,
            ) as customize,
        ):
            prepared = toolbox.prepare_ship_design(arguments)

        self.assertIn(
            "inspect_ship_design_options",
            {item["function"]["name"] for item in toolbox.schemas()},
        )
        self.assertIn(
            "prepare_ship_design",
            {item["function"]["name"] for item in toolbox.schemas()},
        )
        self.assertTrue(customize.call_args.kwargs["upgrade_components_automatically"])
        design_schema = next(
            item
            for item in toolbox.schemas()
            if item["function"]["name"] == "prepare_ship_design"
        )
        self.assertIn(
            "自主拟定",
            design_schema["function"]["parameters"]["properties"]["new_name"][
                "description"
            ],
        )

        with (
            patch(
                "iag.applications.fleet_operations.agent_tools.detect_game_root",
                return_value=self.root,
            ),
            patch(
                "iag.applications.fleet_operations.agent_tools.customize_ship_design",
                return_value=blueprint,
            ) as replay,
            patch(
                "iag.applications.fleet_operations.agent_tools.SessionProxyController"
            ) as controller_type,
        ):
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute_ship_action({"run_id": prepared["run_id"]})

        self.assertTrue(result["success"])
        self.assertEqual(result["design_name"], "IAG_TEST_DESIGN")
        self.assertEqual(replay.call_count, 1)
        self.assertEqual(
            replay.call_args.kwargs["section_replacements"],
            arguments["section_replacements"],
        )
        self.assertEqual(
            controller_type.return_value.arm_and_wait.call_args.kwargs["target"],
            {"blueprint": blueprint},
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

    def test_inspect_summary_uses_the_authorized_capability_state(self) -> None:
        profile = fleet_profile()
        military = profile["fleets"][0]  # type: ignore[index]
        military["maintenance_callable_now"] = False
        military["current_order_state"] = "move_system"
        toolbox = self.toolbox()
        toolbox._profile = lambda: (self.save, profile)  # type: ignore[method-assign]
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_repair": True}},
        )

        _result, summary = toolbox.dispatch("inspect_fleet_state", {})

        self.assertIn("1 支已获至少一项 AI 权限", summary)
        self.assertIn("其中 0 支当前可调用", summary)

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

    def test_direct_attack_requires_enough_power_for_all_known_system_defenders(
        self,
    ) -> None:
        self.store.set_state(
            FLEET_PERMISSIONS_KEY,
            {"7": {"allow_attack": True}},
        )
        profile = fleet_profile()
        profile["hostile_targets"].append(  # type: ignore[union-attr]
            {
                "fleet_id": 100,
                "owner_country_id": 2,
                "ship_class": "shipclass_starbase",
                "military_power": 400,
                "system_id": 20,
            }
        )
        toolbox = self.toolbox()
        toolbox._profile = lambda: (self.save, profile)  # type: ignore[method-assign]

        with self.assertRaisesRegex(FleetToolError, "至少还需 280.00 军力"):
            toolbox.prepare_attack(
                {"fleet_id": 7, "target_fleet_id": 99, "reason": "Attack."}
            )

        support = dict(profile["fleets"][0])  # type: ignore[index]
        support.update(
            {
                "fleet_id": 10,
                "military_power": 300,
                "availability": "UNCERTAIN",
                "attack_callable_now": False,
                "movement": {"current_system_id": 20},
            }
        )
        profile["fleets"].append(support)  # type: ignore[union-attr]

        prepared = toolbox.prepare_attack(
            {"fleet_id": 7, "target_fleet_id": 99, "reason": "Join support."}
        )

        assessment = prepared["space_force_assessment"]
        self.assertTrue(assessment["hard_gate_satisfied"])
        self.assertEqual(
            [item["fleet_id"] for item in assessment["supporting_fleets"]],
            [10],
        )
        self.assertEqual(assessment["known_hostile_military_power"], 650.0)

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

        # A confirmed spend remains reserved until a fresh synchronized save.
        self.save.write_bytes(b"test-after-colonization")
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
        schema_names = {item["function"]["name"] for item in continuation.schemas()}
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
