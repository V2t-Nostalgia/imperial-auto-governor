#!/usr/bin/env python3
"""Integration checks for independent Application conversation agents."""

from __future__ import annotations

import json
import tempfile
import threading
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
            (ROOT / "control_center" / "agent_config.windows.example.json").read_text(
                encoding="utf-8"
            )
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
            store.path.resolve() for store in self.service.application_stores.values()
        }
        self.assertEqual(len(paths), 3)

    def test_reports_default_shared_and_dedicated_model_routes(self) -> None:
        routes = {
            item["application_id"]: item
            for item in self.service.application_agent_routes()
        }

        self.assertEqual(
            set(routes),
            {
                "economy_governance",
                "fleet_operations",
                "research_strategy",
            },
        )
        self.assertEqual(
            routes["economy_governance"]["binding_mode"],
            "dedicated",
        )
        for application_id in ("fleet_operations", "research_strategy"):
            self.assertEqual(routes[application_id]["binding_mode"], "inherited")
            self.assertEqual(
                routes[application_id]["model_source_application_id"],
                "economy_governance",
            )
        self.assertTrue(all(item["conversation_supported"] for item in routes.values()))

    def test_selected_specialist_response_stays_in_private_thread(self) -> None:
        class FakeResearchAgent:
            application_id = "research_strategy"
            display_name = "科研战略"

            def __init__(self, store: Any) -> None:
                self.store = store

            def run_turn(self, **kwargs: Any) -> dict[str, Any]:
                self.store.append(
                    "user",
                    str(kwargs.get("user_content") or ""),
                    kind="operator_message",
                    metadata={"application_id": self.application_id},
                )
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
                self.store.append(
                    "assistant",
                    "工程学优先。",
                    kind="assistant_message",
                    metadata={"application_id": self.application_id},
                )
                return {
                    "final_content": "工程学优先。",
                    "tool_events": [],
                    "execution_facts": {
                        "confirmed": [],
                        "provisional": [],
                    },
                }

        self.service.application_agents["research_strategy"] = FakeResearchAgent(
            self.service.application_stores["research_strategy"]
        )
        self.service._start_job = lambda _kind, action, **_kwargs: action()
        main_before = self.service.conversation_store.public_messages()

        result = self.service.start_chat("检查科研", "research_strategy")
        main_after = self.service.conversation_store.public_messages()
        research = self.service.conversation_payload(
            application_id="research_strategy"
        )

        self.assertEqual(result["final_content"], "工程学优先。")
        self.assertEqual(main_after, main_before)
        self.assertEqual(research["application_id"], "research_strategy")
        self.assertEqual(research["messages"][-1]["content"], "工程学优先。")
        economy = self.service.conversation_payload(
            application_id="economy_governance"
        )
        self.assertFalse(
            any(item["content"] == "工程学优先。" for item in economy["messages"])
        )

    def test_contact_previews_and_histories_remain_application_scoped(self) -> None:
        unique_messages = {
            "economy_governance": "经济线程独有消息",
            "fleet_operations": "舰队线程独有消息",
            "research_strategy": "科研线程独有消息",
        }
        for application_id, content in unique_messages.items():
            self.service.application_stores[application_id].append(
                "assistant",
                content,
                kind="assistant_message",
                visible=True,
                metadata={"application_id": application_id},
            )

        fleet = self.service.conversation_payload(
            application_id="fleet_operations"
        )
        fleet_contents = {item["content"] for item in fleet["messages"]}
        contacts = {
            item["application_id"]: item for item in fleet["applications"]
        }

        self.assertIn(unique_messages["fleet_operations"], fleet_contents)
        self.assertNotIn(unique_messages["economy_governance"], fleet_contents)
        self.assertNotIn(unique_messages["research_strategy"], fleet_contents)
        self.assertEqual(set(contacts), set(unique_messages))
        for application_id, content in unique_messages.items():
            self.assertEqual(
                contacts[application_id]["last_message_preview"],
                content,
            )

    def test_contacts_report_the_actual_autonomous_job_state(self) -> None:
        with self.service._job_lock:
            self.service._job = {
                "state": "running",
                "kind": "autonomous",
            }

        contacts = {
            item["application_id"]: item
            for item in self.service.conversation_payload()["applications"]
        }

        self.assertTrue(contacts["economy_governance"]["running"])
        self.assertEqual(
            contacts["fleet_operations"]["running"],
            contacts["fleet_operations"]["tools_enabled"],
        )
        self.assertEqual(
            contacts["research_strategy"]["running"],
            contacts["research_strategy"]["tools_enabled"],
        )

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

    def test_autonomous_applications_start_concurrently(self) -> None:
        started: list[str] = []
        received_snapshots: list[object] = []
        started_lock = threading.Lock()
        all_started = threading.Event()

        def action(
            *,
            agent: Any,
            world_snapshot: object,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            with started_lock:
                started.append(agent.application_id)
                received_snapshots.append(world_snapshot)
                if len(started) == 3:
                    all_started.set()
            if not all_started.wait(1):
                raise AssertionError("Application workers started serially.")
            return {"final_content": "done"}

        class FakeAgent:
            def __init__(self, application_id: str) -> None:
                self.application_id = application_id

        class FakeReviewer:
            def run_if_due(self, **_kwargs: Any) -> dict[str, Any]:
                return {"state": "not_due", "ran": False}

        self.service.application_agents = {
            application_id: FakeAgent(application_id)
            for application_id in (
                "economy_governance",
                "fleet_operations",
                "research_strategy",
            )
        }
        self.service._application_tools_enabled = lambda *_args: True
        self.service._conversation_action = action
        self.service._specialist_conversation_action = action
        self.service._record_turn_completion = lambda *_args: None
        self.service.joint_plan_reviewer = FakeReviewer()

        class FakeSnapshot:
            path = Path("missing-test-save.sav")

            def source_identity(self) -> dict[str, Any]:
                return {
                    "path": "test.sav",
                    "modified_ns": 1,
                    "size": 2,
                }

            def warm_for_applications(
                self,
                application_ids: list[str],
                **_kwargs: Any,
            ) -> dict[str, Any]:
                return {
                    "schema": "iag.world_snapshot_warmup.v1",
                    "applications": sorted(application_ids),
                    "elapsed_seconds": 0.0,
                }

        pinned_snapshot = FakeSnapshot()

        class FakeWorldStateService:
            def pin(self, *_args: Any, **_kwargs: Any) -> object:
                return pinned_snapshot

        self.service.world_state_service = FakeWorldStateService()

        result = self.service._run_autonomous_suite(
            self.service.conversation_store,
            "advisory",
            source_identity=None,
            game_date="2200.01.01",
        )

        self.assertEqual(set(started), set(self.service.application_agents))
        self.assertEqual(received_snapshots, [pinned_snapshot] * 3)
        self.assertTrue(all(item["success"] for item in result["applications"]))
        self.assertEqual(
            result["world_snapshot"]["applications"],
            sorted(self.service.application_agents),
        )


if __name__ == "__main__":
    unittest.main()
