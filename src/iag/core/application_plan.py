"""Executable, partially invalidatable plans for autonomous Applications."""

from __future__ import annotations

import copy
import json
import re
import threading
from collections.abc import Callable
from contextlib import nullcontext
from functools import wraps
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from iag.core.conversation_store import ConversationStore, now_iso

PLAN_STATE_PREFIX = "application_plan:"
PLAN_AUDIT_PREFIX = "application_plan_audit:"
PLAN_INBOX_PREFIX = "application_plan_inbox:"
PLAN_ARCHIVE_PREFIX = "application_plan_archive:"
PLAN_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
PLAN_TOOL_NAMES = frozenset(
    {
        "inspect_plan_facts",
        "inspect_application_plan",
        "create_application_plan",
        "edit_application_plan",
    }
)
_PLAN_LOCKS_GUARD = threading.Lock()
_PLAN_LOCKS: dict[tuple[str, str, str], threading.RLock] = {}


def _shared_plan_lock(
    store: ConversationStore,
    application_id: str,
) -> threading.RLock:
    key = (
        str(store.path.resolve()),
        str(store.conversation_id),
        str(application_id),
    )
    with _PLAN_LOCKS_GUARD:
        return _PLAN_LOCKS.setdefault(key, threading.RLock())


def _synchronized_plan(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        with self._plan_lock:
            return method(self, *args, **kwargs)

    return wrapped


def _execution_is_provisional(result: dict[str, Any]) -> bool:
    """Return whether a successful tool result still needs game-state confirmation."""
    if result.get("awaiting_fresh_save_confirmation") is True:
        return True
    if result.get("pending_save_confirmation") is True:
        return True
    if result.get("awaiting_save_confirmation") is True:
        return True
    if result.get("provisional_recorded") is True:
        return True
    confirmation_state = str(result.get("confirmation_state") or "").strip().lower()
    if confirmation_state in {
        "pending_save_confirmation",
        "provisional_pending_save",
        "awaiting_fresh_save",
    }:
        return True
    state = str(result.get("state") or "").strip().lower()
    if state in {
        "prepared",
        "pending",
        "provisional",
        "awaiting_save",
        "awaiting_confirmation",
    }:
        return True
    requested = result.get("requested_protocol_steps")
    confirmed = result.get("confirmed_protocol_steps")
    if isinstance(requested, int) and isinstance(confirmed, int):
        return confirmed < requested
    return False


def _provisional_resolution(
    facts: dict[str, Any],
    run_id: Any,
) -> tuple[str, str | None, Any]:
    """Find a save reconciliation for one previously submitted run."""
    selected = str(run_id or "").strip()
    if not selected:
        return "pending", None, None
    confirmations = {"confirmed_by_save", "confirmed_by_packet", "completed"}
    rejections = {"rejected_by_save", "rejected", "failed"}
    pending: tuple[str, str | None, Any] = ("pending", None, None)
    for path, value in facts.items():
        if not path.endswith("/run_id") or str(value or "") != selected:
            continue
        prefix = path.removesuffix("/run_id")
        for field in ("state", "confirmation_state"):
            state_path = f"{prefix}/{field}"
            actual = facts.get(state_path)
            normalized = str(actual or "").strip().lower()
            if normalized in rejections:
                return "rejected", state_path, actual
            if normalized in confirmations:
                return "confirmed", state_path, actual
            if normalized:
                pending = ("pending", state_path, actual)
    return pending


def tool_requires_execution_lock(tool_name: str) -> bool:
    """Serialize stateful preparation and game mutation across Applications."""
    return str(tool_name).startswith(("prepare_", "execute_", "record_"))


class ApplicationPlanError(RuntimeError):
    """A plan cannot be validated, evaluated, or advanced safely."""


class PlanCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fact: str = Field(min_length=1, max_length=240)
    operator: Literal[
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "between",
        "in",
        "not_in",
        "exists",
        "not_exists",
        "truthy",
        "falsy",
    ]
    value: Any = None
    tolerance: float | None = Field(default=None, ge=0)
    description: str = Field(default="", max_length=300)


class PlanAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action_id: str = Field(min_length=1, max_length=80)
    prepare_tool: str = Field(min_length=1, max_length=120)
    prepare_arguments: dict[str, Any] = Field(default_factory=dict)
    execute_tool: str | None = Field(default=None, max_length=120)
    execute_arguments: dict[str, Any] = Field(default_factory=dict)
    cadence: Literal["once", "each_state_change"] = "once"
    completion_mode: Literal["after_success", "wait_for_conditions"] = "after_success"

    @model_validator(mode="after")
    def validate_tool_sequence(self) -> PlanAction:
        if not self.prepare_tool.startswith("prepare_"):
            raise ValueError("A deterministic plan action must start with prepare_*.")
        if self.execute_tool is not None and not self.execute_tool.startswith(
            "execute_"
        ):
            raise ValueError("A deterministic plan action may only execute execute_*.")
        return self


class PlanNode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    node_id: str = Field(min_length=1, max_length=80)
    parent_id: str | None = Field(default=None, max_length=80)
    title: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=1200)
    status: Literal[
        "planned",
        "active",
        "completed",
        "blocked",
        "cancelled",
    ] = "planned"
    depends_on: list[str] = Field(default_factory=list, max_length=32)
    activation_conditions: list[PlanCondition] = Field(
        default_factory=list,
        max_length=32,
    )
    expectations: list[PlanCondition] = Field(default_factory=list, max_length=64)
    stop_conditions: list[PlanCondition] = Field(default_factory=list, max_length=32)
    completion_conditions: list[PlanCondition] = Field(
        default_factory=list,
        max_length=32,
    )
    action: PlanAction | None = None
    decision_policy: Literal["deterministic", "model_on_activation"] = "deterministic"
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_identifiers(self) -> PlanNode:
        identifiers = [self.node_id, *self.depends_on]
        if self.parent_id is not None:
            identifiers.append(self.parent_id)
        if any(not PLAN_NODE_ID_RE.fullmatch(item) for item in identifiers):
            raise ValueError("Plan node identifiers contain unsupported characters.")
        if self.node_id in self.depends_on or self.parent_id == self.node_id:
            raise ValueError("A plan node cannot depend on or parent itself.")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("Plan node dependencies must be unique.")
        return self


class PlanDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    plan_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=2000)
    nodes: list[PlanNode] = Field(min_length=1, max_length=256)
    domain_context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self) -> PlanDraft:
        if not PLAN_NODE_ID_RE.fullmatch(self.plan_id):
            raise ValueError("plan_id contains unsupported characters.")
        _validate_graph(self.nodes)
        return self


def _validate_graph(nodes: list[PlanNode]) -> None:
    by_id = {node.node_id: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("node_id must be unique inside one plan.")
    for node in nodes:
        references = [*node.depends_on]
        if node.parent_id is not None:
            references.append(node.parent_id)
        missing = [item for item in references if item not in by_id]
        if missing:
            raise ValueError(
                f"Plan node {node.node_id!r} references unknown nodes: {missing}."
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError("Plan hierarchy or dependencies contain a cycle.")
        if node_id in visited:
            return
        visiting.add(node_id)
        node = by_id[node_id]
        references = list(node.depends_on)
        if node.parent_id is not None:
            references.append(node.parent_id)
        for reference in references:
            visit(reference)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in by_id:
        visit(node_id)


def _escape_path(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _list_identity(value: dict[str, Any]) -> tuple[str, Any] | None:
    for key in (
        "node_id",
        "fleet_id",
        "fleet_template_id",
        "planet_id",
        "colony_id",
        "system_id",
        "technology_id",
        "area",
        "candidate_id",
        "design_id",
        "starbase_id",
        "country_id",
        "id",
    ):
        if key in value and isinstance(value[key], (str, int)):
            return key, value[key]
    return None


def flatten_plan_facts(
    value: Any,
    *,
    prefix: str = "",
    maximum_facts: int = 20_000,
) -> dict[str, Any]:
    """Flatten nested state into stable, dependency-addressable scalar facts."""
    result: dict[str, Any] = {}

    def visit(item: Any, path: str) -> None:
        if len(result) >= maximum_facts:
            return
        if isinstance(item, dict):
            if not item:
                result[path or "/"] = {}
                return
            for key in sorted(item, key=str):
                visit(item[key], f"{path}/{_escape_path(key)}")
            return
        if isinstance(item, list):
            if not item:
                result[path or "/"] = []
                return
            for index, child in enumerate(item):
                identity = _list_identity(child) if isinstance(child, dict) else None
                segment = (
                    f"{identity[0]}={_escape_path(identity[1])}"
                    if identity is not None
                    else str(index)
                )
                visit(child, f"{path}/{segment}")
            return
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[path or "/"] = item

    visit(value, prefix.rstrip("/"))
    return result


def _condition_result(
    condition: PlanCondition,
    facts: dict[str, Any],
) -> tuple[bool, Any]:
    exists = condition.fact in facts
    actual = facts.get(condition.fact)
    operator = condition.operator
    if operator == "exists":
        return exists, actual
    if operator == "not_exists":
        return not exists, actual
    if not exists:
        return False, None
    if operator == "truthy":
        return bool(actual), actual
    if operator == "falsy":
        return not bool(actual), actual

    expected = condition.value
    tolerance = float(condition.tolerance or 0.0)
    numeric = (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    )
    if operator == "eq":
        passed = (
            abs(float(actual) - float(expected)) <= tolerance
            if numeric
            else actual == expected
        )
    elif operator == "ne":
        passed = (
            abs(float(actual) - float(expected)) > tolerance
            if numeric
            else actual != expected
        )
    elif operator in {"gt", "gte", "lt", "lte"}:
        if not numeric:
            return False, actual
        actual_number = float(actual)
        expected_number = float(expected)
        if operator == "gt":
            passed = actual_number > expected_number - tolerance
        elif operator == "gte":
            passed = actual_number >= expected_number - tolerance
        elif operator == "lt":
            passed = actual_number < expected_number + tolerance
        else:
            passed = actual_number <= expected_number + tolerance
    elif operator == "between":
        if not isinstance(expected, list) or len(expected) != 2:
            return False, actual
        try:
            lower, upper = float(expected[0]), float(expected[1])
            number = float(actual)
        except (TypeError, ValueError):
            return False, actual
        passed = lower - tolerance <= number <= upper + tolerance
    elif operator in {"in", "not_in"}:
        if not isinstance(expected, list):
            return False, actual
        passed = actual in expected
        if operator == "not_in":
            passed = not passed
    else:  # pragma: no cover - exhaustive Literal guard
        passed = False
    return passed, actual


def _conditions_pass(
    conditions: list[PlanCondition],
    facts: dict[str, Any],
) -> bool:
    return all(_condition_result(condition, facts)[0] for condition in conditions)


def _waiver_matches(
    waiver: Any,
    facts: dict[str, Any],
    path: str,
    actual: Any,
) -> bool:
    if isinstance(waiver, dict) and set(waiver) >= {"exists", "value"}:
        return waiver == {"exists": path in facts, "value": actual}
    return path in facts and waiver == actual


def _substitute_prepare_values(value: Any, prepared: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$prepare."):
        current: Any = prepared
        for key in value.removeprefix("$prepare.").split("."):
            if not isinstance(current, dict) or key not in current:
                raise ApplicationPlanError(
                    f"Prepared result does not contain interpolation path {value!r}."
                )
            current = current[key]
        return copy.deepcopy(current)
    if isinstance(value, dict):
        return {
            key: _substitute_prepare_values(child, prepared)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_substitute_prepare_values(child, prepared) for child in value]
    return copy.deepcopy(value)


def _condition_tool_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": (
                    "inspect_plan_facts 返回的稳定事实路径，例如 "
                    "/fleets/fleet_id=42/power。"
                ),
            },
            "operator": {
                "type": "string",
                "enum": [
                    "eq",
                    "ne",
                    "gt",
                    "gte",
                    "lt",
                    "lte",
                    "between",
                    "in",
                    "not_in",
                    "exists",
                    "not_exists",
                    "truthy",
                    "falsy",
                ],
            },
            "value": {
                "description": (
                    "预期值；between 使用 [下限,上限]，in/not_in 使用数组。"
                )
            },
            "tolerance": {
                "type": "number",
                "minimum": 0,
                "description": "由制定计划的模型选择的数值容差。",
            },
            "description": {"type": "string", "maxLength": 300},
        },
        "required": ["fact", "operator"],
        "additionalProperties": False,
    }


def _action_tool_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action_id": {"type": "string"},
            "prepare_tool": {
                "type": "string",
                "description": "必须是本 Application 当前可用的 prepare_* 工具。",
            },
            "prepare_arguments": {"type": "object"},
            "execute_tool": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
            },
            "execute_arguments": {
                "type": "object",
                "description": (
                    "可用 $prepare.run_id 等 $prepare.<path> 引用准备结果。"
                ),
            },
            "cadence": {
                "type": "string",
                "enum": ["once", "each_state_change"],
            },
            "completion_mode": {
                "type": "string",
                "enum": ["after_success", "wait_for_conditions"],
                "description": (
                    "需新存档验证的长动作应使用 wait_for_conditions "
                    "并填写 completion_conditions。"
                ),
            },
        },
        "required": ["action_id", "prepare_tool"],
        "additionalProperties": False,
    }


def _node_tool_schema() -> dict[str, Any]:
    condition_array = {
        "type": "array",
        "items": _condition_tool_schema(),
        "maxItems": 64,
    }
    return {
        "type": "object",
        "properties": {
            "node_id": {"type": "string"},
            "parent_id": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
            },
            "title": {"type": "string"},
            "objective": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["planned", "active", "completed", "blocked", "cancelled"],
            },
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 32,
            },
            "activation_conditions": copy.deepcopy(condition_array),
            "expectations": copy.deepcopy(condition_array),
            "stop_conditions": copy.deepcopy(condition_array),
            "completion_conditions": copy.deepcopy(condition_array),
            "action": {
                "anyOf": [_action_tool_schema(), {"type": "null"}],
            },
            "decision_policy": {
                "type": "string",
                "enum": ["deterministic", "model_on_activation"],
            },
            "details": {"type": "object"},
        },
        "required": ["node_id", "title", "objective"],
        "additionalProperties": False,
    }


PLAN_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "inspect_plan_facts",
            "description": (
                "读取本 Application 可写入计划条件的稳定事实路径。可按前缀筛选；"
                "普通自主周期只会重新检查计划实际引用的路径。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prefixes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 20,
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_application_plan",
            "description": "读取本领域当前计划、局部运行状态和审计记录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "audit_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "include_archived": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_application_plan",
            "description": (
                "为持续任务创建层级计划。条件参数和容差由你根据任务决定；计划节点"
                "可以是目标、约束或决策点，只有下一步完全明确时才附加确定性动作。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "title": {"type": "string"},
                    "objective": {"type": "string"},
                    "nodes": {
                        "type": "array",
                        "items": _node_tool_schema(),
                        "minItems": 1,
                        "maxItems": 256,
                    },
                    "domain_context": {"type": "object"},
                    "reason": {"type": "string", "maxLength": 600},
                },
                "required": ["plan_id", "title", "objective", "nodes", "reason"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_application_plan",
            "description": (
                "局部修改当前计划。必须明确变化、失效、保留、替换及恢复位置；"
                "未提及的节点和已完成记录保持不变。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 1},
                    "observed_change": {"type": "string", "maxLength": 1000},
                    "reason": {"type": "string", "maxLength": 1000},
                    "invalidate_node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "preserve_node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "remove_node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "upsert_nodes": {
                        "type": "array",
                        "items": _node_tool_schema(),
                        "maxItems": 256,
                    },
                    "resume_from_node_id": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                    },
                    "plan_status": {
                        "type": "string",
                        "enum": ["active", "completed", "suspended", "cancelled"],
                    },
                },
                "required": [
                    "plan_id",
                    "expected_revision",
                    "observed_change",
                    "reason",
                    "invalidate_node_ids",
                    "preserve_node_ids",
                    "remove_node_ids",
                    "upsert_nodes",
                    "resume_from_node_id",
                ],
                "additionalProperties": False,
            },
        },
    },
]


class ApplicationPlanBook:
    """Persist and advance one Application-owned hierarchical plan."""

    def __init__(
        self,
        store: ConversationStore,
        application_id: str,
        fact_provider: Callable[[], dict[str, Any]],
    ) -> None:
        self.store = store
        self.application_id = str(application_id)
        self.fact_provider = fact_provider
        self._plan_lock = _shared_plan_lock(store, self.application_id)
        self.state_key = PLAN_STATE_PREFIX + self.application_id
        self.audit_key = PLAN_AUDIT_PREFIX + self.application_id
        self.inbox_key = PLAN_INBOX_PREFIX + self.application_id
        self.archive_key = PLAN_ARCHIVE_PREFIX + self.application_id

    @staticmethod
    def schemas() -> list[dict[str, Any]]:
        return copy.deepcopy(PLAN_TOOL_SCHEMAS)

    @staticmethod
    def handles(tool_name: str) -> bool:
        return tool_name in PLAN_TOOL_NAMES

    @_synchronized_plan
    def current(self) -> dict[str, Any] | None:
        value = self.store.get_state(self.state_key, None)
        return copy.deepcopy(value) if isinstance(value, dict) else None

    @_synchronized_plan
    def _audit(self, event: dict[str, Any]) -> None:
        rendered = {
            "recorded_at": now_iso(),
            "application_id": self.application_id,
            **copy.deepcopy(event),
        }
        history = self.store.get_state(self.audit_key, [])
        history = list(history) if isinstance(history, list) else []
        history.append(rendered)
        self.store.set_state(self.audit_key, history[-500:])
        self.store.append(
            "system",
            json.dumps(rendered, ensure_ascii=False, separators=(",", ":")),
            kind="application_plan_audit",
            visible=False,
            metadata={"application_id": self.application_id},
        )

    @_synchronized_plan
    def _save(self, plan: dict[str, Any]) -> None:
        plan["updated_at"] = now_iso()
        self.store.set_state(self.state_key, plan)

    def _raw_facts(self) -> dict[str, Any]:
        raw = self.fact_provider()
        if not isinstance(raw, dict):
            raise ApplicationPlanError("Application fact provider returned no object.")
        return flatten_plan_facts(raw)

    def inspect_facts(self, arguments: dict[str, Any]) -> dict[str, Any]:
        facts = self._raw_facts()
        prefixes = arguments.get("prefixes", [])
        if not isinstance(prefixes, list):
            raise ApplicationPlanError("prefixes must be an array.")
        selected = {
            path: value
            for path, value in facts.items()
            if not prefixes or any(path.startswith(str(prefix)) for prefix in prefixes)
        }
        limit = max(1, min(int(arguments.get("limit", 500)), 2000))
        limited = dict(sorted(selected.items())[:limit])
        return {
            "schema": "iag.application_plan_facts.v1",
            "application_id": self.application_id,
            "facts": limited,
            "returned_count": len(limited),
            "matching_count": len(selected),
            "truncated": len(limited) < len(selected),
        }

    def inspect(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        options = arguments or {}
        audit_limit = max(1, min(int(options.get("audit_limit", 30)), 100))
        history = self.store.get_state(self.audit_key, [])
        archived = self.store.get_state(self.archive_key, [])
        archived = list(archived) if isinstance(archived, list) else []
        archive_summaries = [
            {
                "plan_id": item.get("plan", {}).get("plan_id"),
                "title": item.get("plan", {}).get("title"),
                "status": item.get("plan", {}).get("status"),
                "revision": item.get("plan", {}).get("revision"),
                "archived_at": item.get("archived_at"),
                "reason": item.get("reason"),
                "superseded_by_plan_id": item.get("superseded_by_plan_id"),
            }
            for item in archived[-20:]
            if isinstance(item, dict) and isinstance(item.get("plan"), dict)
        ]
        return {
            "schema": "iag.application_plan_book.v1",
            "application_id": self.application_id,
            "plan": self.current(),
            "pending_external_reviews": self.pending_external_reviews(),
            "recent_audit": (
                list(history[-audit_limit:]) if isinstance(history, list) else []
            ),
            "archived_plan_summaries": archive_summaries,
            "archived_plans": (
                copy.deepcopy(archived[-5:])
                if options.get("include_archived") is True
                else []
            ),
        }

    def pending_external_reviews(self) -> list[dict[str, Any]]:
        value = self.store.get_state(self.inbox_key, [])
        return copy.deepcopy(list(value)) if isinstance(value, list) else []

    @_synchronized_plan
    def queue_external_review(self, directive: dict[str, Any]) -> None:
        """Queue advice without permitting another Application to edit this plan."""
        review_id = str(directive.get("review_id") or "").strip()
        if not review_id:
            raise ApplicationPlanError("External plan review requires review_id.")
        if str(directive.get("application_id") or "") != self.application_id:
            raise ApplicationPlanError(
                "External plan review targets another Application."
            )
        inbox = self.pending_external_reviews()
        if any(str(item.get("review_id") or "") == review_id for item in inbox):
            return
        queued = {**copy.deepcopy(directive), "queued_at": now_iso()}
        inbox.append(queued)
        self.store.set_state(self.inbox_key, inbox[-20:])
        self._audit(
            {
                "event": "external_review_queued",
                "review_id": review_id,
                "reason": directive.get("reason"),
                "affected_node_ids": directive.get("affected_node_ids", []),
            }
        )

    @_synchronized_plan
    def complete_external_reviews(self, review_ids: list[str]) -> None:
        selected = {str(item) for item in review_ids if str(item)}
        if not selected:
            return
        inbox = self.pending_external_reviews()
        remaining = [
            item for item in inbox if str(item.get("review_id") or "") not in selected
        ]
        completed = sorted(
            selected & {str(item.get("review_id") or "") for item in inbox}
        )
        if not completed:
            return
        self.store.set_state(self.inbox_key, remaining)
        self._audit(
            {
                "event": "external_review_completed",
                "review_ids": completed,
            }
        )

    @_synchronized_plan
    def create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        reason = str(arguments.get("reason") or "").strip()
        if not reason:
            raise ApplicationPlanError("Creating a plan requires a reason.")
        draft = PlanDraft.model_validate(
            {key: value for key, value in arguments.items() if key != "reason"}
        )
        previous = self.current()
        if previous is not None and previous.get("status") in {
            "active",
            "suspended",
        }:
            raise ApplicationPlanError(
                "An active plan already exists; edit it instead of replacing it."
            )
        if previous is not None:
            archived = self.store.get_state(self.archive_key, [])
            archived = list(archived) if isinstance(archived, list) else []
            archived.append(
                {
                    "archived_at": now_iso(),
                    "reason": reason,
                    "superseded_by_plan_id": draft.plan_id,
                    "plan": copy.deepcopy(previous),
                }
            )
            self.store.set_state(self.archive_key, archived[-50:])
        timestamp = now_iso()
        document = draft.model_dump(mode="python")
        nodes = document["nodes"]
        plan = {
            "schema": "iag.application_plan.v1",
            "application_id": self.application_id,
            **document,
            "status": "active",
            "revision": 1,
            "created_at": timestamp,
            "updated_at": timestamp,
            "resume_from_node_id": None,
            "runtime": {"nodes": {}, "last_facts": {}},
            "original_plan": copy.deepcopy(document),
        }
        self._save(plan)
        self._audit(
            {
                "event": "plan_created",
                "plan_id": draft.plan_id,
                "revision": 1,
                "reason": reason,
                "created_nodes": [node["node_id"] for node in nodes],
            }
        )
        return self.inspect()

    @staticmethod
    def _dependents(nodes: dict[str, dict[str, Any]], seeds: set[str]) -> set[str]:
        affected = set(seeds)
        changed = True
        while changed:
            changed = False
            for node_id, node in nodes.items():
                references = set(node.get("depends_on", []))
                parent_id = node.get("parent_id")
                if parent_id:
                    references.add(str(parent_id))
                if node_id not in affected and references.intersection(affected):
                    affected.add(node_id)
                    changed = True
        return affected

    @_synchronized_plan
    def edit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        plan = self.current()
        if plan is None:
            raise ApplicationPlanError("This Application has no current plan.")
        if str(arguments.get("plan_id") or "") != str(plan["plan_id"]):
            raise ApplicationPlanError("plan_id does not match the current plan.")
        if int(arguments.get("expected_revision") or 0) != int(plan["revision"]):
            raise ApplicationPlanError(
                "Plan revision changed; inspect it before editing."
            )
        observed_change = str(arguments.get("observed_change") or "").strip()
        reason = str(arguments.get("reason") or "").strip()
        if not observed_change or not reason:
            raise ApplicationPlanError(
                "Plan edits require both observed_change and reason."
            )

        invalidate = {str(item) for item in arguments.get("invalidate_node_ids", [])}
        preserve = {str(item) for item in arguments.get("preserve_node_ids", [])}
        remove = {str(item) for item in arguments.get("remove_node_ids", [])}
        if invalidate.intersection(preserve) or remove.intersection(preserve):
            raise ApplicationPlanError("A node cannot be both preserved and changed.")
        nodes = {str(item["node_id"]): dict(item) for item in plan["nodes"]}
        unknown = (invalidate | preserve | remove) - set(nodes)
        if unknown:
            raise ApplicationPlanError(f"Unknown plan nodes: {sorted(unknown)}")
        invalidate = self._dependents(nodes, invalidate) - remove
        if invalidate.intersection(preserve):
            raise ApplicationPlanError(
                "A dependent node cannot be preserved while its prerequisite is invalidated."
            )
        before_nodes = {
            node_id: copy.deepcopy(nodes.get(node_id))
            for node_id in invalidate | remove
        }
        for node_id in invalidate:
            if nodes[node_id].get("status") != "completed":
                nodes[node_id]["status"] = "blocked"
        for node_id in remove:
            if nodes[node_id].get("status") == "completed":
                raise ApplicationPlanError("Completed plan nodes cannot be removed.")
            nodes.pop(node_id)

        upserted: list[str] = []
        for value in arguments.get("upsert_nodes", []):
            node = PlanNode.model_validate(value)
            previous = nodes.get(node.node_id)
            before_nodes.setdefault(node.node_id, copy.deepcopy(previous))
            if previous and previous.get("status") == "completed":
                raise ApplicationPlanError("Completed plan nodes are immutable.")
            nodes[node.node_id] = node.model_dump(mode="python")
            upserted.append(node.node_id)
        validated_nodes = [PlanNode.model_validate(item) for item in nodes.values()]
        _validate_graph(validated_nodes)

        resume = arguments.get("resume_from_node_id")
        if resume is not None and str(resume) not in nodes:
            raise ApplicationPlanError("resume_from_node_id is not in the edited plan.")
        runtime = plan.setdefault("runtime", {}).setdefault("nodes", {})
        for node_id in invalidate | remove | set(upserted):
            runtime.pop(node_id, None)
        plan["nodes"] = [node.model_dump(mode="python") for node in validated_nodes]
        after_nodes = {
            node_id: copy.deepcopy(nodes.get(node_id))
            for node_id in set(before_nodes) | set(upserted)
        }
        plan.setdefault("runtime", {}).pop("invalidated_node_ids", None)
        plan["revision"] = int(plan["revision"]) + 1
        plan["resume_from_node_id"] = resume
        if arguments.get("plan_status"):
            plan["status"] = str(arguments["plan_status"])
        self._save(plan)
        self._audit(
            {
                "event": "plan_edited",
                "plan_id": plan["plan_id"],
                "revision": plan["revision"],
                "observed_change": observed_change,
                "reason": reason,
                "invalidated_nodes": sorted(invalidate),
                "preserved_nodes": sorted(preserve),
                "removed_nodes": sorted(remove),
                "upserted_nodes": upserted,
                "before_nodes": before_nodes,
                "after_nodes": after_nodes,
                "resume_from_node_id": resume,
            }
        )
        return self.inspect()

    def dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        if name == "inspect_plan_facts":
            result = self.inspect_facts(arguments)
            return result, f"已读取 {result['returned_count']} 条计划事实。"
        if name == "inspect_application_plan":
            result = self.inspect(arguments)
            return result, "已读取本领域计划书。"
        if name == "create_application_plan":
            result = self.create(arguments)
            return result, "本领域计划书已创建并进入执行状态。"
        if name == "edit_application_plan":
            result = self.edit(arguments)
            return result, "本领域计划书已完成局部修改。"
        raise ApplicationPlanError(f"Unknown plan tool: {name}")

    @_synchronized_plan
    def record_application_tool_event(
        self,
        tool_name: str,
        result: dict[str, Any],
        summary: str,
    ) -> None:
        """Attach actual stateful tool outcomes to the durable plan audit."""
        plan = self.current()
        if plan is None:
            return
        related_nodes = [
            str(node["node_id"])
            for node in plan.get("nodes", [])
            if isinstance(node.get("action"), dict)
            and tool_name
            in {
                node["action"].get("prepare_tool"),
                node["action"].get("execute_tool"),
            }
        ]
        self._audit(
            {
                "event": "application_tool_observed",
                "plan_id": plan.get("plan_id"),
                "tool": str(tool_name),
                "related_node_ids": related_nodes,
                "success": result.get("success") is not False,
                "summary": str(summary),
                "run_id": result.get("run_id"),
                "action": result.get("action"),
                "result": copy.deepcopy(result),
            }
        )

    @_synchronized_plan
    def accept_localized_anomaly(
        self,
        evaluation: dict[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Waive exactly the observed values until one of them changes again."""
        plan = self.current()
        if plan is None or plan.get("plan_id") != evaluation.get("plan_id"):
            raise ApplicationPlanError("The localized exception is no longer current.")
        if any(
            failure.get("kind") == "provisional_execution_rejected"
            for anomaly in evaluation.get("anomalies", [])
            for failure in anomaly.get("failures", [])
        ):
            raise ApplicationPlanError(
                "A save-rejected execution cannot be waived as successful."
            )
        runtime = plan.setdefault("runtime", {})
        waivers = runtime.setdefault("waivers", {})
        accepted: list[str] = []
        for anomaly in evaluation.get("anomalies", []):
            node_id = str(anomaly.get("node_id") or "")
            for failure in anomaly.get("failures", []):
                condition = failure.get("condition", {})
                fact = str(condition.get("fact") or "")
                kind = str(failure.get("kind") or "")
                key = f"{node_id}|{kind}|{fact}"
                waivers[key] = {
                    "exists": failure.get("exists") is True,
                    "value": copy.deepcopy(failure.get("actual")),
                }
                accepted.append(key)
        runtime.pop("invalidated_node_ids", None)
        runtime["last_anomaly_resolution"] = {
            "decision": "continue",
            "reason": str(reason),
            "accepted_failures": accepted,
            "resolved_at": now_iso(),
        }
        self._save(plan)
        self._audit(
            {
                "event": "localized_anomaly_accepted",
                "plan_id": plan["plan_id"],
                "reason": str(reason),
                "accepted_failures": accepted,
            }
        )
        return self.inspect()

    @_synchronized_plan
    def evaluate(self) -> dict[str, Any]:
        plan = self.current()
        external_reviews = self.pending_external_reviews()
        if external_reviews:
            nodes = {
                str(item.get("node_id") or ""): item
                for item in (plan or {}).get("nodes", [])
                if isinstance(item, dict) and item.get("node_id")
            }
            affected_ids = {
                str(node_id)
                for review in external_reviews
                if isinstance(review, dict)
                for node_id in review.get("affected_node_ids", [])
            }
            runtime = (plan or {}).get("runtime", {})
            last_facts = (
                runtime.get("last_facts", {}) if isinstance(runtime, dict) else {}
            )
            return {
                "decision": "localized_model_decision",
                "reason": "external_joint_review",
                "plan_id": (plan or {}).get("plan_id"),
                "revision": (plan or {}).get("revision"),
                "external_review_directives": external_reviews,
                "affected_nodes": [
                    copy.deepcopy(nodes[node_id])
                    for node_id in sorted(affected_ids & set(nodes))
                ],
                "preserved_node_ids": sorted(set(nodes) - affected_ids),
                "relevant_facts": copy.deepcopy(last_facts),
            }
        if plan is None or plan.get("status") != "active":
            return {"decision": "no_active_plan", "plan": plan}
        facts = self._raw_facts()
        runtime = plan.setdefault("runtime", {})
        previous = runtime.get("last_facts", {})
        previous = previous if isinstance(previous, dict) else {}
        previous_presence = {
            str(item) for item in runtime.get("last_fact_presence", [])
        }
        nodes = {str(item["node_id"]): item for item in plan["nodes"]}
        watched = {
            condition["fact"]
            for node in nodes.values()
            for group in (
                "activation_conditions",
                "expectations",
                "stop_conditions",
                "completion_conditions",
            )
            for condition in node.get(group, [])
        }
        current = {path: facts.get(path) for path in watched}
        current_presence = {path for path in watched if path in facts}
        changed_facts = {
            path
            for path in watched
            if path not in previous
            or previous.get(path) != current.get(path)
            or (path in previous_presence) != (path in current_presence)
        }
        first_evaluation = not bool(runtime.get("evaluated_at"))
        runtime_nodes = runtime.setdefault("nodes", {})
        provisional_nodes = {
            node_id
            for node_id, state in runtime_nodes.items()
            if isinstance(state, dict) and state.get("provisional") is True
        }
        directly_affected = {
            node_id
            for node_id, node in nodes.items()
            if first_evaluation
            or any(
                condition.get("fact") in changed_facts
                for group in (
                    "activation_conditions",
                    "expectations",
                    "stop_conditions",
                    "completion_conditions",
                )
                for condition in node.get(group, [])
            )
        } | provisional_nodes
        affected = self._dependents(nodes, directly_affected)
        anomalies: list[dict[str, Any]] = []
        completed: list[str] = []
        waivers = runtime.setdefault("waivers", {})
        dependencies_complete = {
            node_id: all(
                nodes[dependency].get("status") == "completed"
                for dependency in node.get("depends_on", [])
            )
            for node_id, node in nodes.items()
        }
        for node_id in sorted(affected):
            node = nodes[node_id]
            status = node.get("status")
            if status in {"completed", "cancelled", "blocked"}:
                continue
            if status == "planned":
                if not dependencies_complete[node_id]:
                    continue
                activation = [
                    PlanCondition.model_validate(item)
                    for item in node.get("activation_conditions", [])
                ]
                if not _conditions_pass(activation, facts):
                    continue
                node["status"] = "active"
            node_runtime = runtime_nodes.setdefault(node_id, {})
            if node_runtime.get("provisional") is True:
                resolution, resolution_path, actual = _provisional_resolution(
                    facts,
                    node_runtime.get("result", {}).get("run_id"),
                )
                if resolution == "confirmed":
                    node_runtime["provisional"] = False
                    node_runtime["confirmation_fact"] = resolution_path
                    action_document = node.get("action")
                    if (
                        isinstance(action_document, dict)
                        and action_document.get("completion_mode") == "after_success"
                        and not node.get("completion_conditions")
                    ):
                        node["status"] = "completed"
                        node_runtime["completed_at"] = now_iso()
                        completed.append(node_id)
                        continue
                elif resolution == "rejected":
                    anomalies.append(
                        {
                            "node_id": node_id,
                            "failures": [
                                {
                                    "kind": "provisional_execution_rejected",
                                    "condition": {
                                        "fact": resolution_path or "/execution/state",
                                        "operator": "eq",
                                        "value": "confirmed_by_save",
                                        "description": ("已提交动作必须由新存档确认。"),
                                    },
                                    "actual": actual,
                                    "exists": resolution_path in facts,
                                }
                            ],
                        }
                    )
                    continue
            expectations = [
                PlanCondition.model_validate(item)
                for item in node.get("expectations", [])
            ]
            stops = [
                PlanCondition.model_validate(item)
                for item in node.get("stop_conditions", [])
            ]
            failures = []
            for condition in expectations:
                passed, actual = _condition_result(condition, facts)
                if not passed:
                    waiver_key = f"{node_id}|expectation_failed|{condition.fact}"
                    if _waiver_matches(
                        waivers.get(waiver_key),
                        facts,
                        condition.fact,
                        actual,
                    ):
                        continue
                    failures.append(
                        {
                            "kind": "expectation_failed",
                            "condition": condition.model_dump(mode="python"),
                            "actual": actual,
                            "exists": condition.fact in facts,
                        }
                    )
            for condition in stops:
                passed, actual = _condition_result(condition, facts)
                if passed:
                    waiver_key = f"{node_id}|stop_condition_met|{condition.fact}"
                    if _waiver_matches(
                        waivers.get(waiver_key),
                        facts,
                        condition.fact,
                        actual,
                    ):
                        continue
                    failures.append(
                        {
                            "kind": "stop_condition_met",
                            "condition": condition.model_dump(mode="python"),
                            "actual": actual,
                            "exists": condition.fact in facts,
                        }
                    )
            if failures:
                anomalies.append({"node_id": node_id, "failures": failures})
                continue
            completion = [
                PlanCondition.model_validate(item)
                for item in node.get("completion_conditions", [])
            ]
            if completion and _conditions_pass(completion, facts):
                node["status"] = "completed"
                runtime_nodes.setdefault(node_id, {})["completed_at"] = now_iso()
                completed.append(node_id)

        if anomalies:
            seeds = {item["node_id"] for item in anomalies}
            invalidated = self._dependents(nodes, seeds)
            plan["nodes"] = list(nodes.values())
            runtime["last_facts"] = current
            runtime["last_fact_presence"] = sorted(current_presence)
            runtime["evaluated_at"] = now_iso()
            runtime["last_anomaly"] = {
                "anomalies": anomalies,
                "invalidated_node_ids": sorted(invalidated),
                "preserved_node_ids": sorted(set(nodes) - invalidated),
                "changed_facts": sorted(changed_facts),
            }
            runtime["invalidated_node_ids"] = sorted(invalidated)
            self._save(plan)
            self._audit(
                {
                    "event": "plan_anomaly",
                    "plan_id": plan["plan_id"],
                    **runtime["last_anomaly"],
                }
            )
            return {
                "decision": "localized_anomaly",
                "plan_id": plan["plan_id"],
                "revision": plan["revision"],
                **runtime["last_anomaly"],
                "affected_nodes": [nodes[item] for item in sorted(invalidated)],
                "relevant_facts": {
                    path: facts.get(path)
                    for path in sorted(
                        changed_facts
                        | {
                            failure["condition"]["fact"]
                            for anomaly in anomalies
                            for failure in anomaly["failures"]
                        }
                    )
                },
            }

        due_nodes: list[dict[str, Any]] = []
        decision_nodes: list[dict[str, Any]] = []
        resume_from = str(plan.get("resume_from_node_id") or "")
        ordered_nodes = sorted(
            nodes.items(),
            key=lambda item: item[0] != resume_from,
        )
        next_fact_revision = int(runtime.get("fact_revision") or 0) + (
            1 if changed_facts else 0
        )
        for node_id, node in ordered_nodes:
            if node.get("status") not in {"planned", "active"}:
                continue
            if not dependencies_complete[node_id]:
                continue
            activation = [
                PlanCondition.model_validate(item)
                for item in node.get("activation_conditions", [])
            ]
            if not _conditions_pass(activation, facts):
                continue
            if node.get("decision_policy") == "model_on_activation":
                decision_nodes.append(node)
                continue
            action = node.get("action")
            if not isinstance(action, dict):
                continue
            action_state = runtime_nodes.get(node_id, {})
            already_ran = action_state.get("last_action_id") == action.get("action_id")
            changed_since_action = bool(changed_facts) and (
                action_state.get("last_fact_revision") != next_fact_revision
            )
            if action.get("cadence") == "once" and already_ran:
                continue
            if (
                action.get("cadence") == "each_state_change"
                and already_ran
                and not changed_since_action
            ):
                continue
            due_nodes.append(node)

        runtime["last_facts"] = current
        runtime["last_fact_presence"] = sorted(current_presence)
        runtime["evaluated_at"] = now_iso()
        runtime.pop("invalidated_node_ids", None)
        runtime["fact_revision"] = next_fact_revision
        plan["nodes"] = list(nodes.values())
        self._save(plan)
        if completed:
            self._audit(
                {
                    "event": "nodes_completed",
                    "plan_id": plan["plan_id"],
                    "completed_node_ids": completed,
                }
            )
        if decision_nodes:
            return {
                "decision": "localized_model_decision",
                "plan_id": plan["plan_id"],
                "revision": plan["revision"],
                "affected_nodes": decision_nodes,
                "changed_facts": sorted(changed_facts),
                "relevant_facts": {
                    path: facts.get(path) for path in sorted(changed_facts)
                },
            }
        return {
            "decision": "execute" if due_nodes else "continue",
            "plan_id": plan["plan_id"],
            "revision": plan["revision"],
            "due_nodes": due_nodes[:1],
            "changed_facts": sorted(changed_facts),
            "completed_node_ids": completed,
        }

    @_synchronized_plan
    def execute_due_action(
        self,
        evaluation: dict[str, Any],
        toolbox: Any,
        *,
        allow_execute: bool,
        execution_lock: Any = None,
    ) -> dict[str, Any]:
        due = evaluation.get("due_nodes", [])
        if evaluation.get("decision") != "execute" or not due:
            return {
                "decision": evaluation.get("decision", "continue"),
                "executed": False,
            }
        node = dict(due[0])
        plan_before_execution = self.current()
        if (
            plan_before_execution is None
            or plan_before_execution.get("plan_id") != evaluation.get("plan_id")
            or int(plan_before_execution.get("revision") or 0)
            != int(evaluation.get("revision") or 0)
        ):
            raise ApplicationPlanError(
                "Plan revision changed after evaluation; evaluate it again before execution."
            )
        action = PlanAction.model_validate(node.get("action"))
        if not allow_execute:
            return {
                "decision": "planned_action_requires_execute_mode",
                "executed": False,
                "node_id": node["node_id"],
                "action_id": action.action_id,
            }
        context = execution_lock if execution_lock is not None else nullcontext()
        with context:
            assert_current = getattr(
                toolbox,
                "assert_world_snapshot_current",
                None,
            )
            if callable(assert_current):
                assert_current()
            prepared, prepare_summary = toolbox.dispatch(
                action.prepare_tool,
                copy.deepcopy(action.prepare_arguments),
            )
            if prepared.get("success") is False:
                raise ApplicationPlanError(prepare_summary)
            executed = prepared
            execute_summary = ""
            if action.execute_tool:
                execute_arguments = _substitute_prepare_values(
                    action.execute_arguments,
                    prepared,
                )
                if not execute_arguments and prepared.get("run_id") is not None:
                    execute_arguments = {"run_id": prepared["run_id"]}
                if callable(assert_current):
                    assert_current()
                executed, execute_summary = toolbox.dispatch(
                    action.execute_tool,
                    execute_arguments,
                )
                if executed.get("success") is False and not _execution_is_provisional(
                    executed
                ):
                    raise ApplicationPlanError(execute_summary)

        plan = self.current()
        if plan is None:
            raise ApplicationPlanError("Plan disappeared during deterministic action.")
        runtime = plan.setdefault("runtime", {})
        node_runtime = runtime.setdefault("nodes", {}).setdefault(
            str(node["node_id"]),
            {},
        )
        node_runtime.update(
            {
                "last_action_id": action.action_id,
                "last_action_at": now_iso(),
                "last_fact_revision": int(runtime.get("fact_revision") or 0),
                "result": copy.deepcopy(executed),
            }
        )
        provisional = _execution_is_provisional(executed)
        node_runtime["provisional"] = provisional
        if provisional:
            for item in plan["nodes"]:
                if item["node_id"] == node["node_id"]:
                    item["status"] = "active"
                    break
        if action.completion_mode == "after_success" and not provisional:
            for item in plan["nodes"]:
                if item["node_id"] == node["node_id"]:
                    item["status"] = "completed"
                    node_runtime["completed_at"] = now_iso()
                    break
        self._save(plan)
        self._audit(
            {
                "event": "deterministic_action_executed",
                "plan_id": plan["plan_id"],
                "node_id": node["node_id"],
                "action_id": action.action_id,
                "prepare_tool": action.prepare_tool,
                "execute_tool": action.execute_tool,
                "prepare_summary": prepare_summary,
                "execute_summary": execute_summary,
                "provisional": provisional,
                "result": executed,
            }
        )
        return {
            "decision": "executed",
            "executed": True,
            "node_id": node["node_id"],
            "action_id": action.action_id,
            "prepare_summary": prepare_summary,
            "execute_summary": execute_summary,
            "provisional": provisional,
            "result": executed,
        }
