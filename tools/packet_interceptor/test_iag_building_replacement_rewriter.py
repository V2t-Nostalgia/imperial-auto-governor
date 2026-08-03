from __future__ import annotations

import unittest

from iag_building_replacement_rewriter import (
    rewrite_building_replacement_carrier,
)
from iag_stream_command_injector import find_command_records


# Real 4.4.6 co-op command captured while replacing a newly built research lab
# on Sirius-I with a holo theatre. Endpoint and transport header are excluded.
SIRIUS_REPLACEMENT_RECORD = bytes.fromhex(
    "a10004000000b43d01000300f30101000300400201000c0003000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400210000000400410001000300822c010014000000000063400100"
    "1400d81f0000bd3d01000300b22b01000f0016006275696c64696e67"
    "5f686f6c6f5f7468656174726573132a010014004b000000b32b0100"
    "1400940000015a3d01001400a3010000040004000400"
)


def replacement_record_with_source(source_id: str) -> bytes:
    source = b"building_holo_theatres"
    target = source_id.encode("ascii")
    marker_offset = SIRIUS_REPLACEMENT_RECORD.index(source)
    length_offset = marker_offset - 2
    rewritten = (
        SIRIUS_REPLACEMENT_RECORD[:length_offset]
        + len(target).to_bytes(2, "little")
        + target
        + SIRIUS_REPLACEMENT_RECORD[marker_offset + len(source) :]
    )
    delta = len(target) - len(source)
    declared_length = int.from_bytes(rewritten[:2], "little") + delta
    return declared_length.to_bytes(2, "little") + rewritten[2:]


class BuildingReplacementRewriterTests(unittest.TestCase):
    def test_real_record_redirects_target_and_exact_source_object(self) -> None:
        rewritten, metadata = rewrite_building_replacement_carrier(
            SIRIUS_REPLACEMENT_RECORD,
            source_replacement_id="building_holo_theatres",
            target_replacement_id="building_foundry_1",
            build_queue_id=9001,
            colony_id=82,
            zone_id=213,
            source_building_object_id=777,
        )

        self.assertEqual(len(rewritten), len(SIRIUS_REPLACEMENT_RECORD))
        self.assertEqual(find_command_records(rewritten)[0].serial, 33)
        self.assertIn(b"building_foundry_1", rewritten)
        self.assertNotIn(b"building_holo_theatres", rewritten)
        self.assertIn(
            bytes.fromhex("634001001400") + (9001).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("132a01001400") + (82).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("b32b01001400") + (213).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("5a3d01001400") + (777).to_bytes(4, "little"),
            rewritten,
        )
        self.assertEqual(metadata["layout"], "building_replacement_v1")
        self.assertEqual(metadata["dynamic_marker_tag"], "bd3d")
        self.assertEqual(metadata["source_object_field_tag"], "5a3d")
        self.assertEqual(metadata["source_build_queue_id"], 8152)
        self.assertEqual(metadata["source_colony_id"], 75)
        self.assertEqual(metadata["source_zone_id"], 16777364)
        self.assertEqual(metadata["source_building_object_id"], 419)

    def test_shorter_target_is_padded_at_record_boundary(self) -> None:
        rewritten, metadata = rewrite_building_replacement_carrier(
            SIRIUS_REPLACEMENT_RECORD,
            source_replacement_id="building_holo_theatres",
            target_replacement_id="building_factory_1",
            build_queue_id=8152,
            colony_id=75,
            zone_id=16777364,
            source_building_object_id=419,
        )

        self.assertEqual(len(rewritten), len(SIRIUS_REPLACEMENT_RECORD))
        self.assertEqual(
            metadata["padding_length"],
            len("building_holo_theatres") - len("building_factory_1"),
        )
        self.assertTrue(rewritten.endswith(bytes(metadata["padding_length"])))

    def test_production_carrier_keeps_its_larger_envelope(self) -> None:
        carrier_id = "building_upc_replacement_command_relay_target"
        original = replacement_record_with_source(carrier_id)
        rewritten, metadata = rewrite_building_replacement_carrier(
            original,
            source_replacement_id=carrier_id,
            target_replacement_id="building_holo_theatres",
            build_queue_id=8555,
            colony_id=76,
            zone_id=229,
            source_building_object_id=463,
        )

        self.assertEqual(len(rewritten), len(original))
        self.assertEqual(
            metadata["padding_length"],
            len(carrier_id) - len("building_holo_theatres"),
        )
        self.assertIn(b"building_holo_theatres", rewritten)
        self.assertNotIn(carrier_id.encode("ascii"), rewritten)

    def test_upgrade_record_is_not_accepted_as_replacement(self) -> None:
        upgrade_record = SIRIUS_REPLACEMENT_RECORD.replace(
            bytes.fromhex("bd3d01000300"),
            bytes.fromhex("c03d01000300"),
        ).replace(
            bytes.fromhex("5a3d01001400"),
            bytes.fromhex("bf3d01001400"),
        )
        with self.assertRaisesRegex(ValueError, "bd3d"):
            rewrite_building_replacement_carrier(
                upgrade_record,
                source_replacement_id="building_holo_theatres",
                target_replacement_id="building_foundry_1",
                build_queue_id=8152,
                colony_id=75,
                zone_id=16777364,
                source_building_object_id=419,
            )

    def test_unrelated_record_is_not_matched(self) -> None:
        self.assertIsNone(
            rewrite_building_replacement_carrier(
                SIRIUS_REPLACEMENT_RECORD,
                source_replacement_id="building_upc_replacement_command_relay_target",
                target_replacement_id="building_foundry_1",
                build_queue_id=8152,
                colony_id=75,
                zone_id=16777364,
                source_building_object_id=419,
            )
        )


if __name__ == "__main__":
    unittest.main()
