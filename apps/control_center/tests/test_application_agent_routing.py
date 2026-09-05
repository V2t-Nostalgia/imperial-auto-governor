#!/usr/bin/env python3
"""Integration checks for independent Application conversation agents."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from apps.control_center.web_console import ConsoleService
from iag.applications.economy_governance.conversation_agent import (
    ConversationAgent,
)
from iag.applications.fleet_operations.conversation_agent import (
    FleetConversationAgent,
)
from iag.applications.research_strategy.conversation_agent import (
    ResearchConversationAgent,
)

ROOT = Path(__file__).resolve().parents[2]


class ApplicationAgentRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        document = json.loads(
            (ROOT / "control_center" / "agent_config.windows.example.json")
            .read_text(encoding="utf-8")
        )
        document["runtime_root"] = str(root / "runtime")
        document["save_root"] = str(root / "saves")
        document["game_root"] = ""
        self.config_path = root / "agent_config.json"
        self.config_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.service = ConsoleService(self.config_path)

    def tearDown(self) -> None:
        self.service.close()
        self.temporary.cleanup()

    def test_builds_three_agents_with_isolated_histories(self) -> None:
        self.assertIsInstance(
            self.service.application_agents["economy_governance"],
            ConversationAgent,
        )
        self.assertIsInstance(
            self.service.application_agents["fleet_operations"],
            FleetConversationAgent,
        )
        self.assertIsInstance(
            self.service.application_agents["research_strategy"],
            ResearchConversationAgent,
        )
        paths = {
            store.path.resolve()
            for store in self.service.application_stores.values()
        }
        self.assertEqual(len(paths), 3)

    def test_reports_dedicated_model_route_for_each_application(self) -> None:
        routes = {
            item["application_id"]: item
            for item in self.service.application_agent_routes()
        }

        self.assertEqual(set(routes), {
            "economy_governance",
            "fleet_operations",
            "research_strategy",
        })
        self.assertTrue(all(
            item["binding_mode"] == "dedicated"
            for item in routes.values()
        ))
        self.assertTrue(all(
            item["conversation_supported"]
            for item in routes.values()
        ))

    def test_selected_specialist_response_is_mirrored_to_campaign_log(self) -> None:
        class FakeResearchAgent:
            application_id = "research_strategy"
            display_name = "科研战略"

            def run_turn(self, **kwargs: Any) -> dict[str, Any]:
                kwargs["audit_event_callback"](
                    {
                        "tool": "inspect_research_state",
                        "success": True,
                        "summary": "已读取三系科研状态。",
                        "run_id": None,
                    }
                )
                kwargs["visible_event_callback"](
                    "assistant_final",
                    {
                        "role": "assistant",
                        "kind": "assistant_message",
                        "content": "工程学优先。",
                        "round_index": 0,
                    },
                )
                return {
                    "final_content": "工程学优先。",
                    "tool_events": [],
                    "execution_facts": {
                        "confirmed": [],
                        "provisional": [],
                    },
                }

        self.service.application_agents["research_strategy"] = (
            FakeResearchAgent()
        )
        self.service._start_job = (
            lambda _kind, action, **_kwargs: action()
        )

        result = self.service.start_chat("检查科研", "research_strategy")
        messages = self.service.conversation_store.public_messages()

        self.assertEqual(result["final_content"], "工程学优先。")
        self.assertEqual(messages[-1]["content"], "工程学优先。")
        self.assertEqual(
            messages[-1]["metadata"]["application_id"],
            "research_strategy",
        )
        self.assertTrue(any(
            item["role"] == "tool" and
            item["metadata"].get("application_id") == "research_strategy"
            for item in messages
        ))

    def test_legacy_economy_only_binding_is_inherited_without_rewrite(self) -> None:
        document = json.loads(self.config_path.read_text(encoding="utf-8"))
        document["application_model_profiles"] = [
            profile
            for profile in document["application_model_profiles"]
            if profile["application_id"] == "economy_governance"
        ]
        document["application_model_bindings"] = {
            "economy_governance": "economy-default"
        }
        legacy_path = self.config_path.with_name("legacy-agent.json")
        legacy_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        service = ConsoleService(legacy_path)
        try:
            routes = {
                item["application_id"]: item
                for item in service.application_agent_routes()
            }
            self.assertEqual(
                routes["research_strategy"]["binding_mode"],
                "inherited",
            )
            self.assertEqual(
                routes["fleet_operations"]["binding_mode"],
                "inherited",
            )
            persisted = json.loads(legacy_path.read_text(encoding="utf-8"))
            self.assertEqual(
                persisted["application_model_bindings"],
                {"economy_governance": "economy-default"},
            )
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
