from __future__ import annotations

import socket
import struct
import sys
import unittest
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
PACKET_TOOLS = RUNTIME.parent / "packet_interceptor"
for path in (RUNTIME, PACKET_TOOLS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from iag_stream_command_injector import (  # noqa: E402
    EARTH_RESEARCH_COMMAND_TEMPLATE,
    write_uint24_be,
)
from linux_packet import (  # noqa: E402
    internet_checksum,
    parse_ipv4_udp,
    replace_udp_payload,
    rewrite_stellaris_payload,
)


def reliable_header(sender: int, ack: int) -> bytes:
    value = bytes.fromhex(
        "010000000000000000000000000000f0000000000000000000"
    )
    value = write_uint24_be(value, 6, sender)
    return write_uint24_be(value, 10, ack)


def carrier_payload() -> bytes:
    carrier = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE)
    carrier[58:60] = (777).to_bytes(2, "little")
    return reliable_header(1000, 500) + bytes(carrier)


def ipv4_udp_packet(payload: bytes) -> bytes:
    source = socket.inet_aton("192.0.2.10")
    destination = socket.inet_aton("198.51.100.21")
    udp_length = 8 + len(payload)
    udp = bytearray(
        struct.pack("!HHHH", 50001, 40000, udp_length, 0) + payload
    )
    pseudo = (
        source
        + destination
        + b"\x00"
        + bytes([socket.IPPROTO_UDP])
        + udp_length.to_bytes(2, "big")
    )
    checksum = internet_checksum(pseudo + udp)
    udp[6:8] = (checksum or 0xFFFF).to_bytes(2, "big")
    total_length = 20 + udp_length
    ip = bytearray(
        struct.pack(
            "!BBHHHBBH4s4s",
            0x45,
            0,
            total_length,
            123,
            0,
            64,
            socket.IPPROTO_UDP,
            0,
            source,
            destination,
        )
    )
    ip[10:12] = internet_checksum(ip).to_bytes(2, "big")
    return bytes(ip + udp)


class LinuxPacketTests(unittest.TestCase):
    def test_building_rewrite_preserves_packet_and_checksums(self) -> None:
        packet = ipv4_udp_packet(carrier_payload())
        view = parse_ipv4_udp(packet)
        self.assertIsNotNone(view)
        result = rewrite_stellaris_payload(
            view.udp_payload,
            {
                "type": "build_building",
                "planet_id": 3,
                "build_queue_id": 6,
                "zone_id": 0,
                "building_id": "building_foundry_1",
            },
        )
        self.assertIsNotNone(result)
        payload, metadata = result
        modified = replace_udp_payload(packet, view, payload)
        modified_view = parse_ipv4_udp(modified)
        self.assertEqual(len(modified), len(packet))
        self.assertEqual(modified_view.udp_payload[:25], view.udp_payload[:25])
        self.assertIn(b"building_foundry_1", modified_view.udp_payload)
        self.assertEqual(metadata["carrier_serial"], 777)
        self.assertEqual(
            internet_checksum(modified[: modified_view.ip_header_length]),
            0,
        )
        udp_length = len(modified) - modified_view.udp_offset
        pseudo = (
            socket.inet_aton(modified_view.source_ip)
            + socket.inet_aton(modified_view.destination_ip)
            + b"\x00"
            + bytes([socket.IPPROTO_UDP])
            + udp_length.to_bytes(2, "big")
        )
        self.assertEqual(
            internet_checksum(pseudo + modified[modified_view.udp_offset :]),
            0,
        )

    def test_building_carrier_to_zone_remains_equal_length(self) -> None:
        payload = carrier_payload()
        result = rewrite_stellaris_payload(
            payload,
            {
                "type": "build_zone",
                "build_queue_id": 6,
                "colony_id": 0,
                "district_id": 0,
                "slot_selector": 1,
                "zone_type": "zone_trade",
            },
        )
        self.assertIsNotNone(result)
        modified, metadata = result
        self.assertEqual(len(modified), len(payload))
        self.assertEqual(modified[:25], payload[:25])
        self.assertIn(b"zone_trade", modified)
        self.assertNotIn(b"building_research_lab_1", modified)
        self.assertEqual(metadata["declared_record_length"], 153)


if __name__ == "__main__":
    unittest.main()
