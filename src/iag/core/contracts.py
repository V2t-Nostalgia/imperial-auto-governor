"""多 Agent 协作与执行审计的稳定数据契约。

所有模型输出、Application 消息和执行请求都必须在边界处通过这些 Pydantic
模型校验。这里描述“允许交换什么数据”，不负责调用模型、解析存档或操作游戏。
新增字段应优先保持向后兼容；不得用自由字典绕过权限和资源约束。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictContract(BaseModel):
    """拒绝未知字段的契约基类，防止 LLM 悄悄扩展执行协议。"""

    model_config = ConfigDict(extra="forbid")


class FrozenContract(StrictContract):
    """A versioned audit fact whose top-level fields cannot be reassigned."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthorityLevel(StrEnum):
    """玩家允许 Application 使用的最高自主权限。"""

    ADVISORY = "advisory"
    AUTONOMOUS = "autonomous"
    STRATEGIC = "strategic"
    PLAYER_RESERVED = "player_reserved"


class MessageKind(StrEnum):
    """第一版 Agent 总线允许出现的结构化消息类型。"""

    DOMAIN_ASSESSMENT = "domain_assessment"
    ACTION_INTENT = "action_intent"
    RESOURCE_REQUEST = "resource_request"
    ESCALATION = "escalation"
    EXECUTION_RESULT = "execution_result"
    POLICY_NOTICE = "policy_notice"


class Mandate(FrozenContract):
    """主总管授予一个专业 Application 的完整、不可变授权文档。"""

    schema_version: Literal["iag.mandate.v1"] = "iag.mandate.v1"
    mandate_id: str = Field(min_length=1, max_length=96)
    application_id: str = Field(min_length=1, max_length=96)
    issued_by: str = Field(min_length=1, max_length=96)
    issued_at: datetime
    effective_game_date: str | None = None
    expires_game_date: str | None = None
    supersedes_mandate_id: str | None = None
    objectives: list[str] = Field(default_factory=list, max_length=64)
    allowed_action_types: set[str] = Field(default_factory=set)
    authority_ceiling: AuthorityLevel = AuthorityLevel.ADVISORY
    resource_budgets: dict[str, float] = Field(default_factory=dict)
    reserve_floors: dict[str, float] = Field(default_factory=dict)
    escalation_conditions: list[str] = Field(default_factory=list, max_length=64)
    player_directive: str = Field(default="", max_length=12_000)
    config_revision: int = Field(ge=0)


class GameStateView(FrozenContract):
    """为某个 Application 裁剪后的只读游戏状态。"""

    schema_version: Literal["iag.game_state_view.v1"] = "iag.game_state_view.v1"
    campaign_id: str
    source_revision: int = Field(ge=0)
    game_date: str | None = None
    application_id: str
    strategic_summary: str = ""
    domain_state: dict[str, Any] = Field(default_factory=dict)
    cross_domain_constraints: dict[str, Any] = Field(default_factory=dict)


class AgentMessage(FrozenContract):
    """进入战役事件账本的公共消息头。"""

    message_id: str
    kind: MessageKind
    campaign_id: str
    application_id: str
    created_at: datetime
    source_revision: int = Field(ge=0)
    mandate_id: str | None = None
    summary_zh: str = Field(default="", max_length=4_000)


class DomainAssessment(AgentMessage):
    """专业 Agent 对本领域状态、风险和机会的结构化判断。"""

    kind: Literal[MessageKind.DOMAIN_ASSESSMENT] = MessageKind.DOMAIN_ASSESSMENT
    risks: list[dict[str, Any]] = Field(default_factory=list)
    opportunities: list[dict[str, Any]] = Field(default_factory=list)


class ActionIntent(AgentMessage):
    """Application 自主提交给确定性验证器和 Execution Broker 的动作意图。"""

    kind: Literal[MessageKind.ACTION_INTENT] = MessageKind.ACTION_INTENT
    action_type: str
    candidate_id: str
    target_id: str
    priority: int = Field(default=50, ge=0, le=100)
    estimated_cost: dict[str, float] = Field(default_factory=dict)
    authority_level: AuthorityLevel
    preconditions: dict[str, Any] = Field(default_factory=dict)


class ResourceRequest(AgentMessage):
    """Application 请求调整滚动预算时提交的消息。"""

    kind: Literal[MessageKind.RESOURCE_REQUEST] = MessageKind.RESOURCE_REQUEST
    requested_budget: dict[str, float]
    reason: str = Field(min_length=1, max_length=4_000)


class Escalation(AgentMessage):
    """子 Agent 无法在 Mandate 内安全决策时向主总管升级。"""

    kind: Literal[MessageKind.ESCALATION] = MessageKind.ESCALATION
    severity: Literal["low", "medium", "high", "critical"]
    reason_code: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(AgentMessage):
    """Execution Broker 写入的机器事实，不接受模型自行宣称成功。"""

    kind: Literal[MessageKind.EXECUTION_RESULT] = MessageKind.EXECUTION_RESULT
    action_intent_id: str
    status: Literal[
        "confirmed_by_packet",
        "confirmed_by_save",
        "provisional_pending_save",
        "rejected",
        "failed",
        "cancelled",
    ]
    run_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class PolicyNotice(AgentMessage):
    """通知专业 Agent 开始使用一份新 Mandate。"""

    kind: Literal[MessageKind.POLICY_NOTICE] = MessageKind.POLICY_NOTICE
    new_mandate_id: str
    superseded_mandate_id: str | None = None
