import unittest

from iag_stream_command_injector import EARTH_RESEARCH_COMMAND_TEMPLATE
from iag_zone_to_zone_replacer import (
    build_zone_core,
    build_zone_record_from_zone_carrier,
    parse_zone_carrier,
    replace_zone_command,
)


def make_zone_carrier(zone_type: str = "zone_research_unity") -> bytes:
    prefix = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE[:70])
    core = build_zone_core(
        zone_type=zone_type,
        build_queue_id=6,
        planet_id=3,
        target_zone_id=0,
    )
    record = bytes(prefix) + core
    padding = bytes(180 - len(record))
    full = bytearray(record + padding)
    full[:2] = (len(full) - 1).to_bytes(2, "little")
    return bytes(full)


class ZoneToZoneReplacerTests(unittest.TestCase):
    def test_rewrites_captured_physics_zone_to_trade_hard_equal(self):
        carrier = bytes.fromhex(
            "a00004000000b23d01000300f30101000300400201000c0002000000"
            "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
            "1400d83600000400410001000300822c01001400000000005b400100"
            "1400000000009a4401000300b32b01000f0015007a6f6e655f726573"
            "65617263685f70687973696373132a0100140000000000b42b010014"
            "00000000009c4401000c0001000000040004000400"
        )

        parsed = parse_zone_carrier(carrier)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.source_zone, "zone_research_physics")
        record, metadata = build_zone_record_from_zone_carrier(
            carrier,
            target_zone="zone_trade",
        )
        self.assertEqual(len(record), len(carrier))
        self.assertEqual(int.from_bytes(record[:2], "little") + 1, len(carrier))
        self.assertIn(b"zone_trade", record)
        self.assertNotIn(b"zone_research_physics", record)
        self.assertEqual(metadata["padding_length"], 11)
        self.assertEqual(metadata["source_segment_length"], 23)
        self.assertEqual(metadata["target_segment_length"], 12)

    def test_parse_zone_carrier(self):
        carrier = make_zone_carrier()
        parsed = parse_zone_carrier(carrier)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.source_zone, "zone_research_unity")
        self.assertEqual(parsed.build_queue_id, 6)
        self.assertEqual(parsed.planet_id, 3)
        self.assertEqual(parsed.target_zone_id, 0)

    def test_builds_hard_equal_trade_zone_record(self):
        carrier = make_zone_carrier()
        record, metadata = build_zone_record_from_zone_carrier(
            carrier,
            target_zone="zone_trade",
        )
        self.assertEqual(len(record), len(carrier))
        self.assertIn(b"zone_trade", record)
        self.assertNotIn(b"zone_research_unity", record)
        self.assertEqual(int.from_bytes(record[:2], "little") + 1, len(carrier))
        self.assertEqual(metadata["declared_record_length"], len(carrier))
        self.assertGreater(metadata["padding_length"], 0)

    def test_replace_zone_command_in_payload(self):
        carrier = make_zone_carrier()
        payload = b"prefix" + carrier + b"suffix"
        result = replace_zone_command(payload, target_zone="zone_trade")
        self.assertIsNotNone(result)
        modified, metadata = result
        self.assertEqual(len(modified), len(payload))
        self.assertIn(b"zone_trade", modified)
        self.assertEqual(metadata["source_zone"], "zone_research_unity")

    def test_rejects_longer_target_that_does_not_fit(self):
        carrier = make_zone_carrier("zone_trade")
        result = replace_zone_command(
            carrier,
            target_zone="zone_extremely_long_target_name_that_will_not_fit",
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
