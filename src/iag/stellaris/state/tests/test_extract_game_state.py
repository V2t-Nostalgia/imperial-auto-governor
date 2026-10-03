from __future__ import annotations

import unittest

from iag.stellaris.state.extract_game_state import construction_item_profile


class ConstructionItemProfileTests(unittest.TestCase):
    def test_reads_planet_building_mutations(self) -> None:
        upgrade = construction_item_profile(
            1,
            '''
            queue=0
            buildable_planet_upgrade_building={
                building="building_hall_judgment"
                planet=0
                zone=0
                upgrade_building=4
            }
            ''',
        )
        replacement = construction_item_profile(
            2,
            '''
            queue=48
            buildable_planet_replace_building={
                building="building_research_lab_1"
                planet=13
                zone=46
                replace_building=16777302
            }
            ''',
        )

        self.assertEqual(upgrade["kind"], "upgrade_building")
        self.assertEqual(upgrade["source_building_object_id"], 4)
        self.assertEqual(replacement["kind"], "replace_building")
        self.assertEqual(replacement["source_building_object_id"], 16777302)

    def test_reads_starbase_army_and_ship_items(self) -> None:
        starbase = construction_item_profile(
            3,
            '''
            queue=2
            buildable_starbase_building={
                starbase_building="hydroponics_bay"
                slot=3
                starbase=0
            }
            ''',
        )
        army = construction_item_profile(
            4,
            '''
            queue=1
            buildable_army={
                army_type="robotic_army"
                species=182
                planet=0
                starbase=0
            }
            ''',
        )
        colony_ship = construction_item_profile(
            5,
            '''
            queue=3
            buildable_colony_ship={
                ship_design_implementation={
                    design=33555402
                    upgrade=4294967295
                    growth_stage=0
                }
                orbitable={
                    starbase=0
                }
                colonization_data={
                    species=182
                    designation="col_city"
                }
            }
            ''',
        )

        self.assertEqual(starbase["kind"], "starbase_building")
        self.assertEqual(starbase["slot_index"], 3)
        self.assertEqual(army["kind"], "army")
        self.assertEqual(army["army_type"], "robotic_army")
        self.assertEqual(colony_ship["kind"], "colony_ship")
        self.assertEqual(colony_ship["design_id"], 33555402)
        self.assertEqual(colony_ship["colony_designation"], "col_city")

    def test_reads_starbase_upgrade_module_and_direct_ship_items(self) -> None:
        upgrade = construction_item_profile(
            6,
            '''
            queue=2
            buildable_starbase_upgrade={
                starbase_upgrade="starport"
                starbase=184
            }
            ''',
        )
        module = construction_item_profile(
            7,
            '''
            queue=2
            buildable_starbase_module={
                starbase_module="shipyard"
                slot=4
                starbase=720
            }
            ''',
        )
        ship = construction_item_profile(
            8,
            '''
            queue=3
            buildable_ship={
                ship_design_implementation={
                    design=167776127
                    upgrade=4294967295
                    growth_stage=0
                }
                orbitable={ starbase=0 }
            }
            ''',
        )
        reinforcement = construction_item_profile(
            9,
            '''
            queue=3
            buildable_ship_reinforcement={
                ship_design_implementation={
                    design=83890897
                    upgrade=4294967295
                    growth_stage=0
                }
                orbitable={ starbase=0 }
                fleet_template=167772413
            }
            ''',
        )

        self.assertEqual(upgrade["kind"], "starbase_upgrade")
        self.assertEqual(upgrade["target_level"], "starport")
        self.assertEqual(module["kind"], "starbase_module")
        self.assertEqual(module["component_id"], "shipyard")
        self.assertEqual(ship["kind"], "ship")
        self.assertEqual(ship["design_id"], 167776127)
        self.assertEqual(reinforcement["kind"], "ship_reinforcement")
        self.assertEqual(reinforcement["fleet_template_id"], 167772413)


if __name__ == "__main__":
    unittest.main()
