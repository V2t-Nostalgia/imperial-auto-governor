"""Model tools for save-backed, player-authorized fleet movement."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from iag.core.conversation_store import ConversationStore
from iag.stellaris.execution.session_proxy_controller import (
    SessionProxyController,
    SessionProxyError,
)
from iag.stellaris.state.fleet_profiles import (
    extract_fleet_profiles,
    selected_move,
)
from iag.stellaris.state.planet_profiles import load_gamestate
from iag.stellaris.state.save_ingest import resolve_current_save


FLEET_PERMISSIONS_KEY = "fleet_permissions"

INSPECT_FLEETS_TOOL = {
    "type": "function",
    "function": {
        "name": "inspect_fleet_state",
        "description": (
            "读取最新同步存档中的全部玩家舰队、军力、位置、忙碌/交战/MIA 状态，"
            "以及玩家对每支舰队授予的移动和攻击权限。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

PREPARE_MOVE_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_fleet_move",
        "description": (
            "准备一条舰队移动命令。源舰队必须在最新存档中可用，且玩家已对该舰队"
            "开启移动权限；目标星系必须有存档可验证的 d32c 目标对象。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fleet_id": {"type": "integer"},
                "destination_system_id": {"type": "integer"},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["fleet_id", "destination_system_id", "reason"],
            "additionalProperties": False,
        },
    },
}

PREPARE_ATTACK_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_fleet_attack",
        "description": (
            "预检查舰队攻击意图。6b33 非房主请求尚缺成对在线样本，因此当前版本"
            "只会明确拒绝执行，不会构造攻击包。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fleet_id": {"type": "integer"},
                "target_fleet_id": {"type": "integer"},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["fleet_id", "target_fleet_id", "reason"],
            "additionalProperties": False,
        },
    },
}

EXECUTE_FLEET_TOOL = {
    "type": "function",
    "function": {
        "name": "execute_prepared_fleet_order",
        "description": (
            "执行本回合刚由 prepare_fleet_move 生成的命令。执行前会重新读取存档、"
            "复查逐舰队权限和可用状态，并要求会话代理已经锁定本局流量。"
        ),
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
}


class FleetToolError(RuntimeError):
    """A fleet tool call failed a local, save-backed invariant."""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_permissions(value: Any) -> dict[str, dict[str, bool]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, bool]] = {}
    for fleet_id, permission in value.items():
        if not isinstance(permission, dict):
            continue
        result[str(fleet_id)] = {
            "allow_move": bool(permission.get("allow_move", False)),
            "allow_attack": bool(permission.get("allow_attack", False)),
        }
    return result


class FleetToolbox:
    """One model-turn view over fleet state and one prepared order."""

    tool_names: ClassVar[frozenset[str]] = frozenset({
        "inspect_fleet_state",
        "prepare_fleet_move",
        "prepare_fleet_attack",
        "execute_prepared_fleet_order",
    })

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
            self.config.get("experimental_fleet_tools_enabled", False)
        )
        self.attack_enabled = bool(
            self.config.get("experimental_fleet_attack_enabled", False)
        )
        self.prepared: dict[str, Any] | None = None

    def schemas(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        value = [INSPECT_FLEETS_TOOL, PREPARE_MOVE_TOOL]
        if self.attack_enabled:
            value.append(PREPARE_ATTACK_TOOL)
        if self.allow_execute:
            value.append(EXECUTE_FLEET_TOOL)
        return value

    def _save_path(self) -> Path:
        campaign_id = self.store.conversation_metadata().get("campaign_id")
        if not campaign_id:
            raise FleetToolError("当前战役会话尚未绑定房主存档。")
        return resolve_current_save(
            self.config,
            expected_campaign_id=str(campaign_id),
        )

    def _profile(self) -> tuple[Path, dict[str, Any]]:
        path = self._save_path()
        return path, extract_fleet_profiles(load_gamestate(path))

    def inspect(self) -> dict[str, Any]:
        path, profile = self._profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        fleets: list[dict[str, Any]] = []
        for fleet in profile["fleets"]:
            if not fleet.get("player_controllable", False):
                continue
            permission = permissions.get(str(fleet["fleet_id"]), {})
            fleets.append(
                {
                    **fleet,
                    "permission": {
                        "allow_move": bool(permission.get("allow_move", False)),
                        "allow_attack": bool(
                            permission.get("allow_attack", False)
                        ),
                    },
                }
            )
        return {
            **profile,
            "source_save": str(path),
            "source_save_sha256": sha256_file(path),
            "fleets": fleets,
            "attack_protocol_state": "paired_non_host_sample_required",
        }

    def prepare_move(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise FleetToolError("玩家没有启用实验性舰队工具。")
        fleet_id = int(arguments["fleet_id"])
        destination_system_id = int(arguments["destination_system_id"])
        reason = str(arguments["reason"]).strip()
        path, profile = self._profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not permissions.get(str(fleet_id), {}).get("allow_move", False):
            raise FleetToolError(f"玩家没有授权调用舰队 {fleet_id} 进行移动。")
        try:
            order = selected_move(profile, fleet_id, destination_system_id)
        except ValueError as error:
            raise FleetToolError(str(error)) from error
        run_id = "fleet_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": "move_fleet",
            "reason": reason,
            "order": order,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_attack(self, arguments: dict[str, Any]) -> dict[str, Any]:
        fleet_id = int(arguments["fleet_id"])
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not permissions.get(str(fleet_id), {}).get("allow_attack", False):
            raise FleetToolError(f"玩家没有授权调用舰队 {fleet_id} 进行攻击。")
        raise FleetToolError(
            "6b33 目前只有房主权威记录，缺少非房主请求/权威回包成对样本；"
            "v0.5.9 不会猜测攻击请求格式。"
        )

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.allow_execute:
            raise FleetToolError("当前运行策略不允许自动执行。")
        run_id = str(arguments.get("run_id", ""))
        if self.prepared is None or run_id != self.prepared["run_id"]:
            raise FleetToolError("run_id 必须来自本回合刚准备的舰队命令。")
        fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
        destination_system_id = int(
            self.prepared["order"]["destination_system"]["system_id"]
        )
        _, profile = self._profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not permissions.get(str(fleet_id), {}).get("allow_move", False):
            raise FleetToolError("玩家在执行前撤销了该舰队的移动权限。")
        try:
            refreshed = selected_move(profile, fleet_id, destination_system_id)
        except ValueError as error:
            raise FleetToolError(str(error)) from error
        try:
            result = SessionProxyController(self.config).arm_and_wait(
                action="move_fleet",
                target=dict(refreshed["target"]),
                request_id=run_id,
            )
        except SessionProxyError as error:
            raise FleetToolError(str(error)) from error
        success = result.get("outcome") == "confirmed"
        if not success:
            raise FleetToolError(
                str(result.get("error") or "舰队移动未获房主权威确认。")
            )
        self.store.set_state(
            "last_fleet_execution",
            {
                "run_id": run_id,
                "action": "move_fleet",
                "fleet_id": fleet_id,
                "destination_system_id": destination_system_id,
                "recorded_at": now_iso(),
                "proxy_result": result,
            },
        )
        self.prepared = None
        return {
            "schema": "iag.tool_result.fleet_execution.v1",
            "success": True,
            "run_id": run_id,
            "fleet_id": fleet_id,
            "destination_system_id": destination_system_id,
            "confirmation": result,
        }

    def dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        if name == "inspect_fleet_state":
            result = self.inspect()
            summary = (
                f"已读取 {len(result['fleets'])} 支玩家舰队；"
                "新舰队默认不允许模型调用。"
            )
        elif name == "prepare_fleet_move":
            result = self.prepare_move(arguments)
            source = result["order"]["source_fleet"]
            destination = result["order"]["destination_system"]
            summary = (
                f"已准备舰队 {source['name_key'] or source['fleet_id']} 移动到 "
                f"{destination['name_key'] or destination['system_id']}；尚未发包。"
            )
        elif name == "prepare_fleet_attack":
            result = self.prepare_attack(arguments)
            summary = "攻击命令已准备。"
        elif name == "execute_prepared_fleet_order":
            result = self.execute(arguments)
            summary = (
                f"舰队 {result['fleet_id']} 的移动命令已获房主权威确认。"
            )
        else:
            raise FleetToolError(f"Unknown fleet tool: {name}")
        return result, summary
