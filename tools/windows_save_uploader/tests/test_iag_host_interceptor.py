from __future__ import annotations

import sys
import unittest
from pathlib import Path


UPLOADER = Path(__file__).resolve().parents[1]
PACKET_TOOLS = UPLOADER.parent / "packet_interceptor"
for path in (UPLOADER, PACKET_TOOLS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from iag_host_interceptor import (  # noqa: E402
    SOURCE_CARRIER_IDS,
    HostInterceptorError,
    packet_filter_for_peer,
    rewrite_carrier_payload,
    validate_request,
)
from iag_stream_command_injector import (  # noqa: E402
    EARTH_RESEARCH_COMMAND_TEMPLATE,
    find_command_records,
    write_uint24_be,
)


def reliable_header(sender: int, ack: int) -> bytes:
    value = bytes.fromhex(
        "010000000000000000000000000000f0000000000000000000"
    )
    value = write_uint24_be(value, 6, sender)
    return write_uint24_be(value, 10, ack)


def replace_fixture_carrier_id(payload: bytes, target_id: str) -> bytes:
    """Adapt a real research-lab capture to the longer mod carrier layout."""
    source = b"building_research_lab_1"
    target = target_id.encode("ascii")
    marker_offset = payload.index(source)
    record = next(
        item
        for item in find_command_records(payload)
        if item.offset <= marker_offset < item.offset + item.length
    )
    length_offset = marker_offset - 2
    rewritten = (
        payload[:length_offset]
        + len(target).to_bytes(2, "little")
        + target
        + payload[marker_offset + len(source) :]
    )
    delta = len(target) - len(source)
    declared_length = record.length + delta - 1
    return (
        rewritten[: record.offset]
        + declared_length.to_bytes(2, "little")
        + rewritten[record.offset + 2 :]
    )


def carrier_payload() -> bytes:
    carrier = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE)
    carrier[58:60] = (1023).to_bytes(2, "little")
    return replace_fixture_carrier_id(
        reliable_header(1000, 500) + bytes(carrier),
        SOURCE_CARRIER_IDS["build_building"],
    )


def legacy_zone_slot_carrier_payload() -> bytes:
    """Real LLM-to-host carrier captured from the 2026-07-30 UNE retest."""
    return bytes.fromhex(
        "010000000000013f8200181ff40000ef6daeafae120356a7de000000"
        "980004000000b43d01000300f30101000300400201000c0002000000"
        "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
        "14005b0000000400410001000300822c010014000000000063400100"
        "14001d010000b23d01000300b22b01000f0017006275696c64696e67"
        "5f72657365617263685f6c61625f31132a0100140050000000b32b01"
        "001400d4000000040004000400"
    )


def zone_slot_carrier_payload() -> bytes:
    captured = legacy_zone_slot_carrier_payload()
    return replace_fixture_carrier_id(
        captured,
        SOURCE_CARRIER_IDS["build_building"],
    )


def district_generator_carrier_payload() -> bytes:
    """Real 4.4.6 district_generator record captured on Trantor."""
    return bytes.fromhex(
        "890004000000b43d01000300f30101000300400201000c0002000000"
        "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
        "1400830400000400410001000300822c010014000000000063400100"
        "14001d010000b03d01000300b42b01000f0012006469737472696374"
        "5f67656e657261746f72132a0100140050000000040004000400"
    )


def zone_engineering_carrier_payload() -> bytes:
    """Real 4.4.6 zone_research_engineering request captured on 2026-07-30."""
    return bytes.fromhex(
        "a40004000000b43d01000300f30101000300400201000c0003000000"
        "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
        "14001a0000000400410001000300822c010014000000000063400100"
        "140000000000a24401000300b32b01000f0019007a6f6e655f726573"
        "65617263685f656e67696e656572696e67132a0100140000000000b4"
        "2b0100140001000000a44401000c0001000000040004000400"
    )


def research_lab_upgrade_carrier_payload() -> bytes:
    """Real Earth research-lab upgrade captured on 2026-08-02."""
    return bytes.fromhex(
        "a20004000000b43d01000300f30101000300400201000c0002000000"
        "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
        "14001f0000000400410001000300822c010014000000000063400100"
        "140000000000c03d01000300b22b01000f0017006275696c64696e67"
        "5f72657365617263685f6c61625f32132a0100140000000000b32b01"
        "00140001000000bf3d010014003e010001040004000400"
    )


def holo_theatre_replacement_carrier_payload() -> bytes:
    """Real Sirius-I replacement captured on 2026-08-03."""
    return bytes.fromhex(
        "a10004000000b43d01000300f30101000300400201000c0003000000"
        "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
        "1400210000000400410001000300822c010014000000000063400100"
        "1400d81f0000bd3d01000300b22b01000f0016006275696c64696e67"
        "5f686f6c6f5f7468656174726573132a010014004b000000b32b0100"
        "1400940000015a3d01001400a3010000040004000400"
    )


def request(action: dict) -> dict:
    return {
        "schema": "iag.host_executor_request.v1",
        "request_id": "20260729_220319_abcdef123456",
        "expected_client_id": "client-12345678",
        "peer_ip": "192.0.2.10",
        "carrier": {
            "command": SOURCE_CARRIER_IDS[action["type"]],
            "required_direction": "inbound_to_host",
        },
        "action": action,
        "safety": {
            "one_shot": True,
            "preserve_udp_payload_length": True,
            "preserve_command_count": True,
            "preserve_carrier_serial": True,
            "require_authoritative_host_confirmation": True,
            "drop_unrewritable_carrier": True,
        },
    }


class HostInterceptorTests(unittest.TestCase):
    def test_building_rewrite_redirects_carrier_without_resizing(self) -> None:
        action = {
            "type": "build_building",
            "planet_id": 3,
            "build_queue_id": 0,
            "colony_id": 0,
            "zone_id": 0,
            "building_id": "building_holo_theatres",
        }
        validate_request(request(action), client_id="client-12345678")
        original = carrier_payload()
        rewritten, metadata = rewrite_carrier_payload(original, action)
        self.assertEqual(len(rewritten), len(original))
        self.assertEqual(rewritten[:25], original[:25])
        self.assertIn(b"building_holo_theatres", rewritten)
        self.assertNotIn(
            SOURCE_CARRIER_IDS["build_building"].encode("ascii"),
            rewritten,
        )
        self.assertEqual(metadata["carrier_serial"], 1023)

    def test_building_rewrite_uses_record_fields_without_fixed_udp_header(self) -> None:
        action = {
            "type": "build_building",
            "planet_id": 3,
            "build_queue_id": 0,
            "colony_id": 0,
            "zone_id": 0,
            "building_id": "building_holo_theatres",
        }
        carrier = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE)
        carrier[58:60] = (2047).to_bytes(2, "little")
        prefix = bytes.fromhex("deadbeef010203")
        suffix = bytes.fromhex("aabbcc")
        original = replace_fixture_carrier_id(
            prefix + bytes(carrier) + suffix,
            SOURCE_CARRIER_IDS["build_building"],
        )

        rewritten, metadata = rewrite_carrier_payload(original, action)

        self.assertEqual(len(rewritten), len(original))
        self.assertTrue(rewritten.startswith(prefix))
        self.assertTrue(rewritten.endswith(suffix))
        self.assertIn(b"building_holo_theatres", rewritten)
        self.assertNotIn(
            SOURCE_CARRIER_IDS["build_building"].encode("ascii"),
            rewritten,
        )
        self.assertEqual(metadata["carrier_serial"], 2047)
        self.assertEqual(
            metadata["padding_length"],
            len(SOURCE_CARRIER_IDS["build_building"])
            - len("building_holo_theatres"),
        )
        self.assertIn("fixed_length_padding", metadata["match_reason"])

    def test_zone_slot_capture_rewrites_save_derived_target_fields(self) -> None:
        action = {
            "type": "build_building",
            "planet_id": 3,
            "build_queue_id": 901,
            "colony_id": 82,
            "zone_id": 213,
            "building_id": "building_holo_theatres",
        }
        validate_request(request(action), client_id="client-12345678")
        original = zone_slot_carrier_payload()

        rewritten, metadata = rewrite_carrier_payload(original, action)

        self.assertEqual(len(rewritten), len(original))
        self.assertEqual(rewritten[:28], original[:28])
        self.assertEqual(rewritten[28:30], original[28:30])
        self.assertIn(b"building_holo_theatres", rewritten)
        self.assertNotIn(
            SOURCE_CARRIER_IDS["build_building"].encode("ascii"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("634001001400") + (901).to_bytes(4, "little"),
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
        self.assertEqual(metadata["layout"], "zone_slot_building_v1")
        self.assertEqual(metadata["carrier_serial"], 91)
        self.assertEqual(metadata["source_build_queue_id"], 285)
        self.assertEqual(metadata["source_colony_id"], 80)
        self.assertEqual(metadata["source_zone_id"], 212)
        self.assertEqual(metadata["queue_field_tag"], "6340")
        self.assertEqual(metadata["outer_string_field_tag"], "b23d")

    def test_zone_slot_layout_preserves_session_specific_field_tags(self) -> None:
        action = {
            "type": "build_building",
            "planet_id": 3,
            "build_queue_id": 6,
            "colony_id": 9,
            "zone_id": 1,
            "building_id": "building_holo_theatres",
        }
        original = zone_slot_carrier_payload().replace(
            bytes.fromhex("634001001400"),
            bytes.fromhex("aa5501001400"),
            1,
        ).replace(
            bytes.fromhex("b23d01000300"),
            bytes.fromhex("bb6601000300"),
            1,
        )

        rewritten, metadata = rewrite_carrier_payload(original, action)

        self.assertEqual(len(rewritten), len(original))
        self.assertIn(bytes.fromhex("aa5501001400"), rewritten)
        self.assertIn(bytes.fromhex("bb6601000300"), rewritten)
        self.assertEqual(metadata["queue_field_tag"], "aa55")
        self.assertEqual(metadata["outer_string_field_tag"], "bb66")

    def test_long_carrier_fits_mineral_purification_plant(self) -> None:
        action = {
            "type": "build_building",
            "planet_id": 3,
            "build_queue_id": 901,
            "colony_id": 82,
            "zone_id": 213,
            "building_id": "building_mineral_purification_plant",
        }
        validate_request(request(action), client_id="client-12345678")

        original = zone_slot_carrier_payload()
        rewritten, metadata = rewrite_carrier_payload(original, action)

        self.assertEqual(len(rewritten), len(original))
        self.assertIn(b"building_mineral_purification_plant", rewritten)
        self.assertEqual(metadata["padding_length"], 4)

    def test_legacy_research_carrier_remains_accepted_during_rollout(self) -> None:
        action = {
            "type": "build_building",
            "planet_id": 3,
            "build_queue_id": 901,
            "colony_id": 82,
            "zone_id": 213,
            "building_id": "building_holo_theatres",
        }
        legacy_request = request(action)
        legacy_request["carrier"]["command"] = "building_research_lab_1"
        validated = validate_request(
            legacy_request,
            client_id="client-12345678",
        )

        original = legacy_zone_slot_carrier_payload()
        rewritten, metadata = rewrite_carrier_payload(original, validated)

        self.assertEqual(len(rewritten), len(original))
        self.assertIn(b"building_holo_theatres", rewritten)
        self.assertEqual(metadata["padding_length"], 1)

    def test_district_carrier_rewrites_a_main_district(self) -> None:
        action = {
            "type": "build_district",
            "build_queue_id": 901,
            "colony_id": 82,
            "district_type": "district_city",
        }
        validate_request(request(action), client_id="client-12345678")
        original = district_generator_carrier_payload()

        rewritten, metadata = rewrite_carrier_payload(original, action)

        self.assertEqual(len(rewritten), len(original))
        self.assertIn(b"district_city", rewritten)
        self.assertNotIn(b"district_generator", rewritten)
        self.assertEqual(metadata["layout"], "district_construction_v1")
        self.assertEqual(metadata["carrier_serial"], 1155)
        self.assertEqual(metadata["padding_length"], 5)

    def test_engineering_carrier_fits_every_selected_zone(self) -> None:
        selected_zones = (
            "zone_foundry",
            "zone_factory",
            "zone_research",
            "zone_research_physics",
            "zone_research_society",
            "zone_research_engineering",
            "zone_unity",
            "zone_trade",
            "zone_energy",
            "zone_minerals",
            "zone_food",
        )
        original = zone_engineering_carrier_payload()
        for zone_type in selected_zones:
            with self.subTest(zone_type=zone_type):
                action = {
                    "type": "build_zone",
                    "build_queue_id": 901,
                    "colony_id": 82,
                    "district_id": 213,
                    "slot_selector": 2,
                    "zone_type": zone_type,
                }
                validate_request(request(action), client_id="client-12345678")

                rewritten, metadata = rewrite_carrier_payload(original, action)

                self.assertEqual(len(rewritten), len(original))
                self.assertIn(zone_type.encode("ascii"), rewritten)
                self.assertEqual(metadata["layout"], "zone_construction_v1")
                self.assertEqual(metadata["carrier_serial"], 26)
                self.assertEqual(
                    metadata["padding_length"],
                    len("zone_research_engineering") - len(zone_type),
                )
                self.assertEqual(metadata["queue_field_tag"], "6340")
                self.assertEqual(metadata["dynamic_marker_tag"], "a244")
                self.assertEqual(metadata["slot_field_tag"], "a444")
                self.assertEqual(metadata["source_build_queue_id"], 0)
                self.assertEqual(metadata["source_colony_id"], 0)
                self.assertEqual(metadata["source_district_id"], 1)
                self.assertEqual(metadata["source_slot_selector"], 1)

    def test_upgrade_rewrite_redirects_real_earth_record_to_alpha(self) -> None:
        action = {
            "type": "upgrade_building",
            "planet_id": 280,
            "build_queue_id": 8555,
            "colony_id": 76,
            "zone_id": 229,
            "building_object_id": 452,
            "from_building_id": "building_research_lab_1",
            "to_building_id": "building_research_lab_2",
        }
        legacy_request = request(action)
        legacy_request["carrier"]["command"] = "building_research_lab_2"
        validated = validate_request(
            legacy_request,
            client_id="client-12345678",
        )

        original = research_lab_upgrade_carrier_payload()
        rewritten, metadata = rewrite_carrier_payload(original, validated)

        self.assertEqual(len(rewritten), len(original))
        self.assertIn(b"building_research_lab_2", rewritten)
        self.assertIn(
            bytes.fromhex("634001001400") + (8555).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("132a01001400") + (76).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("b32b01001400") + (229).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("bf3d01001400") + (452).to_bytes(4, "little"),
            rewritten,
        )
        self.assertEqual(metadata["layout"], "building_upgrade_v1")
        self.assertEqual(metadata["carrier_serial"], 31)
        self.assertEqual(metadata["source_building_object_id"], 16777534)

    def test_replacement_rewrite_redirects_real_record_to_exact_slot(self) -> None:
        action = {
            "type": "replace_building",
            "planet_id": 280,
            "build_queue_id": 8555,
            "colony_id": 76,
            "zone_id": 229,
            "building_position": 1,
            "building_object_id": 463,
            "from_building_id": "building_commercial_zone",
            "to_building_id": "building_foundry_1",
        }
        legacy_request = request(action)
        legacy_request["carrier"]["command"] = "building_holo_theatres"
        validated = validate_request(
            legacy_request,
            client_id="client-12345678",
        )

        original = holo_theatre_replacement_carrier_payload()
        rewritten, metadata = rewrite_carrier_payload(original, validated)

        self.assertEqual(len(rewritten), len(original))
        self.assertIn(b"building_foundry_1", rewritten)
        self.assertIn(
            bytes.fromhex("634001001400") + (8555).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("132a01001400") + (76).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("b32b01001400") + (229).to_bytes(4, "little"),
            rewritten,
        )
        self.assertIn(
            bytes.fromhex("5a3d01001400") + (463).to_bytes(4, "little"),
            rewritten,
        )
        self.assertEqual(metadata["layout"], "building_replacement_v1")
        self.assertEqual(metadata["carrier_serial"], 33)

    def test_rejects_payload_without_exact_carrier(self) -> None:
        action = {
            "type": "build_building",
            "planet_id": 3,
            "build_queue_id": 0,
            "colony_id": 0,
            "zone_id": 0,
            "building_id": "building_foundry_1",
        }
        self.assertIsNone(rewrite_carrier_payload(b"unrelated", action))

    def test_filter_covers_only_peer_ip(self) -> None:
        value = packet_filter_for_peer("192.0.2.10")
        self.assertIn("ip.SrcAddr == 192.0.2.10", value)
        self.assertIn("ip.DstAddr == 192.0.2.10", value)

    def test_rejects_non_verified_carrier(self) -> None:
        value = request(
            {
                "type": "build_building",
                "planet_id": 3,
                "build_queue_id": 0,
                "colony_id": 0,
                "zone_id": 0,
                "building_id": "building_foundry_1",
            }
        )
        value["carrier"]["command"] = "building_foundry_1"
        with self.assertRaises(HostInterceptorError):
            validate_request(value, client_id="client-12345678")


if __name__ == "__main__":
    unittest.main()
