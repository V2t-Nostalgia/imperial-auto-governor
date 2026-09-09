from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iag.stellaris.state.expansion_profiles import (
    extract_expansion_profiles,
    selected_colonization,
    selected_starbase_operation,
)

SAVE_FIXTURE = r'''
date="2204.09.15"
player={ { name="Player" country=0 } }
country=
{
 0=
 {
  fleets_manager=
  {
   owned_fleets={ }
  }
  controlled_planets={ 3 }
  founder_species_ref=1
  ship_design_collection={ ship_design={ 100 } }
  modules=
  {
   standard_economy_module=
   {
    resources=
    {
     energy=2000
     minerals=3000
     alloys=1000
     influence=500
    }
   }
  }
  tech_status=
  {
   technology="tech_starbase_1"
   level=1
   technology="tech_starbase_2"
   level=1
   technology="tech_hydroponics"
   level=1
  }
 }
}
species_db=
{
 1=
 {
  traits=
  {
   trait="trait_pc_continental_preference"
   trait="trait_adaptive"
  }
 }
}
ship_design=
{
 100=
 {
  name={ key="COLONY_SHIP" literal=yes }
  growth_stages=
  {
   {
    ship_size="colonizer"
    parent=4294967295
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
   44=
   {
    owner=0
    type=starbase
    items={ }
   }
   45=
   {
    owner=0
    type=starbase
    items={ }
   }
   46=
   {
    owner=0
    type=0
    location={ type=0 id=12 }
    items={ }
   }
  }
 }
}
starbase_mgr=
{
 starbases=
 {
  12=
  {
   level="starbase_level_starport"
   build_queue=44
   shipyard_build_queue=46
   station=500
   modules={ 0=shipyard }
   buildings={ }
  }
  13=
  {
   level="starbase_level_outpost"
   build_queue=45
   station=501
   modules={ }
   buildings={ }
  }
 }
}
galactic_object=
{
 10=
 {
  name={ key="NAME_Sol" }
  planet=0
  planet=3
  starbases={ 12 }
 }
 20=
 {
  name={ key="NAME_Alpha_Centauri" }
  planet=20
  planet=200
  starbases={ 13 }
 }
}
planets=
{
 planet=
 {
  0={ planet_class="pc_g_star" name={ key="NAME_Sol" } }
  3=
  {
   planet_class="pc_continental"
   name={ key="NAME_Earth" }
   owner=0
   controller=0
   colony=3
   surveyed_by=1
  }
  20={ planet_class="pc_g_star" name={ key="NAME_Alpha_Centauri_A" } }
  200=
  {
   planet_class="pc_arctic"
   planet_size=16
   name={ key="NAME_New_Terra" }
   surveyed_by=1
  }
 }
}
'''


TRAITS = r'''
trait_pc_continental_preference = {
 modifier = { pc_arctic_habitability = 0.20 }
}
trait_adaptive = {
 modifier = { pop_environment_tolerance = 0.10 }
}
'''

PLANET_CLASSES = r'''
pc_arctic = { colonizable = yes climate = "cold" }
pc_continental = { colonizable = yes climate = "wet" }
pc_g_star = { colonizable = no }
'''

STARBASE_LEVELS = r'''
starbase_level_outpost = {
 next_level = starbase_level_starport
 module_slots = { }
 building_slots = { }
}
starbase_level_starport = {
 next_level = starbase_level_starhold
 module_slots = { "module_1" "module_2" }
 building_slots = { "building_1" }
}
starbase_level_starhold = {
 module_slots = { "module_1" "module_2" "module_3" "module_4" }
 building_slots = { "building_1" "building_2" }
}
'''

STARBASE_MODULES = r'''
shipyard = { initial = yes }
anchorage = { initial = yes }
'''

STARBASE_BUILDINGS = r'''
crew_quarters = { initial = yes }
hydroponics_bay = {
 potential = { owner = { has_technology = tech_hydroponics } }
}
'''


class ExpansionProfileTests(unittest.TestCase):
    def game_root(self, root: Path) -> Path:
        files = {
            "common/traits/test.txt": TRAITS,
            "common/planet_classes/test.txt": PLANET_CLASSES,
            "common/starbase_levels/test.txt": STARBASE_LEVELS,
            "common/starbase_modules/test.txt": STARBASE_MODULES,
            "common/starbase_buildings/test.txt": STARBASE_BUILDINGS,
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return root

    def test_builds_save_and_rule_backed_expansion_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = extract_expansion_profiles(
                SAVE_FIXTURE,
                game_root=self.game_root(Path(directory)),
                minimum_habitability=0.30,
            )

        self.assertEqual(profile["country_stockpile"]["alloys"], 1000)
        self.assertEqual(profile["founder_species"]["species_id"], 1)
        self.assertEqual(len(profile["colonization_candidates"]), 1)
        candidate = profile["colonization_candidates"][0]
        self.assertAlmostEqual(
            candidate["target_planet"]["habitability"]["habitability"],
            0.30,
        )
        self.assertEqual(candidate["target_template"]["target_planet_id"], 200)
        self.assertEqual(
            candidate["target_template"]["source_shipyard_build_queue_id"],
            46,
        )
        self.assertEqual(
            candidate["source_shipyard"]["starbase_index"],
            12,
        )

        actions = {
            item["action"]
            for item in profile["starbase_operation_candidates"]
        }
        self.assertIn("upgrade_starbase", actions)
        self.assertIn("set_starbase_module", actions)
        self.assertIn("set_starbase_building", actions)

    def test_selectors_preserve_protocol_fields_and_replacement_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = extract_expansion_profiles(
                SAVE_FIXTURE,
                game_root=self.game_root(Path(directory)),
                minimum_habitability=0.30,
            )

        candidate_id = profile["colonization_candidates"][0]["candidate_id"]
        colony = selected_colonization(
            profile,
            candidate_id=candidate_id,
            designation="col_mining",
        )
        self.assertEqual(colony["target"]["colony_designation"], "col_mining")
        self.assertEqual(colony["target"]["system_name_key"], "NAME_Alpha_Centauri")

        replacement = next(
            item
            for item in profile["starbase_operation_candidates"]
            if item.get("requires_replacement_permission")
        )
        with self.assertRaisesRegex(ValueError, "not enabled"):
            selected_starbase_operation(
                profile,
                candidate_id=replacement["candidate_id"],
                allow_replacement=False,
            )
        selected = selected_starbase_operation(
            profile,
            candidate_id=replacement["candidate_id"],
            allow_replacement=True,
        )
        self.assertEqual(selected["target"]["starbase_object"], 12)


if __name__ == "__main__":
    unittest.main()
