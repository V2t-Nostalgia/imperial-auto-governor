from __future__ import annotations

import sys
import unittest
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from planner import (  # noqa: E402
    build_candidates,
    carrier_source_ready,
    execution_manifest,
    planning_snapshot,
    read_json,
    validate_plan,
    zone_has_capacity,
)


def snapshot() -> dict:
    return {
        "schema": "iag.game_state.v1",
        "game_date": "2200.01.01",
        "player": {"country_id": 0, "name": "LLM"},
        "country": {
            "stockpile": {
                "energy": 5000,
                "minerals": 5000,
                "food": 1000,
                "consumer_goods": 1000,
            },
            "monthly_balance": {
                "energy": 20,
                "minerals": 20,
                "food": 5,
                "consumer_goods": 5,
            },
        },
        "known_technologies": [
            "tech_basic_science_lab_1",
            "tech_basic_industry",
        ],
        "planet_count": 1,
        "source_save": {
            "path": "/save/test.sav",
            "sha256": "abc",
        },
        "planets": [
            {
                "name_key": "Earth",
                "planet_id": 3,
                "colony_id": 9,
                "planet_class": "pc_continental",
                "build_queue_id": 6,
                "construction": {
                    "has_pending_construction": False,
                    "queued_item_ids": [],
                    "queue_depth": 0,
                    "pending_items": [],
                },
                "safety": {
                    "is_iag_carrier": False,
                    "special_planet_requires_manual_review": False,
                    "eligible_for_first_run": True,
                },
                "metrics": {
                    "final_designation": "col_research",
                    "free_jobs_estimate": 0,
                    "unemployed_pops_estimate": 1,
                    "free_housing_raw": 500,
                    "free_amenities_raw": 100,
                    "stability": 60,
                    "crime": 0,
                    "resource_profit": {},
                    "active_jobs": [],
                },
                "districts": [
                    {
                        "district_id": 100,
                        "type": "district_city",
                        "level": 1,
                        "zone_slot_ids": [
                            "slot_city_government",
                            "slot_city_01",
                            "slot_city_02",
                        ],
                        "available_zone_slots": [
                            {
                                "slot_selector": 1,
                                "slot_id": "slot_city_01",
                                "included_zone_sets": [],
                            }
                        ],
                        "zones": [
                            {
                                "zone_id": 200,
                                "slot_selector": 0,
                                "type": "zone_default",
                                "building_slot_capacity": 2,
                                "occupied_building_positions": [],
                                "available_building_positions": [0, 1],
                                "buildings": [],
                            }
                        ],
                    },
                    {
                        "district_id": 101,
                        "type": "district_research",
                        "level": 1,
                        "zones": [
                            {
                                "zone_id": 201,
                                "slot_selector": 0,
                                "type": "zone_research_physics",
                                "building_slot_capacity": 3,
                                "occupied_building_positions": [],
                                "available_building_positions": [0, 1, 2],
                                "buildings": [],
                            }
                        ],
                    },
                ],
                "zones": [
                    {
                        "zone_id": 200,
                        "type": "zone_default",
                        "district_id": 100,
                        "district_type": "district_city",
                        "district_level": 1,
                        "slot_selector": 0,
                        "building_slot_capacity": 2,
                        "occupied_building_positions": [],
                        "available_building_positions": [0, 1],
                        "buildings": [],
                    },
                    {
                        "zone_id": 201,
                        "type": "zone_research_physics",
                        "district_id": 101,
                        "district_type": "district_research",
                        "district_level": 1,
                        "slot_selector": 0,
                        "building_slot_capacity": 3,
                        "occupied_building_positions": [],
                        "available_building_positions": [0, 1, 2],
                        "buildings": [],
                    },
                ],
            }
        ],
    }


def config() -> dict:
    return {
        "mineral_reserve": 1000,
        "critical_runway_months": 12,
        "maximum_free_jobs_for_expansion": 2,
        "maximum_pending_construction_items_per_planet": 2,
        "minimum_confidence": 0.72,
    }


def add_upgrade_carrier(value: dict) -> None:
    value["planets"].append(
        {
            "safety": {
                "is_iag_carrier": True,
                "eligible_for_first_run": False,
            },
            "zones": [
                {
                    "buildings": [
                        {
                            "object_id": 999,
                            "position": 1,
                            "type": "building_upc_upgrade_command_relay_base",
                        }
                    ]
                }
            ],
        }
    )


class PlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = read_json(RUNTIME / "capabilities.json")

    def test_building_carrier_fits_every_enabled_building_id(self) -> None:
        carrier = self.capabilities["carriers"]["build_building"]
        maximum = int(carrier["maximum_equal_length_id_bytes"])
        self.assertEqual(len(carrier["command"].encode("ascii")), maximum)
        for building_id, capability in self.capabilities["buildings"].items():
            if capability.get("enabled"):
                with self.subTest(building_id=building_id):
                    self.assertLessEqual(len(building_id.encode("ascii")), maximum)

    def test_upgrade_carrier_fits_every_enabled_upgrade_target(self) -> None:
        carrier = self.capabilities["carriers"]["upgrade_building"]
        maximum = int(carrier["maximum_equal_length_id_bytes"])
        self.assertEqual(len(carrier["command"].encode("ascii")), maximum)
        for upgrade in self.capabilities["building_upgrades"]:
            if upgrade.get("enabled"):
                with self.subTest(target=upgrade["to_building_id"]):
                    self.assertLessEqual(
                        len(upgrade["to_building_id"].encode("ascii")),
                        maximum,
                    )

    def test_upgrade_candidates_bind_each_exact_saved_building_slot(self) -> None:
        value = snapshot()
        add_upgrade_carrier(value)
        value["known_technologies"].append("tech_basic_science_lab_2")
        value["country"]["stockpile"]["exotic_gases"] = 500
        planet = value["planets"][0]
        research_zone = planet["zones"][1]
        research_zone.update(
            {
                "building_slot_capacity": 3,
                "occupied_building_positions": [0, 1, 2],
                "available_building_positions": [],
                "buildings": [
                    {
                        "object_id": 452,
                        "position": 0,
                        "type": "building_research_lab_1",
                    },
                    {
                        "object_id": 463,
                        "position": 1,
                        "type": "building_research_lab_1",
                    },
                    {
                        "object_id": 16777664,
                        "position": 2,
                        "type": "building_research_lab_1",
                    },
                ],
            }
        )
        planet["districts"][1]["zones"][0] = dict(research_zone)
        value["layout_rules"] = {
            "building_upgrade_rules": {
                "building_research_lab_1": {
                    "building_research_lab_2": {
                        "status": "available",
                        "prerequisites": ["tech_basic_science_lab_2"],
                        "requires_upgraded_capital": False,
                    }
                }
            },
            "construction_costs": {
                "upgrade_building": {
                    "building_research_lab_2": {
                        "status": "available",
                        "cost": {"minerals": 600, "exotic_gases": 50},
                    }
                }
            },
        }

        upgrades = [
            item
            for item in build_candidates(value, self.capabilities, config())
            if item["action"].get("type") == "upgrade_building"
        ]
        self.assertEqual(len(upgrades), 3)
        self.assertEqual(
            {
                (
                    item["action"]["building_position"],
                    item["action"]["building_object_id"],
                )
                for item in upgrades
            },
            {(0, 452), (1, 463), (2, 16777664)},
        )
        self.assertEqual(len({item["candidate_id"] for item in upgrades}), 3)

        manifest = execution_manifest(
            {"confidence": 0.9, "reasoning_zh": "测试槽位升级"},
            upgrades[0],
            value,
            self.capabilities,
        )
        self.assertEqual(
            manifest["carrier"]["command"],
            "building_upc_upgrade_command_relay_target",
        )
        self.assertIn(manifest["action"]["building_object_id"], {452, 463, 16777664})

    def test_regular_planet_upgrade_uses_non_ringworld_cost_branch(self) -> None:
        value = snapshot()
        add_upgrade_carrier(value)
        value["known_technologies"].append("tech_power_hub_2")
        value["country"]["stockpile"]["rare_crystals"] = 500
        planet = value["planets"][0]
        planet["capital_tier"] = 2
        planet["zones"].append(
            {
                "zone_id": 301,
                "type": "zone_energy",
                "district_id": 102,
                "district_type": "district_generator",
                "district_level": 4,
                "building_slot_capacity": 3,
                "occupied_building_positions": [0],
                "available_building_positions": [1, 2],
                "buildings": [
                    {
                        "object_id": 700,
                        "position": 0,
                        "type": "building_energy_grid",
                    }
                ],
            }
        )
        value["layout_rules"] = {
            "building_upgrade_rules": {
                "building_energy_grid": {
                    "building_energy_nexus": {
                        "status": "available",
                        "prerequisites": ["tech_power_hub_2"],
                        "requires_upgraded_capital": True,
                    }
                }
            },
            "construction_costs": {
                "upgrade_building": {
                    "building_energy_nexus": {
                        "status": "conditional",
                        "conditional_costs": [
                            {
                                "condition": {
                                    "has_ringworld_output_boost": False
                                },
                                "cost": {
                                    "minerals": 600,
                                    "rare_crystals": 50,
                                },
                            },
                            {
                                "condition": {
                                    "has_ringworld_output_boost": True
                                },
                                "cost": {
                                    "minerals": 900,
                                    "rare_crystals": 200,
                                },
                            },
                        ],
                    }
                }
            },
        }

        selected = next(
            item
            for item in build_candidates(value, self.capabilities, config())
            if item["action"].get("to_building_id")
            == "building_energy_nexus"
        )
        self.assertEqual(
            selected["construction_cost"],
            {"minerals": 600, "rare_crystals": 50},
        )

    def test_custom_upgrade_carrier_requires_save_evidence(self) -> None:
        value = snapshot()
        self.assertFalse(
            carrier_source_ready(
                value,
                self.capabilities,
                "upgrade_building",
            )
        )
        add_upgrade_carrier(value)
        self.assertTrue(
            carrier_source_ready(
                value,
                self.capabilities,
                "upgrade_building",
            )
        )

    def test_builds_only_legal_candidates(self) -> None:
        candidates = build_candidates(
            snapshot(),
            self.capabilities,
            config(),
        )
        actions = [item["action"] for item in candidates]
        building_actions = [
            action for action in actions if action.get("type") == "build_building"
        ]
        self.assertTrue(building_actions)
        self.assertTrue(
            all(action.get("colony_id") == 9 for action in building_actions)
        )
        self.assertTrue(
            any(
                action.get("building_id") == "building_research_lab_1"
                for action in actions
            )
        )
        self.assertTrue(
            any(action.get("zone_type") == "zone_trade" for action in actions)
        )

    def test_empty_jobs_prevent_expansion(self) -> None:
        value = snapshot()
        value["planets"][0]["metrics"]["free_jobs_estimate"] = 4
        candidates = build_candidates(value, self.capabilities, config())
        self.assertEqual(candidates, [])

    def test_colonizing_planet_is_not_a_build_target(self) -> None:
        value = snapshot()
        value["planets"][0]["safety"]["is_colonizing"] = True
        value["planets"][0]["safety"]["eligible_for_first_run"] = False
        candidates = build_candidates(value, self.capabilities, config())
        self.assertEqual(candidates, [])

    def test_zone_capacity_uses_enriched_available_positions(self) -> None:
        zone = {
            "type": "zone_default",
            "building_slot_capacity": 2,
            "occupied_building_positions": [0, 1],
            "available_building_positions": [],
            "buildings": [
                {"position": 0, "type": "building_colony_shelter"},
                {"position": 1, "type": "building_holo_theatres"},
            ],
        }
        self.assertFalse(zone_has_capacity(zone))
        zone["buildings"].pop()
        zone["occupied_building_positions"] = [0]
        zone["available_building_positions"] = [1]
        self.assertTrue(zone_has_capacity(zone))

    def test_zone_without_available_position_evidence_fails_closed(self) -> None:
        self.assertFalse(
            zone_has_capacity(
                {
                    "type": "zone_default",
                    "buildings": [],
                }
            )
        )

    def test_critical_runway_prevents_unrelated_expansion(self) -> None:
        value = snapshot()
        value["country"]["stockpile"]["energy"] = 100
        value["country"]["monthly_balance"]["energy"] = -20
        candidates = build_candidates(value, self.capabilities, config())
        self.assertEqual(candidates, [])

    def test_unknown_candidate_is_rejected(self) -> None:
        value = snapshot()
        candidates = build_candidates(value, self.capabilities, config())
        plan = {
            "schema": "iag.agent_plan.v1",
            "source_game_date": value["game_date"],
            "assessment": {
                "risk_level": "low",
                "urgent_risks": [],
                "strategic_priority": "稳健发展",
            },
            "action": {
                "type": "execute_candidate",
                "candidate_id": "iag_candidate_0000000000000000",
            },
            "reasoning_zh": "测试",
            "confidence": 0.9,
            "next_review_months": 1,
        }
        with self.assertRaises(ValueError):
            validate_plan(plan, value, candidates, config())

    def test_all_configurable_review_intervals_are_accepted(self) -> None:
        value = snapshot()
        candidates = build_candidates(value, self.capabilities, config())
        for months in (1, 3, 6, 12):
            with self.subTest(months=months):
                plan = {
                    "schema": "iag.agent_plan.v1",
                    "source_game_date": value["game_date"],
                    "assessment": {
                        "risk_level": "low",
                        "urgent_risks": [],
                        "strategic_priority": "test",
                    },
                    "action": {"type": "noop"},
                    "reasoning_zh": "test",
                    "confidence": 0.9,
                    "next_review_months": months,
                }
                self.assertIsNone(
                    validate_plan(plan, value, candidates, config())
                )

    def test_unsupported_review_interval_is_rejected(self) -> None:
        value = snapshot()
        candidates = build_candidates(value, self.capabilities, config())
        plan = {
            "schema": "iag.agent_plan.v1",
            "source_game_date": value["game_date"],
            "assessment": {
                "risk_level": "low",
                "urgent_risks": [],
                "strategic_priority": "test",
            },
            "action": {"type": "noop"},
            "reasoning_zh": "test",
            "confidence": 0.9,
            "next_review_months": 2,
        }
        with self.assertRaisesRegex(
            ValueError,
            "next_review_months must be one of: 1, 3, 6, 12",
        ):
            validate_plan(plan, value, candidates, config())

    def test_zone_manifest_uses_zone_family_carrier(self) -> None:
        value = snapshot()
        candidates = build_candidates(value, self.capabilities, config())
        selected = next(
            item
            for item in candidates
            if item["action"].get("type") == "build_zone"
        )
        manifest = execution_manifest(
            {"confidence": 0.9, "reasoning_zh": "测试"},
            selected,
            value,
            self.capabilities,
        )

        self.assertEqual(
            manifest["carrier"]["command"],
            "zone_research_engineering",
        )

    def test_mining_district_candidate_uses_capacity_evidence(self) -> None:
        value = snapshot()
        value["known_technologies"].append("tech_mechanized_mining")
        planet = value["planets"][0]
        planet["display_name_hint"] = "NAME_Trappist-I"
        planet["planet_size"] = 16
        planet["district_capacity"] = {
            "status": "available",
            "total": {
                "capacity": 14,
                "built": 1,
                "pending": 0,
                "remaining": 13,
            },
            "by_type": {
                "district_mining": {
                    "capacity_floor": 9,
                    "built": 0,
                    "pending": 0,
                    "remaining_buildable_floor": 9,
                }
            },
        }
        value["layout_rules"] = {
            "district_rules": {
                "district_mining": {
                    "prerequisites": ["tech_mechanized_mining"]
                }
            }
        }

        candidates = build_candidates(value, self.capabilities, config())
        selected = next(
            item
            for item in candidates
            if item["action"].get("district_type") == "district_mining"
        )
        self.assertEqual(selected["action"]["type"], "build_district")
        self.assertEqual(selected["action"]["planet_id"], 3)
        self.assertEqual(
            selected["action"]["planet_name_hint"],
            "NAME_Trappist-I",
        )
        self.assertEqual(
            selected["capacity_evidence"]["remaining_buildable_floor"],
            9,
        )

    def test_district_at_capacity_is_not_a_candidate(self) -> None:
        value = snapshot()
        value["known_technologies"].append("tech_mechanized_mining")
        value["planets"][0]["district_capacity"] = {
            "status": "available",
            "by_type": {
                "district_mining": {
                    "capacity_floor": 9,
                    "built": 9,
                    "pending": 0,
                    "remaining_buildable_floor": 0,
                }
            },
        }

        actions = [
            item["action"]
            for item in build_candidates(value, self.capabilities, config())
        ]
        self.assertFalse(
            any(
                action.get("district_type") == "district_mining"
                for action in actions
            )
        )

    def test_district_manifest_uses_district_family_carrier(self) -> None:
        value = snapshot()
        value["known_technologies"].append("tech_mechanized_mining")
        value["planets"][0]["district_capacity"] = {
            "status": "available",
            "by_type": {
                "district_mining": {
                    "capacity_floor": 3,
                    "built": 0,
                    "pending": 0,
                    "remaining_buildable_floor": 3,
                }
            },
        }
        selected = next(
            item
            for item in build_candidates(value, self.capabilities, config())
            if item["action"].get("district_type") == "district_mining"
        )
        manifest = execution_manifest(
            {"confidence": 0.9, "reasoning_zh": "test"},
            selected,
            value,
            self.capabilities,
        )
        self.assertEqual(
            manifest["carrier"]["command"],
            "district_generator",
        )

    def test_one_pending_building_allows_a_distinct_second_item(self) -> None:
        value = snapshot()
        planet = value["planets"][0]
        planet["construction"] = {
            "has_pending_construction": True,
            "queued_item_ids": [285212704],
            "queue_depth": 1,
            "pending_items": [
                {
                    "item_id": 285212704,
                    "kind": "building",
                    "queue_id": 6,
                    "building_id": "building_research_lab_1",
                    "zone_id": 201,
                    "progress": 263.0,
                    "progress_needed": 360.0,
                    "resources": {"minerals": 400.0},
                }
            ],
        }

        candidates = build_candidates(value, self.capabilities, config())
        actions = [item["action"] for item in candidates]

        self.assertTrue(actions)
        self.assertFalse(
            any(
                action.get("building_id") == "building_research_lab_1"
                and action.get("zone_id") == 201
                for action in actions
            )
        )

    def test_repeatable_industrial_buildings_use_the_third_saved_slot(self) -> None:
        value = snapshot()
        planet = value["planets"][0]
        industrial = {
            "zone_id": 202,
            "slot_selector": 2,
            "type": "zone_industrial",
            "district_id": 100,
            "district_type": "district_city",
            "district_level": 3,
            "building_slot_capacity": 3,
            "occupied_building_positions": [0, 1],
            "available_building_positions": [2],
            "buildings": [
                {"position": 0, "type": "building_factory_1"},
                {"position": 1, "type": "building_foundry_1"},
            ],
        }
        planet["zones"].append(industrial)
        planet["districts"][0]["zones"].append(dict(industrial))

        actions = [
            item["action"]
            for item in build_candidates(value, self.capabilities, config())
        ]
        industrial_buildings = {
            action.get("building_id")
            for action in actions
            if action.get("type") == "build_building"
            and action.get("zone_id") == 202
        }
        self.assertEqual(
            industrial_buildings,
            {"building_factory_1", "building_foundry_1"},
        )

    def test_full_district_does_not_generate_an_out_of_range_zone_slot(self) -> None:
        value = snapshot()
        district = value["planets"][0]["districts"][0]
        district["available_zone_slots"] = []

        actions = [
            item["action"]
            for item in build_candidates(value, self.capabilities, config())
        ]
        self.assertFalse(
            any(action.get("type") == "build_zone" for action in actions)
        )

    def test_resource_zones_match_only_their_rural_district_types(self) -> None:
        value = snapshot()
        expected = {
            "zone_energy": (102, "district_generator", "slot_energy"),
            "zone_minerals": (103, "district_mining", "slot_minerals"),
            "zone_food": (104, "district_farming", "slot_food"),
        }
        for district_id, district_type, slot_id in expected.values():
            value["planets"][0]["districts"].append(
                {
                    "district_id": district_id,
                    "type": district_type,
                    "level": 4,
                    "zone_slot_ids": [slot_id],
                    "available_zone_slots": [
                        {
                            "slot_selector": 0,
                            "slot_id": slot_id,
                            "included_zone_sets": [],
                        }
                    ],
                    "zones": [],
                }
            )

        resource_actions = [
            item["action"]
            for item in build_candidates(value, self.capabilities, config())
            if item["action"].get("zone_type") in expected
        ]

        self.assertEqual(len(resource_actions), 3)
        by_zone = {action["zone_type"]: action for action in resource_actions}
        for zone_type, (district_id, _, slot_id) in expected.items():
            with self.subTest(zone_type=zone_type):
                self.assertEqual(by_zone[zone_type]["district_id"], district_id)
                self.assertEqual(by_zone[zone_type]["slot_selector"], 0)
                self.assertEqual(by_zone[zone_type]["slot_id"], slot_id)

    def test_resource_zone_buildings_are_generated_from_save_capacity(self) -> None:
        value = snapshot()
        planet = value["planets"][0]
        planet["zones"].append(
            {
                "zone_id": 301,
                "type": "zone_energy",
                "district_id": 102,
                "district_type": "district_generator",
                "district_level": 4,
                "building_slot_capacity": 3,
                "occupied_building_positions": [],
                "available_building_positions": [0, 1, 2],
                "buildings": [],
            }
        )
        planet["district_capacity"] = {
            "status": "available",
            "missing_deposit_definitions": [],
            "missing_static_modifier_definitions": [],
            "missing_building_definitions": [],
            "ignored_positive_multipliers": {},
            "by_type": {
                "district_generator": {
                    "capacity_floor": 4,
                    "built": 4,
                    "pending": 0,
                    "remaining_by_type_floor": 0,
                    "remaining_buildable_floor": 0,
                }
            },
        }
        value["known_technologies"].extend(
            ["tech_power_plant_1", "tech_power_hub_1"]
        )

        building_ids = {
            item["action"]["building_id"]
            for item in build_candidates(value, self.capabilities, config())
            if item["action"].get("type") == "build_building"
            and item["action"].get("zone_id") == 301
        }

        self.assertEqual(
            building_ids,
            {
                "building_generator_generic",
                "building_energy_grid",
                "building_generator_districts_1",
                "building_resource_silo",
            },
        )

    def test_capped_resource_building_requires_a_completed_exact_capacity(self) -> None:
        value = snapshot()
        planet = value["planets"][0]
        planet["zones"].append(
            {
                "zone_id": 301,
                "type": "zone_energy",
                "district_id": 102,
                "district_type": "district_generator",
                "district_level": 4,
                "building_slot_capacity": 3,
                "occupied_building_positions": [],
                "available_building_positions": [0, 1, 2],
                "buildings": [],
            }
        )
        planet["district_capacity"] = {
            "status": "available",
            "missing_deposit_definitions": [],
            "missing_static_modifier_definitions": [],
            "missing_building_definitions": [],
            "ignored_positive_multipliers": {},
            "by_type": {
                "district_generator": {
                    "capacity_floor": 5,
                    "built": 4,
                    "pending": 0,
                    "remaining_by_type_floor": 1,
                    "remaining_buildable_floor": 1,
                }
            },
        }
        value["known_technologies"].extend(
            ["tech_power_plant_1", "tech_power_hub_1"]
        )

        building_ids = {
            item["action"].get("building_id")
            for item in build_candidates(value, self.capabilities, config())
            if item["action"].get("zone_id") == 301
        }

        self.assertNotIn("building_generator_districts_1", building_ids)

    def test_resource_planet_unique_families_include_upgraded_buildings(self) -> None:
        value = snapshot()
        planet = value["planets"][0]
        planet["zones"].append(
            {
                "zone_id": 301,
                "type": "zone_energy",
                "district_id": 102,
                "district_type": "district_generator",
                "district_level": 4,
                "building_slot_capacity": 3,
                "occupied_building_positions": [0, 1],
                "available_building_positions": [2],
                "buildings": [
                    {"position": 0, "type": "building_energy_nexus"},
                    {"position": 1, "type": "building_generator_districts_3"},
                ],
            }
        )
        value["known_technologies"].extend(
            ["tech_power_plant_1", "tech_power_hub_1"]
        )

        building_ids = {
            item["action"].get("building_id")
            for item in build_candidates(value, self.capabilities, config())
            if item["action"].get("zone_id") == 301
        }

        self.assertNotIn("building_energy_grid", building_ids)
        self.assertNotIn("building_generator_districts_1", building_ids)
        self.assertIn("building_generator_generic", building_ids)
        self.assertIn("building_resource_silo", building_ids)

    def test_planning_state_exposes_fixed_city_zone_limit(self) -> None:
        value = snapshot()
        value["planets"][0]["capital_tier"] = 2
        district = value["planets"][0]["districts"][0]
        district.update(
            {
                "zone_slot_capacity": 3,
                "zone_slot_count_is_fixed_by_district_type": True,
                "additional_district_levels_unlock_zone_slots": False,
                "specialization_zone_capacity": 2,
                "specialization_zones_occupied": 1,
                "specialization_zones_pending": 0,
                "specialization_zone_slots_available": 1,
                "specialization_zone_slots_locked": 0,
                "zone_slot_statuses": [
                    {"slot_selector": 0, "state": "occupied"},
                    {"slot_selector": 1, "state": "occupied"},
                    {"slot_selector": 2, "state": "available"},
                ],
                "locked_zone_slots": [],
            }
        )

        planned_district = planning_snapshot(value)["planets"][0][
            "districts"
        ][0]
        self.assertEqual(
            planning_snapshot(value)["planets"][0]["capital_tier"],
            2,
        )
        self.assertEqual(planned_district["zone_slot_capacity"], 3)
        self.assertEqual(
            planned_district["specialization_zone_capacity"],
            2,
        )
        self.assertEqual(planned_district["specialization_zones_pending"], 0)
        self.assertFalse(
            planned_district["additional_district_levels_unlock_zone_slots"]
        )
        self.assertEqual(
            planned_district["zone_slot_statuses"][2]["state"],
            "available",
        )

    def test_queue_depth_limit_stops_additional_construction(self) -> None:
        value = snapshot()
        value["planets"][0]["construction"] = {
            "has_pending_construction": True,
            "queued_item_ids": [1, 2],
            "queue_depth": 2,
            "pending_items": [
                {
                    "item_id": 1,
                    "kind": "building",
                    "building_id": "building_research_lab_1",
                    "zone_id": 201,
                },
                {
                    "item_id": 2,
                    "kind": "building",
                    "building_id": "building_precinct_house",
                    "zone_id": 200,
                },
            ],
        }

        self.assertEqual(
            build_candidates(value, self.capabilities, config()),
            [],
        )

    def test_unknown_pending_item_fails_closed(self) -> None:
        value = snapshot()
        value["planets"][0]["construction"] = {
            "has_pending_construction": True,
            "queued_item_ids": [1],
            "queue_depth": 1,
            "pending_items": [
                {
                    "item_id": 1,
                    "kind": "unknown",
                }
            ],
        }

        self.assertEqual(
            build_candidates(value, self.capabilities, config()),
            [],
        )

    def test_zone_catalog_contains_only_the_selected_specializations(self) -> None:
        self.assertEqual(
            set(self.capabilities["zones"]),
            {
                "zone_foundry",
                "zone_factory",
                "zone_research",
                "zone_research_physics",
                "zone_research_society",
                "zone_research_engineering",
                "zone_unity",
                "zone_trade",
                "zone_energy",
                "zone_minerals",
                "zone_food",
            },
        )

    def test_manifest_keeps_source_save_identity(self) -> None:
        value = snapshot()
        value["source_save"].update(
            {"revision": 7, "campaign_id": "a" * 32}
        )
        candidates = build_candidates(value, self.capabilities, config())
        selected = candidates[0]
        plan = {
            "confidence": 0.9,
            "reasoning_zh": "测试",
        }
        manifest = execution_manifest(
            plan,
            selected,
            value,
            self.capabilities,
        )
        self.assertEqual(manifest["source_save_path"], "/save/test.sav")
        self.assertEqual(manifest["source_save_sha256"], "abc")
        self.assertEqual(manifest["source_save_revision"], 7)
        self.assertEqual(manifest["source_campaign_id"], "a" * 32)


if __name__ == "__main__":
    unittest.main()
