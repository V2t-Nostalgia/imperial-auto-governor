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

    def test_unknown_action_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown captured action"):
            adapt_capture_target(
                action_type="not_an_action",
                target={},
                country_id=0,
            )


if __name__ == "__main__":
    unittest.main()
