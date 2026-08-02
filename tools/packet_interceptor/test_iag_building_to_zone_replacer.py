import unittest
from argparse import Namespace

from iag_building_to_zone_replacer import (
    build_packet_filter,
    build_zone_record_from_building_carrier,
    is_building_carrier_command,
    replace_building_command_with_zone,
)
from iag_stream_command_injector import (
    EARTH_RESEARCH_COMMAND_TEMPLATE,
    read_uint24_be,
    write_uint24_be,
)


def header(sender: int, ack: int) -> bytes:
    value = bytes.fromhex(
        "010000000000000000000000000000f0000000000000000000"
    )
    value = write_uint24_be(value, 6, sender)
    return write_uint24_be(value, 10, ack)


class BuildingToZoneReplacerTests(unittest.TestCase):
    def test_filter_can_wildcard_remote_ports(self):
        packet_filter = build_packet_filter(
            Namespace(
                local_ip="192.0.2.10",
                remote_ip="198.51.100.20",
                local_port=63394,
                remote_outbound_port=0,
                remote_inbound_port=0,
            )
        )

        self.assertIn("udp.SrcPort == 63394", packet_filter)
        self.assertIn("udp.DstPort == 63394", packet_filter)
        self.assertNotIn("udp.DstPort == 0", packet_filter)
        self.assertNotIn("udp.SrcPort == 0", packet_filter)

    def test_accepts_current_b23d_building_carrier(self):
        carrier = bytes.fromhex(
            "980004000000b23d01000300f30101000300400201000c0001000000"
            "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
            "14000d5200000400410001000300822c01001400000000005b400100"
            "140000000000b03d01000300b22b01000f0017006275696c64696e67"
            "5f72657365617263685f6c61625f31132a0100140000000000b32b01"
            "00140000000000040004000400"
        )

        self.assertTrue(is_building_carrier_command(carrier))
        zone_record, metadata = build_zone_record_from_building_carrier(
            carrier,
            zone_type="zone_trade",
            build_queue_id=6,
            colony_id=0,
            district_id=0,
            slot_selector=1,
        )
        self.assertEqual(len(zone_record), len(carrier))
        self.assertEqual(metadata["declared_record_length"], len(carrier))
        self.assertEqual(metadata["padding_length"], 3)

    def test_builds_zone_trade_inside_research_lab_carrier(self):
        carrier = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE)
        carrier[58:60] = (777).to_bytes(2, "little")

        zone_record, metadata = build_zone_record_from_building_carrier(
            bytes(carrier),
            zone_type="zone_trade",
            build_queue_id=6,
            colony_id=0,
            district_id=0,
            slot_selector=1,
        )

        self.assertEqual(len(zone_record), len(carrier))
        self.assertIn(b"zone_trade", zone_record)
        self.assertNotIn(b"building_research_lab_1", zone_record)
        self.assertEqual(metadata["carrier_serial"], 777)
        self.assertEqual(metadata["carrier_length"], len(carrier))
        self.assertEqual(metadata["declared_record_length"], len(carrier))
        self.assertEqual(metadata["padding_length"], 3)
        self.assertEqual(int.from_bytes(zone_record[:2], "little") + 1, len(carrier))

    def test_longer_zone_rejects_when_it_does_not_fit(self):
        with self.assertRaises(ValueError):
            build_zone_record_from_building_carrier(
                EARTH_RESEARCH_COMMAND_TEMPLATE,
                zone_type="zone_research_physics",
                build_queue_id=6,
                colony_id=0,
                district_id=0,
                slot_selector=1,
            )

    def test_replaces_one_building_command_without_changing_packet_length(self):
        payload = header(1000, 500) + EARTH_RESEARCH_COMMAND_TEMPLATE

        result = replace_building_command_with_zone(
            payload,
            zone_type="zone_trade",
            build_queue_id=6,
            colony_id=0,
            district_id=0,
            slot_selector=1,
        )

        self.assertIsNotNone(result)
        modified, metadata = result
        self.assertEqual(len(modified), len(payload))
        self.assertEqual(read_uint24_be(modified, 6), 1000)
        self.assertEqual(read_uint24_be(modified, 10), 500)
        self.assertIn(b"zone_trade", modified)
        self.assertNotIn(b"building_research_lab_1", modified)
        self.assertEqual(metadata["padding_length"], 3)
        self.assertEqual(metadata["declared_record_length"], 153)

    def test_non_building_command_is_not_replaced(self):
        carrier = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE)
        carrier[6:12] = bytes.fromhex("be2c01000300")
        carrier = carrier.replace(b"building_research_lab_1", b"not_a_real_object_id__")
        payload = header(1000, 500) + bytes(carrier)

        self.assertIsNone(
            replace_building_command_with_zone(
                payload,
                zone_type="zone_trade",
                build_queue_id=6,
                colony_id=0,
                district_id=0,
                slot_selector=1,
            )
        )


if __name__ == "__main__":
    unittest.main()
