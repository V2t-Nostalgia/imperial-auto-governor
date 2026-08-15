#!/usr/bin/env python3
"""Local display-test mode must remain independent from Agent authentication."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from apps.game_overlay.display_test_window import chunk_visible_text
from apps.game_overlay.overlay_config import (
    load_display_test_configuration,
    update_overlay_settings,
)


class DisplayTestConfigurationTests(unittest.TestCase):
    def test_local_config_has_no_agent_connection_or_secret_reference(self) -> None:
        resource_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "display-test.json"
            configuration = load_display_test_configuration(
                config_path,
                resource_root=resource_root,
            )
            update_overlay_settings(config_path, configuration.settings)
            value = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertTrue(value["display_test"])
        self.assertNotIn("server_url", value)
        self.assertNotIn("server_certificate_sha256", value)
        self.assertNotIn("access_token_file", value["overlay"])
        self.assertEqual(configuration.connection.access_token, "")

    def test_mock_stream_fragments_reconstruct_visible_reply(self) -> None:
        text = "正在重新评估殖民地。"
        fragments = chunk_visible_text(text, size=2)
        self.assertTrue(all(fragments))
        self.assertEqual("".join(fragments), text)


if __name__ == "__main__":
    unittest.main()
