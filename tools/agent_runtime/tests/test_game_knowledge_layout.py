from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from game_knowledge import (  # noqa: E402
    _zone_slot_unlock_status,
    enrich_snapshot_layout,
)


class LayoutKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.game_root = Path(self.temporary.name)
        (self.game_root / "common/zones").mkdir(parents=True)
        (self.game_root / "common/districts").mkdir(parents=True)
        (self.game_root / "common/deposits").mkdir(parents=True)
        (self.game_root / "common/static_modifiers").mkdir(parents=True)
        (self.game_root / "common/zone_slots").mkdir(parents=True)
        (self.game_root / "common/buildings").mkdir(parents=True)
        (self.game_root / "common/scripted_variables").mkdir(parents=True)
        (self.game_root / "common/inline_scripts/zones").mkdir(parents=True)
        (self.game_root / "launcher-settings.json").write_text(
            json.dumps(
                {
                    "version": "Pegasus v4.4.6",
                    "rawVersion": "4.4.6",
                    "modsCompatibilityVersion": "4.4.*",
                    "distPlatform": "steam",
                }
            ),
            encoding="utf-8",
        )
        (self.game_root / "common/zones/00_zones.txt").write_text(
            """
@zone_cost = 500
zone_default = {
    max_buildings = 6
    zone_sets = { default }
}
zone_industrial = {
    resources = { cost = { minerals = @zone_cost } }
    inline_script = {
        script = zones/shared_industrial_zone
    }
    zone_sets = { urban }
}
""",
            encoding="utf-8",
        )
        (
            self.game_root
            / "common/inline_scripts/zones/shared_industrial_zone.txt"
        ).write_text(
            """
planet_modifier = {
    zone_building_slots_add = 3
}
""",
            encoding="utf-8",
        )
        (self.game_root / "common/districts/00_districts.txt").write_text(
            """
@base_cost = 300
district_city = {
    resources = {
        cost = { minerals = 500 trigger = { always = yes } }
        cost = { food = 500 trigger = { always = no } }
    }
    zone_slots = {
        slot_city_government
        slot_city_01
        slot_city_02
    }
}
district_generator = {
    shared_capacity_modifier = district_generator
    prerequisites = { tech_power_plant_1 }
}
district_mining = {
    resources = { cost = { minerals = @base_cost } }
    shared_capacity_modifier = district_mining
    prerequisites = { tech_mechanized_mining }
}
district_farming = {
    shared_capacity_modifier = district_farming
    prerequisites = { tech_industrial_farming }
}
""",
            encoding="utf-8",
        )
        (self.game_root / "common/deposits/00_deposits.txt").write_text(
            """
d_dense_jungle = {
    category = deposit_cat_blockers_natural
    planet_modifier = { planet_max_districts_add = -1 }
}
d_toxic_kelp = {
    category = deposit_cat_blockers_natural
    planet_modifier = { planet_max_districts_add = -1 }
}
d_veiny_cliffs = {
    planet_modifier = { district_mining_max_add = 1 }
}
d_crystal_forest = {
    planet_modifier = { district_mining_max_add = 3 }
}
d_betharian_deposit = {
    planet_modifier = { district_mining_max_add = 4 }
}
d_farming_basin = {
    planet_modifier = { district_farming_max_add = 10 }
}
d_generator_basin = {
    planet_modifier = { district_generator_max_add = 3 }
}
""",
            encoding="utf-8",
        )
        (
            self.game_root
            / "common/static_modifiers/00_static_modifiers.txt"
        ).write_text(
            """
high_gravity = {
    planet_max_districts_add = 2
}
subterranean_wildlife = {
    planet_max_districts_add = 2
    district_mining_max_add = 4
}
""",
            encoding="utf-8",
        )
        (self.game_root / "common/zone_slots/00_zone_slots.txt").write_text(
            """
slot_city_government = {
    start = zone_default
    included_zone_sets = { zone_default }
    unlock = { always = yes }
}
slot_city_01 = {
    included_zone_sets = { urban }
    unlock = { always = yes }
}
slot_city_02 = {
    included_zone_sets = { urban }
    unlock = { has_upgraded_capital = yes }
}
""",
            encoding="utf-8",
        )
        (self.game_root / "common/buildings/00_capitals.txt").write_text(
            """
building_colony_shelter = {
    capital_tier = 1
}
building_capital = {
    capital_tier = 2
}
building_factory_1 = {
    can_build = yes
}
building_foundry_1 = {
    can_build = yes
}
building_research_lab_1 = {
    resources = {
        inline_script = {
            script = buildings/nomadic_cost_switcher
            REGULAR_RESOURCE = minerals
            COST = @b1_minerals
        }
    }
    upgrades = { building_research_lab_2 }
}
building_research_lab_2 = {
    can_build = no
    resources = {
        inline_script = {
            script = buildings/nomadic_cost_switcher
            REGULAR_RESOURCE = minerals
            COST = @b2_minerals
        }
        cost = { exotic_gases = @b2_rare_cost }
    }
    prerequisites = { tech_basic_science_lab_2 }
}
building_energy_nexus = {
    can_build = no
    resources = {
        inline_script = {
            script = buildings/nomadic_cost_switcher_with_additional_trigger
            TRIGGER = "has_ringworld_output_boost = no"
            REGULAR_RESOURCE = minerals
            COST = @b2_minerals
        }
        cost = {
            trigger = { has_ringworld_output_boost = no }
            rare_crystals = @b2_rare_cost
        }
        inline_script = {
            script = buildings/nomadic_cost_switcher_with_additional_trigger
            TRIGGER = "has_ringworld_output_boost = yes"
            REGULAR_RESOURCE = minerals
            COST = 900
        }
        cost = {
            trigger = { has_ringworld_output_boost = yes }
            rare_crystals = 200
        }
    }
}
building_mining_districts_1 = {
    planet_modifier = { district_mining_max_add = 2 }
}
building_farming_districts_1 = {
    planet_modifier = { district_farming_max_add = 2 }
}
""",
            encoding="utf-8",
        )
        (self.game_root / "common/scripted_variables/00_values.txt").write_text(
            "@b1_minerals = 400\n@b2_minerals = 600\n@b2_rare_cost = 50\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(self) -> dict:
        return {"game_root": str(self.game_root)}

    def test_resource_zone_slot_unlocks_from_known_technology(self) -> None:
        expected = {
            "slot_energy": "tech_power_hub_1",
            "slot_minerals": "tech_mineral_purification_1",
            "slot_food": "tech_food_processing_1",
        }
        for slot_id, technology in expected.items():
            with self.subTest(slot_id=slot_id):
                status = _zone_slot_unlock_status(
                    {
                        "status": "available",
                        "slot_id": slot_id,
                        "definition_block": (
                            "has_tech_or_functional_civic = yes"
                        ),
                    },
                    planet={},
                    capital_tier=0,
                    known_technologies={technology},
                )
                self.assertTrue(status["unlocked"])
                self.assertEqual(
                    status["reason"],
                    "required_technology_known",
                )
                self.assertEqual(status["required_technology"], technology)

    def test_resource_zone_slot_fails_closed_for_unmodeled_civic(self) -> None:
        status = _zone_slot_unlock_status(
            {
                "status": "available",
                "slot_id": "slot_food",
                "definition_block": "has_tech_or_functional_civic = yes",
            },
            planet={},
            capital_tier=0,
            known_technologies=set(),
        )
        self.assertFalse(status["unlocked"])
        self.assertEqual(
            status["reason"],
            "requires_technology_or_unmodeled_functional_civic",
        )

    @staticmethod
    def capabilities() -> dict:
        return {
            "buildings": {
                "building_research_lab_1": {"enabled": True},
            },
            "districts": {
                "district_city": {"enabled": True},
                "district_generator": {"enabled": True},
                "district_mining": {"enabled": True},
                "district_farming": {"enabled": True},
            },
            "zones": {
                "zone_industrial": {
                    "enabled": True,
                    "district_type": "district_city",
                }
            },
            "building_upgrades": [
                {
                    "from_building_id": "building_research_lab_1",
                    "to_building_id": "building_research_lab_2",
                    "enabled": True,
                },
                {
                    "from_building_id": "building_energy_grid",
                    "to_building_id": "building_energy_nexus",
                    "enabled": True,
                },
            ],
        }

    @staticmethod
    def snapshot(*, full_district: bool = True) -> dict:
        default_zone = {
            "zone_id": 10,
            "slot_selector": 0,
            "type": "zone_default",
            "buildings": [
                {
                    "object_id": 1,
                    "type": (
                        "building_capital"
                        if full_district
                        else "building_colony_shelter"
                    ),
                    "position": 0,
                }
            ],
        }
        zones = [default_zone]
        references: list[int | None] = [10]
        if full_district:
            industrial_zone = {
                "zone_id": 11,
                "slot_selector": 1,
                "type": "zone_industrial",
                "buildings": [
                    {
                        "object_id": 2,
                        "type": "building_factory_1",
                        "position": 0,
                    },
                    {
                        "object_id": 3,
                        "type": "building_foundry_1",
                        "position": 1,
                    },
                ],
            }
            zones.append(industrial_zone)
            references.extend([11, 12])
            zones.append(
                {
                    "zone_id": 12,
                    "slot_selector": 2,
                    "type": "zone_industrial",
                    "buildings": [],
                }
            )

        district_zones = [dict(zone) for zone in zones]
        flat_zones = [
            {
                **dict(zone),
                "district_id": 5,
                "district_type": "district_city",
                "district_level": 3 if full_district else 1,
            }
            for zone in zones
        ]
        return {
            "source_save": {"version": "Pegasus v4.4.6"},
            "data_quality": {"precision": {}},
            "planets": [
                {
                    "planet_id": 3,
                    "planet_size": 16,
                    "deposit_types": [],
                    "districts": [
                        {
                            "district_id": 5,
                            "type": "district_city",
                            "level": 3 if full_district else 1,
                            "zone_slot_references": references,
                            "zones": district_zones,
                        }
                    ],
                    "zones": flat_zones,
                }
            ],
        }

    def test_combines_save_positions_with_installed_zone_capacity(self) -> None:
        snapshot = self.snapshot()
        enrich_snapshot_layout(
            self.config(),
            self.capabilities(),
            snapshot,
        )

        industrial = snapshot["planets"][0]["zones"][1]
        self.assertEqual(industrial["building_slot_capacity"], 3)
        self.assertEqual(industrial["occupied_building_positions"], [0, 1])
        self.assertEqual(industrial["available_building_positions"], [2])
        self.assertEqual(
            snapshot["planets"][0]["districts"][0]["available_zone_slots"],
            [],
        )

    def test_default_zone_uses_installed_capacity_not_city_level(self) -> None:
        snapshot = self.snapshot(full_district=False)
        enrich_snapshot_layout(
            self.config(),
            self.capabilities(),
            snapshot,
        )

        default_zone = snapshot["planets"][0]["zones"][0]
        self.assertEqual(default_zone["building_slot_capacity"], 6)
        self.assertEqual(
            default_zone["available_building_positions"],
            [1, 2, 3, 4, 5],
        )

    def test_reads_regular_empire_construction_costs_from_local_rules(self) -> None:
        snapshot = self.snapshot()
        enrich_snapshot_layout(
            self.config(),
            self.capabilities(),
            snapshot,
        )

        costs = snapshot["layout_rules"]["construction_costs"]
        self.assertEqual(
            costs["build_building"]["building_research_lab_1"]["cost"],
            {"minerals": 400},
        )
        self.assertEqual(
            costs["upgrade_building"]["building_research_lab_2"]["cost"],
            {"minerals": 600, "exotic_gases": 50},
        )
        self.assertEqual(
            costs["replace_building"]["building_research_lab_1"]["cost"],
            {"minerals": 400},
        )
        conditional = costs["upgrade_building"]["building_energy_nexus"]
        self.assertEqual(conditional["status"], "conditional")
        self.assertIn(
            {
                "condition": {"has_ringworld_output_boost": False},
                "cost": {"minerals": 600, "rare_crystals": 50},
            },
            conditional["conditional_costs"],
        )
        self.assertEqual(
            costs["build_district"]["district_mining"]["cost"],
            {"minerals": 300},
        )
        self.assertEqual(
            costs["build_zone"]["zone_industrial"]["cost"],
            {"minerals": 500},
        )
        self.assertEqual(
            costs["build_district"]["district_city"]["status"],
            "ambiguous",
        )

    def test_verifies_direct_building_upgrade_from_installed_rules(self) -> None:
        snapshot = self.snapshot()
        enrich_snapshot_layout(
            self.config(),
            self.capabilities(),
            snapshot,
        )

        rule = snapshot["layout_rules"]["building_upgrade_rules"][
            "building_research_lab_1"
        ]["building_research_lab_2"]
        self.assertEqual(rule["status"], "available")
        self.assertEqual(rule["prerequisites"], ["tech_basic_science_lab_2"])

    def test_locked_second_city_slot_is_not_reported_available(self) -> None:
        snapshot = self.snapshot(full_district=False)
        enrich_snapshot_layout(
            self.config(),
            self.capabilities(),
            snapshot,
        )

        available = snapshot["planets"][0]["districts"][0][
            "available_zone_slots"
        ]
        self.assertEqual(
            [(item["slot_selector"], item["slot_id"]) for item in available],
            [(1, "slot_city_01")],
        )
        district = snapshot["planets"][0]["districts"][0]
        self.assertEqual(district["zone_slot_capacity"], 3)
        self.assertEqual(district["specialization_zone_capacity"], 2)
        self.assertEqual(district["specialization_zones_occupied"], 0)
        self.assertEqual(district["specialization_zones_pending"], 0)
        self.assertEqual(
            district["specialization_zone_slots_available"],
            1,
        )
        self.assertEqual(district["specialization_zone_slots_locked"], 1)
        self.assertFalse(
            district["additional_district_levels_unlock_zone_slots"]
        )
        self.assertEqual(
            district["locked_zone_slots"][0]["unlock"]["reason"],
            "requires_upgraded_capital",
        )

    def test_more_city_districts_do_not_create_more_zone_slots(self) -> None:
        snapshot = self.snapshot(full_district=False)
        snapshot["planets"][0]["districts"][0]["level"] = 9
        enrich_snapshot_layout(
            self.config(),
            self.capabilities(),
            snapshot,
        )

        district = snapshot["planets"][0]["districts"][0]
        self.assertEqual(district["zone_slot_capacity"], 3)
        self.assertEqual(district["specialization_zone_capacity"], 2)
        self.assertEqual(
            [item["slot_selector"] for item in district["available_zone_slots"]],
            [1],
        )

    def test_pending_zone_reserves_its_fixed_city_slot(self) -> None:
        snapshot = self.snapshot()
        planet = snapshot["planets"][0]
        city = planet["districts"][0]
        city["zone_slot_references"] = [10, 11, None]
        city["zones"] = city["zones"][:2]
        planet["zones"] = planet["zones"][:2]
        planet["construction"] = {
            "pending_items": [
                {
                    "item_id": 99,
                    "kind": "zone",
                    "district_id": 5,
                    "slot_selector": 2,
                    "zone_type": "zone_research_society",
                }
            ]
        }
        enrich_snapshot_layout(
            self.config(),
            self.capabilities(),
            snapshot,
        )

        self.assertEqual(city["available_zone_slots"], [])
        self.assertEqual(city["specialization_zones_occupied"], 1)
        self.assertEqual(city["specialization_zones_pending"], 1)
        self.assertEqual(city["specialization_zone_slots_available"], 0)
        self.assertEqual(city["zone_slot_statuses"][2]["state"], "pending")
        self.assertEqual(
            city["zone_slot_statuses"][2]["pending_zone_type"],
            "zone_research_society",
        )

    def test_version_mismatch_fails_closed(self) -> None:
        snapshot = self.snapshot()
        snapshot["source_save"]["version"] = "Pegasus v4.5.0"
        enrich_snapshot_layout(
            self.config(),
            self.capabilities(),
            snapshot,
        )

        self.assertEqual(snapshot["layout_rules"]["status"], "version_mismatch")
        self.assertNotIn(
            "available_building_positions",
            snapshot["planets"][0]["zones"][0],
        )

    def test_computes_mining_capacity_floor_from_saved_deposits(self) -> None:
        snapshot = self.snapshot(full_district=False)
        snapshot["planets"][0]["deposit_types"] = [
            "d_dense_jungle",
            "d_toxic_kelp",
            "d_veiny_cliffs",
            "d_veiny_cliffs",
            "d_crystal_forest",
            "d_betharian_deposit",
        ]
        enrich_snapshot_layout(
            self.config(),
            self.capabilities(),
            snapshot,
        )

        capacity = snapshot["planets"][0]["district_capacity"]
        self.assertEqual(capacity["total"]["capacity"], 16)
        self.assertEqual(capacity["total"]["blocked_slots"], 2)
        self.assertEqual(capacity["total"]["usable_capacity"], 14)
        self.assertEqual(capacity["total"]["remaining"], 13)
        self.assertEqual(
            capacity["by_type"]["district_mining"]["capacity_floor"],
            9,
        )
        self.assertEqual(
            capacity["by_type"]["district_mining"][
                "remaining_buildable_floor"
            ],
            9,
        )

    def test_combines_static_modifiers_buildings_and_blocked_slots(self) -> None:
        snapshot = self.snapshot(full_district=False)
        planet = snapshot["planets"][0]
        planet["deposit_types"] = [
            "d_dense_jungle",
            "d_toxic_kelp",
            "d_veiny_cliffs",
            "d_veiny_cliffs",
            "d_crystal_forest",
            "d_betharian_deposit",
            "d_farming_basin",
            "d_generator_basin",
        ]
        planet["active_modifiers"] = [
            "high_gravity",
            "subterranean_wildlife",
        ]
        extra_buildings = [
            {
                "object_id": 4,
                "type": "building_mining_districts_1",
                "position": 1,
            },
            {
                "object_id": 5,
                "type": "building_farming_districts_1",
                "position": 2,
            },
        ]
        planet["zones"][0]["buildings"].extend(extra_buildings)

        enrich_snapshot_layout(
            self.config(),
            self.capabilities(),
            snapshot,
        )

        capacity = planet["district_capacity"]
        self.assertEqual(capacity["total"]["capacity"], 20)
        self.assertEqual(capacity["total"]["blocked_slots"], 2)
        self.assertEqual(capacity["total"]["usable_capacity"], 18)
        self.assertEqual(
            capacity["by_type"]["district_generator"]["capacity_floor"],
            3,
        )
        self.assertEqual(
            capacity["by_type"]["district_mining"]["capacity_floor"],
            15,
        )
        self.assertEqual(
            capacity["by_type"]["district_farming"]["capacity_floor"],
            12,
        )
        self.assertEqual(
            capacity["by_type"]["district_mining"][
                "remaining_buildable_floor"
            ],
            15,
        )


if __name__ == "__main__":
    unittest.main()
