from __future__ import annotations

import hashlib
import unittest
from typing import Any

from iag.stellaris.execution.packet.ship_commands import (
    ShipDesignTarget,
    build_ship_design_record,
)


def section(
    template: str,
    slot: str,
    components: list[tuple[str, str]],
) -> dict[str, Any]:
    return {
        "template": template,
        "slot": slot,
        "components": [
            {"slot": component_slot, "component_id": component_id}
            for component_slot, component_id in components
        ],
    }


COMMON_MID = section(
    "BATTLESHIP_MID_S4LHB",
    "mid",
    [
        ("SMALL_GUN_01", "SMALL_PLASMA_3"),
        ("SMALL_GUN_02", "SMALL_DISRUPTOR_3"),
        ("SMALL_GUN_03", "POINT_DEFENCE_3"),
        ("SMALL_GUN_04", "POINT_DEFENCE_3"),
        ("STRIKE_CRAFT_01", "STRIKE_CRAFT_HANGAR_3"),
        ("STRIKE_CRAFT_02", "STRIKE_CRAFT_HANGAR_3"),
        ("LARGE_UTILITY_1", "LARGE_ARMOR_5"),
        ("LARGE_UTILITY_2", "LARGE_DARK_MATTER_DEFLECTOR"),
        ("LARGE_UTILITY_3", "LARGE_ARMOR_5"),
    ],
)


def battleship_blueprint(name: str, *, carrier_computer: bool) -> dict[str, Any]:
    bow_components = [
        ("LARGE_UTILITY_1", "LARGE_ARMOR_5"),
        ("LARGE_UTILITY_2", "LARGE_ARMOR_5"),
        ("LARGE_UTILITY_3", "LARGE_DARK_MATTER_DEFLECTOR"),
        ("EXTRA_LARGE_01", "ARC_EMITTER_2"),
    ]
    stern = section(
        "BATTLESHIP_STERN_L1",
        "stern",
        [
            ("LARGE_GUN_01", "LARGE_PLASMA_3"),
            ("AUX_UTILITY_1", "AUTO_REPAIR_2"),
            ("AUX_UTILITY_2", "AFTERBURNER_2"),
        ],
    )
    return {
        "context_822c": 0,
        "name": name,
        "graphical_culture": "humanoid_01",
        "upgrade_components_automatically": False,
        "growth_stages": [
            {
                "ship_size": "battleship",
                "parent": 0xFFFFFFFF,
                "sections": [
                    section("BATTLESHIP_BOW_M2S4", "bow", bow_components),
                    COMMON_MID,
                    stern,
                ],
                "required_components": [
                    "BATTLESHIP_DARK_MATTER_REACTOR",
                    "PSI_JUMP_DRIVE_1",
                    "BATTLESHIP_SHIP_THRUSTER_5",
                    "SENSOR_4",
                    (
                        "COMBAT_COMPUTER_CARRIER_SAPIENT"
                        if carrier_computer
                        else "COMBAT_COMPUTER_ARTILLERY_SAPIENT"
                    ),
                ],
            }
        ],
    }


def cruiser_blueprint(name: str, *, automatic_upgrade: bool) -> dict[str, Any]:
    return {
        "context_822c": 0,
        "name": name,
        "graphical_culture": "humanoid_01",
        "upgrade_components_automatically": automatic_upgrade,
        "growth_stages": [
            {
                "ship_size": "cruiser",
                "parent": 0xFFFFFFFF,
                "sections": [
                    section(
                        "CRUISER_BOW_M2",
                        "bow",
                        [
                            ("MEDIUM_GUN_01", "MEDIUM_PLASMA_3"),
                            ("MEDIUM_GUN_02", "MEDIUM_DISRUPTOR_3"),
                            ("MEDIUM_UTILITY_1", "MEDIUM_DARK_MATTER_DEFLECTOR"),
                            ("MEDIUM_UTILITY_2", "MEDIUM_ARMOR_5"),
                            ("MEDIUM_UTILITY_3", "MEDIUM_DARK_MATTER_DEFLECTOR"),
                            ("MEDIUM_UTILITY_4", "MEDIUM_ARMOR_5"),
                        ],
                    ),
                    section(
                        "CRUISER_MID_M3",
                        "mid",
                        [
                            ("MEDIUM_GUN_01", "MEDIUM_PLASMA_3"),
                            ("MEDIUM_GUN_02", "MEDIUM_DISRUPTOR_3"),
                            ("MEDIUM_GUN_03", "MEDIUM_PLASMA_3"),
                            ("MEDIUM_UTILITY_1", "MEDIUM_DARK_MATTER_DEFLECTOR"),
                            ("MEDIUM_UTILITY_2", "MEDIUM_ARMOR_5"),
                            ("MEDIUM_UTILITY_3", "MEDIUM_DARK_MATTER_DEFLECTOR"),
                            ("MEDIUM_UTILITY_4", "MEDIUM_ARMOR_5"),
                        ],
                    ),
                    section(
                        "CRUISER_STERN_S2",
                        "stern",
                        [
                            ("SMALL_GUN_01", "SMALL_PLASMA_3"),
                            ("SMALL_GUN_02", "SMALL_DISRUPTOR_3"),
                            ("AUX_UTILITY_1", "AFTERBURNER_2"),
                            ("AUX_UTILITY_2", "AFTERBURNER_2"),
                            ("AUX_UTILITY_3", "AFTERBURNER_2"),
                        ],
                    ),
                ],
                "required_components": [
                    "CRUISER_DARK_MATTER_REACTOR",
                    "PSI_JUMP_DRIVE_1",
                    "CRUISER_SHIP_THRUSTER_5",
                    "SENSOR_4",
                    "COMBAT_COMPUTER_LINE_SAPIENT",
                ],
            }
        ],
    }


class ShipDesignPacketTests(unittest.TestCase):
    def assert_capture(
        self,
        blueprint: dict[str, Any],
        *,
        serial: int,
        expected_length: int,
        expected_sha256: str,
    ) -> None:
        record = build_ship_design_record(
            command_serial=serial,
            actor=2,
            origin=0,
            target=ShipDesignTarget(blueprint),
        )
        self.assertEqual(len(record), expected_length)
        self.assertEqual(hashlib.sha256(record).hexdigest(), expected_sha256)

    def test_multisection_and_required_component_match_live_captures(self) -> None:
        self.assert_capture(
            battleship_blueprint("IAG_BB_F4", carrier_computer=True),
            serial=29,
            expected_length=1341,
            expected_sha256=(
                "9696aba09b139d01330301cec0379cba686c0ccf8a3a34e91d389978ee0d3db4"
            ),
        )

    def test_stern_section_replacement_matches_live_capture(self) -> None:
        blueprint = battleship_blueprint("IAG_BB_D3", carrier_computer=False)
        stage = blueprint["growth_stages"][0]
        stage["sections"][0] = section(
            "BATTLESHIP_BOW_M2S4",
            "bow",
            [
                ("LARGE_UTILITY_1", "LARGE_DARK_MATTER_DEFLECTOR"),
                ("LARGE_UTILITY_2", "LARGE_ARMOR_5"),
                ("LARGE_UTILITY_3", "LARGE_DARK_MATTER_DEFLECTOR"),
                ("EXTRA_LARGE_01", "ENERGY_LANCE_2"),
            ],
        )
        stage["sections"][2] = section(
            "BATTLESHIP_STERN_M2",
            "stern",
            [
                ("MEDIUM_GUN_01", "MEDIUM_PLASMA_3"),
                ("MEDIUM_GUN_02", "MEDIUM_PLASMA_3"),
                ("AUX_UTILITY_1", "AFTERBURNER_2"),
                ("AUX_UTILITY_2", "AFTERBURNER_2"),
                ("AUX_UTILITY_3", "AFTERBURNER_2"),
            ],
        )
        self.assert_capture(
            blueprint,
            serial=48,
            expected_length=1462,
            expected_sha256=(
                "4bd7aeb581add6eb2bd8d4daf011c08555355ac7412a3f699bc7608ce68a5a72"
            ),
        )

    def test_automatic_upgrade_pair_matches_checked_and_unchecked_captures(
        self,
    ) -> None:
        self.assert_capture(
            cruiser_blueprint("IAG_AUTO_G", automatic_upgrade=True),
            serial=23,
            expected_length=1471,
            expected_sha256=(
                "d4c56585f75179dc873cdfb34737166bd0b4007901d3a6b15e53d851165be2da"
            ),
        )
        self.assert_capture(
            cruiser_blueprint("IAG_AUTO_H", automatic_upgrade=False),
            serial=24,
            expected_length=1457,
            expected_sha256=(
                "a00efa18881cc5067dda2f0c469a3f99fb7f990f909353aaaa6ebd80c771dd2e"
            ),
        )


if __name__ == "__main__":
    unittest.main()
