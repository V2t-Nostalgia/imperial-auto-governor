from __future__ import annotations

import unittest
from argparse import Namespace

from iag_packet_interceptor import (
    ADMINISTRATIVE_OFFICE,
    COMMAND_ENVELOPE_DISTANCE,
    COMMAND_ENVELOPE_TYPE,
    FOUNDRY,
    RESEARCH_LAB,
    TEST1_RESEARCH_CORE,
    TEST1_RESEARCH_FRAGMENT,
    build_filter,
    build_planet_signature,
    build_test1_signature,
    build_zone_signature,
    replace_planet_building,
    replace_planet_zone,
    replace_test1_building,
    replace_test1_research_with_admin,
)


class ReplacementTests(unittest.TestCase):
    def test_known_core_is_replaced_without_length_change(self) -> None:
        payload = b"\x01transport-prefix" + TEST1_RESEARCH_CORE + b"suffix"
        result = replace_test1_research_with_admin(payload)

        self.assertTrue(result.matched)
        self.assertEqual(len(result.payload), len(payload))
        self.assertNotIn(RESEARCH_LAB, result.payload)
        self.assertEqual(result.payload.count(ADMINISTRATIVE_OFFICE), 1)

    def test_live_boundary_fragment_is_replaced(self) -> None:
        payload = b"\x01transport-prefix" + TEST1_RESEARCH_FRAGMENT
        result = replace_test1_research_with_admin(payload)

        self.assertTrue(result.matched)
        self.assertEqual(result.reason, "exact_planet_building_boundary_fragment")
        self.assertEqual(len(result.payload), len(payload))
        self.assertNotIn(RESEARCH_LAB, result.payload)
        self.assertEqual(result.payload.count(ADMINISTRATIVE_OFFICE), 1)

    def test_variable_length_building_rewrite_is_blocked(self) -> None:
        source = build_test1_signature(FOUNDRY, boundary=True)
        payload = b"\x01transport-prefix" + source
        result = replace_test1_building(payload, FOUNDRY, RESEARCH_LAB)

        self.assertTrue(result.matched)
        self.assertEqual(result.payload, payload)
        self.assertEqual(
            result.reason,
            "unsafe_variable_length_building_rewrite_blocked;"
            "source_length=18;target_length=23",
        )

    def test_complete_variable_length_building_envelope_is_resized(self) -> None:
        source = build_test1_signature(FOUNDRY)
        envelope_prefix = (
            (len(source) + COMMAND_ENVELOPE_DISTANCE - 1).to_bytes(2, "little")
            + COMMAND_ENVELOPE_TYPE
            + bytes(COMMAND_ENVELOPE_DISTANCE - 6)
        )
        payload = b"batch" + envelope_prefix + source + b"next-record"
        result = replace_test1_building(
            payload,
            FOUNDRY,
            RESEARCH_LAB,
            allow_variable_length=True,
        )

        self.assertTrue(result.matched)
        self.assertEqual(
            result.reason,
            "exact_planet_building_core_with_envelope_resize",
        )
        self.assertEqual(len(result.payload), len(payload) + 5)
        envelope_offset = len(b"batch")
        self.assertEqual(
            int.from_bytes(
                result.payload[envelope_offset : envelope_offset + 2],
                "little",
            ),
            int.from_bytes(
                payload[envelope_offset : envelope_offset + 2],
                "little",
            )
            + 5,
        )
        self.assertTrue(result.payload.endswith(b"next-record"))

    def test_shorter_building_can_preserve_datagram_with_boundary_padding(self) -> None:
        source = build_test1_signature(RESEARCH_LAB)
        envelope_prefix = (
            (len(source) + COMMAND_ENVELOPE_DISTANCE - 1).to_bytes(2, "little")
            + COMMAND_ENVELOPE_TYPE
            + bytes(COMMAND_ENVELOPE_DISTANCE - 6)
        )
        payload = b"batch" + envelope_prefix + source + b"next-record"
        result = replace_planet_building(
            payload,
            RESEARCH_LAB,
            FOUNDRY,
            616,
            308,
            23,
            616,
            308,
            23,
            pad_variable_length=True,
        )

        self.assertTrue(result.matched)
        self.assertEqual(len(result.payload), len(payload))
        self.assertEqual(
            result.reason,
            "exact_planet_building_core_with_fixed_length_padding",
        )
        self.assertNotIn(RESEARCH_LAB, result.payload)
        self.assertEqual(result.payload.count(FOUNDRY), 1)
        self.assertTrue(result.payload.endswith(b"next-record"))
        self.assertEqual(
            result.payload[
                len(b"batch") + len(envelope_prefix) + len(build_test1_signature(FOUNDRY)):
                len(b"batch") + len(envelope_prefix) + len(source)
            ],
            bytes(5),
        )

    def test_earth_command_can_be_redirected_to_test1(self) -> None:
        earth = build_planet_signature(
            RESEARCH_LAB,
            ea3f=6,
            fc29=3,
            placement=0,
            boundary=True,
        )
        result = replace_planet_building(
            b"outer" + earth,
            RESEARCH_LAB,
            RESEARCH_LAB,
            source_ea3f=6,
            source_fc29=3,
            source_placement=0,
            target_ea3f=616,
            target_fc29=308,
            target_placement=23,
        )

        self.assertTrue(result.matched)
        self.assertTrue(
            result.payload.endswith(
                build_test1_signature(RESEARCH_LAB, boundary=True)
            )
        )

    def test_foundry_zone_can_be_replaced_with_factory_zone(self) -> None:
        source = build_zone_signature(
            b"zone_foundry",
            build_queue_id=398,
            planet_id=199,
            target_zone_id=11,
            boundary=True,
        )
        result = replace_planet_zone(
            b"outer" + source,
            b"zone_foundry",
            b"zone_factory",
            398,
            199,
            11,
            398,
            199,
            11,
        )

        self.assertTrue(result.matched)
        self.assertEqual(len(result.payload), len(b"outer" + source))
        self.assertNotIn(b"zone_foundry", result.payload)
        self.assertEqual(result.payload.count(b"zone_factory"), 1)

    def test_variable_length_zone_rewrite_is_blocked(self) -> None:
        source = build_zone_signature(
            b"zone_fortress",
            build_queue_id=398,
            planet_id=199,
            target_zone_id=11,
            boundary=True,
        )
        payload = b"outer" + source
        result = replace_planet_zone(
            payload,
            b"zone_fortress",
            b"zone_trade",
            398,
            199,
            11,
            398,
            199,
            11,
        )

        self.assertTrue(result.matched)
        self.assertEqual(result.payload, payload)
        self.assertEqual(
            result.reason,
            "unsafe_variable_length_zone_rewrite_blocked;"
            "source_length=13;target_length=10",
        )

    def test_fragment_not_at_packet_end_is_rejected(self) -> None:
        payload = TEST1_RESEARCH_FRAGMENT + b"\x99"
        result = replace_test1_research_with_admin(payload)

        self.assertFalse(result.matched)
        self.assertEqual(result.payload, payload)

    def test_unrelated_payload_is_unchanged(self) -> None:
        payload = b"\x01unrelated-building_research_lab_1-state"
        result = replace_test1_research_with_admin(payload)

        self.assertFalse(result.matched)
        self.assertEqual(result.payload, payload)
        self.assertEqual(
            result.reason,
            "stable_core_count=0;boundary_fragment_count=0",
        )

    def test_duplicate_core_is_rejected(self) -> None:
        payload = TEST1_RESEARCH_CORE + TEST1_RESEARCH_CORE
        result = replace_test1_research_with_admin(payload)

        self.assertFalse(result.matched)
        self.assertEqual(result.payload, payload)
        self.assertEqual(
            result.reason,
            "stable_core_count=2;boundary_fragment_count=0",
        )

    def test_discovery_filter_is_udp_only(self) -> None:
        args = Namespace(
            discover=True,
            discover_inbound=False,
            remote_ip="198.51.100.20",
            remote_port=1,
            local_ip="192.0.2.10",
            local_port=2,
        )
        self.assertEqual(build_filter(args), "udp")

    def test_discovery_can_use_exact_inbound_flow(self) -> None:
        args = Namespace(
            discover=True,
            discover_inbound=True,
            remote_ip="198.51.100.20",
            remote_port=1111,
            local_ip="192.0.2.10",
            local_port=2222,
        )
        self.assertIn("inbound and udp", build_filter(args))
        self.assertIn("udp.SrcPort == 1111", build_filter(args))

    def test_trace_filter_is_bidirectional_by_ip(self) -> None:
        args = Namespace(
            trace_flow=True,
            trace_local_port=0,
            discover=False,
            remote_ip="198.51.100.20",
            remote_port=1111,
            local_ip="192.0.2.10",
            local_port=2222,
        )
        packet_filter = build_filter(args)
        self.assertIn("ip.SrcAddr == 198.51.100.20", packet_filter)
        self.assertIn("ip.SrcAddr == 192.0.2.10", packet_filter)
        self.assertNotIn("udp.SrcPort", packet_filter)

    def test_trace_filter_can_follow_one_local_port(self) -> None:
        args = Namespace(
            trace_flow=True,
            trace_all_udp=False,
            trace_local_port=60144,
            discover=False,
            remote_ip="198.51.100.20",
            remote_port=1111,
            local_ip="192.0.2.10",
            local_port=2222,
        )
        self.assertEqual(
            build_filter(args),
            "udp and (udp.SrcPort == 60144 or udp.DstPort == 60144)",
        )

    def test_trace_filter_can_capture_all_udp(self) -> None:
        args = Namespace(
            trace_flow=True,
            trace_all_udp=True,
            trace_local_port=0,
            discover=False,
            remote_ip="198.51.100.20",
            remote_port=1111,
            local_ip="192.0.2.10",
            local_port=2222,
        )
        self.assertEqual(build_filter(args), "udp")


if __name__ == "__main__":
    unittest.main()
