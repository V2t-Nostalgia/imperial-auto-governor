from __future__ import annotations

import unittest

from iag.stellaris.state.fleet_profiles import (
    extract_fleet_profiles,
    resolve_created_fleet_template,
    selected_coordinate_move,
    selected_fleet_reinforcement,
    selected_move,
    selected_new_fleet_reinforcement,
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
    { fleet=2829 }
    { fleet=3 }
    { fleet=16777283 }
   }
  }
  owned_planets={ 3 }
  fleet_template_manager=
  {
   fleet_template={ 0 16777231 }
  }
  ship_design_collection=
  {
   ship_design={ 100 101 }
  }
 }
}
fleet=
{
3=
 {
  name=
  {
   key="%SEQ%"
   variable={ key="fmt" value={ key="HUMAN1_FLEET" } }
   variable={ key="num" value={ key="1" } }
  }
  fleet_template=0
  military_power=417.25
  ships={ 4 5 }
  ship_class=shipclass_military
  movement_manager=
  {
   coordinate={ x=-44.2711 y=8.92758 origin=486 }
   target={ coordinate={ x=6.45575 y=-20.6668 origin=375 } }
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
   coordinate={ x=12.5 y=-7.25 origin=486 }
   target={ coordinate={ x=0 y=0 origin=4294967295 } }
   state=move_idle
   orbit={ orbitable={ starbase=0 } }
  }
  settings={ mobile=yes valid_for_combat=yes }
 }
}
ships=
{
 4=
 {
  ship_design_implementation={ design=100 upgrade=4294967295 growth_stage=0 }
 }
 5=
 {
  ship_design_implementation={ design=100 upgrade=4294967295 growth_stage=0 }
 }
 6=
 {
  ship_design_implementation={ design=101 upgrade=4294967295 growth_stage=0 }
 }
}
fleet_template=
{
 0=
 {
  fleet=3
  home_base={ orbitable={ starbase=0 } }
  fleet_template_design=
  {
   {
    ship_design_implementation=
    { design=100 upgrade=4294967295 growth_stage=0 }
    count=5
   }
  }
  all_queued={ 16777218 3 }
  fleet_size=25
  is_edited_by_human=yes
 }
 16777231=
 {
  fleet=16777283
  home_base={ orbitable={ starbase=0 } }
  fleet_template_design=
  {
   {
    ship_design_implementation=
    { design=101 upgrade=4294967295 growth_stage=0 }
    count=1
   }
  }
  all_queued={ }
  fleet_size=5
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
  hyperlane={ { to=486 length=10 } }
 }
 486=
 {
  name={ key="NAME_Sol" }
  planet=0
  planet=3
  star_class="sc_g"
  discovery={ 0 }
  hyperlane={ { to=375 length=10 } }
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
   coordinate={ x=0 y=0 origin=486 }
  }
  3=
  {
   planet_class="pc_continental"
   name={ key="NAME_Earth" }
   coordinate={ x=12.25 y=-4.5 origin=486 }
  }
  260=
  {
   planet_class="pc_g_star"
   name={ key="NAME_Alpha_Centauri_A" }
   coordinate={ x=0 y=0 origin=375 }
  }
  261=
  {
   planet_class="pc_k_star"
   name={ key="NAME_Alpha_Centauri_B" }
   coordinate={ x=15 y=2 origin=375 }
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
        self.assertEqual(len(systems[486]["coordinate_targets"]), 2)
        self.assertEqual(
            systems[486]["coordinate_targets"][1]["coordinate"]["x"],
            12.25,
        )
        self.assertEqual(systems[375]["coordinate_targets"], [])
        self.assertEqual(systems[375]["hyperlane_neighbors"], [486])
        self.assertTrue(systems[375]["owned_by_owner"])
        self.assertEqual(
            systems[375]["ownership_evidence"],
            ["owned_starbase"],
        )
        self.assertTrue(systems[486]["owned_by_owner"])
        self.assertEqual(systems[486]["owned_colony_ids"], [3])
        fleets = {item["fleet_id"]: item for item in result["fleets"]}
        self.assertEqual(fleets[3]["military_power"], 417.25)
        self.assertEqual(fleets[3]["name_key"], "%SEQ%")
        self.assertEqual(fleets[3]["display_name_hint"], "HUMAN1_FLEET 1")
        self.assertTrue(fleets[3]["player_controllable"])
        self.assertEqual(
            fleets[3]["movement"]["current_coordinate"],
            {"x": -44.2711, "y": 8.92758, "origin": 486},
        )
        self.assertEqual(fleets[3]["availability"], "BUSY")
        self.assertEqual(fleets[16777283]["availability"], "AVAILABLE")
        self.assertEqual(
            fleets[3]["fleet_composition"],
            [
                {
                    "design_id": 100,
                    "upgrade_id": 4294967295,
                    "growth_stage": 0,
                    "target_count": 5,
                    "target_count_was_omitted": False,
                    "current_count": 2,
                    "missing_count": 3,
                }
            ],
        )
        self.assertEqual(
            fleets[3]["reinforcement_queue_item_handles"],
            [16777218, 3],
        )
        self.assertEqual(result["fleet_templates"][0]["fleet_size"], 25)

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
        self.assertEqual(
            move["source_fleet"]["display_name_hint"],
            "HUMAN1_FLEET_2",
        )
        self.assertTrue(move["destination_system"]["adjacent_to_source"])
        self.assertTrue(move["destination_system"]["owned_by_owner"])

    def test_selects_bounded_same_system_coordinate(self) -> None:
        result = extract_fleet_profiles(FIXTURE)
        move = selected_coordinate_move(result, 16777283, 23.4744, -299.04174)
        self.assertEqual(
            move["target"],
            {
                "source_fleet_object": 16777283,
                "x_fixed": 2347440,
                "y_fixed": -29904174,
                "system_origin": 486,
            },
        )
        with self.assertRaisesRegex(ValueError, "configured"):
            selected_coordinate_move(result, 16777283, 1001, 0)

    def test_selects_exact_fleet_template_target_and_reinforcement(self) -> None:
        result = extract_fleet_profiles(FIXTURE)
        selection = selected_fleet_reinforcement(
            result,
            fleet_id=16777283,
            design_id=101,
            target_count=4,
            maximum_target_increase=5,
        )

        self.assertEqual(selection["previous_target_count"], 1)
        self.assertEqual(selection["current_count"], 1)
        self.assertEqual(selection["target_increase"], 3)
        self.assertEqual(
            selection["protocol_sequence"],
            [
                "add_fleet_template_ship",
                "add_fleet_template_ship",
                "add_fleet_template_ship",
                "reinforce_fleet_stage_1",
                "reinforce_fleet_stage_2",
            ],
        )

    def test_resolves_one_new_template_then_builds_initial_composition(self) -> None:
        result = extract_fleet_profiles(FIXTURE)
        result["fleet_templates"].append(
            {
                "fleet_template_id": 177,
                "fleet_id": None,
                "design_targets": [],
                "queued_item_handles": [],
                "fleet_size": None,
                "is_edited_by_human": True,
            }
        )
        template = resolve_created_fleet_template(
            result,
            baseline_template_ids=[0, 16777231],
        )
        self.assertEqual(template["fleet_template_id"], 177)

        selection = selected_new_fleet_reinforcement(
            result,
            fleet_template_id=177,
            design_id=100,
            target_count=3,
            maximum_target_increase=5,
        )
        self.assertEqual(selection["target_increase"], 3)
        self.assertIsNone(selection["fleet_id"])
        self.assertEqual(
            selection["protocol_sequence"],
            [
                "add_fleet_template_ship",
                "add_fleet_template_ship",
                "add_fleet_template_ship",
                "reinforce_fleet_stage_1",
                "reinforce_fleet_stage_2",
            ],
        )

    def test_new_template_resolution_refuses_concurrent_creations(self) -> None:
        result = extract_fleet_profiles(FIXTURE)
        for template_id in (177, 178):
            result["fleet_templates"].append(
                {
                    "fleet_template_id": template_id,
                    "fleet_id": None,
                    "design_targets": [],
                    "queued_item_handles": [],
                }
            )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            resolve_created_fleet_template(
                result,
                baseline_template_ids=[0, 16777231],
            )


if __name__ == "__main__":
    unittest.main()
