from __future__ import annotations

import unittest
import sys
from pathlib import Path


SAVE_STATE = Path(__file__).resolve().parent
if str(SAVE_STATE) not in sys.path:
    sys.path.insert(0, str(SAVE_STATE))

from extract_planet_profiles import (
    extract_profiles,
    planet_active_modifiers,
    planet_name_variables,
)


FIXTURE = """
planets=
{
 planet=
 {
  3=
  {
   name=
   {
    key="NAME_Earth"
   }
   owner=0
   planet_class="pc_continental"
   planet_size=18
   timed_modifier=
   {
    items=
    {
     { modifier="high_gravity" days=-1 }
     { modifier="subterranean_wildlife" days=-1 }
    }
   }
   districts=
   {
    0 36
   }
   build_queue=6
   army_build_queue=7
  }
 }
}
buildings=
{
 0=
 {
  type="building_capital_1"
  position=0
 }
}
districts=
{
 0=
 {
  zones=
  {
   0 36 4294967295
  }
  type="district_city"
  level=3
 }
 36=
 {
  zones=
  {
   4294967295
  }
  type="district_generator"
  level=2
 }
}
zones=
{
 0=
 {
  type="zone_default"
  buildings=
  {
   0
  }
 }
 36=
 {
  type="zone_research_unity"
 }
}
"""


class ExtractProfilesTests(unittest.TestCase):
    def test_extracts_network_relevant_profile(self) -> None:
        result = extract_profiles(FIXTURE, owner=0)
        earth = result["planets"][0]

        self.assertEqual(earth["name"], "NAME_Earth")
        self.assertEqual(earth["planet_id"], 3)
        self.assertEqual(earth["planet_size"], 18)
        self.assertEqual(
            earth["active_modifiers"],
            ["high_gravity", "subterranean_wildlife"],
        )
        self.assertEqual(earth["build_queue_id"], 6)
        self.assertEqual(
            earth["districts"][0]["zone_slot_references"],
            [0, 36, None],
        )
        self.assertEqual(earth["districts"][0]["zones"][0]["zone_id"], 0)
        self.assertEqual(
            earth["districts"][0]["zones"][1]["slot_selector"],
            1,
        )
        self.assertEqual(
            earth["districts"][0]["zones"][1]["type"],
            "zone_research_unity",
        )
        self.assertEqual(
            earth["districts"][0]["zones"][0]["buildings"][0]["type"],
            "building_capital_1",
        )

    def test_preserves_generated_planet_name_variables(self) -> None:
        block = '''
        name=
        {
            key="NEW_COLONY_NAME_1"
            variables=
            {
                {
                    key="NAME"
                    value={ key="NAME_Trappist" }
                }
            }
        }
        '''
        self.assertEqual(
            planet_name_variables(block),
            {"NAME": "NAME_Trappist"},
        )

    def test_reads_static_modifier_rule_ids_not_display_ids(self) -> None:
        block = '''
        timed_modifier=
        {
            items=
            {
                { modifier="high_gravity" days=-1 }
                { modifier="subterranean_wildlife" days=-1 }
            }
        }
        planet_modifier="pm_high_gravity"
        planet_modifier="pm_subterranean_wildlife"
        '''
        self.assertEqual(
            planet_active_modifiers(block),
            ["high_gravity", "subterranean_wildlife"],
        )


if __name__ == "__main__":
    unittest.main()
