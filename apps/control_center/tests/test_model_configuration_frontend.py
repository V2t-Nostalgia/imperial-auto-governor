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

    def test_conversation_refresh_deduplicates_message_ids(self) -> None:
        self.assertIn("renderedConversationMessageIds", self.javascript)
        self.assertIn(
            "renderedConversationMessageIds.has(messageKey)",
            self.javascript,
        )
        self.assertIn("renderedConversationMessageIds.clear()", self.javascript)

    def test_conversation_routes_messages_to_selected_application(self) -> None:
        html_ids = set(re.findall(r'\bid="([^"]+)"', self.html))
        self.assertIn("conversation-application", html_ids)
        self.assertIn(
            "selectedConversationApplicationId",
            self.javascript,
        )
        self.assertIn(
            "application_id: selectedConversationApplicationId",
            self.javascript,
        )
        self.assertIn("application_agents", self.javascript)

    def test_protocol_start_respects_backend_target_readiness(self) -> None:
        self.assertIn("actions.can_start_live", self.javascript)
        self.assertIn("待填写联机目标", self.javascript)

    def test_host_bridge_download_prepares_archive_before_navigation(self) -> None:
        html_ids = set(re.findall(r'\bid="([^"]+)"', self.html))
        self.assertIn("download-host-bridge", html_ids)
        self.assertIn("host-bridge-download-status", html_ids)
        self.assertIn('method: "HEAD"', self.javascript)
        self.assertIn("downloadPairedHostBridge", self.javascript)

    def test_proxy_is_preferred_and_hides_click_calibration(self) -> None:
        self.assertRegex(
            self.html,
            r'<select id="execution-mode"><option value="session_proxy">',
        )
        html_ids = set(re.findall(r'\bid="([^"]+)"', self.html))
        self.assertIn("carrier-calibration-panel", html_ids)
        self.assertIn("fixed-click-guard-row", html_ids)
        self.assertIn("fixed-click-guard-hint", html_ids)
        self.assertIn(
            '$("carrier-calibration-panel").hidden = proxyMode;',
            self.javascript,
        )
        self.assertIn(
            '$("fixed-click-guard-row").hidden = proxyMode;',
            self.javascript,
        )
        self.assertIn(
            '$("fixed-click-guard-hint").hidden = proxyMode;',
            self.javascript,
        )


if __name__ == "__main__":
    unittest.main()
