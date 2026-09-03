#!/usr/bin/env python3
"""Static contracts for the model catalog and Application profile UI."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


class ModelConfigurationFrontendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        self.javascript = (WEB_ROOT / "app_v2.js").read_text(
            encoding="utf-8"
        )

    def test_script_references_only_existing_unique_ids(self) -> None:
        html_ids = re.findall(r'\bid="([^"]+)"', self.html)
        referenced_ids = set(
            re.findall(r'\$\("([^"]+)"\)', self.javascript)
        )

        self.assertEqual(len(html_ids), len(set(html_ids)))
        self.assertEqual(referenced_ids - set(html_ids), set())

    def test_catalog_and_application_surfaces_are_separate(self) -> None:
        required = {
            "application-select",
            "application-profile-select",
            "application-profile-name",
            "application-pool-select",
            "application-model-select",
            "open-model-catalog",
            "model-catalog-dialog",
            "model-pool-list",
            "create-model-pool",
            "catalog-endpoint-list",
            "model-endpoint-editor",
        }
        html_ids = set(re.findall(r'\bid="([^"]+)"', self.html))

        self.assertEqual(required - html_ids, set())
        self.assertNotIn("model-endpoint-select", html_ids)
        self.assertNotIn("model-pool-id", html_ids)

    def test_browser_uses_catalog_profile_routes(self) -> None:
        for route in (
            "/api/model/pools",
            "/api/model/application-profile",
            "/api/model/application-profile/delete",
            "/api/model/probe",
        ):
            self.assertIn(route, self.javascript)

    def test_protocol_suite_has_a_non_llm_control_surface(self) -> None:
        required = {
            "open-protocol-suite",
            "protocol-suite-dialog",
            "protocol-command-list",
            "protocol-scenario-target",
            "start-protocol-suite",
            "confirm-protocol-room",
            "execute-protocol-action",
            "protocol-verdict-actions",
            "finish-protocol-suite",
        }
        html_ids = set(re.findall(r'\bid="([^"]+)"', self.html))
        self.assertEqual(required - html_ids, set())
        for route in (
            "/api/protocol-compatibility/plan/save",
            "/api/protocol-compatibility/offline-check",
            "/api/protocol-compatibility/live/start",
            "/api/protocol-compatibility/action/execute",
            "/api/protocol-compatibility/action/verdict",
            "/api/protocol-compatibility/finish",
        ):
            self.assertIn(route, self.javascript)


if __name__ == "__main__":
    unittest.main()
