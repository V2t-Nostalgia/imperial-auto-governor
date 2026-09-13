#!/usr/bin/env python3
"""Shared persistent tool loop for first-party specialist Applications."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from iag.applications.plan_advisor import PlanExceptionAdvisor
from iag.core.application_plan import (
    ApplicationPlanBook,
    tool_requires_execution_lock,
)
from iag.core.campaign_strategy import (
    public_strategy_state,
    render_strategy_context,
)
from iag.core.context_window import build_context_messages
from iag.core.conversation_store import ConversationStore, now_iso
from iag.core.read_tasks import ReadTask, ReadTaskPool, tool_is_read_only
from iag.infrastructure.llm.model_client import chat_completion_message
from iag.infrastructure.llm.model_pool import TOOL_CALL_PROTOCOLS
from iag.infrastructure.llm.model_pool_runtime import (
    ModelPoolExhaustedError,
    ModelPoolRuntime,
)
from iag.infrastructure.llm.runtime_config import (
    DEFAULT_APPLICATION_ID,
    RuntimeConfig,
    RuntimeSnapshot,
)
from iag.stellaris.state.extract_game_state import load_save_metadata
from iag.stellaris.state.save_ingest import resolve_current_save
from iag.stellaris.state.world_snapshot import WorldSnapshot, WorldStateService

SPECIALIST_RUNTIME_PROTOCOL = """
# 专业 Application 工具协议

你运行在独立的专业会话中。只能处理系统提示规定的领域，不得代替其他
Application 做决策，也不得调用未列出的动作。玩家的明确要求优先于更早的临时
要求，但不能越过本地权限、最新存档、合法候选和执行通道的强制检查。

需要当前游戏事实时必须先调用读取工具。准备动作不等于执行成功；只有执行工具
明确返回成功，动作才可称为已获房主确认。工具失败后说明事实并停止，不得假装
成功，也不得在同一回合自行重试可能已经产生副作用的动作。

自主巡检可以分析、报告或执行本领域动作。只有执行工具出现在本轮工具列表中时
才允许执行；动作必须逐项串行完成。没有必要动作时直接给出简短结论，不要为了
使用工具或消耗额度而制造任务。

不要展示隐藏思维过程。最终回复使用简洁中文，区分已观察、已准备、已确认和
仍待新存档核验的事实。
""".strip()

PLAN_RUNTIME_PROTOCOL = """
# 持续计划书

收到需要跨多个存档持续执行的目标时，先读取本领域事实，再用计划工具建立或局部
修改计划书。计划应记录层级目标、依赖、模型自行确定的预期范围、停止和完成条件；
只有下一步工具及参数已经完全确定时才给节点附加 action。不要把计划退化为固定动作
队列。普通变化处于计划容差内时，本地系统会直接审查和推进，不会再次调用你。

条件中的 fact 必须来自 inspect_plan_facts 返回的稳定路径，不要猜字段。
本领域同时维护一本可包含多个根目标的活动计划书；已有活动计划时用
edit_application_plan 追加或修订，不要用 create_application_plan 覆盖。确定性
action 只允许 prepare_* 后接可选 execute_*；需新存档确认的动作使用
wait_for_conditions 并写明 completion_conditions。

局部异常回到模型时，输入只包含受影响节点和相关事实。此时优先调用
edit_application_plan 修补受影响部分，明确失效、保留、替换及恢复位置；不要重新读取
全局状态或重写仍有效的上层目标、已完成节点和无关分支。
""".strip()


class SpecialistConversationAgentError(RuntimeError):
    """A specialist Application cannot complete a persistent tool turn."""


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise TypeError("Tool arguments are not a JSON object.")
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as error:
        raise ValueError(f"Tool arguments are invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise TypeError("Tool arguments must decode to a JSON object.")
    return value


def _normalized_tool_calls(raw: Any, round_index: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for call_index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        function = function if isinstance(function, dict) else {}
        result.append(
            {
                "id": str(item.get("id") or f"iag_call_{round_index}_{call_index}"),
                "type": "function",
                "function": {
                    "name": str(function.get("name") or ""),
                    "arguments": function.get("arguments", "{}"),
                },
            }
        )
    return result


def _assistant_protocol_message(message: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content") or "",
    }
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        result["tool_calls"] = tool_calls
        if message.get("reasoning_content") is not None:
            result["reasoning_content"] = message.get("reasoning_content")
    if isinstance(message.get("anthropic_content"), list):
        result["anthropic_content"] = message["anthropic_content"]
    return result


def _supports_visible_delta_callback(function: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "visible_delta_callback"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _supports_keyword(function: Callable[..., Any], name: str) -> bool:
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _render_tool_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


class SpecialistConversationAgent:
    """Run one isolated Application model against one domain toolbox."""

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        store: ConversationStore,
        action_store: ConversationStore,
        *,
        application_id: str,
        display_name: str,
        role_prompt_path: Path,
        toolbox_factory: Callable[..., Any],
        execute_tool_names: Iterable[str],
        fact_state_keys: Iterable[str] = (),
        model_pool_runtime: ModelPoolRuntime | None = None,
        completion_fn: Callable[..., dict[str, Any]] = chat_completion_message,
        execution_lock: Any = None,
        plan_advisor: PlanExceptionAdvisor | None = None,
        world_state_service: WorldStateService | None = None,
        read_task_pool: ReadTaskPool | None = None,
    ) -> None:
        self.runtime_config = runtime_config
        self.store = store
        self.action_store = action_store
        self.application_id = application_id
        self.display_name = display_name
        self.role_prompt_path = role_prompt_path
        self.toolbox_factory = toolbox_factory
        self.execute_tool_names = frozenset(execute_tool_names)
        self.fact_state_keys = tuple(fact_state_keys)
        self.model_pool_runtime = model_pool_runtime
        self.completion_fn = completion_fn
        self.execution_lock = execution_lock
        self.world_state_service = world_state_service
        self.read_task_pool = read_task_pool
        self.plan_advisor = plan_advisor or PlanExceptionAdvisor(
            runtime_config,
            completion_fn=completion_fn,
        )

    def _runtime_snapshot(self) -> tuple[RuntimeSnapshot, str]:
        try:
            return self.runtime_config.snapshot(self.application_id), "dedicated"
        except KeyError:
            # Older configurations only contain the economy binding. Keep the
            # specialist usable without silently mutating the player's config.
            return self.runtime_config.snapshot(DEFAULT_APPLICATION_ID), "inherited"

    def _role_prompt(self) -> str:
        try:
            return self.role_prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as error:
            raise SpecialistConversationAgentError(
                f"Application prompt does not exist: {self.role_prompt_path}"
            ) from error

    def _localized_prompt(self) -> str:
        return (
            self._role_prompt()
            + "\n\n"
            + SPECIALIST_RUNTIME_PROTOCOL
            + "\n\n"
            + PLAN_RUNTIME_PROTOCOL
            + "\n\n本轮仅处理用户消息中的局部计划异常。不要重建整局态势、"
            "读取无关事实或改写未出现在局部上下文中的计划分支。"
        )

    def _prompt(
        self,
        config: dict[str, Any],
        plan_book: ApplicationPlanBook,
        world_snapshot: WorldSnapshot | None = None,
    ) -> str:
        role_prompt = self._role_prompt()

        game_date: str | None = None
        metadata = self.action_store.conversation_metadata()
        campaign_id = metadata.get("campaign_id")
        if campaign_id:
            try:
                save_path = (
                    world_snapshot.path
                    if world_snapshot is not None
                    else resolve_current_save(
                        config,
                        expected_campaign_id=str(campaign_id),
                    )
                )
                game_date = load_save_metadata(save_path).get("date")
            except Exception:  # noqa: BLE001 - prompt metadata is best-effort
                decade = self.action_store.get_state("decade_planning", {})
                if isinstance(decade, dict):
                    game_date = decade.get("last_game_date")
        strategy = public_strategy_state(
            self.action_store,
            game_date,
            renewal_lead_months=int(config.get("decade_plan_renewal_lead_months", 12)),
        )
        facts = {
            key: self.action_store.get_state(key, None) for key in self.fact_state_keys
        }
        plan = plan_book.current()
        plan_context = (
            {
                key: value
                for key, value in plan.items()
                if key not in {"runtime", "original_plan"}
            }
            if isinstance(plan, dict)
            else None
        )
        return (
            role_prompt
            + "\n\n"
            + SPECIALIST_RUNTIME_PROTOCOL
            + "\n\n"
            + PLAN_RUNTIME_PROTOCOL
            + "\n\n"
            + render_strategy_context(strategy)
            + "\n\n# 本领域机器事实\n\n"
            + json.dumps(facts, ensure_ascii=False, indent=2)
            + "\n\n# 本领域当前计划\n\n"
            + json.dumps(plan_context, ensure_ascii=False, indent=2)
            + "\n\n机器事实与后续新存档优先于模型自然语言自述。"
        )

    @staticmethod
    def _execution_fact(
        tool_name: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        fact = {
            "tool": tool_name,
            "run_id": result.get("run_id"),
            "action": result.get("action"),
            "success": result.get("success") is True,
        }
        for key in (
            "area",
            "technology_id",
            "fleet_id",
            "destination_system_id",
            "design_id",
            "design_name",
            "fleet_template_id",
            "confirmed_protocol_steps",
            "requested_protocol_steps",
        ):
            if key in result:
                fact[key] = result[key]
        return fact

    def run_turn(
        self,
        *,
        trigger: str,
        user_content: str | None = None,
        autonomy_mode: str = "paused",
        visible_event_callback: (Callable[[str, dict[str, Any]], None] | None) = None,
        audit_event_callback: (Callable[[dict[str, Any]], None] | None) = None,
        world_snapshot: WorldSnapshot | None = None,
    ) -> dict[str, Any]:
        if trigger not in {"chat", "manual_review", "autonomous"}:
            raise SpecialistConversationAgentError(f"Unsupported trigger: {trigger}")
        if autonomy_mode not in {"paused", "advisory", "execute"}:
            raise SpecialistConversationAgentError(
                f"Unsupported autonomy mode: {autonomy_mode}"
            )

        runtime, binding_mode = self._runtime_snapshot()
        config = runtime.settings
        allow_execute = trigger == "chat" or autonomy_mode == "execute"
        toolbox_kwargs: dict[str, Any] = {"allow_execute": allow_execute}
        if _supports_keyword(self.toolbox_factory, "world_snapshot"):
            toolbox_kwargs["world_snapshot"] = world_snapshot
        if _supports_keyword(self.toolbox_factory, "world_state_service"):
            toolbox_kwargs["world_state_service"] = self.world_state_service
        toolbox = self.toolbox_factory(
            config,
            self.action_store,
            **toolbox_kwargs,
        )
        plan_fact_provider = getattr(toolbox, "inspect", None)
        if not callable(plan_fact_provider):
            plan_fact_provider = lambda: {
                "application_id": self.application_id,
                "fact_provider_available": False,
            }
        plan_book = ApplicationPlanBook(
            self.store,
            self.application_id,
            plan_fact_provider,
        )
        tool_schemas = [*toolbox.schemas(), *plan_book.schemas()]
        if not tool_schemas:
            raise SpecialistConversationAgentError(
                f"{self.display_name}的实验工具尚未在前端启用。"
            )
        localized_plan_context: dict[str, Any] | None = None
        external_review_ids: list[str] = []
        if trigger == "autonomous":
            try:
                evaluation = plan_book.evaluate()
                decision = str(evaluation.get("decision") or "")
                if decision in {"continue", "execute"}:
                    advancement = plan_book.execute_due_action(
                        evaluation,
                        toolbox,
                        allow_execute=allow_execute,
                        execution_lock=self.execution_lock,
                    )
                    provisional_count = int(
                        advancement.get("executed") is True
                        and advancement.get("provisional") is True
                    )
                    confirmed_count = int(
                        advancement.get("executed") is True and provisional_count == 0
                    )
                    execution_facts = {"confirmed": [], "provisional": []}
                    if advancement.get("executed"):
                        action = evaluation.get("due_nodes", [{}])[0].get("action", {})
                        fact = self._execution_fact(
                            str(
                                action.get("execute_tool")
                                or action.get("prepare_tool")
                                or "application_plan"
                            ),
                            dict(advancement.get("result") or {}),
                        )
                        bucket = "provisional" if provisional_count else "confirmed"
                        execution_facts[bucket].append(fact)
                    if advancement.get("executed"):
                        if provisional_count:
                            summary = (
                                f"{self.display_name}已按计划提交节点 "
                                f"{advancement.get('node_id')}，正等待新存档确认；"
                                "未调用主模型。"
                            )
                        else:
                            summary = (
                                f"{self.display_name}已通过计划书审查并执行节点 "
                                f"{advancement.get('node_id')}；未调用主模型。"
                            )
                    else:
                        summary = (
                            f"{self.display_name}当前状态仍在计划允许范围内；"
                            "本轮无需调用主模型。"
                        )
                    self.store.append(
                        "system",
                        summary,
                        kind="plan_autonomy",
                        visible=True,
                        metadata={"application_id": self.application_id},
                    )
                    if audit_event_callback is not None:
                        audit_event_callback(
                            {
                                "tool": "application_plan",
                                "success": True,
                                "summary": summary,
                                "run_id": None,
                            }
                        )
                    autonomy_record = {
                        "finished_at": now_iso(),
                        "application_id": self.application_id,
                        "trigger": trigger,
                        "mode": autonomy_mode,
                        "plan_decision": advancement.get("decision"),
                        "executed_count": confirmed_count,
                        "provisional_count": provisional_count,
                    }
                    self.store.set_state("last_autonomy", autonomy_record)
                    return {
                        "schema": "iag.application_plan_turn.v1",
                        "application_id": self.application_id,
                        "application_display_name": self.display_name,
                        "trigger": trigger,
                        "finished_at": now_iso(),
                        "final_content": summary,
                        "plan_evaluation": evaluation,
                        "plan_advancement": advancement,
                        "tool_events": [],
                        "execution_facts": execution_facts,
                        "executed": bool(advancement.get("executed")),
                        "executed_count": confirmed_count,
                        "provisional_count": provisional_count,
                        "context": {"mode": "deterministic_plan"},
                    }
                if decision == "localized_anomaly":
                    adviser = self.plan_advisor.handle(evaluation, plan_book)
                    if adviser.get("decision") in {"continue", "patched"}:
                        action = (
                            "确认原计划继续"
                            if adviser.get("decision") == "continue"
                            else "完成局部计划修补"
                        )
                        summary = (
                            f"{self.display_name}快速参谋已{action}；主模型未调用。"
                        )
                        self.store.append(
                            "system",
                            summary,
                            kind="plan_adviser",
                            visible=True,
                            metadata={"application_id": self.application_id},
                        )
                        if audit_event_callback is not None:
                            audit_event_callback(
                                {
                                    "tool": "fast_plan_adviser",
                                    "success": True,
                                    "summary": summary,
                                    "run_id": None,
                                }
                            )
                        self.store.set_state(
                            "last_autonomy",
                            {
                                "finished_at": now_iso(),
                                "application_id": self.application_id,
                                "trigger": trigger,
                                "mode": autonomy_mode,
                                "plan_decision": adviser.get("decision"),
                                "executed_count": 0,
                                "provisional_count": 0,
                            },
                        )
                        return {
                            "schema": "iag.application_plan_turn.v1",
                            "application_id": self.application_id,
                            "application_display_name": self.display_name,
                            "trigger": trigger,
                            "finished_at": now_iso(),
                            "final_content": summary,
                            "plan_evaluation": evaluation,
                            "plan_adviser": adviser,
                            "tool_events": [],
                            "execution_facts": {
                                "confirmed": [],
                                "provisional": [],
                            },
                            "executed": False,
                            "executed_count": 0,
                            "provisional_count": 0,
                            "context": {"mode": "fast_plan_adviser"},
                        }
                    localized_plan_context = adviser.get("main_context")
                elif decision == "localized_model_decision":
                    external_review_ids = [
                        str(item.get("review_id") or "")
                        for item in evaluation.get("external_review_directives", [])
                        if isinstance(item, dict) and item.get("review_id")
                    ]
                    localized_plan_context = {
                        "schema": "iag.localized_plan_decision.v1",
                        **evaluation,
                    }
            except Exception as error:  # noqa: BLE001 - isolate domain failure
                localized_plan_context = {
                    "schema": "iag.localized_plan_runtime_error.v1",
                    "application_id": self.application_id,
                    "error": f"{type(error).__name__}: {error}",
                    "instruction": (
                        "只检查计划运行错误及当前计划；不要重读无关全局状态。"
                    ),
                }

        if not bool(config.get("tool_calling_enabled", True)):
            raise SpecialistConversationAgentError("当前模型配置已关闭工具调用。")

        if localized_plan_context is not None:
            tool_schemas = [
                schema
                for schema in tool_schemas
                if schema.get("function", {}).get("name") == "edit_application_plan"
            ]

        if self.model_pool_runtime is None:
            self.model_pool_runtime = ModelPoolRuntime(runtime.model_pool)
        else:
            self.model_pool_runtime.replace_pool(runtime.model_pool)
        try:
            endpoint = self.model_pool_runtime.context_endpoint(
                provider=TOOL_CALL_PROTOCOLS,
                require_tools=True,
            )
        except ModelPoolExhaustedError as error:
            raise SpecialistConversationAgentError(
                f"{self.display_name}没有可用且支持工具的模型端点。"
            ) from error

        if trigger == "chat":
            text = str(user_content or "").strip()
            if not text:
                raise SpecialistConversationAgentError("消息不能为空。")
            if len(text) > int(config.get("chat_message_max_chars", 12_000)):
                raise SpecialistConversationAgentError("消息超过本地长度限制。")
            message_id = self.store.append(
                "user",
                text,
                kind="operator_message",
                visible=True,
                metadata={"application_id": self.application_id},
            )
            if visible_event_callback is not None:
                visible_event_callback(
                    "user_message",
                    {
                        "id": message_id,
                        "role": "user",
                        "kind": "operator_message",
                        "content": text,
                    },
                )
        else:
            label = "玩家要求立即巡检" if trigger == "manual_review" else "后台自主巡检"
            trigger_content = (
                "[计划局部处理] 只处理下列异常或计划决策点，不要重新检查完整世界：\n"
                + json.dumps(
                    localized_plan_context,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if localized_plan_context is not None
                else f"[{label}] 请完成本轮{self.display_name}审计，只处理本领域。"
            )
            self.store.append(
                "user",
                trigger_content,
                kind="autonomy_trigger",
                visible=True,
                metadata={
                    "trigger": trigger,
                    "application_id": self.application_id,
                },
            )

        request_options = runtime.request_options

        def pooled_completion(
            _budget_endpoint: Any,
            messages: list[dict[str, Any]],
            *,
            request_options: dict[str, Any],
            tools: list[dict[str, Any]] | None,
        ) -> dict[str, Any]:
            assert self.model_pool_runtime is not None
            return self.model_pool_runtime.execute(
                lambda candidate: self.completion_fn(
                    candidate,
                    messages,
                    request_options=request_options,
                    tools=tools,
                ),
                provider=TOOL_CALL_PROTOCOLS,
                require_tools=True,
            )

        supports_visible_stream = _supports_visible_delta_callback(self.completion_fn)

        def visible_completion(
            messages: list[dict[str, Any]],
            *,
            round_index: int,
        ) -> dict[str, Any]:
            stream_started = False

            def visible_delta(delta: str) -> None:
                nonlocal stream_started
                if visible_event_callback is None or not delta:
                    return
                if not stream_started:
                    stream_started = True
                    visible_event_callback(
                        "assistant_started",
                        {"round_index": round_index},
                    )
                visible_event_callback(
                    "assistant_delta",
                    {"round_index": round_index, "delta": delta},
                )

            def operation(candidate: Any) -> dict[str, Any]:
                keyword_arguments: dict[str, Any] = {
                    "request_options": request_options,
                    "tools": tool_schemas,
                }
                if supports_visible_stream:
                    keyword_arguments["visible_delta_callback"] = visible_delta
                result = self.completion_fn(
                    candidate,
                    messages,
                    **keyword_arguments,
                )
                if not supports_visible_stream:
                    visible_delta(str(result.get("content") or ""))
                return result

            assert self.model_pool_runtime is not None
            return self.model_pool_runtime.execute(
                operation,
                provider=TOOL_CALL_PROTOCOLS,
                require_tools=True,
            )

        system_prompt = (
            self._localized_prompt()
            if localized_plan_context is not None
            else self._prompt(config, plan_book, world_snapshot)
        )
        if localized_plan_context is not None:
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "只处理以下局部计划上下文：\n"
                        + json.dumps(
                            localized_plan_context,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    ),
                },
            ]
            context_stats = {
                "schema": "iag.context_window.v1",
                "mode": "localized_plan_exception",
                "included_segments": 1,
                "omitted_segments": 0,
            }
        else:
            messages, context_stats = build_context_messages(
                self.store,
                config,
                endpoint=endpoint,
                request_options=request_options,
                system_prompt=system_prompt,
                tool_schemas=tool_schemas,
                completion_fn=pooled_completion,
                application_id=self.application_id,
            )

        maximum_rounds = max(
            1,
            min(int(config.get("tool_loop_max_rounds", 12)), 16),
        )
        final_content = ""
        tool_events: list[dict[str, Any]] = []
        confirmed: list[dict[str, Any]] = []
        provisional: list[dict[str, Any]] = []
        for round_index in range(maximum_rounds):
            assistant = visible_completion(messages, round_index=round_index)
            valid_calls = _normalized_tool_calls(
                assistant.get("tool_calls"),
                round_index,
            )
            assistant = dict(assistant)
            if valid_calls:
                assistant["tool_calls"] = valid_calls
            content = str(assistant.get("content") or "")
            reasoning_content = assistant.get("reasoning_content")
            assistant_metadata: dict[str, Any] = {
                "origin": "model",
                "application_id": self.application_id,
            }
            if isinstance(assistant.get("anthropic_content"), list):
                assistant_metadata["anthropic_content"] = assistant["anthropic_content"]
            assistant_message_id = self.store.append(
                "assistant",
                content,
                reasoning_content=(
                    str(reasoning_content) if reasoning_content is not None else None
                ),
                tool_calls=valid_calls or None,
                kind="tool_call" if valid_calls else "assistant_message",
                visible=bool(content),
                metadata=assistant_metadata,
            )
            if content and visible_event_callback is not None:
                visible_event_callback(
                    "assistant_final",
                    {
                        "id": assistant_message_id,
                        "role": "assistant",
                        "kind": ("tool_call" if valid_calls else "assistant_message"),
                        "content": content,
                        "round_index": round_index,
                    },
                )
            messages.append(_assistant_protocol_message(assistant))
            if not valid_calls:
                final_content = content.strip()
                break

            def dispatch_call(
                call: dict[str, Any],
                call_index: int,
                _round_index: int = round_index,
            ) -> tuple[str, str, bool, bool, dict[str, Any], str]:
                call_id = str(
                    call.get("id") or f"iag_call_{_round_index}_{call_index}"
                )
                function = call.get("function")
                function = function if isinstance(function, dict) else {}
                name = str(function.get("name") or "")
                dispatch_succeeded = True
                is_plan_tool = plan_book.handles(name)
                try:
                    arguments = _parse_arguments(function.get("arguments", "{}"))
                    if is_plan_tool:
                        result, summary = plan_book.dispatch(name, arguments)
                    else:
                        if not tool_is_read_only(name, toolbox):
                            assert_current = getattr(
                                toolbox,
                                "assert_world_snapshot_current",
                                None,
                            )
                            if callable(assert_current):
                                assert_current()
                        lock_context = (
                            self.execution_lock
                            if self.execution_lock is not None
                            and tool_requires_execution_lock(name)
                            else nullcontext()
                        )
                        with lock_context:
                            result, summary = toolbox.dispatch(name, arguments)
                except Exception as error:  # noqa: BLE001 - tool boundary
                    dispatch_succeeded = False
                    result = {
                        "schema": "iag.tool_error.v1",
                        "success": False,
                        "tool": name,
                        "error": f"{type(error).__name__}: {error}",
                    }
                    summary = f"工具 {name or 'unknown'} 被本地拒绝：{error}"
                return (
                    call_id,
                    name,
                    is_plan_tool,
                    dispatch_succeeded,
                    result,
                    summary,
                )

            def timed_out_call(
                call: dict[str, Any],
                call_index: int,
                error: BaseException,
                _round_index: int = round_index,
            ) -> tuple[str, str, bool, bool, dict[str, Any], str]:
                call_id = str(
                    call.get("id") or f"iag_call_{_round_index}_{call_index}"
                )
                function = call.get("function")
                function = function if isinstance(function, dict) else {}
                name = str(function.get("name") or "")
                return (
                    call_id,
                    name,
                    plan_book.handles(name),
                    False,
                    {
                        "schema": "iag.tool_error.v1",
                        "success": False,
                        "tool": name,
                        "error": f"{type(error).__name__}: {error}",
                    },
                    f"工具 {name or 'unknown'} 读取超时：{error}",
                )

            dispatched: list[
                tuple[str, str, bool, bool, dict[str, Any], str]
            ] = []
            call_index = 0
            while call_index < len(valid_calls):
                call = valid_calls[call_index]
                function = call.get("function")
                function = function if isinstance(function, dict) else {}
                name = str(function.get("name") or "")
                parallel_read = (
                    self.read_task_pool is not None
                    and not plan_book.handles(name)
                    and tool_is_read_only(name, toolbox)
                )
                if not parallel_read:
                    dispatched.append(dispatch_call(call, call_index))
                    call_index += 1
                    continue

                end = call_index
                while end < len(valid_calls):
                    next_function = valid_calls[end].get("function")
                    next_function = (
                        next_function if isinstance(next_function, dict) else {}
                    )
                    next_name = str(next_function.get("name") or "")
                    if plan_book.handles(next_name) or not tool_is_read_only(
                        next_name,
                        toolbox,
                    ):
                        break
                    end += 1
                batch_calls = valid_calls[call_index:end]
                base_index = call_index
                batch_results = self.read_task_pool.run(
                    [
                        ReadTask(
                            key=str(item.get("id") or offset),
                            fn=(
                                lambda item=item, offset=offset, base_index=base_index: dispatch_call(
                                    item,
                                    base_index + offset,
                                )
                            ),
                        )
                        for offset, item in enumerate(batch_calls)
                    ],
                    timeout_seconds=float(
                        config.get("read_task_timeout_seconds", 45.0)
                    ),
                )
                for offset, (item, value) in enumerate(
                    zip(batch_calls, batch_results)
                ):
                    if isinstance(value, BaseException):
                        dispatched.append(
                            timed_out_call(item, call_index + offset, value)
                        )
                    else:
                        dispatched.append(value)
                call_index = end

            for (
                call_id,
                name,
                is_plan_tool,
                dispatch_succeeded,
                result,
                summary,
            ) in dispatched:
                if not is_plan_tool and tool_requires_execution_lock(name):
                    plan_book.record_application_tool_event(
                        name,
                        result,
                        summary,
                    )
                success = dispatch_succeeded and result.get("success") is not False
                if name in self.execute_tool_names:
                    fact = self._execution_fact(name, result)
                    confirmed_steps = int(result.get("confirmed_protocol_steps") or 0)
                    if result.get("success") is True:
                        confirmed.append(fact)
                    elif confirmed_steps > 0:
                        provisional.append(fact)
                rendered = _render_tool_result(result)
                self.store.append(
                    "tool",
                    rendered,
                    tool_call_id=call_id,
                    tool_name=name,
                    kind="tool_result",
                    visible=True,
                    metadata={
                        "public_summary": summary,
                        "success": success,
                        "run_id": result.get("run_id"),
                        "application_id": self.application_id,
                    },
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": rendered,
                    }
                )
                event = {
                    "tool": name,
                    "success": success,
                    "summary": summary,
                    "run_id": result.get("run_id"),
                }
                tool_events.append(event)
                if audit_event_callback is not None:
                    audit_event_callback(dict(event))
        else:
            final_content = "本轮工具调用超过上限，已停止继续调用。"
            self.store.append(
                "assistant",
                final_content,
                kind="error",
                visible=True,
                metadata={"application_id": self.application_id},
            )

        if not final_content:
            final_content = "本轮已完成专业工具处理。"
            self.store.append(
                "assistant",
                final_content,
                kind="local_status",
                visible=True,
                metadata={"application_id": self.application_id},
            )
        if confirmed or provisional:
            lines = [f"{self.display_name}本地执行事实账本："]
            if confirmed:
                lines.append(f"- 本轮已确认 {len(confirmed)} 项。")
            if provisional:
                lines.append(
                    f"- 本轮有 {len(provisional)} 项只完成部分协议步骤，"
                    "必须等待新存档核验。"
                )
            lines.append("- 后续规划以机器记录和新存档为准。")
            self.store.append(
                "system",
                "\n".join(lines),
                kind="execution_fact_ledger",
                visible=True,
                metadata={
                    "application_id": self.application_id,
                    "confirmed": confirmed,
                    "provisional": provisional,
                    "success": not provisional,
                },
            )

        prepared = getattr(toolbox, "prepared", None)
        prepared_run_id = prepared.get("run_id") if isinstance(prepared, dict) else None
        autonomy_record = {
            "finished_at": now_iso(),
            "application_id": self.application_id,
            "trigger": trigger,
            "mode": autonomy_mode,
            "prepared_run_id": prepared_run_id,
            "executed_count": len(confirmed),
            "provisional_count": len(provisional),
        }
        if trigger in {"manual_review", "autonomous"}:
            self.store.set_state("last_autonomy", autonomy_record)
        plan_book.complete_external_reviews(external_review_ids)
        return {
            "schema": "iag.application_conversation_turn.v1",
            "application_id": self.application_id,
            "application_display_name": self.display_name,
            "model_profile_id": runtime.application_profile.profile_id,
            "model_binding_mode": binding_mode,
            "finished_at": now_iso(),
            "trigger": trigger,
            "final_content": final_content,
            "tool_events": tool_events,
            "execution_facts": {
                "confirmed": confirmed,
                "provisional": provisional,
            },
            "context": context_stats,
            "prepared_run_id": prepared_run_id,
            "executed": bool(confirmed or provisional),
            "executed_count": len(confirmed),
            "provisional_count": len(provisional),
        }
