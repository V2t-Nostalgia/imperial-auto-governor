from __future__ import annotations

import unittest

from iag_same_family_construction_rewriter import (
    rewrite_district_carrier,
    rewrite_zone_carrier,
)


DISTRICT_GENERATOR_RECORD = bytes.fromhex(
    "890004000000b43d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400830400000400410001000300822c010014000000000063400100"
    "14001d010000b03d01000300b42b01000f0012006469737472696374"
    "5f67656e657261746f72132a0100140050000000040004000400"
)

ZONE_PHYSICS_RECORD = bytes.fromhex(
    "a00004000000b43d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14008d0400000400410001000300822c010014000000000063400100"
    "14001d010000a24401000300b32b01000f0015007a6f6e655f726573"
    "65617263685f70687973696373132a0100140050000000b42b010014"
    "00c0000000a44401000c0001000000040004000400"
)


class SameFamilyConstructionRewriterTests(unittest.TestCase):
    def test_district_carrier_rewrites_save_fields_and_stays_fixed_length(
        self,
    ) -> None:
        rewritten, metadata = rewrite_district_carrier(
            DISTRICT_GENERATOR_RECORD,
            source_district_type="district_generator",
            target_district_type="district_city",
            build_queue_id=901,
            colony_id=82,
        )

        self.assertEqual(len(rewritten), len(DISTRICT_GENERATOR_RECORD))
        self.assertEqual(rewritten[:2], DISTRICT_GENERATOR_RECORD[:2])
        self.assertIn(b"district_city", rewritten)
        self.assertNotIn(b"district_generator", rewritten)
        self.assertIn(
            bytes.fromhex("634001001400") + (901).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("132a01001400") + (82).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(bytes.fromhex("b03d01000300"), rewritten)
        self.assertEqual(metadata["carrier_serial"], 1155)
        self.assertEqual(metadata["padding_length"], 5)

    def test_district_carrier_rejects_a_longer_target(self) -> None:
        with self.assertRaises(ValueError):
            rewrite_district_carrier(
                DISTRICT_GENERATOR_RECORD,
                source_district_type="district_generator",
                target_district_type="district_name_that_is_too_long",
                build_queue_id=901,
                colony_id=82,
            )

    def test_zone_carrier_rewrites_context_and_stays_fixed_length(self) -> None:
        rewritten, metadata = rewrite_zone_carrier(
            ZONE_PHYSICS_RECORD,
            source_zone_type="zone_research_physics",
            target_zone_type="zone_trade",
            build_queue_id=901,
            colony_id=82,
            district_id=213,
            slot_selector=2,
        )

        self.assertEqual(len(rewritten), len(ZONE_PHYSICS_RECORD))
        self.assertEqual(rewritten[:2], ZONE_PHYSICS_RECORD[:2])
        self.assertIn(b"zone_trade", rewritten)
        self.assertNotIn(b"zone_research_physics", rewritten)
        self.assertIn(
            bytes.fromhex("634001001400") + (901).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("132a01001400") + (82).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("b42b01001400") + (213).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("a44401000c00") + (2).to_bytes(4, "little"),
            rewritten,
        )
        self.assertEqual(metadata["carrier_serial"], 1165)
        self.assertEqual(metadata["padding_length"], 11)

    def test_zone_carrier_preserves_session_specific_tags(self) -> None:
        source = ZONE_PHYSICS_RECORD.replace(
            bytes.fromhex("a24401000300"),
            bytes.fromhex("aa5501000300"),
            1,
        ).replace(
            bytes.fromhex("a44401000c00"),
            bytes.fromhex("bb6601000c00"),
            1,
        )

        rewritten, metadata = rewrite_zone_carrier(
            source,
            source_zone_type="zone_research_physics",
            target_zone_type="zone_trade",
            build_queue_id=901,
            colony_id=82,
            district_id=213,
            slot_selector=2,
        )

        self.assertIn(bytes.fromhex("aa5501000300"), rewritten)
        self.assertIn(bytes.fromhex("bb6601000c00"), rewritten)
        self.assertEqual(metadata["dynamic_marker_tag"], "aa55")
        self.assertEqual(metadata["slot_field_tag"], "bb66")

    def test_zone_carrier_rejects_a_longer_target(self) -> None:
        with self.assertRaises(ValueError):
            rewrite_zone_carrier(
                ZONE_PHYSICS_RECORD,
                source_zone_type="zone_research_physics",
                target_zone_type="zone_research_engineering",
                build_queue_id=901,
                colony_id=82,
                district_id=213,
                slot_selector=2,
            )


if __name__ == "__main__":
    unittest.main()
