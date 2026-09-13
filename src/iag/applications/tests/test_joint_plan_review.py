#!/usr/bin/env python3
"""Tests for compressed annual coordination without cross-plan mutation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from iag.applications.joint_plan_review import JointPlanReviewer
from iag.core.application_plan import ApplicationPlanBook
from iag.core.conversation_store import ConversationStore
from iag.infrastructure.llm.runtime_config import RuntimeConfig


def runtime_config(path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        path,
        {
            "joint_plan_review_enabled": True,
            "joint_plan_review_months": 12,
            "model_pools": [
                {
                    "pool_id": "main",
                    "display_name": "Main",
                    "endpoints": [
                        {
                            "endpoint_id": "local",
                            "display_name": "Local",
                            "model_id": "model",
                            "model": "model",
                            "model_transport": "raw_http",
                            "provider": "chat_completions_compatible",
                            "base_url": "http://127.0.0.1:1/v1",
                            "supports_reasoning": False,
                            "model_context_window_tokens": 32000,
                            "auth_mode": "none",
                            "max_output_tokens": 2000,
                            "priority": 0,
                            "enabled": True,
                            "supports_tools": True,
                        }
                    ],
                }
            ],
            "application_model_profiles": [
                {
                    "profile_id": "economy-default",
                    "display_name": "Default",
                    "application_id": "economy_governance",
                    "pool_id": "main",
                    "model_id": "model",
                }
            ],
            "application_model_bindings": {"economy_governance": "economy-default"},
        },
    )


class JointPlanReviewerTests(unittest.TestCase):
    def test_annual_review_queues_local_directive_without_editing_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main_store = ConversationStore(root / "main.sqlite3")
            fleet_store = ConversationStore(root / "fleet.sqlite3")
            book = ApplicationPlanBook(
                fleet_store,
                "fleet_operations",
                lambda: {"fleets": [{"fleet_id": 7, "power": 9000}]},
            )
            book.create(
                {
                    "plan_id": "war",
                    "title": "War",
                    "objective": "Advance while the economy can support it.",
                    "reason": "test",
                    "nodes": [
                        {
                            "node_id": "reserve",
                            "title": "Reserve",
                            "objective": "Keep a reserve fleet.",
                        }
                    ],
                }
            )
            book.evaluate()
            requests: list[list[dict[str, Any]]] = []

            def completion(
                _endpoint: Any,
                messages: list[dict[str, Any]],
                **_kwargs: Any,
            ) -> dict[str, Any]:
                requests.append(messages)
                return {
                    "content": json.dumps(
                        {
                            "summary": "Economy now constrains the reserve.",
                            "directives": [
                                {
                                    "application_id": "fleet_operations",
                                    "reason": "Alloy support changed.",
                                    "affected_node_ids": ["reserve", "unknown"],
                                    "instruction": "Recheck reserve size only.",
                                    "dependencies": ["economy alloy plan"],
                                }
                            ],
                        }
                    )
                }

            reviewer = JointPlanReviewer(
                runtime_config(root / "config.json"),
                completion_fn=completion,
            )
            stores = {
                "economy_governance": main_store,
                "fleet_operations": fleet_store,
            }
            scheduled = reviewer.run_if_due(
                game_date="2200.01.01",
                main_store=main_store,
                application_stores=stores,
            )
            before = book.current()
            result = reviewer.run_if_due(
                game_date="2201.01.01",
                main_store=main_store,
                application_stores=stores,
            )
            after = book.current()
            inbox = book.pending_external_reviews()

        self.assertEqual(scheduled["state"], "scheduled")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(before["revision"], after["revision"])
        self.assertEqual(inbox[0]["affected_node_ids"], ["reserve"])
        self.assertEqual(len(requests), 1)
        request_text = requests[0][1]["content"]
        self.assertIn("watched_facts", request_text)
        self.assertNotIn("original_plan", request_text)


if __name__ == "__main__":
    unittest.main()
