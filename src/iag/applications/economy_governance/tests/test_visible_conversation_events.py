#!/usr/bin/env python3
"""Ensure the governor exposes only player and model-visible conversation data."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from iag.applications.economy_governance.conversation_agent import ConversationAgent
from iag.core.conversation_store import ConversationStore
from iag.infrastructure.llm.model_pool import ModelEndpoint, ModelPool
from iag.infrastructure.llm.model_pool_runtime import ModelPoolRuntime


class FakeRuntimeConfig:
    def __init__(self, root: Path, pool: ModelPool) -> None:
        self.value = SimpleNamespace(
            settings={
                "runtime_root": str(root),
                "tool_calling_enabled": True,
                "context_compression_enabled": False,
                "tool_loop_max_rounds": 4,
                "chat_message_max_chars": 12000,
            },
            model_pool=pool,
            request_options={},
        )

    def snapshot(self) -> Any:
        return self.value


class FakeToolbox:
    prepared_run_id = None
    executed = False
    review_recorded = True

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.successful_executions: list[dict[str, Any]] = []
        self.provisional_executions: list[dict[str, Any]] = []

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "inspect_empire_state",
                    "description": "Read state.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        return {"success": True, "run_id": None}, "tool audit must stay private"


class StreamingCompletion:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self,
        endpoint: ModelEndpoint,
        messages: list[dict[str, Any]],
        *,
        request_options: dict[str, Any],
        tools: list[dict[str, Any]] | None,
        visible_delta_callback: Any = None,
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "reasoning_content": "private reasoning",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "inspect_empire_state",
                            "arguments": "{}",
                        },
                    }
                ],
            }
        visible_delta_callback("矿物收入")
        visible_delta_callback("需要修复。")
        return {
            "role": "assistant",
            "content": "矿物收入需要修复。",
            "reasoning_content": "more private reasoning",
        }


class TestConversationAgent(ConversationAgent):
    def _prompt(self, config: dict[str, Any]) -> str:
        return "system"


class VisibleConversationEventTests(unittest.TestCase):
    def test_stream_events_exclude_reasoning_and_tool_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            endpoint = ModelEndpoint.model_validate(
                {
                    "endpoint_id": "test",
                    "model_id": "test-model",
                    "model": "test-model",
                    "model_transport": "openai_sdk",
                    "provider": "chat_completions_compatible",
                    "base_url": "https://example.test/v1",
                    "supports_reasoning": True,
                    "model_context_window_tokens": 64000,
                    "auth_mode": "none",
                    "max_output_tokens": 8000,
                    "priority": 0,
                    "enabled": True,
                    "supports_tools": True,
                }
            )
            pool = ModelPool(
                pool_id="test",
                display_name="Test",
                endpoints=[endpoint],
            )
            store = ConversationStore(root / "conversation.sqlite3")
            events: list[tuple[str, dict[str, Any]]] = []
            agent = TestConversationAgent(
                FakeRuntimeConfig(root, pool),
                store,
                model_pool_runtime=ModelPoolRuntime(pool),
                completion_fn=StreamingCompletion(),
                toolbox_factory=FakeToolbox,
            )

            result = agent.run_turn(
                trigger="chat",
                user_content="检查经济",
                autonomy_mode="advisory",
                visible_event_callback=lambda event, payload: events.append(
                    (event, payload)
                ),
            )

            rendered = repr(events)
            self.assertEqual(result["final_content"], "矿物收入需要修复。")
            self.assertNotIn("private reasoning", rendered)
            self.assertNotIn("tool audit", rendered)
            self.assertEqual(
                [event for event, _payload in events],
                [
                    "user_message",
                    "assistant_started",
                    "assistant_delta",
                    "assistant_delta",
                    "assistant_final",
                ],
            )


if __name__ == "__main__":
    unittest.main()
