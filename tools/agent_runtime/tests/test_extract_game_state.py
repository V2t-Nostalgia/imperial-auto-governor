from __future__ import annotations

import sys
import unittest
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from extract_game_state import (  # noqa: E402
    colony_is_establishing,
    construction_item_profile,
    construction_items,
    construction_queues,
    is_iag_carrier_planet,
    planet_display_name_hint,
)


class ExtractGameStateTests(unittest.TestCase):
    def test_detects_initial_colonization_growth(self) -> None:
        colony = """
        colonizing_species=4060086273
        current_month_growth_details={
            key="GROWTH_CAT_COLONIZATION"
            value=3
        }
        """
        self.assertTrue(colony_is_establishing(colony))

    def test_established_colony_is_not_marked_colonizing(self) -> None:
        colony = """
        stability=75
        employable_pops=2400
        current_month_growth_details={
            key="GROWTH_CAT_PROMOTION"
            value=0
        }
        """
        self.assertFalse(colony_is_establishing(colony))

    def test_detects_trantor_by_localisation_key(self) -> None:
        self.assertTrue(
            is_iag_carrier_planet(
                'name="NAME_IAG_Carrier_Enclave"',
                "",
                "NAME_IAG_Carrier_Enclave",
            )
        )

    def test_detects_terminus_by_localisation_key(self) -> None:
        self.assertTrue(
            is_iag_carrier_planet(
                'name="NAME_IAG_Carrier_Zone_Enclave"',
                "",
                "NAME_IAG_Carrier_Zone_Enclave",
            )
        )

    def test_ordinary_planet_is_not_carrier(self) -> None:
        self.assertFalse(is_iag_carrier_planet('name="Earth"', "", "Earth"))

    def test_generated_planet_name_hint_keeps_system_identity(self) -> None:
        self.assertEqual(
            planet_display_name_hint(
                "NEW_COLONY_NAME_1",
                {"NAME": "NAME_Trappist"},
            ),
            "NAME_Trappist-I",
        )

    def test_extracts_pending_planet_building_details(self) -> None:
        text = """
        construction=
        {
          queue_mgr=
          {
            queues=
            {
              6=
              {
                items=
                {
                  285212704
                }
                owner=0
                type=planet
              }
            }
          }
          item_mgr=
          {
            items=
            {
              285212704=
              {
                queue=6
                progress=263
                progress_needed=360
                resources=
                {
                  minerals=400
                }
                buildable_planet_building=
                {
                  building="building_research_lab_1"
                  planet=9
                  zone=201
                }
              }
            }
          }
        }
        """
        queues = construction_queues(text)
        items = construction_items(text)
        profile = construction_item_profile(
            285212704,
            items[285212704],
        )

        self.assertEqual(queues[6].count("285212704"), 1)
        self.assertEqual(profile["kind"], "building")
        self.assertEqual(profile["building_id"], "building_research_lab_1")
        self.assertEqual(profile["zone_id"], 201)
        self.assertEqual(profile["progress"], 263.0)
        self.assertEqual(profile["progress_needed"], 360.0)
        self.assertEqual(profile["resources"], {"minerals": 400.0})

    def test_extracts_pending_zone_target_context(self) -> None:
        profile = construction_item_profile(
            50331695,
            """
            queue=214
            buildable_zone=
            {
              zone="zone_industrial"
              planet=53
              district=152
              zone_slot=2
            }
            """,
        )

        self.assertEqual(profile["kind"], "zone")
        self.assertEqual(profile["zone_type"], "zone_industrial")
        self.assertEqual(profile["colony_id"], 53)
        self.assertEqual(profile["district_id"], 152)
        self.assertEqual(profile["slot_selector"], 2)


if __name__ == "__main__":
    unittest.main()
