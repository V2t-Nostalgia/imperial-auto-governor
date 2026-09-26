from __future__ import annotations

import importlib.util
import struct
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("decode_fleet_movement.py")
SPEC = importlib.util.spec_from_file_location("iag_fleet_movement_decoder", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot import {MODULE_PATH}")
decoder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = decoder
SPEC.loader.exec_module(decoder)
decode_event = decoder.decode_event


def _hex_blob(size: int, fields: list[tuple[str, int, int]]) -> str:
    data = bytearray(size)
    for fmt, offset, value in fields:
        struct.pack_into(fmt, data, offset, value)
    return data.hex()


class DecodeFleetMovementTests(unittest.TestCase):
    def test_decodes_direct_move_coordinate_and_append_mode(self) -> None:
        event = {
            "sequence": 7,
            "phase": "session_post",
            "command_type": "CFleetFlyToCoordinatesCommand",
            "serial": 42,
            "known_command_hex": _hex_blob(
                0x50,
                [
                    ("<I", 0x1C, 888),
                    ("<q", 0x28, 12 * 32768),
                    ("<q", 0x30, -3 * 32768),
                    ("<q", 0x38, 32768 // 2),
                    ("<I", 0x40, 47),
                    ("<B", 0x45, 1),
                    ("<B", 0x48, 1),
                    ("<B", 0x49, 0),
                ],
            ),
        }

        decoded = decode_event(event)

        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded["fleet_id"], 888)
        self.assertEqual(decoded["queue_mode"], "append")
        self.assertEqual(decoded["coordinate"]["origin_system_id"], 47)
        self.assertEqual(decoded["coordinate"]["x"], 12.0)
        self.assertEqual(decoded["coordinate"]["y"], -3.0)
        self.assertEqual(decoded["coordinate"]["display_height"], 0.5)
        self.assertTrue(decoded["coordinate"]["randomize_display_height"])

    def test_decodes_single_fleet_replace_jump_order(self) -> None:
        event = {
            "command_type": "CQueueFleetOrderCommand",
            "known_command_hex": _hex_blob(
                0x30,
                [
                    ("<i", 0x1C, 2),
                    ("<I", 0x20, 0),
                    ("<I", 0x24, 888),
                ],
            ),
            "nested_type": "CJumpDriveFleetOrder",
            "known_nested_hex": _hex_blob(
                0x28,
                [("<I", 0x24, 232)],
            ),
        }

        decoded = decode_event(event)

        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded["country_id"], 0)
        self.assertEqual(decoded["fleet_ids"], [888])
        self.assertEqual(decoded["queue_mode"], "replace")
        self.assertEqual(
            decoded["order"],
            {
                "order_type": "CJumpDriveFleetOrder",
                "destination_system_id": 232,
            },
        )

    def test_decodes_multi_fleet_replace_move_order(self) -> None:
        event = {
            "command_type": "CQueueFleetsOrderCommand",
            "known_command_hex": _hex_blob(
                0x48,
                [
                    ("<i", 0x1C, 2),
                    ("<I", 0x38, 11),
                ],
            ),
            "related_ids": [101, 202, 303],
            "nested_type": "CMoveToSystemPointFleetOrder",
            "known_nested_hex": _hex_blob(
                0x50,
                [
                    ("<B", 0x21, 1),
                    ("<q", 0x30, 2 * 32768),
                    ("<q", 0x38, 4 * 32768),
                    ("<I", 0x48, 77),
                ],
            ),
        }

        decoded = decode_event(event)

        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded["country_id"], 11)
        self.assertEqual(decoded["fleet_ids"], [101, 202, 303])
        self.assertEqual(decoded["queue_mode"], "replace")
        self.assertEqual(decoded["order"]["coordinate"]["x"], 2.0)
        self.assertEqual(decoded["order"]["coordinate"]["y"], 4.0)
        self.assertEqual(
            decoded["order"]["coordinate"]["origin_system_id"], 77
        )
        self.assertTrue(decoded["order"]["has_triggered_message"])

    def test_decodes_normal_and_own_only_return_orders(self) -> None:
        for own_only in (0, 1):
            with self.subTest(own_only=own_only):
                event = {
                    "command_type": "CQueueFleetsOrderCommand",
                    "known_command_hex": _hex_blob(
                        0x48,
                        [
                            ("<i", 0x1C, 2),
                            ("<I", 0x38, 1),
                        ],
                    ),
                    "related_ids": [888],
                    "nested_type": "CReturnFleetOrder",
                    "known_nested_hex": _hex_blob(
                        0x28,
                        [
                            ("<B", 0x20, 0),
                            ("<B", 0x21, own_only),
                            ("<B", 0x22, 1),
                        ],
                    ),
                }

                decoded = decode_event(event)

                self.assertIsNotNone(decoded)
                assert decoded is not None
                self.assertEqual(decoded["queue_mode"], "replace")
                self.assertEqual(decoded["fleet_ids"], [888])
                self.assertEqual(
                    decoded["order"],
                    {
                        "order_type": "CReturnFleetOrder",
                        "base_flag_20": False,
                        "own_starbases_only": bool(own_only),
                        "try_home_base": True,
                    },
                )

    def test_decodes_cancel_one_order(self) -> None:
        event = {
            "command_type": "CFleetCancelOrderCommand",
            "known_command_hex": _hex_blob(
                0x28,
                [
                    ("<I", 0x1C, 4),
                    ("<I", 0x20, 555),
                    ("<i", 0x24, 3),
                ],
            ),
        }

        decoded = decode_event(event)

        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded["operation"], "cancel_order")
        self.assertEqual(decoded["country_id"], 4)
        self.assertEqual(decoded["fleet_id"], 555)
        self.assertEqual(decoded["order_index"], 3)

    def test_decodes_planet_and_starbase_orbit_targets(self) -> None:
        for kind, expected in ((1, "planet"), (2, "starbase")):
            with self.subTest(kind=kind):
                event = {
                    "command_type": "CFleetOrbitPlanetCommand",
                    "known_command_hex": _hex_blob(
                        0x40,
                        [
                            ("<I", 0x1C, 888),
                            ("<I", 0x20, 3 if kind == 1 else 218),
                            ("<B", 0x24, kind),
                            ("<B", 0x38, 0),
                            ("<B", 0x39, 0),
                        ],
                    ),
                }

                decoded = decode_event(event)

                self.assertIsNotNone(decoded)
                assert decoded is not None
                self.assertEqual(decoded["fleet_id"], 888)
                self.assertEqual(decoded["target_kind"], expected)
                self.assertEqual(decoded["queue_mode"], "replace")

    def test_decodes_live_attack_command(self) -> None:
        event = {
            "command_type": "CFollowFleetCommand",
            "known_command_hex": _hex_blob(
                0x28,
                [
                    ("<I", 0x1C, 888),
                    ("<I", 0x20, 220),
                    ("<B", 0x24, 1),
                    ("<B", 0x25, 0),
                    ("<B", 0x26, 0),
                    ("<B", 0x27, 0),
                ],
            ),
        }

        decoded = decode_event(event)

        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded["fleet_id"], 888)
        self.assertEqual(decoded["target_fleet_id"], 220)
        self.assertTrue(decoded["attack"])
        self.assertFalse(decoded["cancelled"])
        self.assertEqual(decoded["queue_mode"], "replace")

    def test_decodes_merge_and_upgrade_commands(self) -> None:
        merge = decode_event(
            {
                "command_type": "CMergeFleetsCommand",
                "known_command_hex": _hex_blob(
                    0x40,
                    [
                        ("<I", 0x1C, 5),
                        ("<B", 0x38, 0),
                        ("<B", 0x39, 0),
                    ],
                ),
                "related_ids": [101, 202],
            }
        )
        self.assertIsNotNone(merge)
        assert merge is not None
        self.assertEqual(merge["operation"], "merge_fleets")
        self.assertEqual(merge["fleet_ids"], [101, 202])

        upgrade = decode_event(
            {
                "command_type": "CFleetUpgradeDesignCommand",
                "known_command_hex": _hex_blob(
                    0x30,
                    [
                        ("<I", 0x1C, 5),
                        ("<I", 0x20, 888),
                        ("<I", 0x24, 9167),
                        ("<B", 0x28, 1),
                        ("<B", 0x29, 0),
                    ],
                ),
            }
        )
        self.assertIsNotNone(upgrade)
        assert upgrade is not None
        self.assertEqual(upgrade["operation"], "upgrade_fleet")
        self.assertEqual(upgrade["fleet_id"], 888)
        self.assertEqual(upgrade["construction_queue_id"], 9167)
        self.assertEqual(upgrade["queue_mode"], "append")

    def test_decodes_emergency_ftl_command(self) -> None:
        decoded = decode_event(
            {
                "command_type": "CFleetCombatEmergencyFTLCommand",
                "known_command_hex": _hex_blob(
                    0x20,
                    [("<I", 0x1C, 888)],
                ),
            }
        )

        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded["operation"], "emergency_ftl")
        self.assertEqual(decoded["fleet_id"], 888)

    def test_rejects_unknown_or_truncated_events(self) -> None:
        self.assertIsNone(
            decode_event(
                {
                    "command_type": "CUnknownCommand",
                    "known_command_hex": "00" * 0x20,
                }
            )
        )
        self.assertIsNone(
            decode_event(
                {
                    "command_type": "CFleetFlyToCoordinatesCommand",
                    "known_command_hex": "not-hex",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
