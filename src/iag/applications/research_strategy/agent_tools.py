"""Strict model tools for synchronized-save research selection."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from iag.core.conversation_store import ConversationStore
from iag.stellaris.execution import (
    ActionRisk,
    CandidateIdentity,
    ExecutionAuthority,
    ExecutionBroker,
    ExecutionBrokerError,
    OrderedActionSequence,
    PreparedAction,
    ResearchTarget,
    SourceSnapshot,
    build_execution_broker,
)
from iag.stellaris.game_knowledge import (
    detect_game_root,
    technology_rule,
)
from iag.stellaris.state.planet_profiles import load_gamestate
from iag.stellaris.state.research_profiles import (
    RESEARCH_AREAS,
    extract_research_profile,
)
from iag.stellaris.state.save_ingest import (
    read_manifest,
    resolve_current_save,
    save_manifest_revision,
)
from iag.stellaris.state.world_snapshot import WorldSnapshot, WorldStateService

INSPECT_RESEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "inspect_research_state",
        "description": (
            "读取最新同步存档中的三系当前研究、已完成科技和合法候选；"
            "本地原版文件只用于补充候选的规则证据。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

PREPARE_RESEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_research_selection",
        "description": (
            "准备一个科技选择。technology_id 必须属于最新存档中对应领域的"
            "legal_candidate_ids；进行中的研究默认禁止替换。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "area": {"type": "string", "enum": list(RESEARCH_AREAS)},
                "technology_id": {"type": "string", "maxLength": 160},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["area", "technology_id", "reason"],
            "additionalProperties": False,
        },
    },
}

EXECUTE_RESEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "execute_prepared_research",
        "description": (
            "执行本回合刚准备的科技选择。执行前重新读取存档并复查候选；"
            "切换研究时会分别确认停止旧项目和开始新项目。"
        ),
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
}


class TechnologyToolError(RuntimeError):
    """A research tool call failed a local save-backed invariant."""


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class TechnologyToolbox:
    """One model-turn view over research state and one prepared selection."""

    parallel_read_tools: ClassVar[frozenset[str]] = frozenset(
        {"inspect_research_state"}
    )

    tool_names: ClassVar[frozenset[str]] = frozenset(
        {
            "inspect_research_state",
            "prepare_research_selection",
            "execute_prepared_research",
        }
    )

    def __init__(
        self,
        config: dict[str, Any],
        store: ConversationStore,
        *,
        allow_execute: bool,
        world_snapshot: WorldSnapshot | None = None,
        world_state_service: WorldStateService | None = None,
        execution_broker: ExecutionBroker | None = None,
    ) -> None:
        self.config = dict(config)
        self.store = store
        self.allow_execute = allow_execute
        self.world_snapshot = world_snapshot
        self.world_state_service = world_state_service
        self.execution_broker = execution_broker
        self.enabled = bool(
            self.config.get("experimental_research_tools_enabled", False)
        )
        self.reselection_enabled = bool(
            self.config.get(
                "experimental_research_reselection_enabled",
                False,
            )
        )
        self.prepared: dict[str, Any] | None = None

    def _source_snapshot(
        self,
        path: Path,
        profile: dict[str, Any],
    ) -> SourceSnapshot:
        metadata = self.store.conversation_metadata()
        campaign_id = str(metadata.get("campaign_id") or "unbound")
        revision = 0
        save_hash = sha256_file(path)
        if self.world_snapshot is not None:
            identity = self.world_snapshot.identity
            campaign_id = identity.campaign_id or campaign_id
            revision = int(identity.revision or 0)
            save_hash = identity.sha256
        else:
            manifest = read_manifest(self.config) or {}
            manifest_path = str(manifest.get("stored_path") or "")
            if (
                manifest_path
                and Path(manifest_path).expanduser().resolve() == path.resolve()
            ):
                campaign_id = str(manifest.get("campaign_id") or campaign_id)
                revision = int(save_manifest_revision(self.config, manifest) or 0)
                save_hash = str(manifest.get("sha256") or save_hash)
        return SourceSnapshot(
            campaign_id=campaign_id,
            revision=revision,
            save_sha256=save_hash,
            game_date=(
                str(profile.get("game_date"))
                if profile.get("game_date") is not None
                else None
            ),
        )

    def _validate_source_snapshot(self, source: SourceSnapshot) -> None:
        self.assert_world_snapshot_current()
        path = self._save_path()
        if sha256_file(path) != source.save_sha256:
            raise TechnologyToolError(
                "科研命令绑定的同步存档已经变化，必须重新读取候选。"
            )
        manifest = read_manifest(self.config) or {}
        if (
            source.revision
            and int(save_manifest_revision(self.config, manifest) or 0)
            != source.revision
        ):
            raise TechnologyToolError("科研命令绑定的存档 revision 已经过时。")

    def _broker(self) -> ExecutionBroker:
        if self.execution_broker is None:
            self.execution_broker = build_execution_broker(
                self.config,
                application_action_types={
                    "research_strategy": frozenset({"start_research", "stop_research"})
                },
                snapshot_validator=self._validate_source_snapshot,
                candidate_validator=self._validate_execution_candidate,
            )
        return self.execution_broker

    def _validate_execution_candidate(self, action: PreparedAction) -> None:
        target = action.target
        if not isinstance(target, ResearchTarget):
            raise TechnologyToolError("科研执行 target 类型无效。")
        expected_candidate_id = f"research:{target.area}:{target.technology_id}"
        if action.candidate.candidate_id != expected_candidate_id:
            raise TechnologyToolError("科研候选身份与已验证 target 不一致。")
        _path, profile = self._profile()
        if int(profile["owner_country_id"]) != action.authority.actor_country_id:
            raise TechnologyToolError("科研动作 authority 不属于当前玩家国家。")
        field = profile["fields"][target.area]
        current = field.get("current")
        current_id = current.get("technology_id") if current else None
        if current_id != target.expected_current_technology_id:
            raise TechnologyToolError("科研候选验证期间当前项目已经变化。")
        if action.action_type == "start_research":
            if target.technology_id not in field["legal_candidate_ids"]:
                raise TechnologyToolError("科研候选已不在当前合法候选中。")
        elif action.action_type == "stop_research":
            if target.technology_id != current_id:
                raise TechnologyToolError("待停止科技不是当前研究项目。")
        else:
            raise TechnologyToolError("科研 Application 收到了非科研动作。")

    def schemas(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        schemas = [INSPECT_RESEARCH_TOOL, PREPARE_RESEARCH_TOOL]
        if self.allow_execute:
            schemas.append(EXECUTE_RESEARCH_TOOL)
        return schemas

    def _save_path(self) -> Path:
        campaign_id = self.store.conversation_metadata().get("campaign_id")
        if not campaign_id:
            raise TechnologyToolError("当前战役会话尚未绑定房主存档。")
        if self.world_snapshot is not None:
            return self.world_snapshot.path
        return resolve_current_save(
            self.config,
            expected_campaign_id=str(campaign_id),
        )

    def _pinned_snapshot(self) -> WorldSnapshot | None:
        if self.world_snapshot is not None:
            return self.world_snapshot
        if self.world_state_service is None:
            return None
        campaign_id = self.store.conversation_metadata().get("campaign_id")
        if not campaign_id:
            raise TechnologyToolError("当前战役会话尚未绑定房主存档。")
        self.world_snapshot = self.world_state_service.pin(
            self.config,
            expected_campaign_id=str(campaign_id),
        )
        return self.world_snapshot

    def assert_world_snapshot_current(self) -> None:
        snapshot = self.world_snapshot
        service = self.world_state_service
        if snapshot is None or service is None:
            return
        campaign_id = self.store.conversation_metadata().get("campaign_id")
        service.assert_current(
            snapshot,
            self.config,
            expected_campaign_id=(str(campaign_id) if campaign_id else None),
        )

    def _profile(self) -> tuple[Path, dict[str, Any]]:
        game_root = detect_game_root(self.config)

        snapshot = self._pinned_snapshot()
        if snapshot is not None:
            return snapshot.path, snapshot.research_profile(game_root=game_root)
        path = self._save_path()

        def area_for(technology_id: str) -> str | None:
            if game_root is None:
                return None
            rule = technology_rule(game_root, technology_id)
            return str(rule.get("area") or "") or None

        return path, extract_research_profile(
            load_gamestate(path),
            technology_area=area_for,
        )

    def inspect(self) -> dict[str, Any]:
        path, profile = self._profile()
        game_root = detect_game_root(self.config)
        fields: dict[str, dict[str, Any]] = {}
        for area, field in profile["fields"].items():
            candidates = []
            for technology_id in field["legal_candidate_ids"]:
                definition = (
                    technology_rule(game_root, technology_id)
                    if game_root is not None
                    else {"status": "game_root_unavailable"}
                )
                candidates.append(
                    {
                        "technology_id": technology_id,
                        "definition": definition,
                    }
                )
            fields[area] = {**field, "legal_candidates": candidates}
        return {
            **profile,
            "fields": fields,
            "source_save": str(path),
            "source_save_sha256": sha256_file(path),
            "protocol_state": "paired_non_host_samples_verified_experimental",
            "reselection_enabled": self.reselection_enabled,
        }

    def prepare(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise TechnologyToolError("玩家没有启用实验性科研工具。")
        area = str(arguments["area"])
        technology_id = str(arguments["technology_id"]).strip()
        reason = str(arguments["reason"]).strip()
        if area not in RESEARCH_AREAS:
            raise TechnologyToolError(f"未知科研领域：{area}。")
        path, profile = self._profile()
        field = profile["fields"][area]
        if field.get("auto_researching"):
            raise TechnologyToolError("该领域启用了游戏原生自动科研，拒绝与其竞争。")
        if technology_id not in field["legal_candidate_ids"]:
            raise TechnologyToolError(
                f"{technology_id} 不在最新存档的 {area} 合法候选中。"
            )
        current = field.get("current")
        current_id = current.get("technology_id") if current else None
        if current_id == technology_id:
            raise TechnologyToolError(f"{technology_id} 已经是该领域当前研究。")
        if current_id is not None and not self.reselection_enabled:
            raise TechnologyToolError("该领域已有进行中的研究，玩家尚未启用中途换题。")

        target = ResearchTarget(
            area=area,
            technology_id=technology_id,
            expected_current_technology_id=current_id,
        ).model_dump(mode="json")
        sequence: list[dict[str, Any]] = []
        if current_id is not None:
            sequence.append(
                {
                    "action": "stop_research",
                    "target": ResearchTarget(
                        area=area,
                        technology_id=current_id,
                        expected_current_technology_id=current_id,
                    ).model_dump(mode="json"),
                }
            )
        sequence.append({"action": "start_research", "target": target})
        run_id = "research_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        source_snapshot = self._source_snapshot(path, profile)
        self.prepared = {
            "run_id": run_id,
            "area": area,
            "technology_id": technology_id,
            "expected_current_technology_id": current_id,
            "reason": reason,
            "sequence": sequence,
            "source_snapshot": source_snapshot.model_dump(mode="json"),
            "source_save_sha256": source_snapshot.save_sha256,
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.allow_execute:
            raise TechnologyToolError("当前运行策略不允许自动执行。")
        run_id = str(arguments.get("run_id", ""))
        if self.prepared is None or run_id != self.prepared["run_id"]:
            raise TechnologyToolError("run_id 必须来自本回合刚准备的科研命令。")

        _path, profile = self._profile()
        area = str(self.prepared["area"])
        technology_id = str(self.prepared["technology_id"])
        field = profile["fields"][area]
        current = field.get("current")
        current_id = current.get("technology_id") if current else None
        expected = self.prepared["expected_current_technology_id"]
        if current_id != expected:
            raise TechnologyToolError(
                "准备后该领域当前研究已经变化，必须基于新存档重新规划。"
            )
        if technology_id not in field["legal_candidate_ids"]:
            raise TechnologyToolError("准备后的新存档不再提供该科技候选。")

        source_snapshot = SourceSnapshot.model_validate(
            self.prepared["source_snapshot"]
        )
        granted_actions = tuple(
            str(step["action"]) for step in self.prepared["sequence"]
        )
        allowed_risks = [ActionRisk.STATE_CHANGE]
        if "stop_research" in granted_actions:
            allowed_risks.append(ActionRisk.DESTRUCTIVE_STATE_CHANGE)
        authority = ExecutionAuthority(
            application_id="research_strategy",
            execution_authorized=self.allow_execute,
            granted_action_types=granted_actions,
            allowed_risk_classes=tuple(allowed_risks),
            actor_country_id=int(profile["owner_country_id"]),
        )
        prepared_actions: list[PreparedAction] = []
        for index, step in enumerate(self.prepared["sequence"], start=1):
            step_action = str(step["action"])
            semantic_target = ResearchTarget.model_validate(step["target"])
            candidate = CandidateIdentity.for_target(
                f"research:{area}:{semantic_target.technology_id}",
                semantic_target,
            )
            prepared_actions.append(
                PreparedAction(
                    request_id=f"{run_id}:{index}",
                    action_intent_id=run_id,
                    application_id="research_strategy",
                    action_type=step_action,
                    target=semantic_target,
                    candidate=candidate,
                    source_snapshot=source_snapshot,
                    authority=authority,
                )
            )

        confirmations: list[dict[str, Any]] = []
        broker_diagnostics: list[dict[str, Any]] = []
        completed_steps = 0
        try:
            sequence_result = self._broker().execute_sequence(
                OrderedActionSequence(
                    request_id=run_id,
                    actions=tuple(prepared_actions),
                )
            )
            broker_diagnostics = [
                item.model_dump(mode="json") for item in sequence_result.results
            ]
            confirmations = [
                {
                    "request_id": item.run_id,
                    "action": item.evidence.get("action_type"),
                    "status": item.status,
                }
                for item in sequence_result.results
            ]
            completed_steps = sum(
                item.status in {"confirmed_by_packet", "confirmed_by_save"}
                for item in sequence_result.results
            )
            if sequence_result.status not in {
                "confirmed_by_packet",
                "confirmed_by_save",
            }:
                raise TechnologyToolError(
                    sequence_result.error or "科研命令未获房主权威确认。"
                )
        except (ExecutionBrokerError, TechnologyToolError) as error:
            self.store.set_state(
                "last_research_execution_diagnostics",
                {
                    "run_id": run_id,
                    "broker_results": broker_diagnostics,
                    "recorded_at": now_iso(),
                },
            )
            self.store.set_state(
                "last_research_execution",
                {
                    "run_id": run_id,
                    "success": False,
                    "completed_steps": completed_steps,
                    "confirmations": confirmations,
                    "error": str(error),
                    "recorded_at": now_iso(),
                },
            )
            self.prepared = None
            raise TechnologyToolError(str(error)) from error

        result = {
            "schema": "iag.tool_result.research_execution.v1",
            "success": True,
            "run_id": run_id,
            "area": area,
            "technology_id": technology_id,
            "replaced_technology_id": expected,
            "confirmations": confirmations,
        }
        self.store.set_state(
            "last_research_execution_diagnostics",
            {
                "run_id": run_id,
                "broker_results": broker_diagnostics,
                "recorded_at": now_iso(),
            },
        )
        self.store.set_state(
            "last_research_execution",
            {**result, "recorded_at": now_iso()},
        )
        self.prepared = None
        return result

    def dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        if name == "inspect_research_state":
            result = self.inspect()
            occupied = sum(
                field.get("current") is not None for field in result["fields"].values()
            )
            summary = f"已读取三系科研状态；{occupied} 个领域正在研究。"
        elif name == "prepare_research_selection":
            result = self.prepare(arguments)
            summary = (
                f"已准备 {result['area']} 科技 {result['technology_id']}；尚未发包。"
            )
        elif name == "execute_prepared_research":
            result = self.execute(arguments)
            summary = (
                f"{result['area']} 科技 {result['technology_id']} 已获房主权威确认。"
            )
        else:
            raise TechnologyToolError(f"Unknown research tool: {name}")
        return result, summary
