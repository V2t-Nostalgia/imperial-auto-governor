"""Strict model tools for synchronized-save research selection."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from iag.core.conversation_store import ConversationStore
from iag.stellaris.execution.session_proxy_controller import (
    SessionProxyController,
    SessionProxyError,
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
from iag.stellaris.state.save_ingest import resolve_current_save

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
    ) -> None:
        self.config = dict(config)
        self.store = store
        self.allow_execute = allow_execute
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
        return resolve_current_save(
            self.config,
            expected_campaign_id=str(campaign_id),
        )

    def _profile(self) -> tuple[Path, dict[str, Any]]:
        path = self._save_path()
        game_root = detect_game_root(self.config)

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
            raise TechnologyToolError(
                "该领域已有进行中的研究，玩家尚未启用中途换题。"
            )

        target = {
            "context_822c": int(profile["owner_country_id"]),
            "technology_id": technology_id,
        }
        sequence: list[dict[str, Any]] = []
        if current_id is not None:
            sequence.append(
                {
                    "action": "stop_research",
                    "target": {
                        "context_822c": int(profile["owner_country_id"]),
                        "technology_id": current_id,
                    },
                }
            )
        sequence.append({"action": "start_research", "target": target})
        run_id = "research_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "area": area,
            "technology_id": technology_id,
            "expected_current_technology_id": current_id,
            "reason": reason,
            "sequence": sequence,
            "source_save_sha256": sha256_file(path),
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

        _, profile = self._profile()
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

        confirmations: list[dict[str, Any]] = []
        completed_steps = 0
        controller = SessionProxyController(self.config)
        try:
            for index, step in enumerate(self.prepared["sequence"], start=1):
                result = controller.arm_and_wait(
                    action=str(step["action"]),
                    target=dict(step["target"]),
                    request_id=f"{run_id}_{index}",
                )
                confirmations.append(result)
                if result.get("outcome") != "confirmed":
                    raise TechnologyToolError(
                        str(result.get("error") or "科研命令未获房主权威确认。")
                    )
                completed_steps += 1
        except (SessionProxyError, TechnologyToolError) as error:
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
                field.get("current") is not None
                for field in result["fields"].values()
            )
            summary = f"已读取三系科研状态；{occupied} 个领域正在研究。"
        elif name == "prepare_research_selection":
            result = self.prepare(arguments)
            summary = (
                f"已准备 {result['area']} 科技 {result['technology_id']}；"
                "尚未发包。"
            )
        elif name == "execute_prepared_research":
            result = self.execute(arguments)
            summary = (
                f"{result['area']} 科技 {result['technology_id']} "
                "已获房主权威确认。"
            )
        else:
            raise TechnologyToolError(f"Unknown research tool: {name}")
        return result, summary
