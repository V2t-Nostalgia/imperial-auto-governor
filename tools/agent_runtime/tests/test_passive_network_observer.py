from __future__ import annotations

import ipaddress
import socket
import struct
import sys
import unittest
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from passive_network_observer import (  # noqa: E402
    COMMAND_MARKER,
    RELIABLE_MARKER,
    RELIABLE_PREFIX,
    DatagramView,
    FlowTracker,
    is_reliable_transport_payload,
    parse_ethernet_ipv4_udp,
    build_udp_port_filter,
    telemetry_document,
)


def ethernet_udp_frame(
    source_ip: str,
    destination_ip: str,
    source_port: int,
    destination_port: int,
    payload: bytes,
) -> bytes:
    ethernet = bytes.fromhex("00112233445566778899aabb0800")
    udp_length = 8 + len(payload)
    udp = struct.pack("!HHHH", source_port, destination_port, udp_length, 0)
    total_length = 20 + udp_length
    ipv4 = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        total_length,
        1,
        0,
        64,
        socket.IPPROTO_UDP,
        0,
        ipaddress.IPv4Address(source_ip).packed,
        ipaddress.IPv4Address(destination_ip).packed,
    )
    return ethernet + ipv4 + udp + payload


class PassiveObserverTests(unittest.TestCase):
    def test_recognizes_observed_reliable_marker_variants(self) -> None:
        self.assertTrue(
            is_reliable_transport_payload(RELIABLE_PREFIX + b"\x00payload")
        )
        self.assertTrue(
            is_reliable_transport_payload(RELIABLE_PREFIX + b"\x01payload")
        )
        self.assertFalse(
            is_reliable_transport_payload(RELIABLE_PREFIX + b"\x02payload")
        )

    def test_bpf_filter_targets_owned_source_and_destination_ports(self) -> None:
        program = build_udp_port_filter([53708])

        self.assertEqual(len(program), 14)
        self.assertEqual(program[0], (0x28, 0, 0, 12))
        self.assertEqual(program[-1], (0x06, 0, 0, 0))
        self.assertEqual(
            program.count((0x15, 0, 1, 53708)),
            2,
        )
    def test_parses_ethernet_ipv4_udp(self) -> None:
        payload = RELIABLE_MARKER + b"payload"
        result = parse_ethernet_ipv4_udp(
            ethernet_udp_frame(
                "192.0.2.10",
                "198.51.100.20",
                53708,
                58276,
                payload,
            )
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source_port, 53708)
        self.assertEqual(result.destination_port, 58276)
        self.assertEqual(result.payload, payload)

    def test_selects_bidirectional_reliable_steam_flow(self) -> None:
        tracker = FlowTracker(window_seconds=12)
        now = 100.0
        tracker.observe(
            DatagramView(
                "192.0.2.10",
                "198.51.100.20",
                53708,
                58276,
                RELIABLE_MARKER + COMMAND_MARKER,
            ),
            direction="outbound",
            owner_names={"steam"},
            now_mono=now,
            observed_at="2026-07-29T12:00:00+08:00",
        )
        tracker.observe(
            DatagramView(
                "198.51.100.20",
                "192.0.2.10",
                58276,
                53708,
                RELIABLE_PREFIX + b"\x01host",
            ),
            direction="inbound",
            owner_names={"steam"},
            now_mono=now + 0.1,
            observed_at="2026-07-29T12:00:00.100+08:00",
        )

        result = telemetry_document(
            tracker,
            now_mono=now + 0.2,
            configured_host_ip=None,
            interface="ens34",
            game_process_present=True,
        )

        self.assertTrue(result["candidate_active"])
        self.assertEqual(result["transport_owner"], "steam")
        self.assertEqual(result["host_ip"], "198.51.100.20")
        self.assertEqual(result["local_udp_port"], 53708)
        self.assertEqual(result["host_destination_port"], 58276)
        self.assertGreater(
            result["protocol_markers"]["command_marker_packets"],
            0,
        )

    def test_selects_unique_bidirectional_flow_with_one_way_reliable_marker(
        self,
    ) -> None:
        tracker = FlowTracker(window_seconds=12)
        now = 200.0
        tracker.observe(
            DatagramView(
                "192.0.2.10",
                "198.51.100.20",
                53708,
                58276,
                RELIABLE_MARKER + b"client",
            ),
            direction="outbound",
            owner_names={"steam"},
            now_mono=now,
            observed_at="2026-07-29T12:00:00+08:00",
        )
        tracker.observe(
            DatagramView(
                "198.51.100.20",
                "192.0.2.10",
                58276,
                53708,
                b"host-without-leading-reliable-marker",
            ),
            direction="inbound",
            owner_names={"steam"},
            now_mono=now + 0.1,
            observed_at="2026-07-29T12:00:00.100+08:00",
        )

        result = telemetry_document(
            tracker,
            now_mono=now + 0.2,
            configured_host_ip=None,
            interface="ens34",
            game_process_present=True,
        )

        self.assertTrue(result["candidate_active"])
        self.assertEqual(result["host_ip"], "198.51.100.20")
        self.assertEqual(result["local_udp_port"], 53708)
        self.assertEqual(result["host_destination_port"], 58276)

    def test_does_not_guess_between_two_eligible_peers(self) -> None:
        tracker = FlowTracker(window_seconds=12)
        now = 300.0
        for index, remote_ip in enumerate(("198.51.100.20", "203.0.113.31")):
            observed = now + index
            tracker.observe(
                DatagramView(
                    "192.0.2.10",
                    remote_ip,
                    53708,
                    58276 + index,
                    RELIABLE_MARKER + b"client",
                ),
                direction="outbound",
                owner_names={"steam"},
                now_mono=observed,
                observed_at=f"2026-07-29T12:00:0{index}+08:00",
            )
            tracker.observe(
                DatagramView(
                    remote_ip,
                    "192.0.2.10",
                    58276 + index,
                    53708,
                    b"host",
                ),
                direction="inbound",
                owner_names={"steam"},
                now_mono=observed + 0.1,
                observed_at=f"2026-07-29T12:00:0{index}.100+08:00",
            )

        result = telemetry_document(
            tracker,
            now_mono=now + 2,
            configured_host_ip=None,
            interface="ens34",
            game_process_present=True,
        )

        self.assertFalse(result["candidate_active"])
        self.assertIsNone(result["host_ip"])
        self.assertEqual(result["candidate_count"], 2)


if __name__ == "__main__":
    unittest.main()
