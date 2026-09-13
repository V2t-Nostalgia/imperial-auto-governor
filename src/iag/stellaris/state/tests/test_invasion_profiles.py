from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iag.stellaris.state.invasion_profiles import (
    extract_invasion_profiles,
    selected_army_landing,
    selected_army_recruitment,
    selected_orbital_bombardment,
)


EXTRACTION_FIXTURE = r'''
date="2405.07.01"
player={ { name="Player" country=0 } }
country=
{
 0=
 {
  fleets_manager={ owned_fleets={ { fleet=1 } { fleet=2 } } }
  terra_incognita={ systems={ 10 20 } }
  founder_species_ref=1
  modules={ standard_economy_module={ resources={ minerals=2000 } } }
  tech_status=
  {
   technology="tech_droid_workers"
   level=1
  }
  relations_manager={
   relation={ owner=0 country=2 contact=yes communications=yes hostile=yes wars={ 42 } }
   wars={ 42 }
  }
 }
 2={ fleets_manager={ owned_fleets={ } } }
}
war=
{
 42=
 {
  attackers=
  {
   { country=0 }
  }
  defenders=
  {
   { country=2 }
  }
 }
}
fleet=
{
 1={ ship_class=shipclass_military ships={ 1 } movement_manager={ coordinate={ x=0 y=0 origin=10 } state=move_idle } settings={ mobile=yes valid_for_combat=yes } }
 2={ ship_class=shipclass_transport ships={ 2 } movement_manager={ coordinate={ x=0 y=0 origin=10 } state=move_idle } settings={ mobile=yes valid_for_combat=yes } }
}
ships=
{
 1={ ship_design_implementation={ design=1 upgrade=4294967295 growth_stage=0 } }
 2={ army=201 ship_design_implementation={ design=2 upgrade=4294967295 growth_stage=0 } }
}
fleet_template=
{ }
galactic_object=
{
 10=
 {
  name={ key="START" }
  planet=10
  discovery={ 0 }
  hyperlane={ { to=20 length=10 } }
 }
 20=
 {
  name={ key="FORTRESS" }
  planet=20
  planet=70
  planet=71
  discovery={ 0 }
  hyperlane={ { to=10 length=10 } }
  inhibitor_owners={ 2 }
 }
}
planets=
{
 10={ planet_class="pc_g_star" name={ key="START" } coordinate={ x=0 y=0 origin=10 } }
 20={ planet_class="pc_g_star" name={ key="FORTRESS" } coordinate={ x=0 y=0 origin=20 } }
 70={ planet_class="pc_continental" name={ key="HOSTILE_WORLD" } coordinate={ x=1 y=1 origin=20 } owner=2 colony=4 bombardment_damage=61 }
 71={ planet_class="pc_continental" name={ key="OCCUPIED_WORLD" } coordinate={ x=2 y=2 origin=20 } owner=2 controller=0 colony=5 }
}
colony=
{
 4=
 {
  army={ 101 102 }
  districts={ 300 }
 }
 5={ army={ 103 } }
}
army=
{
 101={ type="defense_army" owner=2 health=80 max_health=100 morale=40 }
 102={ type="defense_army" owner=2 health=50 max_health=50 morale=30 }
 103={ type="defense_army" owner=0 health=100 max_health=100 morale=50 }
 201={ type="robotic_army" owner=0 health=100 max_health=100 morale=50 }
}
districts=
{
 300=
 {
  zones=
  {
   400
  }
 }
}
zones=
{ 400={ buildings={ 501 } } }
buildings=
{ 501={ type="building_fortress" } }
species_db=
{ 1={ traits={ trait="trait_mechanical" } } }
starbase_mgr=
{
 starbases=
 {
 }
}
construction=
{ queue_mgr={ queues={ } } }
'''


def profile() -> dict[str, object]:
    target = {
        "planet_id": 70,
        "colony_id": 4,
        "system_id": 6,
        "reachable_from_fleet_ids": [4778, 33554946],
        "route_evidence": [],
    }
    return {
        "owner_country_id": 0,
        "fleets": [
            {
                "fleet_id": 4778,
                "ship_class": "shipclass_military",
                "bombardment_verified_family": True,
                "attack_callable_now": True,
            },
            {
                "fleet_id": 33554946,
                "ship_class": "shipclass_transport",
                "landing_verified_family": True,
                "landing_callable_now": True,
            },
        ],
        "hostile_colonies": [target],
        "army_recruitment_candidates": [
            {
                "candidate_id": "verified",
                "army_type": "robotic_army",
                "species_id": 182,
                "source_colony": {"source_colony_id": 114},
                "recruitment_starbase": {"starbase_index": 0},
                "maximum_count": 5,
                "target_template": {
                    "context_822c": 0,
                    "army_build_queue_id": 8686,
                    "army_subtype": 0,
                    "army_type": "robotic_army",
                    "species_id": 182,
                    "source_colony_object": 114,
                    "recruitment_starbase_object": 0,
                },
            }
        ],
    }


class InvasionProfileSelectionTests(unittest.TestCase):
    def test_extracts_ground_state_and_excludes_player_occupied_world(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_root = Path(temporary)
            definition_dir = game_root / "common" / "buildings"
            definition_dir.mkdir(parents=True)
            (definition_dir / "fortress.txt").write_text(
                "building_fortress = { planetary_ftl_inhibitor = yes }\n",
                encoding="utf-8",
            )
            extracted = extract_invasion_profiles(
                EXTRACTION_FIXTURE,
                game_root=game_root,
                fleet_profile={
                    "game_date": "2405.07.01",
                    "fleets": [
                        {
                            "fleet_id": 1,
                            "ship_ids": [1],
                            "ship_class": "shipclass_military",
                            "bombardment_verified_family": True,
                            "attack_callable_now": True,
                            "movement": {"current_system_id": 10},
                        },
                        {
                            "fleet_id": 2,
                            "ship_ids": [2],
                            "ship_count": 1,
                            "ship_class": "shipclass_transport",
                            "landing_verified_family": True,
                            "landing_callable_now": True,
                            "movement": {"current_system_id": 10},
                        },
                    ],
                },
            )

        self.assertEqual(
            [item["planet_id"] for item in extracted["hostile_colonies"]],
            [70],
        )
        target = extracted["hostile_colonies"][0]
        self.assertEqual(target["bombardment_damage"], 61)
        self.assertEqual(target["defending_armies"]["army_count"], 2)
        self.assertEqual(target["defending_armies"]["current_health_total"], 130)
        self.assertTrue(target["has_planetary_ftl_inhibitor_source"])
        self.assertEqual(
            target["planetary_ftl_inhibitor_sources"][0]["building_type"],
            "building_fortress",
        )
        transport = next(
            item for item in extracted["fleets"] if item["fleet_id"] == 2
        )
        self.assertEqual(transport["transport_armies"]["army_count"], 1)
        self.assertEqual(transport["transport_armies"]["median_health_ratio"], 1.0)

    def test_bombardment_uses_stance_then_planet_move(self) -> None:
        selected = selected_orbital_bombardment(
            profile(),
            fleet_id=4778,
            target_planet_id=70,
            stance="selective",
        )
        self.assertEqual(
            [step["action"] for step in selected["protocol_sequence"]],
            ["set_orbital_bombardment_stance", "move_fleet"],
        )
        self.assertEqual(
            selected["protocol_sequence"][1]["target"]["destination_object"],
            70,
        )

    def test_landing_resolves_planet_to_colony_object(self) -> None:
        selected = selected_army_landing(
            profile(),
            transport_fleet_id=33554946,
            target_planet_id=70,
        )
        self.assertEqual(
            selected["protocol_sequence"][0]["target"]["target_colony_object"],
            4,
        )

    def test_recruitment_expands_count_into_ordinary_commands(self) -> None:
        selected = selected_army_recruitment(
            profile(),
            candidate_id="verified",
            count=5,
        )
        self.assertEqual(len(selected["protocol_sequence"]), 5)
        self.assertTrue(
            all(
                step["target"]["army_build_queue_id"] == 8686
                for step in selected["protocol_sequence"]
            )
        )


if __name__ == "__main__":
    unittest.main()
