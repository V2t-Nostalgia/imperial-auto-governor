from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from iag.applications.fleet_operations.conversation_agent import (
    FleetConversationAgent,
)
from iag.core.conversation_store import ConversationStore
from iag.core.read_tasks import ReadTaskPool
from iag.infrastructure.llm.model_pool import ModelEndpoint, ModelPool
from iag.infrastructure.llm.model_pool_runtime import ModelPoolRuntime


def _test_pool() -> ModelPool:
    endpoint = ModelEndpoint.model_validate(
        {
            "endpoint_id": "fleet-test",
            "model_id": "fleet-model",
            "model": "fleet-model",
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
        pool_id="fleet-pool",
        display_name="Fleet",
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
                "experimental_fleet_tools_enabled": True,
            },
            model_pool=pool,
            request_options={},
            application_profile=SimpleNamespace(profile_id="fleet-profile"),
        )

    def snapshot(self, application_id: str = "economy_governance") -> Any:
        self.calls.append(application_id)
        return self.value


class ConfirmedFleetToolbox:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.prepared = None

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "execute_prepared_fleet_order",
                    "description": "Execute test order.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def dispatch(
        self,
        name: str,
        _arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        return (
            {
                "success": True,
                "run_id": "fleet-run-1",
                "action": "move_fleet",
                "fleet_id": 42,
                "destination_system_id": 7,
            },
            f"{name} confirmed",
        )


class FleetCompletion:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self,
        _endpoint: ModelEndpoint,
        _messages: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "fleet-call-1",
                        "type": "function",
                        "function": {
                            "name": "execute_prepared_fleet_order",
                            "arguments": "{}",
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "舰队命令已确认。"}


class FleetConversationAgentTests(unittest.TestCase):
    def test_consecutive_inspect_calls_run_in_bounded_parallel_pool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = _test_pool()
            runtime = FakeRuntimeConfig(root, pool)
            history = ConversationStore(root / "fleet.sqlite3")
            actions = ConversationStore(root / "campaign.sqlite3")
            started = 0
            maximum_active = 0
            active = 0
            lock = threading.Lock()
            all_started = threading.Event()

            class ParallelInspectToolbox:
                prepared = None
                parallel_read_tools = frozenset(
                    f"inspect_test_{index}" for index in range(4)
                )

                def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                    pass

                @staticmethod
                def schemas() -> list[dict[str, Any]]:
                    return [
                        {
                            "type": "function",
                            "function": {
                                "name": f"inspect_test_{index}",
                                "description": "Read independent test state.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {},
                                },
                            },
                        }
                        for index in range(4)
                    ]

                @staticmethod
                def dispatch(
                    name: str,
                    _arguments: dict[str, Any],
                ) -> tuple[dict[str, Any], str]:
                    nonlocal started, active, maximum_active
                    with lock:
                        started += 1
                        active += 1
                        maximum_active = max(maximum_active, active)
                        if started == 4:
                            all_started.set()
                    try:
                        if not all_started.wait(1):
                            raise AssertionError("Inspect tools were dispatched serially.")
                        return {"success": True, "tool": name}, name
                    finally:
                        with lock:
                            active -= 1

            completion_calls = 0

            def completion(
                _endpoint: ModelEndpoint,
                _messages: list[dict[str, Any]],
                **_kwargs: Any,
            ) -> dict[str, Any]:
                nonlocal completion_calls
                completion_calls += 1
                if completion_calls == 1:
                    return {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"read-{index}",
                                "type": "function",
                                "function": {
                                    "name": f"inspect_test_{index}",
                                    "arguments": "{}",
                                },
                            }
                            for index in range(4)
                        ],
                    }
                return {"role": "assistant", "content": "读取完成。"}

            read_pool = ReadTaskPool(max_workers=4, timeout_seconds=2)
            try:
                result = FleetConversationAgent(
                    runtime,
                    history,
                    actions,
                    model_pool_runtime=ModelPoolRuntime(pool),
                    completion_fn=completion,
                    toolbox_factory=ParallelInspectToolbox,
                    read_task_pool=read_pool,
                ).run_turn(
                    trigger="chat",
                    user_content="并行读取",
                )
            finally:
                read_pool.close()

        self.assertEqual(maximum_active, 4)
        self.assertEqual(len(result["tool_events"]), 4)
        self.assertEqual(read_pool.status()["submitted"], 4)

    def test_confirmed_fleet_execution_enters_specialist_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = _test_pool()
            runtime = FakeRuntimeConfig(root, pool)
            history = ConversationStore(root / "fleet.sqlite3")
            actions = ConversationStore(root / "campaign.sqlite3")
            audits: list[dict[str, Any]] = []
            agent = FleetConversationAgent(
                runtime,
                history,
                actions,
                model_pool_runtime=ModelPoolRuntime(pool),
                completion_fn=FleetCompletion(),
                toolbox_factory=ConfirmedFleetToolbox,
            )

            result = agent.run_turn(
                trigger="chat",
                user_content="执行舰队命令",
                autonomy_mode="paused",
                audit_event_callback=audits.append,
            )

            self.assertIn("fleet_operations", runtime.calls)
            self.assertEqual(result["executed_count"], 1)
            self.assertEqual(
                result["execution_facts"]["confirmed"][0]["fleet_id"],
                42,
            )
            self.assertEqual(audits[0]["tool"], "execute_prepared_fleet_order")

    def test_real_fleet_toolbox_does_not_expose_other_domains(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = _test_pool()
            runtime = FakeRuntimeConfig(root, pool)
            history = ConversationStore(root / "fleet.sqlite3")
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
                return {"role": "assistant", "content": "舰队维持现状。"}

            FleetConversationAgent(
                runtime,
                history,
                actions,
                model_pool_runtime=ModelPoolRuntime(pool),
                completion_fn=completion,
            ).run_turn(
                trigger="chat",
                user_content="检查舰队",
            )

            self.assertIn("inspect_fleet_state", observed_tools)
            self.assertIn("execute_prepared_fleet_order", observed_tools)
            self.assertNotIn("inspect_empire_state", observed_tools)
            self.assertNotIn("inspect_research_state", observed_tools)


if __name__ == "__main__":
    unittest.main()
