"""Independent persistent model agent for research selection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from iag.applications.specialist_conversation_agent import (
    SpecialistConversationAgent,
)
from iag.core.conversation_store import ConversationStore
from iag.core.paths import research_strategy_root
from iag.core.read_tasks import ReadTaskPool
from iag.infrastructure.llm.model_client import chat_completion_message
from iag.infrastructure.llm.model_pool_runtime import ModelPoolRuntime
from iag.infrastructure.llm.runtime_config import RuntimeConfig
from iag.stellaris.state.world_snapshot import WorldStateService

from .agent_tools import TechnologyToolbox


class ResearchConversationAgent(SpecialistConversationAgent):
    """Research specialist with its own prompt, model route, and history."""

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        store: ConversationStore,
        action_store: ConversationStore,
        *,
        model_pool_runtime: ModelPoolRuntime | None = None,
        completion_fn: Callable[..., dict[str, Any]] = chat_completion_message,
        toolbox_factory: Callable[..., Any] = TechnologyToolbox,
        execution_lock: Any = None,
        world_state_service: WorldStateService | None = None,
        read_task_pool: ReadTaskPool | None = None,
    ) -> None:
        super().__init__(
            runtime_config,
            store,
            action_store,
            application_id="research_strategy",
            display_name="科研战略",
            role_prompt_path=research_strategy_root()
            / "prompts"
            / "research_director_zh.md",
            toolbox_factory=toolbox_factory,
            execute_tool_names={"execute_prepared_research"},
            fact_state_keys={"last_research_execution"},
            model_pool_runtime=model_pool_runtime,
            completion_fn=completion_fn,
            execution_lock=execution_lock,
            world_state_service=world_state_service,
            read_task_pool=read_task_pool,
        )
