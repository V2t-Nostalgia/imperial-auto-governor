"""Infrequent, compressed cross-Application plan coordination."""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from iag.applications.economy_governance.agent_tools import game_month_index
from iag.core.application_plan import PLAN_STATE_PREFIX, ApplicationPlanBook
from iag.core.conversation_store import ConversationStore, now_iso
from iag.infrastructure.llm.model_client import chat_completion_message
from iag.infrastructure.llm.model_pool_runtime import ModelPoolRuntime
from iag.infrastructure.llm.runtime_config import RuntimeConfig

JOINT_REVIEW_STATE_KEY = "joint_plan_review_schedule"
JOINT_REVIEW_HISTORY_KEY = "joint_plan_review_history"

JOINT_REVIEW_SYSTEM_PROMPT = """
你是 IAG 的跨领域联合审查者。输入只包含舰队、经济和科研计划经压缩后的
目标、活动节点、已监视事实和异常。你只判断跨领域配合与冲突，不直接
修改任何计划，不要重新理解完整世界，不要生成具体游戏命令。

只输出一个 JSON 对象：
{
  "summary":"简短的全局协调结论",
  "directives":[
    {
      "application_id":"fleet_operations|economy_governance|research_strategy",
      "reason":"为什么本领域需要复查",
      "affected_node_ids":["受影响的现有节点 ID"],
      "instruction":"该领域模型应局部检查或调整什么",
      "dependencies":["相关的其他领域前提"]
    }
  ]
}

没有冲突时 directives 必须是空数组。不得为了寻找更优方案而改动正常计划。
不要输出 Markdown、隐藏推理或 JSON 之外的文字。
""".strip()


def _parse_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Joint plan reviewer did not return a JSON object.")
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise TypeError("Joint plan reviewer response must be a JSON object.")
    return value


def _compact_plan(
    store: ConversationStore,
    application_id: str,
    *,
    node_limit: int,
    fact_limit: int,
) -> dict[str, Any] | None:
    plan = store.get_state(PLAN_STATE_PREFIX + application_id, None)
    if not isinstance(plan, dict):
        return None
    nodes = [item for item in plan.get("nodes", []) if isinstance(item, dict)]
    nodes.sort(
        key=lambda item: (
            item.get("status") in {"completed", "cancelled"},
            str(item.get("node_id") or ""),
        )
    )
    compact_nodes = []
    for node in nodes[:node_limit]:
        compact_nodes.append(
            {
                key: copy.deepcopy(node.get(key))
                for key in (
                    "node_id",
                    "parent_id",
                    "title",
                    "objective",
                    "status",
                    "depends_on",
                    "expectations",
                    "stop_conditions",
                    "completion_conditions",
                    "decision_policy",
                )
                if key in node
            }
        )
    runtime = plan.get("runtime", {})
    runtime = runtime if isinstance(runtime, dict) else {}
    last_facts = runtime.get("last_facts", {})
    last_facts = last_facts if isinstance(last_facts, dict) else {}
    return {
        "application_id": application_id,
        "plan_id": plan.get("plan_id"),
        "revision": plan.get("revision"),
        "title": plan.get("title"),
        "objective": plan.get("objective"),
        "status": plan.get("status"),
        "nodes": compact_nodes,
        "node_count": len(nodes),
        "nodes_truncated": len(compact_nodes) < len(nodes),
        "watched_facts": dict(sorted(last_facts.items())[:fact_limit]),
        "last_anomaly": copy.deepcopy(runtime.get("last_anomaly")),
        "last_anomaly_resolution": copy.deepcopy(
            runtime.get("last_anomaly_resolution")
        ),
    }


def compressed_joint_context(
    application_stores: Mapping[str, ConversationStore],
    *,
    maximum_chars: int,
) -> dict[str, Any]:
    """Return bounded plan-owned facts, never a fresh complete world dump."""
    budgets = ((128, 300), (80, 180), (48, 100), (24, 50), (12, 20))
    for node_limit, fact_limit in budgets:
        domains = [
            compact
            for application_id, store in application_stores.items()
            if (
                compact := _compact_plan(
                    store,
                    application_id,
                    node_limit=node_limit,
                    fact_limit=fact_limit,
                )
            )
            is not None
        ]
        payload = {
            "schema": "iag.joint_plan_review_context.v1",
            "domains": domains,
        }
        if len(json.dumps(payload, ensure_ascii=False)) <= maximum_chars:
            return payload
    return {
        "schema": "iag.joint_plan_review_context.v1",
        "domains": [
            {
                "application_id": item["application_id"],
                "plan_id": item["plan_id"],
                "revision": item["revision"],
                "title": str(item.get("title") or "")[:160],
                "objective": str(item.get("objective") or "")[:600],
                "status": item.get("status"),
                "nodes": [],
                "context_truncated": True,
            }
            for item in (
                _compact_plan(
                    store,
                    application_id,
                    node_limit=0,
                    fact_limit=0,
                )
                for application_id, store in application_stores.items()
            )
            if item is not None
        ],
    }


class JointPlanReviewer:
    """Run a rare main-model review and enqueue domain-owned directives."""

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        *,
        completion_fn: Callable[..., dict[str, Any]] = chat_completion_message,
    ) -> None:
        self.runtime_config = runtime_config
        self.completion_fn = completion_fn

    @staticmethod
    def _advance_due(current: int, due: int, interval: int) -> int:
        next_due = due
        while next_due <= current:
            next_due += interval
        return next_due

    def run_if_due(
        self,
        *,
        game_date: Any,
        main_store: ConversationStore,
        application_stores: Mapping[str, ConversationStore],
    ) -> dict[str, Any]:
        runtime = self.runtime_config.snapshot()
        settings = runtime.settings
        if not bool(settings.get("joint_plan_review_enabled", True)):
            return {"state": "disabled", "ran": False}
        interval = max(
            1,
            min(int(settings.get("joint_plan_review_months", 12)), 120),
        )
        current = game_month_index(game_date)
        if current is None:
            return {"state": "game_date_unavailable", "ran": False}
        schedule = main_store.get_state(JOINT_REVIEW_STATE_KEY, {})
        schedule = schedule if isinstance(schedule, dict) else {}
        due = schedule.get("next_due_month_index")
        if due is None:
            schedule = {
                "schema": "iag.joint_plan_review_schedule.v1",
                "interval_months": interval,
                "next_due_month_index": current + interval,
                "initialized_at": now_iso(),
            }
            main_store.set_state(JOINT_REVIEW_STATE_KEY, schedule)
            return {"state": "scheduled", "ran": False, **schedule}
        due = int(due)
        if current < due:
            return {
                "state": "not_due",
                "ran": False,
                "current_month_index": current,
                "next_due_month_index": due,
            }

        maximum_chars = max(
            4_000,
            min(int(settings.get("joint_plan_review_max_chars", 24_000)), 60_000),
        )
        context = compressed_joint_context(
            application_stores,
            maximum_chars=maximum_chars,
        )
        if not context["domains"]:
            schedule["next_due_month_index"] = self._advance_due(
                current,
                due,
                interval,
            )
            schedule["last_state"] = "no_plans"
            main_store.set_state(JOINT_REVIEW_STATE_KEY, schedule)
            return {"state": "no_plans", "ran": False}

        try:
            model_runtime = ModelPoolRuntime(runtime.model_pool)
            response = model_runtime.execute(
                lambda endpoint: self.completion_fn(
                    endpoint,
                    [
                        {"role": "system", "content": JOINT_REVIEW_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "game_date": game_date,
                                    **context,
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    ],
                    request_options=runtime.request_options,
                    tools=None,
                )
            )
            answer = _parse_json_object(str(response.get("content") or ""))
            raw_directives = answer.get("directives", [])
            if not isinstance(raw_directives, list):
                raise TypeError("Joint review directives must be an array.")
            queued: list[dict[str, Any]] = []
            for index, raw in enumerate(raw_directives[:12]):
                if not isinstance(raw, dict):
                    raise TypeError("Joint review directive must be an object.")
                application_id = str(raw.get("application_id") or "")
                store = application_stores.get(application_id)
                if store is None:
                    raise ValueError(
                        f"Joint review targeted unknown Application {application_id!r}."
                    )
                plan = store.get_state(PLAN_STATE_PREFIX + application_id, {})
                known_nodes = {
                    str(item.get("node_id") or "")
                    for item in (plan if isinstance(plan, dict) else {}).get(
                        "nodes", []
                    )
                    if isinstance(item, dict) and item.get("node_id")
                }
                affected = [
                    str(item)
                    for item in raw.get("affected_node_ids", [])
                    if str(item) in known_nodes
                ]
                directive = {
                    "schema": "iag.joint_plan_review_directive.v1",
                    "review_id": (f"joint-{current}-{index}-{uuid.uuid4().hex[:8]}"),
                    "reviewed_game_date": game_date,
                    "application_id": application_id,
                    "reason": str(raw.get("reason") or "")[:1000],
                    "affected_node_ids": affected,
                    "instruction": str(raw.get("instruction") or "")[:2000],
                    "dependencies": [
                        str(item)[:500] for item in raw.get("dependencies", [])[:20]
                    ],
                }
                ApplicationPlanBook(
                    store,
                    application_id,
                    dict,
                ).queue_external_review(directive)
                queued.append(directive)

            summary = str(answer.get("summary") or "跨领域计划审查已完成。")[:2000]
            record = {
                "schema": "iag.joint_plan_review.v1",
                "reviewed_at": now_iso(),
                "game_date": game_date,
                "summary": summary,
                "queued_directives": queued,
            }
            history = main_store.get_state(JOINT_REVIEW_HISTORY_KEY, [])
            history = list(history) if isinstance(history, list) else []
            main_store.set_state(JOINT_REVIEW_HISTORY_KEY, (history + [record])[-50:])
            schedule.update(
                {
                    "interval_months": interval,
                    "next_due_month_index": self._advance_due(
                        current,
                        due,
                        interval,
                    ),
                    "last_review_game_date": game_date,
                    "last_reviewed_at": record["reviewed_at"],
                    "last_state": "completed",
                }
            )
            main_store.set_state(JOINT_REVIEW_STATE_KEY, schedule)
            public_summary = (
                f"年度跨领域计划审查已完成，向 {len(queued)} 个领域投递了局部复查。"
            )
            main_store.append(
                "system",
                public_summary,
                kind="joint_plan_review",
                visible=True,
                metadata={"success": True, "game_date": game_date},
            )
            return {"state": "completed", "ran": True, **record}
        except Exception as error:  # noqa: BLE001 - retry on a later game month
            detail = f"{type(error).__name__}: {error}"
            schedule.update(
                {
                    "interval_months": interval,
                    "next_due_month_index": current + 1,
                    "last_attempt_game_date": game_date,
                    "last_attempted_at": now_iso(),
                    "last_state": "failed",
                    "last_error": detail,
                }
            )
            main_store.set_state(JOINT_REVIEW_STATE_KEY, schedule)
            main_store.append(
                "system",
                f"年度跨领域计划审查未完成：{detail}",
                kind="joint_plan_review",
                visible=True,
                metadata={"success": False, "game_date": game_date},
            )
            return {"state": "failed", "ran": True, "error": detail}
