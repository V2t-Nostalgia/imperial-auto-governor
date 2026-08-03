from __future__ import annotations

import json
import sys
import unittest
import uuid
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from migrate_config_v2 import migrate_config  # noqa: E402


class ConfigMigrationTests(unittest.TestCase):
    def test_adds_safe_defaults_without_replacing_provider(self) -> None:
        root = RUNTIME / "tests" / "runtime_test_data"
        path = root / f"migration_{uuid.uuid4().hex}.json"
        path.write_text(
            json.dumps(
                {
                    "provider": "responses_compatible",
                    "base_url": "https://example.test/v1",
                    "model": "existing-model",
                    "autonomy_mode": "advisory",
                }
            ),
            encoding="utf-8",
        )
        try:
            result = migrate_config(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["provider"], "responses_compatible")
            self.assertEqual(value["model"], "existing-model")
            self.assertEqual(value["autonomy_mode"], "advisory")
            self.assertEqual(value["save_source_mode"], "host_upload")
            self.assertEqual(value["save_review_interval_months"], 1)
            self.assertEqual(
                value["save_upload_token_file"],
                "secrets/save_upload_token",
            )
            self.assertTrue(value["tool_calling_enabled"])
            self.assertFalse(value["web_research_enabled"])
            self.assertEqual(value["host_discovery_timeout_seconds"], 20)
            self.assertEqual(value["host_discovery_poll_seconds"], 0.5)
            self.assertEqual(value["interceptor_privilege_mode"], "capability")
            self.assertEqual(value["execution_transport"], "windows_host_bridge")
            self.assertEqual(value["host_executor_ready_timeout_seconds"], 30)
            self.assertTrue(value["carrier_navigation_enabled"])
            self.assertEqual(
                value["carrier_navigation_profiles"]["build_building"],
                "calibration/carrier_building_open.json",
            )
            self.assertEqual(
                value["carrier_navigation_profiles"]["upgrade_building"],
                "calibration/carrier_upgrade_open.json",
            )
            self.assertEqual(
                value["carrier_click_profiles"]["upgrade_building"],
                "calibration/carrier_upgrade_click.json",
            )
            self.assertEqual(
                value["carrier_navigation_profiles"]["replace_building"],
                "calibration/carrier_replacement_open.json",
            )
            self.assertEqual(
                value["carrier_intermediate_profiles"]["replace_building"],
                "calibration/carrier_replacement_button.json",
            )
            self.assertEqual(
                value["carrier_click_profiles"]["replace_building"],
                "calibration/carrier_replacement_click.json",
            )
            self.assertEqual(
                value["carrier_click_step_delay_seconds"],
                0.45,
            )
            self.assertEqual(value["carrier_pointer_settle_seconds"], 0.20)
            self.assertEqual(value["carrier_click_hold_seconds"], 0.08)
            self.assertEqual(value["carrier_post_click_settle_seconds"], 0.25)
            self.assertTrue(value["fixed_click_guard_enabled"])
            self.assertEqual(value["maximum_source_save_lag_versions"], 2)
            self.assertNotIn("autonomy_mode", result["added"])
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
