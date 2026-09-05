"""Independent persistent model agent for research selection."""

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
    ) -> None:
        super().__init__(
            runtime_config,
            store,
            action_store,
            application_id="research_strategy",
            display_name="科研战略",
            role_prompt_path=(
                Path(__file__).resolve().parent
                / "prompts"
                / "research_director_zh.md"
            ),
            toolbox_factory=toolbox_factory,
            execute_tool_names={"execute_prepared_research"},
            fact_state_keys={"last_research_execution"},
            model_pool_runtime=model_pool_runtime,
            completion_fn=completion_fn,
        )
