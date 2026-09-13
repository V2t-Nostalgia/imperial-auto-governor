import unittest

from iag.stellaris.execution.packet.autonomous_commands import _actor_origin
from iag.stellaris.execution.packet.building_mutation_commands import (
    BuildingReplacementTarget,
    BuildingUpgradeTarget,
    build_building_replacement_record,
    build_building_upgrade_record,
)
from iag.stellaris.execution.packet.expansion_commands import (
    ExistingColonyShipTarget,
    OrderColonyShipTarget,
    StarbaseComponentTarget,
    StarbaseUpgradeTarget,
    build_existing_colony_ship_record,
    build_order_colony_ship_record,
    build_starbase_component_record,
    build_starbase_upgrade_record,
)
from iag.stellaris.execution.packet.fleet_operation_commands import (
    AUTOMATION_ASTRAL_RIFTS,
    AUTOMATION_COMPLETE_SPECIAL_PROJECTS,
    AUTOMATION_MINING_STATIONS,
    AUTOMATION_OBSERVATION_POSTS,
    AUTOMATION_RESEARCH_STATIONS,
    AUTOMATION_SEND_GRAVITY_SNARES,
    ConstructionShipStarbaseTarget,
    FleetAttackTarget,
    FleetRepairTarget,
    FleetUpgradeTarget,
    ShipAutomationTarget,
    build_construction_ship_starbase_record,
    build_fleet_attack_record,
    build_fleet_repair_record,
    build_fleet_upgrade_record,
    build_ship_automation_record,
)
from iag.stellaris.execution.packet.ground_warfare_commands import (
    ArmyLandingTarget,
    ArmyRecruitmentTarget,
    BombardmentStanceTarget,
    build_army_landing_record,
    build_army_recruitment_record,
    build_bombardment_stance_record,
)
from iag.stellaris.execution.packet.iag_stream_command_injector import (
    RELIABLE_HEADER_LENGTH,
    find_command_records,
    write_uint24_be,
)
from iag.stellaris.execution.packet.ship_commands import (
    application_prefix_for_record,
)
from iag.stellaris.execution.session_proxy import (
    ArmRequest,
    _build_request_record,
    build_action_probe,
    inject_at_packet_boundary,
    retag_matching_response,
)


def reliable_header(sender: int = 1000, ack: int = 500) -> bytes:
    payload = bytes.fromhex("010000000000000000000000000000f0000000000000000000")
    payload = write_uint24_be(payload, 6, sender)
    return write_uint24_be(payload, 10, ack)


ATTACK_RECORD = bytes.fromhex(
    "6400040000006b3301000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400800000000400410001000300502c0100140003000000d62e0100"
    "14007b0100004c0101000e000104000400"
)
FLEET_REPAIR_RECORD = bytes.fromhex(
    "8c00040000008f3201000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14001900000004004100010003009032010003002030010003009232"
    "01000e0000163b01000e0000b03801000e0001624501001400ffffff"
    "ff04000400822c0100140000000000502c01001400550f000f04000400"
)
FLEET_UPGRADE_RECORD = bytes.fromhex(
    "7500040000008f2f01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14001a0000000400410001000300822c0100140000000000502c0100"
    "1400550f000fc33d01001400cf230000634001000e0000de3501000e"
    "000004000400"
)
ASTRAL_AUTOMATION_RECORD = bytes.fromhex(
    "e700040000008f3201000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400190000000400410001000300903201000300f74501000300b038"
    "01000e0001624501001400ffffffff1e3f010003000f001700415554"
    "4f4d4154494f4e5f41535452414c5f52494654530400414601000e00"
    "00473001000e0000483001000e0000493001000e00004a3001000e00"
    "014b3001000e00008d4301000e00003d4601000e00003e4601000e00"
    "003f4601000e000004000400822c0100140000000000502c01001400"
    "0100000004000400"
)
GRAVITY_AUTOMATION_RECORD = bytes.fromhex(
    "ee00040000008f3201000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14001d0000000400410001000300903201000300f74501000300b038"
    "01000e0001624501001400ffffffff1e3f010003000f001e00415554"
    "4f4d4154494f4e5f53454e445f475241564954595f534e4152455304"
    "00414601000e0000473001000e0000483001000e0000493001000e00"
    "004a3001000e00004b3001000e00008d4301000e00013d4601000e00"
    "003e4601000e00003f4601000e000004000400822c01001400000000"
    "00502c010014000100000004000400"
)
BUILDING_UPGRADE_RECORD = bytes.fromhex(
    "a20004000000b43d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14001f0000000400410001000300822c010014000000000063400100"
    "140000000000c03d01000300b22b01000f0017006275696c64696e67"
    "5f72657365617263685f6c61625f32132a0100140000000000b32b01"
    "00140001000000bf3d010014003e010001040004000400"
)
BUILDING_REPLACEMENT_RECORD = bytes.fromhex(
    "a10004000000b43d01000300f30101000300400201000c0003000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400210000000400410001000300822c010014000000000063400100"
    "1400d81f0000bd3d01000300b22b01000f0016006275696c64696e67"
    "5f686f6c6f5f7468656174726573132a010014004b000000b32b0100"
    "1400940000015a3d01001400a3010000040004000400"
)
BUILD_STARBASE_RECORD = bytes.fromhex(
    "710004000000e02c01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400750000000400410001000300e42c01000b3a502c010014000200"
    "0000e92d01001400bc000000634001000e0000de3501000e00000400"
    "0400"
)
CONSTRUCTION_AUTOMATION_RECORD = bytes.fromhex(
    "5200040000008f3201000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400100000000400410001000300903201000300f74501000300b038"
    "01000e0001624501001400ffffffff1e3f010003000f001a00415554"
    "4f4d4154494f4e5f4d494e494e475f53544154494f4e530f001c00"
    "4155544f4d4154494f4e5f52455345415243485f53544154494f4e"
    "530f001c004155544f4d4154494f4e5f4f42534552564154494f4e"
    "5f504f5354530f0024004155544f4d4154494f4e5f434f4d504c45"
    "54455f5350454349414c5f50524f4a454354530400414601000e0001"
    "473001000e0000483001000e0000493001000e00004a3001000e0000"
    "4b3001000e00008d4301000e00003d4601000e00013e4601000e0001"
    "3f4601000e000104000400822c0100140000000000502c01001400e4"
    "00000104000400"
)
ORDER_COLONY_RECORD = bytes.fromhex(
    "1d00040000003d3701000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400160000000400410001000300822c01001400000000003c370100"
    "0300e52f010003004c2b0100140001000017cd2a01000f000800636f"
    "6c5f636974790400c74401000300652c010014000200000014350100"
    "1400ffffffffc84401000c00000000000400962d0100140014010000"
    "c33d0100140003000000d83d01001400ffffffff1b0001000300dc00"
    "01000f0011004e45575f434f4c4f4e595f4e414d455f313c400100"
    "03000300dc0001000f0004004e414d45d20201000300dc0001000f00"
    "13004e414d455f416c7068615f43656e746175726904000400040004"
    "00040004000400"
)
EXISTING_COLONY_RECORD = bytes.fromhex(
    "c70004000000e62c01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400200000000400410001000300502c01001400d0020001132a0100"
    "140014010000634001000e0000de3501000e00001b0001000300dc00"
    "01000f0011004e45575f434f4c4f4e595f4e414d455f313c400100"
    "03000300dc0001000f0004004e414d45d20201000300dc0001000f00"
    "13004e414d455f416c7068615f43656e746175726904000400040004"
    "0004000400"
)
STARBASE_UPGRADE_RECORD = bytes.fromhex(
    "8e0004000000b43d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400560000000400410001000300822c010014000000000063400100"
    "1400b3200000cb3d010003001c3a01000f0017007374617262617365"
    "5f6c6576656c5f73746172706f72740c3a0100140068000000040004"
    "000400"
)
STARBASE_MODULE_RECORD = bytes.fromhex(
    "890004000000b43d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14005b0000000400410001000300822c010014000000000063400100"
    "1400b3200000ca3d01000300193a01000f0008007368697079617264"
    "632c01000c00000000000c3a0100140068000000040004000400"
)
STARBASE_BUILDING_RECORD = bytes.fromhex(
    "8e0004000000b43d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14005e0000000400410001000300822c010014000000000063400100"
    "1400b3200000c93d010003001a3a01000f000d00637265775f717561"
    "7274657273632c01000c00000000000c3a0100140068000000040004"
    "000400"
)
BOMBARDMENT_STANCE_RECORD = bytes.fromhex(
    "690004000000d62d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14001a0000000400410001000300502c01001400aa120000d22d0100"
    "0f000e00696e6469736372696d696e61746504000400"
)
ARMY_LANDING_RECORD = bytes.fromhex(
    "6b00040000006f3301000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14001b0000000400410001000300502c01001400020200021e390100"
    "140004000000634001000e0000de3501000e000004000400"
)
ARMY_RECRUITMENT_RECORD = bytes.fromhex(
    "970004000000b43d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14001c0000000400410001000300822c010014000000000063400100"
    "140001000000c83d01000300ec4601000f000c00726f626f7469635f"
    "61726d794c2b01001400b6000000132a01001400000000000c3a0100"
    "140000000000040004000400"
)


class ExtendedProtocolCommandTests(unittest.TestCase):
    def test_ground_warfare_commands_match_live_records(self) -> None:
        stance = BombardmentStanceTarget(4778, "indiscriminate")
        self.assertEqual(
            build_bombardment_stance_record(
                command_serial=26,
                actor=2,
                origin=0,
                target=stance,
            ),
            BOMBARDMENT_STANCE_RECORD,
        )
        landing = ArmyLandingTarget(33554946, 4)
        self.assertEqual(
            build_army_landing_record(
                command_serial=27,
                actor=2,
                origin=0,
                target=landing,
            ),
            ARMY_LANDING_RECORD,
        )
        recruitment = ArmyRecruitmentTarget(0, 1, "robotic_army", 182, 0, 0)
        self.assertEqual(
            build_army_recruitment_record(
                command_serial=28,
                actor=2,
                origin=0,
                target=recruitment,
            ),
            ARMY_RECRUITMENT_RECORD,
        )

    def test_building_mutations_match_live_pairs(self) -> None:
        upgrade = BuildingUpgradeTarget(
            0,
            0,
            0,
            1,
            16777534,
            "building_research_lab_2",
        )
        self.assertEqual(
            build_building_upgrade_record(
                command_serial=31, actor=2, origin=0, target=upgrade
            ),
            BUILDING_UPGRADE_RECORD,
        )
        replacement = BuildingReplacementTarget(
            0,
            8152,
            75,
            16777364,
            419,
            "building_holo_theatres",
        )
        self.assertEqual(
            build_building_replacement_record(
                command_serial=33, actor=3, origin=0, target=replacement
            ),
            BUILDING_REPLACEMENT_RECORD,
        )

    def test_fleet_attack_matches_live_pair(self) -> None:
        target = FleetAttackTarget(3, 379)
        self.assertEqual(
            build_fleet_attack_record(
                command_serial=128, actor=2, origin=0, target=target
            ),
            ATTACK_RECORD,
        )

    def test_fleet_maintenance_matches_live_pairs(self) -> None:
        repair = FleetRepairTarget(0, 251662165)
        self.assertEqual(
            build_fleet_repair_record(
                command_serial=25,
                actor=2,
                origin=0,
                target=repair,
            ),
            FLEET_REPAIR_RECORD,
        )
        upgrade = FleetUpgradeTarget(0, 251662165, 9167)
        self.assertEqual(
            build_fleet_upgrade_record(
                command_serial=26,
                actor=2,
                origin=0,
                target=upgrade,
            ),
            FLEET_UPGRADE_RECORD,
        )

    def test_construction_ship_starbase_matches_live_pair(self) -> None:
        target = ConstructionShipStarbaseTarget(2, 188)
        self.assertEqual(
            build_construction_ship_starbase_record(
                command_serial=117, actor=2, origin=0, target=target
            ),
            BUILD_STARBASE_RECORD,
        )

    def test_long_construction_automation_matches_live_pair(self) -> None:
        target = ShipAutomationTarget(
            context_822c=0,
            source_fleet_object=16777444,
            options=(
                AUTOMATION_MINING_STATIONS,
                AUTOMATION_RESEARCH_STATIONS,
                AUTOMATION_OBSERVATION_POSTS,
                AUTOMATION_COMPLETE_SPECIAL_PROJECTS,
            ),
        )
        record = build_ship_automation_record(
            command_serial=16, actor=2, origin=0, target=target
        )
        self.assertEqual(record, CONSTRUCTION_AUTOMATION_RECORD)
        self.assertEqual(application_prefix_for_record(record), bytes.fromhex("000001"))

    def test_late_game_science_automation_options_match_live_pairs(self) -> None:
        astral = ShipAutomationTarget(0, 1, (AUTOMATION_ASTRAL_RIFTS,))
        self.assertEqual(
            build_ship_automation_record(
                command_serial=25,
                actor=2,
                origin=0,
                target=astral,
            ),
            ASTRAL_AUTOMATION_RECORD,
        )
        gravity = ShipAutomationTarget(
            0,
            1,
            (AUTOMATION_SEND_GRAVITY_SNARES,),
        )
        self.assertEqual(
            build_ship_automation_record(
                command_serial=29,
                actor=2,
                origin=0,
                target=gravity,
            ),
            GRAVITY_AUTOMATION_RECORD,
        )

    def test_both_colonization_paths_match_live_pairs(self) -> None:
        ordered = OrderColonyShipTarget(
            context_822c=0,
            species_id=385875969,
            colony_designation="col_city",
            design_id=2,
            upgrade_id=0xFFFFFFFF,
            growth_stage=0,
            target_planet_id=276,
            source_shipyard_build_queue_id=3,
            system_name_key="NAME_Alpha_Centauri",
        )
        self.assertEqual(
            build_order_colony_ship_record(
                command_serial=22, actor=2, origin=0, target=ordered
            ),
            ORDER_COLONY_RECORD,
        )
        existing = ExistingColonyShipTarget(
            source_fleet_object=16777936,
            target_planet_id=276,
            system_name_key="NAME_Alpha_Centauri",
        )
        self.assertEqual(
            build_existing_colony_ship_record(
                command_serial=32, actor=2, origin=0, target=existing
            ),
            EXISTING_COLONY_RECORD,
        )

    def test_starbase_actions_match_live_pairs(self) -> None:
        upgrade = StarbaseUpgradeTarget(
            context_822c=0,
            build_queue_id=8371,
            target_level="starbase_level_starport",
            starbase_object=104,
        )
        self.assertEqual(
            build_starbase_upgrade_record(
                command_serial=86, actor=2, origin=0, target=upgrade
            ),
            STARBASE_UPGRADE_RECORD,
        )
        module = StarbaseComponentTarget(0, 8371, "shipyard", 0, 104)
        self.assertEqual(
            build_starbase_component_record(
                kind="module",
                command_serial=91,
                actor=2,
                origin=0,
                target=module,
            ),
            STARBASE_MODULE_RECORD,
        )
        building = StarbaseComponentTarget(0, 8371, "crew_quarters", 0, 104)
        self.assertEqual(
            build_starbase_component_record(
                kind="building",
                command_serial=94,
                actor=2,
                origin=0,
                target=building,
            ),
            STARBASE_BUILDING_RECORD,
        )

    def test_extended_record_scanner_uses_application_length_page(self) -> None:
        application = bytes.fromhex("000001") + CONSTRUCTION_AUTOMATION_RECORD
        records = find_command_records(application)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].offset, 3)
        self.assertEqual(records[0].length, 339)

    def test_extended_response_is_correlated_and_retagged(self) -> None:
        target = ShipAutomationTarget(
            context_822c=0,
            source_fleet_object=16777444,
            options=(
                AUTOMATION_MINING_STATIONS,
                AUTOMATION_RESEARCH_STATIONS,
                AUTOMATION_OBSERVATION_POSTS,
                AUTOMATION_COMPLETE_SPECIAL_PROJECTS,
            ),
        )
        request = ArmRequest(
            request_id="automation-1",
            session_id="test",
            action="configure_ship_automation",
            source_actor=2,
            host_actor=1,
            request_origin=0,
            target=target,
        )
        response = build_ship_automation_record(
            command_serial=303, actor=2, origin=0, target=target
        )
        payload = reliable_header() + application_prefix_for_record(response) + response
        rewritten = retag_matching_response(payload, request=request)
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        result, metadata = rewritten
        record = result[RELIABLE_HEADER_LENGTH + 3 :]
        self.assertEqual(_actor_origin(record), (1, 0))
        self.assertEqual(metadata["source_fleet_object"], 16777444)

    def test_every_new_action_correlates_its_authoritative_response(self) -> None:
        cases = (
            (
                "upgrade_building",
                BuildingUpgradeTarget(0, 0, 0, 1, 16777534, "building_research_lab_2"),
            ),
            (
                "replace_building",
                BuildingReplacementTarget(
                    0, 8152, 75, 16777364, 419, "building_holo_theatres"
                ),
            ),
            ("attack_fleet", FleetAttackTarget(3, 379)),
            ("repair_fleet", FleetRepairTarget(0, 251662165)),
            ("upgrade_fleet", FleetUpgradeTarget(0, 251662165, 9167)),
            ("build_starbase", ConstructionShipStarbaseTarget(2, 188)),
            (
                "order_colony_ship_and_colonize",
                OrderColonyShipTarget(
                    0,
                    385875969,
                    "col_city",
                    2,
                    0xFFFFFFFF,
                    0,
                    276,
                    3,
                    "NAME_Alpha_Centauri",
                ),
            ),
            (
                "colonize_with_existing_ship",
                ExistingColonyShipTarget(16777936, 276, "NAME_Alpha_Centauri"),
            ),
            (
                "upgrade_starbase",
                StarbaseUpgradeTarget(
                    0,
                    8371,
                    "starbase_level_starport",
                    104,
                ),
            ),
            (
                "set_starbase_module",
                StarbaseComponentTarget(0, 8371, "shipyard", 0, 104),
            ),
            (
                "set_starbase_building",
                StarbaseComponentTarget(0, 8371, "crew_quarters", 0, 104),
            ),
            (
                "set_orbital_bombardment_stance",
                BombardmentStanceTarget(4778, "selective"),
            ),
            ("land_armies", ArmyLandingTarget(33554946, 4)),
            (
                "recruit_army",
                ArmyRecruitmentTarget(0, 1, "robotic_army", 182, 0, 0),
            ),
        )
        for action, target in cases:
            with self.subTest(action=action):
                request = ArmRequest(
                    request_id=f"{action}-1",
                    session_id="test",
                    action=action,
                    source_actor=2,
                    host_actor=1,
                    request_origin=0,
                    target=target,
                )
                response = _build_request_record(request, command_serial=303)
                payload = (
                    reliable_header()
                    + application_prefix_for_record(response)
                    + response
                )
                rewritten = retag_matching_response(payload, request=request)
                self.assertIsNotNone(rewritten)
                assert rewritten is not None
                result, metadata = rewritten
                record = result[
                    RELIABLE_HEADER_LENGTH
                    + len(application_prefix_for_record(response)) :
                ]
                self.assertEqual(_actor_origin(record), (1, 0))
                self.assertEqual(metadata["action"], action)
                self.assertEqual(metadata["host_command_serial_u32"], 303)

    def test_injection_and_offline_probe_use_dynamic_prefix(self) -> None:
        target = OrderColonyShipTarget(
            0,
            385875969,
            "col_city",
            2,
            0xFFFFFFFF,
            0,
            276,
            3,
            "NAME_Alpha_Centauri",
        )
        request = ArmRequest(
            request_id="colony-1",
            session_id="test",
            action="order_colony_ship_and_colonize",
            source_actor=2,
            host_actor=1,
            request_origin=0,
            target=target,
        )
        injection = inject_at_packet_boundary(
            reliable_header(),
            request=request,
            command_serial=22,
            max_payload_length=1400,
        )
        self.assertIsNotNone(injection)
        assert injection is not None
        self.assertEqual(
            injection.payload[RELIABLE_HEADER_LENGTH : RELIABLE_HEADER_LENGTH + 3],
            bytes.fromhex("000001"),
        )
        probe = build_action_probe(
            action="order_colony_ship_and_colonize",
            target={
                "context_822c": 0,
                "species_id": 385875969,
                "colony_designation": "col_city",
                "design_id": 2,
                "upgrade_id": 0xFFFFFFFF,
                "growth_stage": 0,
                "target_planet_id": 276,
                "source_shipyard_build_queue_id": 3,
                "system_name_key": "NAME_Alpha_Centauri",
            },
        )
        self.assertEqual(probe["application_prefix_hex"], "000001")


if __name__ == "__main__":
    unittest.main()
