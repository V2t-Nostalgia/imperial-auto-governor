from __future__ import annotations

import unittest

from iag.stellaris.execution.iag_supervisor import (
    SupervisorError,
    session_proxy_action_target,
)


class SupervisorExecutionModeTests(unittest.TestCase):
    def test_building_action_maps_without_carrier_fields(self) -> None:
        action_type, target = session_proxy_action_target(
            {
                "type": "build_building",
                "build_queue_id": 8,
                "colony_id": 12,
                "zone_id": 15,
                "building_id": "building_holo_theatres",
            }
        )
        self.assertEqual(action_type, "build_building")
        self.assertEqual(target["context_822c"], 0)
        self.assertEqual(target["building_id"], "building_holo_theatres")

    def test_district_and_zone_actions_keep_save_identifiers(self) -> None:
        _kind, district = session_proxy_action_target(
            {
                "type": "build_district",
                "build_queue_id": 8,
                "colony_id": 12,
                "district_type": "district_mining",
            }
        )
        self.assertEqual(district["district_type"], "district_mining")

        _kind, zone = session_proxy_action_target(
            {
                "type": "build_zone",
                "build_queue_id": 8,
                "colony_id": 12,
                "district_id": 22,
                "slot_selector": 2,
                "zone_type": "zone_research_physics",
            }
        )
        self.assertEqual(zone["district_id"], 22)
        self.assertEqual(zone["slot_selector"], 2)

    def test_upgrade_and_replacement_map_to_full_proxy_targets(self) -> None:
        _, upgrade = session_proxy_action_target(
            {
                "type": "upgrade_building",
                "build_queue_id": 8,
                "colony_id": 12,
                "zone_id": 15,
                "building_object_id": 21,
                "to_building_id": "building_research_lab_2",
            }
        )
        self.assertEqual(upgrade["building_object_id"], 21)
        self.assertEqual(upgrade["building_id"], "building_research_lab_2")

        _, replacement = session_proxy_action_target(
            {
                "type": "replace_building",
                "build_queue_id": 8,
                "colony_id": 12,
                "zone_id": 15,
                "building_object_id": 22,
                "to_building_id": "building_holo_theatres",
            }
        )
        self.assertEqual(replacement["source_building_object_id"], 22)
        self.assertEqual(replacement["building_id"], "building_holo_theatres")

    def test_unknown_economy_action_is_rejected(self) -> None:
        with self.assertRaisesRegex(SupervisorError, "不支持经济动作"):
            session_proxy_action_target({"type": "demolish_planet"})


if __name__ == "__main__":
    unittest.main()
