import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iag.stellaris.execution.packet.autonomous_commands import (
    RESEARCH_LAB_RECORD_TEMPLATE,
    BuildingTarget,
    build_building_record,
)
from iag.stellaris.execution.packet.iag_stream_command_injector import (
    RELIABLE_HEADER_LENGTH,
    StreamTranslator,
    is_reliable_packet,
    read_uint24_be,
    write_uint24_be,
)
from iag.stellaris.execution.session_proxy import (
    APPLICATION_COMMAND_PREFIX,
    COMMAND_RECORD_LENGTH,
    INSERTED_LENGTH,
    ArmRequest,
    DistrictConstructionTarget,
    FleetMoveTarget,
    FlowDiscovery,
    FlowKey,
    ZoneSpecializationTarget,
    build_district_record,
    build_fleet_move_record,
    build_zone_specialization_record,
    inject_at_packet_boundary,
    next_actor_serial,
    observe_command_serials,
    packet_filter_for_flow,
    parse_arm_request,
    retag_matching_response,
    session_udp_port_owners,
    translate_outbound_command_serials,
    write_json,
)


def header(sender: int = 1000, ack: int = 500) -> bytes:
    payload = bytes.fromhex("010000000000000000000000000000f0000000000000000000")
    payload = write_uint24_be(payload, 6, sender)
    return write_uint24_be(payload, 10, ack)


ZONE_RESEARCH_PHYSICS_RECORD = bytes.fromhex(
    "a00004000000b43d01000300f30101000300400201000c0001000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14004e1000000400410001000300822c010014000000000063400100"
    "140000000000a24401000300b32b01000f0015007a6f6e655f726573"
    "65617263685f70687973696373132a0100140000000000b42b010014"
    "0000000000a44401000c0001000000040004000400"
)

FLEET_ONE_TO_SOL_RECORD = bytes.fromhex(
    "730004000000d32c01000300f30101000300400201000c0001000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14006e1600000400410001000300502c010014000300000063400100"
    "0e0000de3501000e00008b3d010003000c3a01001400000000000400"
    "04000400"
)


class SessionProxyTests(unittest.TestCase):
    def test_reliable_header_accepts_independent_sender_epoch(self) -> None:
        inbound_after_wrap = bytearray(header())
        inbound_after_wrap[5] = 1
        self.assertTrue(is_reliable_packet(bytes(inbound_after_wrap)))

    def test_status_reader_lock_is_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            with patch.object(Path, "replace", side_effect=PermissionError("locked")):
                self.assertFalse(write_json(path, {"state": "TRANSLATING"}))
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_required_ready_file_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ready.json"
            with patch.object(Path, "replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    write_json(path, {"state": "READY"}, required=True)

    def test_flow_discovery_requires_both_directions(self) -> None:
        discovery = FlowDiscovery(minimum_each_direction=2)
        reliable = header()
        for index in range(2):
            self.assertIsNone(
                discovery.observe(
                    src_ip="192.0.2.10",
                    src_port=50000,
                    dst_ip="192.0.2.20",
                    dst_port=51000,
                    local_ip="192.0.2.10",
                    host_ip="192.0.2.20",
                    port_owners={},
                    game_process_present=True,
                    is_outbound=True,
                    is_inbound=False,
                    payload=reliable,
                    observed_at=float(index + 1),
                )
            )
        self.assertIsNone(discovery.locked)
        self.assertIsNone(
            discovery.observe(
                src_ip="192.0.2.20",
                src_port=51000,
                dst_ip="192.0.2.10",
                dst_port=50000,
                local_ip="192.0.2.10",
                host_ip="192.0.2.20",
                port_owners={},
                game_process_present=True,
                is_outbound=False,
                is_inbound=True,
                payload=reliable,
                observed_at=3.0,
            )
        )
        locked = discovery.observe(
            src_ip="192.0.2.20",
            src_port=51000,
            dst_ip="192.0.2.10",
            dst_port=50000,
            local_ip="192.0.2.10",
            host_ip="192.0.2.20",
            port_owners={},
            game_process_present=True,
            is_outbound=False,
            is_inbound=True,
            payload=reliable,
            observed_at=4.0,
        )
        self.assertIsNotNone(locked)
        assert locked is not None
        self.assertEqual(locked.local_port, 50000)
        self.assertEqual(locked.host_port, 51000)
        self.assertEqual(locked.host_ip, "192.0.2.20")
        self.assertEqual(locked.route, "direct_peer_ip")

    def test_owned_direct_flow_uses_v058_bidirectional_threshold(self) -> None:
        discovery = FlowDiscovery(minimum_each_direction=3)
        common = {
            "local_ip": "192.0.2.10",
            "host_ip": "192.0.2.20",
            "port_owners": {58778: {"steam.exe"}},
            "game_process_present": True,
        }
        discovery.observe(
            src_ip="192.0.2.10",
            src_port=58778,
            dst_ip="192.0.2.20",
            dst_port=52489,
            is_outbound=True,
            is_inbound=False,
            payload=header(),
            observed_at=1.0,
            **common,
        )
        locked = discovery.observe(
            src_ip="192.0.2.20",
            src_port=52489,
            dst_ip="192.0.2.10",
            dst_port=58778,
            is_outbound=False,
            is_inbound=True,
            payload=b"ordinary-inbound-frame",
            observed_at=2.0,
            **common,
        )
        self.assertIsNotNone(locked)
        assert locked is not None
        self.assertEqual(locked.local_port, 58778)
        self.assertEqual(locked.host_port, 52489)
        self.assertEqual(locked.route, "direct_peer_ip_owned_port")

    def test_flow_discovery_locks_steam_relay_by_transport_port(self) -> None:
        discovery = FlowDiscovery(minimum_each_direction=2)
        reliable = header()
        for index in range(2):
            discovery.observe(
                src_ip="192.0.2.10",
                src_port=52000,
                dst_ip="198.51.100.45",
                dst_port=61000,
                local_ip="192.0.2.10",
                host_ip="192.0.2.20",
                port_owners={52000: {"steam.exe"}},
                game_process_present=True,
                is_outbound=True,
                is_inbound=False,
                payload=reliable if index == 0 else b"steam-outbound-frame",
                observed_at=float(index + 1),
            )
        for index in range(2):
            locked = discovery.observe(
                src_ip="198.51.100.45",
                src_port=61000,
                dst_ip="192.0.2.10",
                dst_port=52000,
                local_ip="192.0.2.10",
                host_ip="192.0.2.20",
                port_owners={52000: {"steam.exe"}},
                game_process_present=True,
                is_outbound=False,
                is_inbound=True,
                payload=b"steam-inbound-frame",
                observed_at=float(index + 3),
            )
        self.assertIsNotNone(locked)
        assert locked is not None
        self.assertEqual(locked.host_ip, "198.51.100.45")
        self.assertEqual(locked.host_port, 61000)
        self.assertEqual(locked.route, "steam_brokered_udp_port")

    def test_flow_discovery_refuses_ambiguous_relay_candidates(self) -> None:
        discovery = FlowDiscovery(
            minimum_each_direction=1,
            relay_settle_seconds=0.5,
        )
        for peer_index, (local_port, remote_ip, remote_port) in enumerate(
            (
                (52000, "198.51.100.45", 61000),
                (52001, "198.51.100.46", 61001),
            )
        ):
            start = float(peer_index) / 10.0
            discovery.observe(
                src_ip="192.0.2.10",
                src_port=local_port,
                dst_ip=remote_ip,
                dst_port=remote_port,
                local_ip="192.0.2.10",
                host_ip="192.0.2.20",
                port_owners={
                    52000: {"steam.exe"},
                    52001: {"steam.exe"},
                },
                game_process_present=True,
                is_outbound=True,
                is_inbound=False,
                payload=header(),
                observed_at=1.0 + start,
            )
            discovery.observe(
                src_ip=remote_ip,
                src_port=remote_port,
                dst_ip="192.0.2.10",
                dst_port=local_port,
                local_ip="192.0.2.10",
                host_ip="192.0.2.20",
                port_owners={
                    52000: {"steam.exe"},
                    52001: {"steam.exe"},
                },
                game_process_present=True,
                is_outbound=False,
                is_inbound=True,
                payload=header(),
                observed_at=1.1 + start,
            )
        result = discovery.observe(
            src_ip="198.51.100.45",
            src_port=61000,
            dst_ip="192.0.2.10",
            dst_port=52000,
            local_ip="192.0.2.10",
            host_ip="192.0.2.20",
            port_owners={52000: {"steam.exe"}, 52001: {"steam.exe"}},
            game_process_present=True,
            is_outbound=False,
            is_inbound=True,
            payload=header(),
            observed_at=2.0,
        )
        self.assertIsNone(result)
        self.assertIsNone(discovery.locked)
        self.assertIsNone(discovery.provisional)

    def test_flow_discovery_requires_reliable_marker_for_relay(self) -> None:
        discovery = FlowDiscovery(
            minimum_each_direction=1,
            relay_settle_seconds=0.0,
        )
        common = {
            "local_ip": "192.0.2.10",
            "host_ip": "192.0.2.20",
            "port_owners": {52000: {"steam.exe"}},
            "game_process_present": True,
        }
        discovery.observe(
            src_ip="192.0.2.10",
            src_port=52000,
            dst_ip="198.51.100.45",
            dst_port=61000,
            is_outbound=True,
            is_inbound=False,
            payload=b"ordinary-steam-frame",
            observed_at=1.0,
            **common,
        )
        result = discovery.observe(
            src_ip="198.51.100.45",
            src_port=61000,
            dst_ip="192.0.2.10",
            dst_port=52000,
            is_outbound=False,
            is_inbound=True,
            payload=b"ordinary-steam-frame",
            observed_at=2.0,
            **common,
        )
        self.assertIsNone(result)
        self.assertIsNone(discovery.provisional)

    def test_flow_discovery_ignores_non_stellaris_relay_port(self) -> None:
        discovery = FlowDiscovery(minimum_each_direction=2)
        result = discovery.observe(
            src_ip="192.0.2.10",
            src_port=53000,
            dst_ip="198.51.100.45",
            dst_port=61000,
            local_ip="192.0.2.10",
            host_ip="192.0.2.20",
            port_owners={52000: {"steam.exe"}},
            game_process_present=True,
            is_outbound=True,
            is_inbound=False,
            payload=header(),
            observed_at=1.0,
        )
        self.assertIsNone(result)
        self.assertEqual(discovery.candidate_payload(), [])

    def test_locked_relay_filter_uses_actual_peer_tuple(self) -> None:
        packet_filter = packet_filter_for_flow(
            FlowKey(
                local_ip="192.0.2.10",
                local_port=52000,
                host_ip="198.51.100.45",
                host_port=61000,
                route="stellaris_process_udp_port",
            )
        )
        self.assertIn("ip.DstAddr == 198.51.100.45", packet_filter)
        self.assertIn("udp.SrcPort == 52000", packet_filter)
        self.assertIn("udp.DstPort == 61000", packet_filter)
        self.assertNotIn("192.0.2.20", packet_filter)

    def test_session_udp_ports_include_game_and_steam_owners(self) -> None:
        with patch(
            "iag.stellaris.execution.session_proxy.owned_udp_ports",
            return_value=(
                {
                    52000: {"stellaris.exe"},
                    52001: {"steam.exe"},
                },
                True,
            ),
        ):
            owners, game_present = session_udp_port_owners(
                ["stellaris"],
                ["steam"],
            )
        self.assertTrue(game_present)
        self.assertEqual(owners[52000], {"stellaris.exe"})
        self.assertEqual(owners[52001], {"steam.exe"})

    def test_arm_file_requires_current_session_id(self) -> None:
        value = {
            "request_id": "request-1",
            "session_id": "current-session",
            "action": "build_building",
            "request_origin": 0,
            "target": {
                "context_822c": 17,
                "build_queue_id": 131,
                "colony_id": 32,
                "zone_id": 142,
                "building_id": "building_research_lab_1",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "arm.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            request = parse_arm_request(path, "current-session")
            self.assertEqual(request.request_origin, 0)
            with self.assertRaisesRegex(ValueError, "session_id"):
                parse_arm_request(path, "stale-session")

    def test_next_serial_is_shared_across_client_origins(self) -> None:
        self.assertEqual(next_actor_serial({}, 2), 1)
        self.assertEqual(
            next_actor_serial(
                {
                    ("outbound", 2, 0): 9,
                    ("inbound", 2, 0): 37,
                    ("outbound", 2, 1): 417,
                },
                2,
            ),
            418,
        )
        self.assertEqual(
            next_actor_serial(
                {("outbound", 2, 0): 12},
                2,
                reserved_serials=2,
                previous_synthetic_serial=14,
            ),
            15,
        )

    def test_multiple_stream_insertions_compose_in_both_directions(self) -> None:
        first = StreamTranslator(injection_offset=1000, inserted_length=156)
        second = StreamTranslator(injection_offset=1156, inserted_length=119)
        outbound = header(sender=1000, ack=500)
        for translator in (first, second):
            outbound = translator.translate_outbound(outbound)
        self.assertEqual(read_uint24_be(outbound, 6), 1275)

        inbound = header(sender=4000, ack=1275)
        for translator in (second, first):
            inbound = translator.translate_inbound(inbound)
        self.assertEqual(read_uint24_be(inbound, 10), 1000)

    def test_building_record_resizes_for_longer_identifier(self) -> None:
        target = BuildingTarget(
            context_822c=0,
            build_queue_id=11,
            colony_id=12,
            zone_id=13,
            building_id="building_mineral_purification_plant",
        )
        record = build_building_record(
            command_serial=20,
            actor=2,
            origin=0,
            target=target,
        )
        self.assertIn(target.building_id.encode("ascii"), record)
        self.assertEqual(int.from_bytes(record[:2], "little") + 1, len(record))
        self.assertGreater(len(record), len(RESEARCH_LAB_RECORD_TEMPLATE))

    def test_translates_four_byte_serials_across_both_origins(self) -> None:
        first = bytearray(RESEARCH_LAB_RECORD_TEMPLATE)
        first[58:62] = (0x0000FFFF).to_bytes(4, "little")
        second = bytearray(RESEARCH_LAB_RECORD_TEMPLATE)
        second[58:62] = (0x00010000).to_bytes(4, "little")
        origin_tag = bytes.fromhex("130401000e00")
        second[second.index(origin_tag) + len(origin_tag)] = 1
        payload = header() + bytes(first) + bytes(second)

        rewritten, changes = translate_outbound_command_serials(
            payload,
            actor=2,
        )

        first_offset = RELIABLE_HEADER_LENGTH + 58
        second_offset = RELIABLE_HEADER_LENGTH + len(first) + 58
        self.assertEqual(
            int.from_bytes(rewritten[first_offset : first_offset + 4], "little"),
            0x00010000,
        )
        self.assertEqual(
            int.from_bytes(rewritten[second_offset : second_offset + 4], "little"),
            0x00010001,
        )
        self.assertEqual(
            [(change["origin"], change["serial_u32"]) for change in changes],
            [(0, 0x0000FFFF), (1, 0x00010000)],
        )

    def test_injection_matches_genuine_client_request_envelope(self) -> None:
        request = ArmRequest(
            request_id="request-1",
            session_id="test",
            action="build_building",
            source_actor=2,
            host_actor=1,
            request_origin=0,
            target=BuildingTarget(
                context_822c=17,
                build_queue_id=131,
                colony_id=32,
                zone_id=142,
            ),
        )
        carrier = header(sender=2000, ack=9000)
        injection = inject_at_packet_boundary(
            carrier,
            request=request,
            command_serial=1,
            max_payload_length=1400,
        )
        self.assertIsNotNone(injection)
        assert injection is not None
        self.assertEqual(COMMAND_RECORD_LENGTH, len(RESEARCH_LAB_RECORD_TEMPLATE))
        self.assertEqual(INSERTED_LENGTH, 3 + len(RESEARCH_LAB_RECORD_TEMPLATE))
        self.assertEqual(len(injection.payload), 181)
        self.assertEqual(
            injection.payload[RELIABLE_HEADER_LENGTH:RELIABLE_HEADER_LENGTH + 3],
            APPLICATION_COMMAND_PREFIX,
        )
        observations: dict[tuple[str, int, int], int] = {}
        parsed = observe_command_serials(
            injection.payload,
            observations,
            "outbound",
        )
        self.assertEqual(parsed[0]["actor"], 2)
        self.assertEqual(parsed[0]["origin"], 0)
        self.assertEqual(parsed[0]["serial_u32"], 1)

        translator = StreamTranslator(injection.injection_offset, INSERTED_LENGTH)
        next_local = header(sender=2000, ack=9000) + b"next"
        next_host = translator.translate_outbound(next_local)
        self.assertEqual(
            read_uint24_be(next_host, 6),
            2000 + INSERTED_LENGTH,
        )
        self.assertEqual(next_host[RELIABLE_HEADER_LENGTH:], b"next")

    def test_injection_rejects_nonempty_application_packet(self) -> None:
        request = ArmRequest(
            request_id="request-1",
            session_id="test",
            action="build_building",
            source_actor=2,
            host_actor=1,
            request_origin=0,
            target=BuildingTarget(
                context_822c=0,
                build_queue_id=0,
                colony_id=0,
                zone_id=0,
            ),
        )
        self.assertIsNone(
            inject_at_packet_boundary(
                header() + b"existing-application-data",
                request=request,
                command_serial=1,
                max_payload_length=1400,
            )
        )

    def test_zone_arm_builds_current_session_record_and_dynamic_envelope(self) -> None:
        target = ZoneSpecializationTarget(
            context_822c=0,
            build_queue_id=0,
            colony_id=0,
            district_id=0,
            slot_selector=1,
            zone_type="zone_research_physics",
        )
        request = ArmRequest(
            request_id="request-1",
            session_id="test",
            action="build_zone",
            source_actor=3,
            host_actor=1,
            request_origin=0,
            target=target,
            template_record=ZONE_RESEARCH_PHYSICS_RECORD,
            previous_serial_floor=141,
        )
        carrier = header(sender=4000, ack=9000)
        injection = inject_at_packet_boundary(
            carrier,
            request=request,
            command_serial=142,
            max_payload_length=1400,
        )
        self.assertIsNotNone(injection)
        assert injection is not None
        self.assertEqual(injection.command_record_length, 161)
        self.assertEqual(injection.inserted_length, 164)
        self.assertEqual(len(injection.payload), 25 + 164)
        observations: dict[tuple[str, int, int], int] = {}
        parsed = observe_command_serials(injection.payload, observations, "outbound")
        self.assertEqual(
            (parsed[0]["actor"], parsed[0]["origin"], parsed[0]["serial_u32"]),
            (3, 0, 142),
        )
        self.assertIn(b"zone_research_physics", injection.payload)

    def test_builtin_zone_template_resizes_for_another_specialization(self) -> None:
        target = ZoneSpecializationTarget(
            context_822c=23,
            build_queue_id=41,
            colony_id=43,
            district_id=47,
            slot_selector=2,
            zone_type="zone_research_physics",
        )
        record = build_zone_specialization_record(
            command_serial=149,
            actor=2,
            origin=0,
            target=target,
        )
        self.assertIn(b"zone_research_physics", record)
        self.assertNotIn(b"zone_research_engineering", record)
        self.assertEqual(int.from_bytes(record[:2], "little") + 1, len(record))

    def test_district_template_resizes_for_mining_district(self) -> None:
        target = DistrictConstructionTarget(
            context_822c=29,
            build_queue_id=31,
            colony_id=37,
            district_type="district_mining",
        )
        record = build_district_record(
            command_serial=151,
            actor=2,
            origin=0,
            target=target,
        )
        self.assertIn(b"district_mining", record)
        self.assertNotIn(b"district_generator", record)
        self.assertEqual(int.from_bytes(record[:2], "little") + 1, len(record))

    def test_zone_response_retags_only_correlated_actor(self) -> None:
        target = ZoneSpecializationTarget(
            context_822c=0,
            build_queue_id=0,
            colony_id=0,
            district_id=0,
            slot_selector=1,
            zone_type="zone_research_physics",
        )
        request = ArmRequest(
            request_id="request-1",
            session_id="test",
            action="build_zone",
            source_actor=3,
            host_actor=1,
            request_origin=0,
            target=target,
            template_record=ZONE_RESEARCH_PHYSICS_RECORD,
        )
        response_record = build_zone_specialization_record(
            command_serial=7000,
            actor=3,
            origin=0,
            target=target,
            template_record=ZONE_RESEARCH_PHYSICS_RECORD,
        )
        response = header() + b"\x00\x00\x00" + response_record
        retagged = retag_matching_response(response, request=request)
        self.assertIsNotNone(retagged)
        assert retagged is not None
        rewritten, metadata = retagged
        observations: dict[tuple[str, int, int], int] = {}
        parsed = observe_command_serials(rewritten, observations, "inbound")
        self.assertEqual(parsed[0]["actor"], 1)
        self.assertEqual(parsed[0]["serial_u32"], 7000)
        self.assertEqual(metadata["zone_type"], "zone_research_physics")

    def test_fleet_arm_reuses_fresh_destination_and_changes_source(self) -> None:
        target = FleetMoveTarget(
            source_fleet_object=16777283,
            destination_tag_hex="0c3a01001400",
            destination_object=0,
        )
        request = ArmRequest(
            request_id="request-1",
            session_id="test",
            action="move_fleet",
            source_actor=2,
            host_actor=1,
            request_origin=0,
            target=target,
            template_record=FLEET_ONE_TO_SOL_RECORD,
            previous_serial_floor=167,
        )
        injection = inject_at_packet_boundary(
            header(sender=6000, ack=9000),
            request=request,
            command_serial=168,
            max_payload_length=1400,
        )
        self.assertIsNotNone(injection)
        assert injection is not None
        self.assertEqual(injection.command_record_length, 116)
        self.assertEqual(injection.inserted_length, 119)
        record = injection.payload[RELIABLE_HEADER_LENGTH + 3 :]
        self.assertEqual(record[6:8], bytes.fromhex("d32c"))
        self.assertEqual(int.from_bytes(record[24:28], "little"), 2)
        self.assertEqual(int.from_bytes(record[58:62], "little"), 168)
        self.assertEqual(int.from_bytes(record[76:80], "little"), 16777283)
        self.assertIn(bytes.fromhex("0c3a0100140000000000"), record)

    def test_fleet_response_retags_only_correlated_move(self) -> None:
        target = FleetMoveTarget(
            source_fleet_object=16777283,
            destination_tag_hex="0c3a01001400",
            destination_object=0,
        )
        request = ArmRequest(
            request_id="request-1",
            session_id="test",
            action="move_fleet",
            source_actor=2,
            host_actor=1,
            request_origin=0,
            target=target,
            template_record=FLEET_ONE_TO_SOL_RECORD,
        )
        response_record = build_fleet_move_record(
            command_serial=6000,
            actor=2,
            origin=0,
            target=target,
            template_record=FLEET_ONE_TO_SOL_RECORD,
        )
        response = header() + b"\x00\x00\x00" + response_record
        retagged = retag_matching_response(response, request=request)
        self.assertIsNotNone(retagged)
        assert retagged is not None
        rewritten, metadata = retagged
        record = rewritten[RELIABLE_HEADER_LENGTH + 3 :]
        self.assertEqual(int.from_bytes(record[24:28], "little"), 1)
        self.assertEqual(int.from_bytes(record[58:62], "little"), 6000)
        self.assertEqual(metadata["source_fleet_object"], 16777283)
        self.assertEqual(metadata["destination_object"], 0)

    def test_fleet_arm_retargets_same_destination_form(self) -> None:
        target = FleetMoveTarget(
            source_fleet_object=16777283,
            destination_tag_hex="0c3a01001400",
            destination_object=118,
        )
        record = build_fleet_move_record(
            command_serial=6001,
            actor=2,
            origin=0,
            target=target,
            template_record=FLEET_ONE_TO_SOL_RECORD,
        )
        self.assertIn(bytes.fromhex("0c3a0100140076000000"), record)

    def test_fleet_arm_rejects_unverified_destination_form_switch(self) -> None:
        target = FleetMoveTarget(
            source_fleet_object=16777283,
            destination_tag_hex="132a01001400",
            destination_object=260,
        )
        with self.assertRaisesRegex(ValueError, "destination form"):
            build_fleet_move_record(
                command_serial=6001,
                actor=2,
                origin=0,
                target=target,
                template_record=FLEET_ONE_TO_SOL_RECORD,
            )


if __name__ == "__main__":
    unittest.main()
