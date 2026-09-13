from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iag.stellaris.state.ship_profiles import (
    clone_ship_design,
    customize_ship_design,
    extract_ship_profiles,
    ship_design_options,
)

SAVE_FIXTURE = r"""
date="2200.07.01"
player={ { name="Player" country=0 } }
country=
{
 0=
 {
  ship_design_collection={ ship_design={ 100 } }
  tech_status=
  {
   technology="tech_lasers_1"
   level=1
   technology="tech_mass_drivers_1"
   level=1
   technology="tech_flak_batteries_1"
   level=1
  }
 }
}
ship_design=
{
 100=
 {
  name={ key="TEST_CORVETTE" literal=yes }
  graphical_culture="mammalian_01"
  growth_stages=
  {
   {
    ship_size="corvette"
    parent=4294967295
    section=
    {
     template="CORVETTE_MID_S2PD1"
     slot="mid"
     component={ slot="SMALL_GUN_01" template="FLAK_BATTERY_1" }
     component={ slot="SMALL_GUN_02" template="SMALL_MASS_DRIVER_1" }
     component={ slot="SMALL_GUN_03" template="SMALL_MASS_DRIVER_1" }
    }
   }
  }
 }
}
construction=
{
 queue_mgr=
 {
  queues=
  {
   2=
   {
    owner=0
    type=0
    location={ type=0 id=0 }
    items={ }
   }
   3=
   {
    owner=0
    type=0
    location={ type=0 id=0 }
    items={ 17 }
   }
  }
 }
 item_mgr=
 {
  items=
  {
   17=
   {
    queue=3
    paying_country=0
    progress=12
    progress_needed=100
    buildable_ship=
    {
     ship_design_implementation=
     { design=100 upgrade=4294967295 growth_stage=0 }
     orbitable={ starbase=0 }
    }
   }
  }
 }
}
starbase_mgr=
{
 starbases=
 {
  0=
  {
   level="starbase_level_starport"
   build_queue=2
   shipyard_build_queue=3
   station=0
   modules={ 0=shipyard }
  }
 }
}
galactic_object=
{
 13=
 {
  name={ key="NAME_Sol" }
  starbases={ 0 }
 }
}
"""

AUTO_DESIGN_DISABLED_FIXTURE = SAVE_FIXTURE.replace(
    "ship_design_collection={ ship_design={ 100 } }",
    "ship_design_collection={ ship_design={ 100 } auto_gen_design=no }",
).replace(
    ' graphical_culture="mammalian_01"',
    ' auto_gen_design=yes\n graphical_culture="mammalian_01"',
    1,
)
AUTO_DESIGN_ENABLED_FIXTURE = AUTO_DESIGN_DISABLED_FIXTURE.replace(
    "auto_gen_design=no",
    "auto_gen_design=yes",
    1,
)


SECTION_RULE = r"""
ship_section_template = {
 key = "CORVETTE_MID_S2PD1"
 ship_size = corvette
 fits_on_slot = mid
 component_slot = {
  name = "SMALL_GUN_01"
  template = "point_defence_turret"
 }
 component_slot = {
  name = "SMALL_GUN_02"
  template = "small_turret"
 }
 component_slot = {
  name = "SMALL_GUN_03"
  template = "small_turret"
 }
 small_utility_slots = 3
 aux_utility_slots = 1
}
"""


COMPONENT_RULES = r"""
weapon_component_template = {
 key = "SMALL_RED_LASER"
 size = small
 type = instant
 prerequisites = { "tech_lasers_1" }
 tags = { weapon_type_energy s_slot }
 potential = {
  OR = {
   from = { country_uses_bio_ships = no }
   is_arkship_ship = yes
  }
 }
}
weapon_component_template = {
 key = "SMALL_BLUE_LASER"
 size = small
 type = instant
 prerequisites = { "tech_lasers_2" }
 tags = { weapon_type_energy s_slot }
}
weapon_component_template = {
 key = "SMALL_MASS_DRIVER_1"
 size = small
 type = instant
 prerequisites = { "tech_mass_drivers_1" }
 tags = { weapon_type_kinetic s_slot }
}
weapon_component_template = {
 key = "FLAK_BATTERY_1"
 size = point_defence
 type = point_defence
 prerequisites = { "tech_flak_batteries_1" }
 tags = { weapon_type_point_defense }
}
"""

MUTATION_COMPONENT_RULE = r"""
weapon_component_template = {
 key = "RED_EYE_BEAM_SMALL"
 size = small
 type = instant
 prerequisites = { "tech_lasers_1" }
 tags = { weapon_type_energy s_slot }
}
"""

MULTISECTION_RULES = r"""
ship_section_template = {
 key = "BATTLESHIP_BOW_L1"
 ship_size = battleship
 fits_on_slot = bow
 component_slot = { name = "LARGE_GUN_01" template = "large_turret" }
 large_utility_slots = 1
}
ship_section_template = {
 key = "BATTLESHIP_BOW_M1"
 ship_size = battleship
 fits_on_slot = bow
 component_slot = { name = "MEDIUM_GUN_01" template = "medium_turret" }
 large_utility_slots = 1
}
ship_section_template = {
 key = "BATTLESHIP_STERN_S1"
 ship_size = battleship
 fits_on_slot = stern
 component_slot = { name = "SMALL_GUN_01" template = "small_turret" }
}
ship_section_template = {
 key = "WRONG_SIZE_BOW"
 ship_size = cruiser
 fits_on_slot = bow
 component_slot = { name = "MEDIUM_GUN_01" template = "medium_turret" }
}
"""

MULTISECTION_COMPONENTS = r"""
weapon_component_template = {
 key = "LARGE_TEST_GUN"
 size = large
 power = -10
 prerequisites = { "tech_test_weapons" }
 tags = { l_slot }
}
weapon_component_template = {
 key = "MEDIUM_TEST_GUN"
 size = medium
 power = -8
 prerequisites = { "tech_test_weapons" }
 tags = { m_slot }
}
weapon_component_template = {
 key = "MEDIUM_BASE_GUN"
 size = medium
 power = -4
 tags = { m_slot }
}
weapon_component_template = {
 key = "SMALL_TEST_GUN"
 size = small
 power = -5
 prerequisites = { "tech_test_weapons" }
 tags = { s_slot }
}
utility_component_template = {
 key = "LARGE_TEST_SHIELD"
 size = large
 power = -5
 prerequisites = { "tech_test_shields" }
 component_set = "TEST_SHIELD"
}
utility_component_template = {
 key = "BATTLESHIP_TEST_REACTOR_1"
 size = small
 power = 100
 initial = yes
 component_set = "power_core"
 potential = { ship_uses_battleship_reactors = yes }
}
utility_component_template = {
 key = "BATTLESHIP_TEST_REACTOR_2"
 size = small
 power = 150
 prerequisites = { "tech_test_reactor" }
 component_set = "power_core"
 potential = { ship_uses_battleship_reactors = yes }
}
utility_component_template = {
 key = "TEST_COMPUTER_LINE_1"
 size = small
 power = -5
 initial = yes
 component_set = "combat_computers"
 ship_behavior = "line"
 potential = { ship_uses_line_role = yes }
}
utility_component_template = {
 key = "TEST_COMPUTER_LINE_2"
 size = small
 power = -5
 prerequisites = { "tech_test_computer" }
 component_set = "combat_computers"
 ship_behavior = "line"
 potential = { ship_uses_line_role = yes }
}
utility_component_template = {
 key = "TEST_COMPUTER_TORPEDO"
 size = small
 power = -5
 prerequisites = { "tech_test_computer" }
 component_set = "combat_computers"
 ship_behavior = "torpedo"
 potential = { ship_uses_torpedo_role = yes }
}
utility_component_template = {
 key = "BATTLESHIP_TEST_AURA"
 size = large
 power = -10
 component_set = "ship_aura_components"
}
"""


def multisection_profile() -> dict[str, object]:
    return {
        "owner_country_id": 0,
        "known_technologies": [
            "tech_test_weapons",
            "tech_test_shields",
            "tech_test_reactor",
            "tech_test_computer",
        ],
        "component_choice_index": [],
        "designs": [
            {
                "design_id": 200,
                "name_key": "SOURCE_BATTLESHIP",
                "graphical_culture": "mammalian_01",
                "upgrade_components_automatically": False,
                "clone_protocol_supported": True,
                "growth_stages": [
                    {
                        "ship_size": "battleship",
                        "parent": 0xFFFFFFFF,
                        "sections": [
                            {
                                "template": "BATTLESHIP_BOW_L1",
                                "slot": "bow",
                                "components": [
                                    {
                                        "slot": "LARGE_GUN_01",
                                        "component_id": "LARGE_TEST_GUN",
                                    },
                                    {
                                        "slot": "LARGE_UTILITY_1",
                                        "component_id": "LARGE_TEST_SHIELD",
                                    },
                                ],
                            },
                            {
                                "template": "BATTLESHIP_STERN_S1",
                                "slot": "stern",
                                "components": [
                                    {
                                        "slot": "SMALL_GUN_01",
                                        "component_id": "SMALL_TEST_GUN",
                                    }
                                ],
                            },
                        ],
                        "required_components": [
                            "BATTLESHIP_TEST_REACTOR_1",
                            "TEST_COMPUTER_LINE_1",
                            "BATTLESHIP_TEST_AURA",
                        ],
                    }
                ],
            }
        ],
    }


class ShipProfileTests(unittest.TestCase):
    def _game_root(self, root: Path) -> Path:
        section = root / "common" / "section_templates" / "corvette.txt"
        section.parent.mkdir(parents=True)
        section.write_text(SECTION_RULE, encoding="utf-8")
        components = root / "common" / "component_templates" / "weapons.txt"
        components.parent.mkdir(parents=True)
        components.write_text(COMPONENT_RULES, encoding="utf-8")
        mutation = components.parent / "01_mutation_weapon_components.txt"
        mutation.write_text(MUTATION_COMPONENT_RULE, encoding="utf-8")
        multisection = section.parent / "battleship.txt"
        multisection.write_text(MULTISECTION_RULES, encoding="utf-8")
        extra_components = components.parent / "ship_design_test.txt"
        extra_components.write_text(MULTISECTION_COMPONENTS, encoding="utf-8")
        return root

    def test_numeric_shipyard_queue_is_resolved_from_starbase_link(self) -> None:
        profile = extract_ship_profiles(SAVE_FIXTURE)

        self.assertEqual(len(profile["shipyards"]), 1)
        shipyard = profile["shipyards"][0]
        self.assertEqual(shipyard["build_queue_id"], 3)
        self.assertEqual(shipyard["queue_type"], "0")
        self.assertEqual(
            shipyard["queue_link_authority"],
            "starbase_shipyard_build_queue",
        )
        self.assertEqual(shipyard["queue_length"], 1)
        self.assertEqual(shipyard["queued_items"][0]["design_id"], 100)

    def test_disabled_automatic_design_templates_are_marked_hidden(self) -> None:
        profile = extract_ship_profiles(AUTO_DESIGN_DISABLED_FIXTURE)
        design = profile["designs"][0]

        self.assertFalse(profile["automatic_design_enabled"])
        self.assertEqual(profile["country_design_record_count"], 1)
        self.assertEqual(profile["player_visible_design_count"], 0)
        self.assertEqual(profile["hidden_autogenerated_design_count"], 1)
        self.assertTrue(design["auto_generated"])
        self.assertFalse(design["player_visible"])
        self.assertFalse(design["model_clone_source_eligible"])
        self.assertEqual(design["visibility"], "hidden_disabled_autogenerated")
        with self.assertRaisesRegex(ValueError, "hidden autogenerated template"):
            ship_design_options(
                profile,
                source_design_id=100,
                game_root=Path("unused"),
            )

    def test_enabled_automatic_design_templates_remain_visible(self) -> None:
        profile = extract_ship_profiles(AUTO_DESIGN_ENABLED_FIXTURE)
        design = profile["designs"][0]

        self.assertTrue(profile["automatic_design_enabled"])
        self.assertEqual(profile["player_visible_design_count"], 1)
        self.assertEqual(profile["hidden_autogenerated_design_count"], 0)
        self.assertTrue(design["player_visible"])
        self.assertTrue(design["model_clone_source_eligible"])
        self.assertEqual(design["visibility"], "active_autogenerated")

    def test_installed_rules_add_only_tech_unlocked_slot_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game_root = self._game_root(Path(directory))
            profile = extract_ship_profiles(
                SAVE_FIXTURE,
                game_root=game_root,
            )

        choices = {
            item["component_slot"]: item["component_ids"]
            for item in profile["component_choice_index"]
        }
        self.assertEqual(choices["SMALL_GUN_01"], ["FLAK_BATTERY_1"])
        self.assertIn("SMALL_RED_LASER", choices["SMALL_GUN_02"])
        self.assertIn("SMALL_MASS_DRIVER_1", choices["SMALL_GUN_02"])
        self.assertNotIn("SMALL_BLUE_LASER", choices["SMALL_GUN_02"])
        self.assertNotIn("RED_EYE_BEAM_SMALL", choices["SMALL_GUN_02"])

        blueprint = clone_ship_design(
            profile,
            source_design_id=100,
            new_name="RULE_BACKED",
            component_replacements=[
                {
                    "section_slot": "mid",
                    "component_slot": "SMALL_GUN_02",
                    "component_id": "SMALL_RED_LASER",
                }
            ],
        )
        self.assertEqual(
            blueprint["component_replacements"][0]["to_component_id"],
            "SMALL_RED_LASER",
        )
        with self.assertRaisesRegex(ValueError, "not legal"):
            clone_ship_design(
                profile,
                source_design_id=100,
                new_name="ILLEGAL_PD",
                component_replacements=[
                    {
                        "section_slot": "mid",
                        "component_slot": "SMALL_GUN_01",
                        "component_id": "SMALL_MASS_DRIVER_1",
                    }
                ],
            )

    def test_full_design_supports_sections_required_components_and_auto_upgrade(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game_root = self._game_root(Path(directory))
            profile = multisection_profile()
            options = ship_design_options(
                profile,
                source_design_id=200,
                game_root=game_root,
                section_template="BATTLESHIP_BOW_M1",
            )
            blueprint = customize_ship_design(
                profile,
                source_design_id=200,
                new_name="CUSTOM_BATTLESHIP",
                section_replacements=[
                    {
                        "section_slot": "bow",
                        "section_template": "BATTLESHIP_BOW_M1",
                        "components": [
                            {
                                "component_slot": "MEDIUM_GUN_01",
                                "component_id": "MEDIUM_TEST_GUN",
                            }
                        ],
                    }
                ],
                component_replacements=[
                    {
                        "section_slot": "bow",
                        "component_slot": "LARGE_UTILITY_1",
                        "component_id": "LARGE_TEST_SHIELD",
                    }
                ],
                required_component_replacements=[
                    {
                        "component_set": "power_core",
                        "component_id": "BATTLESHIP_TEST_REACTOR_2",
                    },
                    {
                        "component_set": "combat_computers",
                        "component_id": "TEST_COMPUTER_LINE_2",
                    },
                ],
                upgrade_components_automatically=True,
                game_root=game_root,
            )

        selected = options["selected_section"]
        self.assertIsNotNone(selected)
        medium_slot = next(
            item
            for item in selected["component_slots"]
            if item["component_slot"] == "MEDIUM_GUN_01"
        )
        self.assertIn(
            "MEDIUM_TEST_GUN",
            {item["component_id"] for item in medium_slot["components"]},
        )
        self.assertIn(
            "MEDIUM_BASE_GUN",
            {item["component_id"] for item in medium_slot["components"]},
        )
        utility_slot = next(
            item
            for item in selected["component_slots"]
            if item["component_slot"] == "LARGE_UTILITY_1"
        )
        self.assertNotIn(
            "BATTLESHIP_TEST_AURA",
            {item["component_id"] for item in utility_slot["components"]},
        )
        required_sets = {
            item["component_set"] for item in options["required_component_options"]
        }
        self.assertIn("ship_aura_components", required_sets)
        computer_options = next(
            item
            for item in options["required_component_options"]
            if item["component_set"] == "combat_computers"
        )
        self.assertNotIn(
            "TEST_COMPUTER_TORPEDO",
            {item["component_id"] for item in computer_options["components"]},
        )
        stage = blueprint["growth_stages"][0]
        self.assertEqual(stage["sections"][0]["template"], "BATTLESHIP_BOW_M1")
        self.assertEqual(len(stage["sections"][0]["components"]), 2)
        self.assertIn("BATTLESHIP_TEST_REACTOR_2", stage["required_components"])
        self.assertTrue(blueprint["upgrade_components_automatically"])
        self.assertEqual(blueprint["power_balance"]["status"], "validated")

    def test_full_design_rejects_wrong_section_and_component_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game_root = self._game_root(Path(directory))
            profile = multisection_profile()
            with self.assertRaisesRegex(ValueError, "not unlocked and legal"):
                customize_ship_design(
                    profile,
                    source_design_id=200,
                    new_name="WRONG_SECTION",
                    section_replacements=[
                        {
                            "section_slot": "bow",
                            "section_template": "WRONG_SIZE_BOW",
                            "components": [],
                        }
                    ],
                    game_root=game_root,
                )
            with self.assertRaisesRegex(ValueError, "not unlocked and legal"):
                customize_ship_design(
                    profile,
                    source_design_id=200,
                    new_name="WRONG_COMPONENT",
                    section_replacements=[
                        {
                            "section_slot": "bow",
                            "section_template": "BATTLESHIP_BOW_M1",
                            "components": [
                                {
                                    "component_slot": "MEDIUM_GUN_01",
                                    "component_id": "SMALL_TEST_GUN",
                                }
                            ],
                        }
                    ],
                    game_root=game_root,
                )


if __name__ == "__main__":
    unittest.main()
