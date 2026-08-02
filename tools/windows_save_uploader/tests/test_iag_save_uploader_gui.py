from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from iag_save_uploader_gui import default_config, read_config, run_health_check


class SaveUploaderGuiConfigTests(unittest.TestCase):
    def test_missing_config_is_created_for_first_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "iag_save_uploader.json"

            config = read_config(config_path)

            self.assertTrue(config_path.is_file())
            self.assertEqual(config, default_config())
            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8")),
                default_config(),
            )
            self.assertEqual(
                config["upload_token_file"],
                "secrets/save_upload_token",
            )

    def test_existing_config_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "iag_save_uploader.json"
            expected = {"server_url": "https://192.0.2.10:8765"}
            config_path.write_text(
                json.dumps(expected),
                encoding="utf-8",
            )

            self.assertEqual(read_config(config_path), expected)

    @patch("iag_save_uploader_gui.PinnedHTTPSUploader")
    def test_health_check_uses_pinned_https_heartbeat(self, client_type) -> None:
        client = client_type.return_value
        client.heartbeat.return_value = {"ok": True, "status": "ok"}

        result = run_health_check(
            [
                "--health-check",
                "--server-url",
                "https://192.0.2.10:8765",
                "--certificate-sha256",
                "a" * 64,
                "--token",
                "test-token",
            ]
        )

        self.assertEqual(result, 0)
        client_type.assert_called_once_with(
            "https://192.0.2.10:8765",
            "a" * 64,
            "test-token",
            timeout_seconds=10,
        )
        client.heartbeat.assert_called_once()


if __name__ == "__main__":
    unittest.main()
