"""Independent persistent model agent for fleet and ship operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from iag.applications.specialist_conversation_agent import (
    SpecialistConversationAgent,
)
from iag.core.conversation_store import ConversationStore
from iag.core.paths import fleet_operations_root
from iag.core.read_tasks import ReadTaskPool
from iag.infrastructure.llm.model_client import chat_completion_message
from iag.infrastructure.llm.model_pool_runtime import ModelPoolRuntime
from iag.infrastructure.llm.runtime_config import RuntimeConfig
from iag.stellaris.state.world_snapshot import WorldStateService

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
        execution_lock: Any = None,
        world_state_service: WorldStateService | None = None,
        read_task_pool: ReadTaskPool | None = None,
    ) -> None:
        super().__init__(
            runtime_config,
            store,
            action_store,
            application_id="fleet_operations",
            display_name="舰队与扩张行动",
            role_prompt_path=fleet_operations_root()
            / "prompts"
            / "fleet_operator_zh.md",
            toolbox_factory=toolbox_factory,
            execute_tool_names={
                "execute_prepared_fleet_order",
                "execute_prepared_ship_action",
            },
            fact_state_keys={
                "fleet_permissions",
                "fleet_full_delegation",
                "last_fleet_execution",
                "last_expansion_execution",
                "last_invasion_execution",
                "last_ship_execution",
                "last_new_fleet_creation",
                "pending_new_fleet_creation",
                "last_army_recruitment",
                "pending_army_recruitment",
                "last_campaign_route",
                "pending_campaign_route",
            },
            model_pool_runtime=model_pool_runtime,
            completion_fn=completion_fn,
            execution_lock=execution_lock,
            world_state_service=world_state_service,
            read_task_pool=read_task_pool,
        )
