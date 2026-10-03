from __future__ import annotations

import unittest

from iag.stellaris.execution._native_capture_adapter import (
    adapt_capture_target,
    uses_serialized_body_adapter,
)
from iag.stellaris.execution.protocol_compatibility import OFFLINE_FIXTURE_TARGETS
from iag.stellaris.execution.session_proxy import (
    _build_request_record,
    parse_arm_document,
)


class NativeCaptureAdapterTests(unittest.TestCase):
    def test_scalar_capture_target_remains_structured(self) -> None:
        target = OFFLINE_FIXTURE_TARGETS["build_building"]

        adapted = adapt_capture_target(
            action_type="build_building",
            target=target,
            country_id=2,
        )

        self.assertEqual(adapted, target)
        self.assertIsNot(adapted, target)
        self.assertFalse(uses_serialized_body_adapter("build_building"))

    def test_nested_capture_targets_use_existing_reviewed_builder(self) -> None:
        for action_type in ("configure_ship_automation", "create_ship_design"):
            with self.subTest(action_type=action_type):
                target = OFFLINE_FIXTURE_TARGETS[action_type]
                adapted = adapt_capture_target(
                    action_type=action_type,
                    target=target,
                    country_id=2,
                )
                request = parse_arm_document(
                    {
                        "request_id": f"materialize-{action_type}",
                        "session_id": "native-capture-materializer",
                        "action": action_type,
                        "source_actor": 2,
                        "host_actor": 2,
                        "request_origin": 0,
                        "target": target,
                    },
                    "native-capture-materializer",
                )
                expected = _build_request_record(request, command_serial=1)[6:].hex()

                self.assertEqual(adapted, {"verified_record_hex": expected})
                self.assertTrue(uses_serialized_body_adapter(action_type))

    def test_signed_coordinate_fields_are_encoded_as_decimal_strings(self) -> None:
        adapted = adapt_capture_target(
            action_type="move_fleet_to_coordinate",
            target={
                "source_fleet_object": 4778,
                "x_fixed": 1780000,
                "y_fixed": -1930000,
                "system_origin": 2,
            },
            country_id=0,
        )

        self.assertEqual(adapted["x_fixed"], "1780000")
        self.assertEqual(adapted["y_fixed"], "-1930000")

    def test_coordinate_adapter_rejects_non_integer_coordinates(self) -> None:
        with self.assertRaisesRegex(ValueError, "x_fixed must be an integer"):
            adapt_capture_target(
                action_type="move_fleet_to_coordinate",
                target={
                    "source_fleet_object": 4778,
                    "x_fixed": "1780000",
                    "y_fixed": -1930000,
                    "system_origin": 2,
                },
                country_id=0,
            )

    def test_unknown_action_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown captured action"):
            adapt_capture_target(
                action_type="not_an_action",
                target={},
                country_id=0,
            )


if __name__ == "__main__":
    unittest.main()
