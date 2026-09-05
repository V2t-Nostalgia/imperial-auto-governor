"""Independent persistent model agent for fleet and ship operations."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from iag.applications.specialist_conversation_agent import (
    SpecialistConversationAgent,
)
from iag.core.conversation_store import ConversationStore
from iag.infrastructure.llm.model_client import chat_completion_message
from iag.infrastructure.llm.model_pool_runtime import ModelPoolRuntime
from iag.infrastructure.llm.runtime_config import RuntimeConfig

from .agent_tools import FleetToolbox


class FleetConversationAgent(SpecialistConversationAgent):
    """Fleet specialist with its own prompt, model route, and history."""

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        store: ConversationStore,
        action_store: ConversationStore,
        *,
        model_pool_runtime: ModelPoolRuntime | None = None,
        completion_fn: Callable[..., dict[str, Any]] = chat_completion_message,
        toolbox_factory: Callable[..., Any] = FleetToolbox,
    ) -> None:
        super().__init__(
            runtime_config,
            store,
            action_store,
            application_id="fleet_operations",
            display_name="舰队行动",
            role_prompt_path=(
                Path(__file__).resolve().parent
                / "prompts"
                / "fleet_operator_zh.md"
            ),
            toolbox_factory=toolbox_factory,
            execute_tool_names={
                "execute_prepared_fleet_order",
                "execute_prepared_ship_action",
            },
            fact_state_keys={
                "fleet_permissions",
                "last_fleet_execution",
                "last_ship_execution",
                "last_new_fleet_creation",
                "pending_new_fleet_creation",
            },
            model_pool_runtime=model_pool_runtime,
            completion_fn=completion_fn,
        )
