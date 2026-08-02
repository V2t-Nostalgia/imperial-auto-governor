from __future__ import annotations

import unittest

from iag_building_upgrade_rewriter import rewrite_building_upgrade_carrier
from iag_stream_command_injector import find_command_records


EARTH_UPGRADE_RECORD = bytes.fromhex(
    "a20004000000b43d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14001f0000000400410001000300822c010014000000000063400100"
    "140000000000c03d01000300b22b01000f0017006275696c64696e67"
    "5f72657365617263685f6c61625f32132a0100140000000000b32b01"
    "00140001000000bf3d010014003e010001040004000400"
)

ALPHA_CENTAURI_UPGRADE_RECORD = bytes.fromhex(
    "a20004000000b43d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400210000000400410001000300822c010014000000000063400100"
    "14006b210000c03d01000300b22b01000f0017006275696c64696e67"
    "5f72657365617263685f6c61625f32132a010014004c000000b32b01"
    "001400e5000000bf3d01001400c4010000040004000400"
)


class BuildingUpgradeRewriterTests(unittest.TestCase):
    def test_real_earth_record_redirects_to_alpha_context(self) -> None:
        rewritten, metadata = rewrite_building_upgrade_carrier(
            EARTH_UPGRADE_RECORD,
            source_upgrade_id="building_research_lab_2",
            target_upgrade_id="building_research_lab_2",
            build_queue_id=8555,
            colony_id=76,
            zone_id=229,
            building_object_id=452,
        )

        self.assertEqual(len(rewritten), len(EARTH_UPGRADE_RECORD))
        self.assertEqual(find_command_records(rewritten)[0].serial, 31)
        expected = bytearray(ALPHA_CENTAURI_UPGRADE_RECORD)
        expected[58:60] = (31).to_bytes(2, "little")
        self.assertEqual(rewritten, bytes(expected))
        self.assertEqual(metadata["layout"], "building_upgrade_v1")
        self.assertEqual(metadata["source_build_queue_id"], 0)
        self.assertEqual(metadata["source_colony_id"], 0)
        self.assertEqual(metadata["source_zone_id"], 1)
        self.assertEqual(metadata["source_building_object_id"], 16777534)

    def test_shorter_target_is_padded_at_record_boundary(self) -> None:
        rewritten, metadata = rewrite_building_upgrade_carrier(
            EARTH_UPGRADE_RECORD,
            source_upgrade_id="building_research_lab_2",
            target_upgrade_id="building_factory_2",
            build_queue_id=8555,
            colony_id=76,
            zone_id=229,
            building_object_id=452,
        )

        self.assertEqual(len(rewritten), len(EARTH_UPGRADE_RECORD))
        self.assertIn(b"building_factory_2", rewritten)
        self.assertNotIn(b"building_research_lab_2", rewritten)
        self.assertEqual(
            metadata["padding_length"],
            len("building_research_lab_2") - len("building_factory_2"),
        )
        self.assertTrue(rewritten.endswith(bytes(metadata["padding_length"])))

    def test_rejects_longer_target(self) -> None:
        with self.assertRaises(ValueError):
            rewrite_building_upgrade_carrier(
                EARTH_UPGRADE_RECORD,
                source_upgrade_id="building_research_lab_2",
                target_upgrade_id="building_mineral_purification_hub",
                build_queue_id=8555,
                colony_id=76,
                zone_id=229,
                building_object_id=452,
            )

    def test_unrelated_record_is_not_matched(self) -> None:
        self.assertIsNone(
            rewrite_building_upgrade_carrier(
                EARTH_UPGRADE_RECORD,
                source_upgrade_id="building_holo_theatres",
                target_upgrade_id="building_holo_theatres",
                build_queue_id=8555,
                colony_id=76,
                zone_id=229,
                building_object_id=452,
            )
        )


if __name__ == "__main__":
    unittest.main()
