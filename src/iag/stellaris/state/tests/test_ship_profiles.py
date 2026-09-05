from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iag.stellaris.state.ship_profiles import (
    clone_ship_design,
    extract_ship_profiles,
)


SAVE_FIXTURE = r'''
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
'''


SECTION_RULE = r'''
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
'''


COMPONENT_RULES = r'''
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
'''

MUTATION_COMPONENT_RULE = r'''
weapon_component_template = {
 key = "RED_EYE_BEAM_SMALL"
 size = small
 type = instant
 prerequisites = { "tech_lasers_1" }
 tags = { weapon_type_energy s_slot }
}
'''


class ShipProfileTests(unittest.TestCase):
    def _game_root(self, root: Path) -> Path:
        section = root / "common" / "section_templates" / "corvette.txt"
        section.parent.mkdir(parents=True)
        section.write_text(SECTION_RULE, encoding="utf-8")
        components = (
            root / "common" / "component_templates" / "weapons.txt"
        )
        components.parent.mkdir(parents=True)
        components.write_text(COMPONENT_RULES, encoding="utf-8")
        mutation = components.parent / "01_mutation_weapon_components.txt"
        mutation.write_text(MUTATION_COMPONENT_RULE, encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
