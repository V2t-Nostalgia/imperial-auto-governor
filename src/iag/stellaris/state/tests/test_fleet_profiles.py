from __future__ import annotations

import unittest

from iag.stellaris.state.fleet_profiles import (
    attack_target_routes,
    country_relation_profiles,
    extract_fleet_profiles,
    fleet_availability,
    hostile_fleet_targets,
    military_fleet_callability,
    resolve_created_fleet_template,
    selected_attack,
    selected_construction_ship_starbase,
    selected_coordinate_move,
    selected_fleet_reinforcement,
    selected_fleet_repair,
    selected_fleet_upgrade,
    selected_move,
    selected_new_fleet_reinforcement,
    selected_ship_automation,
)

FIXTURE = """
date="2204.09.15"
player=
{
 { name="Vertin" country=0 }
 { name="LLM" country=0 }
}
council_positions=
{
 council_positions=
 {
  17={ country=0 type="councilor_research" leader=70 }
 }
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
    { fleet=8 }
    { fleet=9 }
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
  intel=
  {
   {
    object=375
    hostile=
    {
     {
      owner=2
      name={ key="ALIEN_FLEET" }
      coordinate={ x=11 y=12 origin=375 }
      military_power=250
     }
    }
   }
  }
 }
 2=
 {
  fleets_manager=
  {
   owned_fleets={ { fleet=99 } }
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
  ships={ 4 5 7 }
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
 8=
 {
  name={ key="SCIENCE_SHIP" }
  ships={ 8 }
  ship_class=shipclass_science_ship
  movement_manager=
  {
   coordinate={ x=0 y=0 origin=486 }
   state=move_idle
   orbit={ }
  }
  settings={ mobile=yes valid_for_combat=no }
 }
 9=
 {
  name={ key="CONSTRUCTION_SHIP" }
  ships={ 9 }
  ship_class=shipclass_constructor
  movement_manager=
  {
   coordinate={ x=0 y=0 origin=486 }
   state=move_idle
   orbit={ }
  }
  settings={ mobile=yes valid_for_combat=no }
 }
 99=
 {
  name={ key="HOSTILE_FLEET" }
  military_power=250
  ships={ 99 }
  ship_class=shipclass_military
  movement_manager=
  {
   coordinate={ x=11 y=12 origin=375 }
   state=move_idle
   orbit={ }
  }
  settings={ mobile=yes valid_for_combat=yes }
 }
}
ships=
{
 4=
 {
  ship_design_implementation={ design=100 upgrade=4294967295 growth_stage=0 }
  hitpoints=200
  max_hitpoints=200
  armor_hitpoints=100
  max_armor_hitpoints=100
  shield_hitpoints=200
  max_shield_hitpoints=200
 }
 5=
 {
  ship_design_implementation={ design=100 upgrade=4294967295 growth_stage=0 }
  hitpoints=100
  max_hitpoints=200
  max_armor_hitpoints=100
  shield_hitpoints=50
  max_shield_hitpoints=200
 }
 7=
 {
  ship_design_implementation={ design=100 upgrade=4294967295 growth_stage=0 }
  hitpoints=50
  max_hitpoints=200
  armor_hitpoints=50
  max_armor_hitpoints=100
  max_shield_hitpoints=200
 }
 6=
 {
  ship_design_implementation={ design=101 upgrade=100 growth_stage=0 }
  hitpoints=75
  max_hitpoints=100
  armor_hitpoints=100
  max_armor_hitpoints=100
  shield_hitpoints=100
  max_shield_hitpoints=100
 }
 8=
 {
  ship_design_implementation={ design=100 upgrade=4294967295 growth_stage=0 }
  leader=70
 }
 9={ ship_design_implementation={ design=100 upgrade=4294967295 growth_stage=0 } }
 99={ ship_design_implementation={ design=100 upgrade=4294967295 growth_stage=0 } }
}
construction=
{
 queue_mgr=
 {
  queues=
  {
   900={ owner=0 type=0 items={ } }
  }
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
  hyperlane={ { to=375 length=10 } { to=600 length=12 } }
 }
 600=
 {
  name={ key="NAME_Barnards_Star" }
  planet=600
  star_class="sc_m"
  discovery={ 0 }
  hyperlane={ { to=486 length=12 } }
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
  600=
  {
   planet_class="pc_m_star"
   name={ key="NAME_Barnards_Star" }
   surveyed_by=1
   coordinate={ x=0 y=0 origin=600 }
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
   shipyard_build_queue=900
   station=2829
   modules={ 0=shipyard 1=anchorage }
  }
 }
}
"""


WAR_ROUTE_FIXTURE = """
date="2405.07.01"
player={ { name="Player" country=0 } }
country=
{
 0=
 {
  fleets_manager={ owned_fleets={ { fleet=1 } } }
  terra_incognita={ systems={ 10 11 12 } }
  relations_manager=
  {
   relation=
   {
    owner=0
    country=2
    contact=yes
    communications=yes
    hostile=yes
    wars={ 42 }
   }
   relation=
   {
    owner=0
    country=3
    contact=yes
    communications=yes
    hostile=yes
    truce=7
   }
   wars={ 42 }
  }
  intel=
  {
   {
    object=12
    hostile=
    {
     {
      owner=2
      name={ key="DEEP_STARBASE" }
      coordinate={ x=2 y=2 origin=12 }
      military_power=200
     }
    }
   }
   {
    object=10
    hostile=
    {
     {
      owner=3
      name={ key="TRUCED_FLEET" }
      coordinate={ x=9 y=9 origin=10 }
      military_power=50
     }
    }
   }
  }
 }
 2={ fleets_manager={ owned_fleets={ { fleet=90 } { fleet=91 } } } }
 3={ fleets_manager={ owned_fleets={ { fleet=92 } } } }
}
war=
{
 42=
 {
  attackers={ { country=0 } }
  defenders={ { country=2 } }
 }
}
fleet=
{
 1=
 {
  name={ key="PLAYER_FLEET" }
  military_power=500
  ships={ 1 }
  ship_class=shipclass_military
  movement_manager={ coordinate={ x=0 y=0 origin=10 } state=move_idle }
  settings={ mobile=yes valid_for_combat=yes }
 }
 90=
 {
  name={ key="FRONTIER_STARBASE" }
  military_power=100
  ships={ 90 }
  ship_class=shipclass_starbase
  movement_manager={ coordinate={ x=1 y=1 origin=11 } state=move_idle }
  settings={ mobile=no station=yes valid_for_combat=yes }
 }
 91=
 {
  name={ key="DEEP_STARBASE" }
  military_power=200
  ships={ 91 }
  ship_class=shipclass_starbase
  movement_manager={ coordinate={ x=2 y=2 origin=12 } state=move_idle }
  settings={ mobile=no station=yes valid_for_combat=yes }
 }
 92=
 {
  name={ key="TRUCED_FLEET" }
  military_power=50
  ships={ 92 }
  ship_class=shipclass_military
  movement_manager={ coordinate={ x=9 y=9 origin=10 } state=move_idle }
  settings={ mobile=yes valid_for_combat=yes }
 }
}
ships=
{
 1={ ship_design_implementation={ design=1 upgrade=4294967295 growth_stage=0 } }
 90={ ship_design_implementation={ design=90 upgrade=4294967295 growth_stage=0 } }
 91={ ship_design_implementation={ design=91 upgrade=4294967295 growth_stage=0 } }
 92={ ship_design_implementation={ design=92 upgrade=4294967295 growth_stage=0 } }
}
fleet_template=
{
}
galactic_object=
{
 10=
 {
  name={ key="START" }
  planet=10
  discovery={ 0 }
  hyperlane={ { to=11 length=10 } }
 }
 11=
 {
  name={ key="FRONTIER" }
  planet=11
  discovery={ 0 }
  hyperlane={ { to=10 length=10 } { to=12 length=10 } }
  ftl_inhibitor_presence={ 90 }
  inhibitor_owners={ 2 }
 }
 12=
 {
  name={ key="BEHIND_INHIBITOR" }
  planet=12
  discovery={ 0 }
  hyperlane={ { to=11 length=10 } }
 }
}
planets=
{
 10={ planet_class="pc_g_star" name={ key="START" } coordinate={ x=0 y=0 origin=10 } }
 11={ planet_class="pc_g_star" name={ key="FRONTIER" } coordinate={ x=0 y=0 origin=11 } }
 12={ planet_class="pc_g_star" name={ key="BEHIND_INHIBITOR" } coordinate={ x=0 y=0 origin=12 } }
}
starbase_mgr=
{
 starbases=
 {
 }
}
"""


class ExtractFleetProfilesTests(unittest.TestCase):
    def test_sensor_visible_enemy_military_fleet_becomes_attack_target(self) -> None:
        country_block = """
            sensor_range_fleets={ 99 }
            relations_manager=
            {
              relation=
              {
                country=2
                contact=yes
                communications=yes
                hostile=yes
                wars={ 42 }
              }
            }
        """
        targets = hostile_fleet_targets(
            owner=0,
            country_block=country_block,
            countries={
                0: """
                    fleets_manager=
                    {
                      owned_fleets={ { fleet=1 } }
                    }
                """,
                2: """
                    fleets_manager=
                    {
                      owned_fleets={ { fleet=99 } { fleet=100 } }
                    }
                """,
            },
            fleets={
                1: """
                    ships={ 1 }
                    ship_class=shipclass_military
                    movement_manager={ coordinate={ x=0 y=0 origin=10 } }
                    settings={ mobile=yes valid_for_combat=yes }
                """,
                99: """
                    name={ key="VISIBLE_ENEMY" }
                    military_power=250
                    ships={ 99 }
                    ship_class=shipclass_military
                    movement_manager={ coordinate={ x=1 y=2 origin=11 } }
                    settings={ mobile=yes valid_for_combat=yes }
                """,
                100: """
                    name={ key="HIDDEN_ENEMY" }
                    military_power=500
                    ships={ 100 }
                    ship_class=shipclass_military
                    movement_manager={ coordinate={ x=3 y=4 origin=12 } }
                    settings={ mobile=yes valid_for_combat=yes }
                """,
            },
            systems={
                11: 'name={ key="VISIBLE_SYSTEM" }',
                12: 'name={ key="HIDDEN_SYSTEM" }',
            },
            known_systems={10, 11, 12},
            active_war_opponent_ids={2},
            relations=country_relation_profiles(country_block),
        )

        self.assertEqual([target["fleet_id"] for target in targets], [99])
        self.assertEqual(
            targets[0]["target_authority"],
            "player_sensor_range_fleet_exact_object",
        )
        self.assertTrue(targets[0]["current_sensor_contact"])
        self.assertEqual(targets[0]["intel_military_power"], 250.0)

    def test_closed_third_party_borders_block_attack_route(self) -> None:
        country_block = """
        relations_manager={
          relation={
            country=3
            contact=yes
            communications=yes
            borders=yes
          }
          relation={
            country=4
            contact=yes
            communications=yes
            borders=yes
            forced_open_borders= "2406.01.01"
          }
        }
        """
        relations = country_relation_profiles(country_block)

        self.assertTrue(relations[3]["closed_borders"])
        self.assertFalse(relations[3]["forced_open_borders"])
        self.assertTrue(relations[4]["closed_borders"])
        self.assertTrue(relations[4]["forced_open_borders"])

        reachable, blocked = attack_target_routes(
            owner=0,
            country_block=country_block,
            countries={
                0: """
                    fleets_manager=
                    {
                      owned_fleets={ { fleet=1 } }
                    }
                """,
                2: """
                    fleets_manager=
                    {
                      owned_fleets={ { fleet=91 } }
                    }
                """,
                3: """
                    fleets_manager=
                    {
                      owned_fleets={ { fleet=90 } }
                    }
                """,
            },
            fleets={
                1: """
                    ship_class=shipclass_military
                    movement_manager=
                    {
                      coordinate={ x=0 y=0 origin=10 }
                    }
                """,
                90: """
                    ship_class=shipclass_starbase
                    movement_manager=
                    {
                      coordinate={ x=0 y=0 origin=11 }
                    }
                """,
                91: """
                    ship_class=shipclass_starbase
                    movement_manager=
                    {
                      coordinate={ x=0 y=0 origin=12 }
                    }
                """,
            },
            systems={
                10: """
                    name={ key="START" }
                    hyperlane=
                    {
                      { to=11 length=10 }
                    }
                """,
                11: (
                    """
                    name={ key="CLOSED_BORDER" }
                    hyperlane=
                    {
                      { to=10 length=10 }
                      { to=12 length=10 }
                    }
                    """
                ),
                12: (
                    """
                    name={ key="ENEMY_TARGET" }
                    hyperlane=
                    {
                      { to=11 length=10 }
                    }
                    """
                ),
            },
            known_systems={10, 11, 12},
            relations=relations,
            active_war_opponent_ids={2},
            owned_fleets=[
                {
                    "fleet_id": 1,
                    "attack_verified_family": True,
                    "movement": {"current_system_id": 10},
                }
            ],
            targets=[{"fleet_id": 91, "system_id": 12}],
        )

        self.assertEqual(reachable, [])
        self.assertEqual([target["fleet_id"] for target in blocked], [91])
        route = blocked[0]["route_evidence"][0]
        self.assertEqual(route["status"], "blocked_by_border_access_or_restriction")
        self.assertEqual(route["blocking_system_ids"], [11])
        self.assertEqual(route["blocking_system_names"], ["CLOSED_BORDER"])
        self.assertEqual(route["blocking_country_ids"], [3])

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
        self.assertEqual(fleets[3]["availability"], "AVAILABLE")
        self.assertEqual(fleets[3]["current_order_state"], "move_system")
        self.assertTrue(fleets[3]["move_callable_now"])
        self.assertTrue(fleets[3]["attack_callable_now"])
        self.assertFalse(fleets[3]["reinforcement_callable_now"])
        self.assertFalse(fleets[3]["maintenance_callable_now"])
        self.assertEqual(fleets[16777283]["availability"], "AVAILABLE")
        self.assertTrue(fleets[16777283]["needs_repair"])
        self.assertEqual(fleets[16777283]["upgradeable_ship_count"], 1)
        self.assertEqual(result["shipyards"][0]["shipyard_build_queue_id"], 900)
        self.assertEqual(fleets[8]["leader_ids"], [70])
        self.assertTrue(fleets[8]["has_council_leader"])
        self.assertFalse(fleets[8]["can_automate_astral_rifts"])
        self.assertEqual(
            fleets[3]["durability_summary"],
            {
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
            },
        )
        self.assertEqual(
            fleets[3]["fleet_composition"],
            [
                {
                    "design_id": 100,
                    "upgrade_id": 4294967295,
                    "growth_stage": 0,
                    "target_count": 5,
                    "target_count_was_omitted": False,
                    "current_count": 3,
                    "missing_count": 2,
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
        redirected = selected_move(result, 3, 375)
        self.assertEqual(redirected["source_fleet"]["fleet_id"], 3)

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

    def test_resolves_hostile_intel_and_selects_attack(self) -> None:
        result = extract_fleet_profiles(FIXTURE)
        self.assertEqual(
            [item["fleet_id"] for item in result["hostile_targets"]],
            [99],
        )
        attack = selected_attack(result, 16777283, 99)
        self.assertEqual(
            attack["target"],
            {"source_fleet_object": 16777283, "target_fleet_object": 99},
        )
        redirected = selected_attack(result, 3, 99)
        self.assertEqual(redirected["target"]["source_fleet_object"], 3)

    def test_limits_war_targets_to_known_reachable_side_of_inhibitor(self) -> None:
        result = extract_fleet_profiles(WAR_ROUTE_FIXTURE)

        self.assertEqual(result["active_war_ids"], [42])
        self.assertEqual(result["active_war_opponent_country_ids"], [2])
        self.assertEqual(
            result["attack_target_summary"],
            {
                "reachable": 1,
                "blocked": 1,
                "current_sensor_contacts": 1,
                "known_active_war_starbases": 2,
            },
        )
        self.assertEqual(
            [target["fleet_id"] for target in result["hostile_targets"]],
            [90],
        )
        frontier = result["hostile_targets"][0]
        self.assertEqual(
            frontier["target_authority"],
            "active_war_known_starbase_save_mapping",
        )
        self.assertEqual(frontier["reachable_from_fleet_ids"], [1])
        self.assertIsNone(frontier["military_power"])

        self.assertEqual(
            [target["fleet_id"] for target in result["blocked_hostile_targets"]],
            [91],
        )
        blocked_route = result["blocked_hostile_targets"][0]["route_evidence"][0]
        self.assertEqual(
            blocked_route["status"],
            "blocked_by_hostile_ftl_inhibitor",
        )
        self.assertEqual(blocked_route["blocking_system_ids"], [11])
        self.assertEqual(selected_attack(result, 1, 90)["action"], "attack_fleet")
        with self.assertRaisesRegex(ValueError, "not a uniquely resolved"):
            selected_attack(result, 1, 91)

    def test_military_callability_separates_orders_from_operational_state(
        self,
    ) -> None:
        for movement_state in ("move_system", "move_wind_up"):
            movement = {"state": movement_state}
            availability, reasons = fleet_availability(
                ship_class="shipclass_military",
                ship_ids=[1],
                mobile=True,
                valid_for_combat=True,
                movement=movement,
                mia_origin=None,
                combat_fleet_ids=[],
            )
            callability = military_fleet_callability(
                availability=availability,
                movement=movement,
                has_current_order=True,
            )
            self.assertEqual(availability, "AVAILABLE")
            self.assertEqual(reasons, [])
            self.assertTrue(callability["move_callable_now"])
            self.assertTrue(callability["attack_callable_now"])
            self.assertFalse(callability["maintenance_callable_now"])

        availability, reasons = fleet_availability(
            ship_class="shipclass_military",
            ship_ids=[1],
            mobile=True,
            valid_for_combat=True,
            movement={"state": "move_unverified"},
            mia_origin=None,
            combat_fleet_ids=[],
        )
        self.assertEqual(availability, "BUSY")
        self.assertEqual(reasons, ["movement_state:move_unverified"])

    def test_selects_verified_civilian_ship_actions(self) -> None:
        result = extract_fleet_profiles(FIXTURE)
        automation = selected_ship_automation(
            result,
            8,
            ["AUTOMATION_EXPLORE", "AUTOMATION_SURVEY"],
        )
        self.assertEqual(automation["target"]["source_fleet_object"], 8)
        with self.assertRaisesRegex(ValueError, "not valid"):
            selected_ship_automation(
                result,
                8,
                ["AUTOMATION_MINING_STATIONS"],
            )
        gravity = selected_ship_automation(
            result,
            8,
            ["AUTOMATION_SEND_GRAVITY_SNARES"],
        )
        self.assertEqual(
            gravity["target"]["options"],
            ["AUTOMATION_SEND_GRAVITY_SNARES"],
        )
        with self.assertRaisesRegex(ValueError, "council member"):
            selected_ship_automation(
                result,
                8,
                ["AUTOMATION_ASTRAL_RIFTS"],
            )

        starbase = selected_construction_ship_starbase(result, 9, 600)
        self.assertEqual(
            starbase["target"],
            {"source_fleet_object": 9, "target_system_object": 600},
        )

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
                "reinforce_selected_fleet",
            ],
        )

    def test_selects_save_backed_repair_and_upgrade_targets(self) -> None:
        result = extract_fleet_profiles(FIXTURE)
        repair = selected_fleet_repair(result, 16777283)
        self.assertEqual(
            repair["target"],
            {"context_822c": 0, "source_fleet_object": 16777283},
        )
        upgrade = selected_fleet_upgrade(result, 16777283, 900)
        self.assertEqual(
            upgrade["target"],
            {
                "context_822c": 0,
                "source_fleet_object": 16777283,
                "shipyard_build_queue_id": 900,
            },
        )
        with self.assertRaisesRegex(ValueError, "not an owned"):
            selected_fleet_upgrade(result, 16777283, 901)

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
                "reinforce_selected_fleet",
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
