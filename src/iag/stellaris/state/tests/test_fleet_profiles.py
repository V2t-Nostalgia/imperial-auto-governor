from __future__ import annotations

import unittest

from iag.stellaris.state.fleet_profiles import (
    extract_fleet_profiles,
    selected_move,
)


FIXTURE = """
date="2204.09.15"
player=
{
 { name="Vertin" country=0 }
 { name="LLM" country=0 }
}
country=
{
 0=
 {
  fleets_manager=
  {
   owned_fleets=
   {
    { fleet=3 }
    { fleet=16777283 }
   }
  }
 }
}
fleet=
{
 3=
 {
  name={ key="HUMAN1_FLEET_1" }
  fleet_template=0
  military_power=417.25
  ships={ 4 5 }
  ship_class=shipclass_military
  movement_manager=
  {
   coordinate={ origin=486 }
   target={ coordinate={ origin=375 } }
   state=move_system
   orbit={ }
  }
  settings={ mobile=yes valid_for_combat=yes }
 }
 16777283=
 {
  name={ key="HUMAN1_FLEET_2" }
  fleet_template=16777231
  ships={ 6 }
  ship_class=shipclass_military
  movement_manager=
  {
   coordinate={ origin=486 }
   target={ coordinate={ origin=4294967295 } }
   state=move_idle
   orbit={ orbitable={ starbase=0 } }
  }
  settings={ mobile=yes valid_for_combat=yes }
 }
}
galactic_object=
{
 375=
 {
  name={ key="NAME_Alpha_Centauri" }
  planet=260
  planet=261
  star_class="sc_binary_1"
  discovery={ 0 }
  starbases={ 118 }
 }
 486=
 {
  name={ key="NAME_Sol" }
  planet=0
  planet=3
  star_class="sc_g"
  discovery={ 0 }
 }
}
planets=
{
 planet=
 {
  0=
  {
   planet_class="pc_g_star"
   name={ key="NAME_Sol" }
   coordinate={ origin=486 }
  }
  3=
  {
   planet_class="pc_continental"
   name={ key="NAME_Earth" }
   coordinate={ origin=486 }
  }
  260=
  {
   planet_class="pc_g_star"
   name={ key="NAME_Alpha_Centauri_A" }
   coordinate={ origin=375 }
  }
  261=
  {
   planet_class="pc_k_star"
   name={ key="NAME_Alpha_Centauri_B" }
   coordinate={ origin=375 }
  }
 }
}
starbase_mgr=
{
 starbases=
 {
  118=
  {
   level="starbase_level_outpost"
   build_queue=8380
   station=2829
  }
 }
}
"""


class ExtractFleetProfilesTests(unittest.TestCase):
    def test_extracts_verified_destination_object_types(self) -> None:
        result = extract_fleet_profiles(FIXTURE)
        systems = {item["system_id"]: item for item in result["systems"]}

        self.assertEqual(result["owner_country_id"], 0)
        self.assertEqual(result["player_country_ids"], [0])
        self.assertEqual(
            systems[375]["move_destination"],
            {
                "destination_tag_hex": "0c3a01001400",
                "destination_object": 118,
                "destination_kind": "starbase_manager_entry",
                "starbase_level": "starbase_level_outpost",
                "station_object": 2829,
            },
        )
        self.assertEqual(
            systems[486]["move_destination"]["destination_object"],
            0,
        )
        self.assertEqual(
            systems[486]["move_destination"]["destination_tag_hex"],
            "132a01001400",
        )
        fleets = {item["fleet_id"]: item for item in result["fleets"]}
        self.assertEqual(fleets[3]["military_power"], 417.25)
        self.assertTrue(fleets[3]["player_controllable"])
        self.assertEqual(fleets[3]["availability"], "BUSY")
        self.assertEqual(fleets[16777283]["availability"], "AVAILABLE")

    def test_selects_owned_military_fleet_and_system_target(self) -> None:
        result = extract_fleet_profiles(FIXTURE)
        move = selected_move(result, 16777283, 375)
        self.assertEqual(
            move["target"],
            {
                "source_fleet_object": 16777283,
                "destination_tag_hex": "0c3a01001400",
                "destination_object": 118,
            },
        )


if __name__ == "__main__":
    unittest.main()
