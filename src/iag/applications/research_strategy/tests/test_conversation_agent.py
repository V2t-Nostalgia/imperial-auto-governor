from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from iag.applications.research_strategy.conversation_agent import (
    ResearchConversationAgent,
)
from iag.core.conversation_store import ConversationStore
from iag.infrastructure.llm.model_pool import ModelEndpoint, ModelPool
from iag.infrastructure.llm.model_pool_runtime import ModelPoolRuntime


def _test_pool() -> ModelPool:
    endpoint = ModelEndpoint.model_validate(
        {
            "endpoint_id": "research-test",
            "model_id": "research-model",
            "model": "research-model",
            "model_transport": "openai_sdk",
            "provider": "chat_completions_compatible",
            "base_url": "https://example.test/v1",
            "supports_reasoning": False,
            "model_context_window_tokens": 64000,
            "auth_mode": "none",
            "max_output_tokens": 8000,
            "priority": 0,
            "enabled": True,
            "supports_tools": True,
        }
    )
    return ModelPool(
        pool_id="research-pool",
        display_name="Research",
        endpoints=[endpoint],
    )


class FakeRuntimeConfig:
    def __init__(self, root: Path, pool: ModelPool) -> None:
        self.calls: list[str] = []
        self.value = SimpleNamespace(
            settings={
                "runtime_root": str(root),
                "tool_calling_enabled": True,
                "context_compression_enabled": False,
                "tool_loop_max_rounds": 4,
                "chat_message_max_chars": 12000,
                "experimental_research_tools_enabled": True,
            },
            model_pool=pool,
            request_options={},
            application_profile=SimpleNamespace(
                profile_id="research-profile"
            ),
        )

    def snapshot(self, application_id: str = "economy_governance") -> Any:
        self.calls.append(application_id)
        return self.value


class ResearchConversationAgentTests(unittest.TestCase):
    def test_uses_research_model_route_and_only_research_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = _test_pool()
            runtime = FakeRuntimeConfig(root, pool)
            history = ConversationStore(root / "research.sqlite3")
            actions = ConversationStore(root / "campaign.sqlite3")
            observed_tools: list[str] = []

            def completion(
                _endpoint: ModelEndpoint,
                _messages: list[dict[str, Any]],
                *,
                request_options: dict[str, Any],
                tools: list[dict[str, Any]] | None,
            ) -> dict[str, Any]:
                del request_options
                observed_tools.extend(
                    item["function"]["name"] for item in tools or []
                )
                return {"role": "assistant", "content": "科研维持现状。"}

            agent = ResearchConversationAgent(
                runtime,
                history,
                actions,
                model_pool_runtime=ModelPoolRuntime(pool),
                completion_fn=completion,
            )
            result = agent.run_turn(
                trigger="chat",
                user_content="检查科研",
                autonomy_mode="paused",
            )

            self.assertIn("research_strategy", runtime.calls)
            self.assertEqual(result["model_binding_mode"], "dedicated")
            self.assertEqual(
                set(observed_tools),
                {
                    "inspect_research_state",
                    "prepare_research_selection",
                    "execute_prepared_research",
                },
            )
            self.assertEqual(actions.public_state()["stored_messages"], 0)
            self.assertGreater(history.public_state()["stored_messages"], 0)

    def test_advisory_review_does_not_expose_research_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = _test_pool()
            runtime = FakeRuntimeConfig(root, pool)
            history = ConversationStore(root / "research.sqlite3")
            actions = ConversationStore(root / "campaign.sqlite3")
            observed_tools: list[str] = []

            def completion(
                _endpoint: ModelEndpoint,
                _messages: list[dict[str, Any]],
                **kwargs: Any,
            ) -> dict[str, Any]:
                observed_tools.extend(
                    item["function"]["name"]
                    for item in kwargs.get("tools") or []
                )
                return {"role": "assistant", "content": "只做科研建议。"}

            ResearchConversationAgent(
                runtime,
                history,
                actions,
                model_pool_runtime=ModelPoolRuntime(pool),
                completion_fn=completion,
            ).run_turn(
                trigger="manual_review",
                autonomy_mode="advisory",
            )

            self.assertNotIn("execute_prepared_research", observed_tools)


if __name__ == "__main__":
    unittest.main()
