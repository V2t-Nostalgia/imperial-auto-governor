from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from iag.applications.research_strategy.conversation_agent import (
    ResearchConversationAgent,
)
from iag.core.application_plan import ApplicationPlanBook
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
            application_profile=SimpleNamespace(profile_id="research-profile"),
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
                observed_tools.extend(item["function"]["name"] for item in tools or [])
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
                    "inspect_plan_facts",
                    "inspect_application_plan",
                    "create_application_plan",
                    "edit_application_plan",
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
                    item["function"]["name"] for item in kwargs.get("tools") or []
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

    def test_valid_autonomous_plan_skips_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = _test_pool()
            runtime = FakeRuntimeConfig(root, pool)
            history = ConversationStore(root / "research.sqlite3")
            actions = ConversationStore(root / "campaign.sqlite3")
            facts = {"research": {"physics_candidates": 3}}

            class FakeToolbox:
                def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                    pass

                @staticmethod
                def schemas() -> list[dict[str, Any]]:
                    return []

                @staticmethod
                def dispatch(
                    _name: str,
                    _arguments: dict[str, Any],
                ) -> tuple[dict[str, Any], str]:
                    raise AssertionError("No action is due.")

                @staticmethod
                def inspect() -> dict[str, Any]:
                    return facts

            ApplicationPlanBook(
                history,
                "research_strategy",
                lambda: facts,
            ).create(
                {
                    "plan_id": "research-focus",
                    "title": "Research focus",
                    "objective": "Maintain a physics candidate buffer.",
                    "reason": "test",
                    "nodes": [
                        {
                            "node_id": "physics",
                            "title": "Physics",
                            "objective": "Keep candidates available.",
                            "expectations": [
                                {
                                    "fact": "/research/physics_candidates",
                                    "operator": "gte",
                                    "value": 1,
                                }
                            ],
                        }
                    ],
                }
            )

            agent = ResearchConversationAgent(
                runtime,
                history,
                actions,
                model_pool_runtime=ModelPoolRuntime(pool),
                completion_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("The main model must not be called.")
                ),
                toolbox_factory=FakeToolbox,
            )
            result = agent.run_turn(
                trigger="autonomous",
                autonomy_mode="advisory",
            )

        self.assertEqual(result["context"]["mode"], "deterministic_plan")

    def test_plan_anomaly_sends_only_local_context_to_main_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = _test_pool()
            runtime = FakeRuntimeConfig(root, pool)
            history = ConversationStore(root / "research.sqlite3")
            actions = ConversationStore(root / "campaign.sqlite3")
            facts = {
                "research": {
                    "physics_candidates": 0,
                    "engineering_candidates": 3,
                }
            }

            class FakeToolbox:
                def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                    pass

                @staticmethod
                def schemas() -> list[dict[str, Any]]:
                    return []

                @staticmethod
                def inspect() -> dict[str, Any]:
                    return facts

            ApplicationPlanBook(
                history,
                "research_strategy",
                lambda: facts,
            ).create(
                {
                    "plan_id": "research-focus",
                    "title": "Research focus",
                    "objective": "Maintain physics options.",
                    "reason": "test",
                    "nodes": [
                        {
                            "node_id": "physics",
                            "title": "Physics",
                            "objective": "Keep candidates available.",
                            "expectations": [
                                {
                                    "fact": "/research/physics_candidates",
                                    "operator": "gte",
                                    "value": 1,
                                }
                            ],
                        },
                        {
                            "node_id": "engineering",
                            "title": "UNRELATED_PLAN_BRANCH_MARKER",
                            "objective": "Keep engineering choices available.",
                            "expectations": [
                                {
                                    "fact": "/research/engineering_candidates",
                                    "operator": "gte",
                                    "value": 1,
                                }
                            ],
                        },
                    ],
                }
            )
            history.append("user", "UNRELATED_FULL_HISTORY_MARKER")
            observed_messages: list[dict[str, Any]] = []
            observed_tools: list[str] = []

            def completion(
                _endpoint: ModelEndpoint,
                messages: list[dict[str, Any]],
                **kwargs: Any,
            ) -> dict[str, Any]:
                observed_messages.extend(messages)
                observed_tools.extend(
                    item["function"]["name"] for item in kwargs.get("tools") or []
                )
                return {"role": "assistant", "content": "Local plan review complete."}

            result = ResearchConversationAgent(
                runtime,
                history,
                actions,
                model_pool_runtime=ModelPoolRuntime(pool),
                completion_fn=completion,
                toolbox_factory=FakeToolbox,
            ).run_turn(
                trigger="autonomous",
                autonomy_mode="advisory",
            )

        self.assertEqual(result["context"]["mode"], "localized_plan_exception")
        self.assertEqual(len(observed_messages), 2)
        rendered = json.dumps(observed_messages, ensure_ascii=False)
        self.assertNotIn("UNRELATED_FULL_HISTORY_MARKER", rendered)
        self.assertNotIn("UNRELATED_PLAN_BRANCH_MARKER", rendered)
        self.assertIn("physics_candidates", rendered)
        self.assertEqual(observed_tools, ["edit_application_plan"])


if __name__ == "__main__":
    unittest.main()
