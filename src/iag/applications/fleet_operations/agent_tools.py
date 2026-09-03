"""Model tools for save-backed, player-authorized fleet movement."""

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
from iag.stellaris.state.fleet_profiles import (
    extract_fleet_profiles,
    resolve_created_fleet_template,
    selected_coordinate_move,
    selected_fleet_reinforcement,
    selected_move,
    selected_new_fleet_reinforcement,
)
from iag.stellaris.state.planet_profiles import load_gamestate
from iag.stellaris.state.save_ingest import resolve_current_save
from iag.stellaris.state.ship_profiles import (
    clone_ship_design,
    extract_ship_profiles,
)

FLEET_PERMISSIONS_KEY = "fleet_permissions"
PENDING_NEW_FLEET_KEY = "pending_new_fleet_creation"

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

PREPARE_COORDINATE_MOVE_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_fleet_coordinate_move",
        "description": (
            "准备一条 4f2c 星系内坐标移动。舰队必须获玩家移动授权且当前可用；"
            "目标 X/Y 必须位于玩家配置的安全边界内，星系 origin 来自存档。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fleet_id": {"type": "integer"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["fleet_id", "x", "y", "reason"],
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

INSPECT_SHIPS_TOOL = {
    "type": "function",
    "function": {
        "name": "inspect_ship_state",
        "description": (
            "读取玩家当前舰船设计、组件槽位、直接船坞队列和在建舰船。"
            "建设项 ID 是带代际的对象句柄，不应当作普通队列序号。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

PREPARE_SHIP_DESIGN_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_ship_design_clone",
        "description": (
            "从最新存档中的一份合法单区段护卫舰设计克隆新设计。组件只能替换为"
            "同一舰种、区段和槽位中已经由存档证明合法的组件。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_design_id": {"type": "integer"},
                "new_name": {"type": "string", "maxLength": 48},
                "component_replacements": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "section_slot": {"type": "string"},
                            "component_slot": {"type": "string"},
                            "component_id": {"type": "string"},
                        },
                        "required": [
                            "section_slot",
                            "component_slot",
                            "component_id",
                        ],
                        "additionalProperties": False,
                    },
                },
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": [
                "source_design_id",
                "new_name",
                "component_replacements",
                "reason",
            ],
            "additionalProperties": False,
        },
    },
}

PREPARE_FLEET_REINFORCEMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_fleet_reinforcement",
        "description": (
            "把指定玩家舰队中某一设计的目标总数调整为 target_count，然后按舰队"
            "管理器的两阶段协议请求增援。每次增加目标编制均会独立等待房主确认。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fleet_id": {"type": "integer"},
                "design_id": {"type": "integer"},
                "target_count": {"type": "integer", "minimum": 1},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": [
                "fleet_id",
                "design_id",
                "target_count",
                "reason",
            ],
            "additionalProperties": False,
        },
    },
}

PREPARE_NEW_FLEET_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_new_fleet",
        "description": (
            "准备从零创建一支舰队。第一阶段只创建空 Fleet Manager 模板；"
            "模板 ID 必须由后续新存档确认，不会按观察到的连续数字猜测。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "design_id": {"type": "integer"},
                "target_count": {"type": "integer", "minimum": 1},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["design_id", "target_count", "reason"],
            "additionalProperties": False,
        },
    },
}

EXECUTE_SHIP_TOOL = {
    "type": "function",
    "function": {
        "name": "execute_prepared_ship_action",
        "description": (
            "执行本回合刚准备的舰船设计、指定舰队增援或新舰队阶段。执行前"
            "重新读取存档；模板增量和两阶段增援逐项确认，首个失败后立即停止。"
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
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


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
            "allow_reinforce": bool(
                permission.get("allow_reinforce", False)
            ),
        }
    return result


class FleetToolbox:
    """One model-turn view over fleet state and one prepared order."""

    tool_names: ClassVar[frozenset[str]] = frozenset({
        "inspect_fleet_state",
        "prepare_fleet_move",
        "prepare_fleet_coordinate_move",
        "prepare_fleet_attack",
        "execute_prepared_fleet_order",
        "inspect_ship_state",
        "prepare_ship_design_clone",
        "prepare_fleet_reinforcement",
        "prepare_new_fleet",
        "execute_prepared_ship_action",
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
        self.coordinate_enabled = bool(
            self.config.get(
                "experimental_fleet_coordinate_tools_enabled",
                False,
            )
        )
        self.coordinate_limit = float(
            self.config.get("fleet_coordinate_max_abs", 1000.0)
        )
        self.ship_design_enabled = bool(
            self.config.get("experimental_ship_design_tools_enabled", False)
        )
        self.fleet_reinforcement_enabled = bool(
            self.config.get(
                "experimental_fleet_reinforcement_tools_enabled",
                False,
            )
        )
        self.new_fleet_enabled = bool(
            self.config.get("experimental_new_fleet_tools_enabled", False)
        )
        self.maximum_target_increase = max(
            1,
            min(
                int(self.config.get("maximum_fleet_reinforcement_increase", 5)),
                20,
            ),
        )
        self.maximum_new_fleet_ships = max(
            1,
            min(
                int(self.config.get("maximum_new_fleet_initial_ships", 5)),
                20,
            ),
        )
        self.prepared: dict[str, Any] | None = None

    def schemas(self) -> list[dict[str, Any]]:
        value: list[dict[str, Any]] = []
        if self.enabled:
            value.extend([INSPECT_FLEETS_TOOL, PREPARE_MOVE_TOOL])
            if self.coordinate_enabled:
                value.append(PREPARE_COORDINATE_MOVE_TOOL)
            if self.attack_enabled:
                value.append(PREPARE_ATTACK_TOOL)
            if self.allow_execute:
                value.append(EXECUTE_FLEET_TOOL)
        if (
            self.ship_design_enabled
            or self.fleet_reinforcement_enabled
            or self.new_fleet_enabled
        ):
            value.append(INSPECT_SHIPS_TOOL)
        if self.ship_design_enabled:
            value.append(PREPARE_SHIP_DESIGN_TOOL)
        if self.fleet_reinforcement_enabled:
            if not self.enabled:
                value.append(INSPECT_FLEETS_TOOL)
            value.append(PREPARE_FLEET_REINFORCEMENT_TOOL)
        if self.new_fleet_enabled:
            value.append(PREPARE_NEW_FLEET_TOOL)
        if (
            self.allow_execute
            and (
                self.ship_design_enabled
                or self.fleet_reinforcement_enabled
                or self.new_fleet_enabled
            )
        ):
            value.append(EXECUTE_SHIP_TOOL)
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

    def _ship_profile(self) -> tuple[Path, dict[str, Any]]:
        path = self._save_path()
        return path, extract_ship_profiles(load_gamestate(path))

    def inspect_ships(self) -> dict[str, Any]:
        path, profile = self._ship_profile()
        pending = self._pending_new_fleet_status()
        return {
            **profile,
            "source_save": str(path),
            "source_save_sha256": sha256_file(path),
            "maximum_fleet_reinforcement_increase": self.maximum_target_increase,
            "ship_design_enabled": self.ship_design_enabled,
            "fleet_reinforcement_enabled": self.fleet_reinforcement_enabled,
            "new_fleet_enabled": self.new_fleet_enabled,
            "maximum_new_fleet_initial_ships": self.maximum_new_fleet_ships,
            "pending_new_fleet_creation": pending,
        }

    def _pending_new_fleet_status(self) -> dict[str, Any] | None:
        raw = self.store.get_state(PENDING_NEW_FLEET_KEY)
        if not isinstance(raw, dict):
            return None
        path, profile = self._profile()
        current_hash = sha256_file(path)
        status = {
            **raw,
            "current_save_sha256": current_hash,
            "current_game_date": profile.get("game_date"),
            "fresh_save_available": current_hash
            != str(raw.get("last_action_save_sha256") or ""),
        }
        phase = str(raw.get("phase") or "")
        if not status["fresh_save_available"]:
            return status
        try:
            template = resolve_created_fleet_template(
                profile,
                baseline_template_ids=[
                    int(value) for value in raw.get("baseline_template_ids", [])
                ],
                expected_template_id=(
                    int(raw["resolved_template_id"])
                    if raw.get("resolved_template_id") is not None
                    else None
                ),
            )
        except (TypeError, ValueError) as error:
            status["resolution_error"] = str(error)
            return status

        template_id = int(template["fleet_template_id"])
        status["resolved_template_id"] = template_id
        status["resolution_ready"] = phase in {
            "awaiting_template_save",
            "configuration_partial",
            "awaiting_fleet_save",
        }
        if phase == "awaiting_fleet_save":
            target = next(
                (
                    item
                    for item in template.get("design_targets", [])
                    if int(item["design_id"]) == int(raw["design_id"])
                ),
                None,
            )
            if target is not None and int(target["target_count"]) >= int(
                raw["target_count"]
            ):
                completed = {
                    **status,
                    "phase": "confirmed_in_save",
                    "fleet_id": template.get("fleet_id"),
                    "queued_item_handles": list(
                        template.get("queued_item_handles", [])
                    ),
                    "ships_completed": 0,
                    "confirmed_at": now_iso(),
                }
                self.store.set_state("last_new_fleet_creation", completed)
                self.store.set_state(PENDING_NEW_FLEET_KEY, None)
                return completed
        return status

    def prepare_ship_design(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.ship_design_enabled:
            raise FleetToolError("玩家没有启用实验性舰船设计工具。")
        path, profile = self._ship_profile()
        replacements = arguments.get("component_replacements")
        if not isinstance(replacements, list):
            raise FleetToolError("component_replacements 必须是数组。")
        try:
            blueprint = clone_ship_design(
                profile,
                source_design_id=int(arguments["source_design_id"]),
                new_name=str(arguments["new_name"]).strip(),
                component_replacements=[
                    dict(item) for item in replacements if isinstance(item, dict)
                ],
            )
        except (TypeError, ValueError) as error:
            raise FleetToolError(str(error)) from error
        if len(blueprint["component_replacements"]) != len(replacements):
            raise FleetToolError("每一项 component_replacements 都必须是对象。")
        run_id = "ship_design_" + datetime.now(UTC).strftime(
            "%Y%m%d_%H%M%S_%f"
        )
        self.prepared = {
            "run_id": run_id,
            "action": "create_ship_design",
            "reason": str(arguments["reason"]).strip(),
            "source_design_id": int(arguments["source_design_id"]),
            "new_name": str(arguments["new_name"]).strip(),
            "component_replacements_input": [dict(item) for item in replacements],
            "target": {"blueprint": blueprint},
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_fleet_reinforcement(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.fleet_reinforcement_enabled:
            raise FleetToolError("玩家没有启用实验性舰队增援工具。")
        path, profile = self._profile()
        fleet_id = int(arguments["fleet_id"])
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not permissions.get(str(fleet_id), {}).get(
            "allow_reinforce", False
        ):
            raise FleetToolError(f"玩家没有授权调用舰队 {fleet_id} 进行增援。")
        try:
            selection = selected_fleet_reinforcement(
                profile,
                fleet_id=fleet_id,
                design_id=int(arguments["design_id"]),
                target_count=int(arguments["target_count"]),
                maximum_target_increase=self.maximum_target_increase,
            )
        except (TypeError, ValueError) as error:
            raise FleetToolError(str(error)) from error
        run_id = "fleet_reinforce_" + datetime.now(UTC).strftime(
            "%Y%m%d_%H%M%S_%f"
        )
        self.prepared = {
            "run_id": run_id,
            "action": "reinforce_fleet_to_target",
            "reason": str(arguments["reason"]).strip(),
            "fleet_id": fleet_id,
            "design_id": int(arguments["design_id"]),
            "target_count": int(arguments["target_count"]),
            "selection": selection,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_new_fleet(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.new_fleet_enabled:
            raise FleetToolError("玩家没有启用实验性新建舰队工具。")
        pending = self._pending_new_fleet_status()
        if pending is not None and pending.get("phase") != "confirmed_in_save":
            raise FleetToolError("当前战役已有一支新舰队处于创建或存档确认阶段。")

        path, profile = self._profile()
        design_id = int(arguments["design_id"])
        target_count = int(arguments["target_count"])
        if design_id not in profile.get("player_ship_design_ids", []):
            raise FleetToolError(f"舰船设计 {design_id} 不属于玩家。")
        if not 1 <= target_count <= self.maximum_new_fleet_ships:
            raise FleetToolError(
                "新舰队初始数量超出玩家配置的单次上限。"
            )
        baseline_template_ids = sorted(
            int(item["fleet_template_id"])
            for item in profile.get("fleet_templates", [])
        )
        run_id = "new_fleet_" + datetime.now(UTC).strftime(
            "%Y%m%d_%H%M%S_%f"
        )
        self.prepared = {
            "run_id": run_id,
            "action": "create_new_fleet",
            "reason": str(arguments["reason"]).strip(),
            "design_id": design_id,
            "target_count": target_count,
            "baseline_template_ids": baseline_template_ids,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_new_fleet_configuration(self) -> dict[str, Any]:
        """Prepare phase two after a fresh save resolves the new template ID.

        This method remains public for deterministic continuation code and
        diagnostics, but is intentionally no longer exposed as an LLM tool.
        """
        if not self.new_fleet_enabled:
            raise FleetToolError("玩家没有启用实验性新建舰队工具。")
        pending = self._pending_new_fleet_status()
        if not isinstance(pending, dict):
            raise FleetToolError("当前战役没有等待配置的新舰队模板。")
        if pending.get("phase") not in {
            "awaiting_template_save",
            "configuration_partial",
            "awaiting_fleet_save",
        }:
            raise FleetToolError(
                f"当前新舰队阶段不能继续配置：{pending.get('phase')}。"
            )
        if not pending.get("fresh_save_available"):
            raise FleetToolError("必须先同步一份创建模板后的新存档。")
        if pending.get("resolution_error"):
            raise FleetToolError(str(pending["resolution_error"]))
        template_id = pending.get("resolved_template_id")
        if template_id is None:
            raise FleetToolError("新存档尚未唯一确认新舰队模板 ID。")

        path, profile = self._profile()
        try:
            selection = selected_new_fleet_reinforcement(
                profile,
                fleet_template_id=int(template_id),
                design_id=int(pending["design_id"]),
                target_count=int(pending["target_count"]),
                maximum_target_increase=self.maximum_new_fleet_ships,
            )
        except (TypeError, ValueError) as error:
            raise FleetToolError(str(error)) from error
        run_id = "new_fleet_config_" + datetime.now(UTC).strftime(
            "%Y%m%d_%H%M%S_%f"
        )
        self.prepared = {
            "run_id": run_id,
            "action": "configure_new_fleet",
            "reason": str(pending.get("reason") or ""),
            "creation_run_id": str(pending["creation_run_id"]),
            "design_id": int(pending["design_id"]),
            "target_count": int(pending["target_count"]),
            "fleet_template_id": int(template_id),
            "baseline_template_ids": [
                int(value) for value in pending.get("baseline_template_ids", [])
            ],
            "selection": selection,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def continue_pending_new_fleet_from_save(self) -> dict[str, Any]:
        """Advance one save-dependent new-fleet phase without invoking an LLM."""
        if not self.allow_execute:
            raise FleetToolError("当前运行策略不允许自动续接新舰队工作流。")
        if not self.new_fleet_enabled:
            return {
                "schema": "iag.save_continuation.new_fleet.v1",
                "state": "disabled",
                "mutated_game": False,
                "reason": "new_fleet_tools_disabled",
            }

        status = self._pending_new_fleet_status()
        if not isinstance(status, dict):
            return {
                "schema": "iag.save_continuation.new_fleet.v1",
                "state": "idle",
                "mutated_game": False,
                "reason": "no_pending_new_fleet",
            }
        if status.get("phase") == "confirmed_in_save":
            return {
                "schema": "iag.save_continuation.new_fleet.v1",
                "state": "confirmed_in_save",
                "mutated_game": False,
                "fleet_id": status.get("fleet_id"),
                "fleet_template_id": status.get("resolved_template_id"),
                "game_date": status.get("current_game_date"),
            }
        if not status.get("fresh_save_available"):
            return {
                "schema": "iag.save_continuation.new_fleet.v1",
                "state": "waiting_for_fresh_save",
                "mutated_game": False,
                "phase": status.get("phase"),
                "game_date": status.get("current_game_date"),
            }

        current_hash = str(status.get("current_save_sha256") or "")
        raw = self.store.get_state(PENDING_NEW_FLEET_KEY, {})
        pending = dict(raw) if isinstance(raw, dict) else {}
        if (
            current_hash
            and pending.get("last_continuation_attempt_save_sha256")
            == current_hash
        ):
            return {
                "schema": "iag.save_continuation.new_fleet.v1",
                "state": "already_attempted_for_save",
                "mutated_game": False,
                "phase": status.get("phase"),
                "game_date": status.get("current_game_date"),
            }

        pending.update(
            {
                "last_continuation_attempt_save_sha256": current_hash,
                "last_continuation_attempt_game_date": status.get(
                    "current_game_date"
                ),
                "last_continuation_attempted_at": now_iso(),
            }
        )
        self.store.set_state(PENDING_NEW_FLEET_KEY, pending)

        if status.get("resolution_error"):
            pending["last_continuation_error"] = str(
                status["resolution_error"]
            )
            pending["last_continuation_state"] = "needs_review"
            self.store.set_state(PENDING_NEW_FLEET_KEY, pending)
            return {
                "schema": "iag.save_continuation.new_fleet.v1",
                "state": "needs_review",
                "mutated_game": False,
                "phase": status.get("phase"),
                "error": str(status["resolution_error"]),
                "game_date": status.get("current_game_date"),
            }

        try:
            prepared = self.prepare_new_fleet_configuration()
            execution = self.execute_ship_action(
                {"run_id": prepared["run_id"]}
            )
        except FleetToolError as error:
            latest = self.store.get_state(PENDING_NEW_FLEET_KEY, {})
            failed = dict(latest) if isinstance(latest, dict) else pending
            failed.update(
                {
                    "last_continuation_state": "failed",
                    "last_continuation_error": str(error),
                    "last_continuation_failed_at": now_iso(),
                }
            )
            self.store.set_state(PENDING_NEW_FLEET_KEY, failed)
            return {
                "schema": "iag.save_continuation.new_fleet.v1",
                "state": "failed",
                "mutated_game": False,
                "phase": status.get("phase"),
                "error": str(error),
                "game_date": status.get("current_game_date"),
            }

        confirmed_steps = int(execution.get("confirmed_protocol_steps", 0))
        return {
            "schema": "iag.save_continuation.new_fleet.v1",
            "state": (
                "executed" if execution.get("success") else "partially_executed"
            ),
            "mutated_game": confirmed_steps > 0,
            "phase": "awaiting_fleet_save",
            "game_date": status.get("current_game_date"),
            "fleet_template_id": execution.get("fleet_template_id"),
            "fleet_id": execution.get("fleet_id"),
            "confirmed_protocol_steps": confirmed_steps,
            "requested_protocol_steps": execution.get(
                "requested_protocol_steps"
            ),
            "execution": execution,
        }

    def execute_ship_action(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.allow_execute:
            raise FleetToolError("当前运行策略不允许自动执行。")
        run_id = str(arguments.get("run_id", ""))
        if self.prepared is None or run_id != self.prepared["run_id"]:
            raise FleetToolError("run_id 必须来自本回合刚准备的舰船动作。")
        action = str(self.prepared.get("action") or "")
        if action in {
            "reinforce_fleet_to_target",
            "create_new_fleet",
            "configure_new_fleet",
        }:
            profile_path, profile = self._profile()
        else:
            profile_path, profile = self._ship_profile()
        if action == "create_ship_design":
            try:
                blueprint = clone_ship_design(
                    profile,
                    source_design_id=int(self.prepared["source_design_id"]),
                    new_name=str(self.prepared["new_name"]),
                    component_replacements=list(
                        self.prepared["component_replacements_input"]
                    ),
                )
                result = SessionProxyController(self.config).arm_and_wait(
                    action=action,
                    target={"blueprint": blueprint},
                    request_id=run_id,
                )
            except (SessionProxyError, TypeError, ValueError) as error:
                raise FleetToolError(str(error)) from error
            if result.get("outcome") != "confirmed":
                raise FleetToolError(
                    str(result.get("error") or "舰船设计未获房主权威确认。")
                )
            execution = {
                "schema": "iag.tool_result.ship_action.v1",
                "success": True,
                "action": action,
                "run_id": run_id,
                "design_name": blueprint["name"],
                "confirmed_quantity": 1,
                "awaiting_fresh_save_design_id": True,
                "confirmations": [result],
            }
        elif action == "create_new_fleet":
            if not self.new_fleet_enabled:
                raise FleetToolError("玩家在执行前关闭了实验性新建舰队工具。")
            current_template_ids = sorted(
                int(item["fleet_template_id"])
                for item in profile.get("fleet_templates", [])
            )
            if current_template_ids != list(
                self.prepared["baseline_template_ids"]
            ):
                raise FleetToolError(
                    "准备后舰队模板集合已经变化；请重新规划，避免归属错误。"
                )
            design_id = int(self.prepared["design_id"])
            if design_id not in profile.get("player_ship_design_ids", []):
                raise FleetToolError("准备使用的舰船设计已不再属于玩家。")
            try:
                result = SessionProxyController(self.config).arm_and_wait(
                    action="create_fleet_template",
                    target={"context_822c": int(profile["owner_country_id"])},
                    request_id=run_id,
                )
            except SessionProxyError as error:
                raise FleetToolError(str(error)) from error
            if result.get("outcome") != "confirmed":
                raise FleetToolError(
                    str(result.get("error") or "新舰队模板未获房主权威确认。")
                )
            pending = {
                "schema": "iag.pending_new_fleet.v1",
                "phase": "awaiting_template_save",
                "creation_run_id": run_id,
                "reason": str(self.prepared["reason"]),
                "design_id": design_id,
                "target_count": int(self.prepared["target_count"]),
                "baseline_template_ids": current_template_ids,
                "last_action_save_sha256": sha256_file(profile_path),
                "last_action_game_date": profile.get("game_date"),
                "creation_confirmed_at": now_iso(),
            }
            self.store.set_state(PENDING_NEW_FLEET_KEY, pending)
            execution = {
                "schema": "iag.tool_result.ship_action.v1",
                "success": True,
                "action": action,
                "run_id": run_id,
                "design_id": design_id,
                "requested_target_count": int(self.prepared["target_count"]),
                "confirmed_quantity": 0,
                "ships_completed": 0,
                "awaiting_fresh_save_template_id": True,
                "confirmation": result,
            }
        elif action in {
            "reinforce_fleet_to_target",
            "configure_new_fleet",
        }:
            is_new_fleet = action == "configure_new_fleet"
            if is_new_fleet:
                if not self.new_fleet_enabled:
                    raise FleetToolError(
                        "玩家在执行前关闭了实验性新建舰队工具。"
                    )
                template_id = int(self.prepared["fleet_template_id"])
                try:
                    resolve_created_fleet_template(
                        profile,
                        baseline_template_ids=list(
                            self.prepared["baseline_template_ids"]
                        ),
                        expected_template_id=template_id,
                    )
                    refreshed = selected_new_fleet_reinforcement(
                        profile,
                        fleet_template_id=template_id,
                        design_id=int(self.prepared["design_id"]),
                        target_count=int(self.prepared["target_count"]),
                        maximum_target_increase=self.maximum_new_fleet_ships,
                    )
                except (TypeError, ValueError) as error:
                    raise FleetToolError(str(error)) from error
                fleet_id = refreshed.get("fleet_id")
            else:
                fleet_id = int(self.prepared["fleet_id"])
                permissions = normalized_permissions(
                    self.store.get_state(FLEET_PERMISSIONS_KEY, {})
                )
                if not permissions.get(str(fleet_id), {}).get(
                    "allow_reinforce", False
                ):
                    raise FleetToolError(
                        "玩家在执行前撤销了该舰队的增援权限。"
                    )
                try:
                    refreshed = selected_fleet_reinforcement(
                        profile,
                        fleet_id=fleet_id,
                        design_id=int(self.prepared["design_id"]),
                        target_count=int(self.prepared["target_count"]),
                        maximum_target_increase=self.maximum_target_increase,
                    )
                except (TypeError, ValueError) as error:
                    raise FleetToolError(str(error)) from error
            confirmations: list[dict[str, Any]] = []
            error_text: str | None = None
            sequence = list(refreshed["protocol_sequence"])
            for ordinal, protocol_action in enumerate(sequence, 1):
                target_key = (
                    "template_edit_target"
                    if protocol_action == "add_fleet_template_ship"
                    else "reinforcement_target"
                )
                try:
                    result = SessionProxyController(self.config).arm_and_wait(
                        action=protocol_action,
                        target=dict(refreshed[target_key]),
                        request_id=f"{run_id}_{ordinal:03d}",
                    )
                except SessionProxyError as error:
                    error_text = str(error)
                    break
                confirmations.append(result)
                if result.get("outcome") != "confirmed":
                    error_text = str(
                        result.get("error")
                        or f"第 {ordinal} 个舰队增援协议步骤未获房主确认。"
                    )
                    break
            confirmed_steps = sum(
                result.get("outcome") == "confirmed" for result in confirmations
            )
            confirmed_actions = [
                sequence[index]
                for index, result in enumerate(confirmations)
                if result.get("outcome") == "confirmed"
            ]
            confirmed_increase = confirmed_actions.count(
                "add_fleet_template_ship"
            )
            reinforcement_required = "reinforce_fleet_stage_1" in sequence
            reinforcement_confirmed = (
                reinforcement_required
                and confirmed_actions[-2:]
                == [
                    "reinforce_fleet_stage_1",
                    "reinforce_fleet_stage_2",
                ]
            )
            execution = {
                "schema": "iag.tool_result.ship_action.v1",
                "success": confirmed_steps == len(sequence),
                "partial": 0 < confirmed_steps < len(sequence),
                "action": action,
                "run_id": run_id,
                "fleet_id": fleet_id,
                "fleet_template_id": int(
                    refreshed["template_edit_target"]["fleet_template_id"]
                ),
                "design_id": int(self.prepared["design_id"]),
                "previous_target_count": refreshed["previous_target_count"],
                "requested_target_count": refreshed["requested_target_count"],
                "confirmed_target_increase": confirmed_increase,
                "confirmed_protocol_steps": confirmed_steps,
                "requested_protocol_steps": len(sequence),
                "reinforcement_request_required": reinforcement_required,
                "reinforcement_request_confirmed": reinforcement_confirmed,
                "ships_completed": 0,
                "awaiting_fresh_save_confirmation": True,
                "error": error_text,
                "confirmations": confirmations,
            }
            if is_new_fleet:
                pending = self.store.get_state(PENDING_NEW_FLEET_KEY, {})
                pending = dict(pending) if isinstance(pending, dict) else {}
                pending.update(
                    {
                        "phase": (
                            "awaiting_fleet_save"
                            if execution["success"]
                            else "configuration_partial"
                        ),
                        "resolved_template_id": int(
                            refreshed["fleet_template_id"]
                        ),
                        "last_action_save_sha256": sha256_file(profile_path),
                        "last_action_game_date": profile.get("game_date"),
                        "confirmed_target_increase": confirmed_increase,
                        "confirmed_protocol_steps": confirmed_steps,
                        "configuration_attempted_at": now_iso(),
                    }
                )
                self.store.set_state(PENDING_NEW_FLEET_KEY, pending)
        else:
            raise FleetToolError(f"不支持的已准备舰船动作：{action}。")

        self.store.set_state(
            "last_ship_execution",
            {**execution, "recorded_at": now_iso()},
        )
        self.prepared = None
        return execution

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
                        "allow_reinforce": bool(
                            permission.get("allow_reinforce", False)
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
            "coordinate_protocol_state": (
                "paired_non_host_samples_verified_experimental"
            ),
            "coordinate_limit": self.coordinate_limit,
            "reinforcement_protocol_state": (
                "paired_non_host_samples_verified_experimental"
            ),
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
        run_id = "fleet_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
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

    def prepare_coordinate_move(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.enabled or not self.coordinate_enabled:
            raise FleetToolError("玩家没有启用实验性星系内坐标移动。")
        fleet_id = int(arguments["fleet_id"])
        reason = str(arguments["reason"]).strip()
        path, profile = self._profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not permissions.get(str(fleet_id), {}).get("allow_move", False):
            raise FleetToolError(f"玩家没有授权调用舰队 {fleet_id} 进行移动。")
        try:
            order = selected_coordinate_move(
                profile,
                fleet_id,
                arguments["x"],
                arguments["y"],
                maximum_abs_coordinate=self.coordinate_limit,
            )
        except (TypeError, ValueError) as error:
            raise FleetToolError(str(error)) from error
        run_id = "fleet_xy_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": "move_fleet_to_coordinate",
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
        action = str(self.prepared["action"])
        _, profile = self._profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not permissions.get(str(fleet_id), {}).get("allow_move", False):
            raise FleetToolError("玩家在执行前撤销了该舰队的移动权限。")
        if action == "move_fleet":
            destination_system_id = int(
                self.prepared["order"]["destination_system"]["system_id"]
            )
            try:
                refreshed = selected_move(profile, fleet_id, destination_system_id)
            except ValueError as error:
                raise FleetToolError(str(error)) from error
        elif action == "move_fleet_to_coordinate":
            destination = self.prepared["order"]["destination_coordinate"]
            try:
                refreshed = selected_coordinate_move(
                    profile,
                    fleet_id,
                    destination["x"],
                    destination["y"],
                    maximum_abs_coordinate=self.coordinate_limit,
                )
            except ValueError as error:
                raise FleetToolError(str(error)) from error
            destination_system_id = int(destination["system_origin"])
        else:
            raise FleetToolError(f"不支持的已准备舰队动作：{action}。")
        try:
            result = SessionProxyController(self.config).arm_and_wait(
                action=action,
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
                "action": action,
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
            "action": action,
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
        elif name == "prepare_fleet_coordinate_move":
            result = self.prepare_coordinate_move(arguments)
            source = result["order"]["source_fleet"]
            destination = result["order"]["destination_coordinate"]
            summary = (
                f"已准备舰队 {source['name_key'] or source['fleet_id']} 在星系 "
                f"{destination['system_origin']} 内移动到 "
                f"({destination['x']}, {destination['y']})；尚未发包。"
            )
        elif name == "prepare_fleet_attack":
            result = self.prepare_attack(arguments)
            summary = "攻击命令已准备。"
        elif name == "execute_prepared_fleet_order":
            result = self.execute(arguments)
            summary = (
                f"舰队 {result['fleet_id']} 的移动命令已获房主权威确认。"
            )
        elif name == "inspect_ship_state":
            result = self.inspect_ships()
            summary = (
                f"已读取 {len(result['designs'])} 份玩家舰船设计和 "
                f"{len(result['shipyards'])} 个直接船坞队列。"
            )
        elif name == "prepare_ship_design_clone":
            result = self.prepare_ship_design(arguments)
            summary = (
                f"已准备舰船设计 {result['new_name']}；尚未提交，"
                "提交后需等待新存档分配 design_id。"
            )
        elif name == "prepare_fleet_reinforcement":
            result = self.prepare_fleet_reinforcement(arguments)
            summary = (
                f"已准备把舰队 {result['fleet_id']} 的设计 "
                f"{result['design_id']} 目标总数调整为 "
                f"{result['target_count']} 并请求增援；尚未发包。"
            )
        elif name == "prepare_new_fleet":
            result = self.prepare_new_fleet(arguments)
            summary = (
                f"已准备创建空舰队模板，计划使用设计 {result['design_id']}、"
                f"初始目标 {result['target_count']} 艘；尚未发包。"
            )
        elif name == "execute_prepared_ship_action":
            result = self.execute_ship_action(arguments)
            if result["action"] == "create_ship_design":
                summary = "舰船设计命令已获房主确认；等待新存档分配 design_id。"
            elif result["action"] == "create_new_fleet":
                summary = (
                    "空舰队模板创建命令已获房主确认；固定续接器将在新存档"
                    "唯一确认 fleet_template_id 后自动配置舰船，无需再次调用模型。"
                )
            elif result["action"] == "configure_new_fleet":
                if result["success"]:
                    summary = (
                        "新舰队初始编制和增援请求均已获房主确认；"
                        "舰船尚未完成，等待新存档核实模板、队列与实体舰队 ID。"
                    )
                else:
                    summary = (
                        f"新舰队配置仅确认 {result['confirmed_protocol_steps']}/"
                        f"{result['requested_protocol_steps']} 个协议步骤；"
                        "已停止并等待新存档后再续接。"
                    )
            elif result["success"] and result["reinforcement_request_required"]:
                summary = (
                    "舰队模板调整和增援请求均已获房主确认；"
                    "舰船尚未建成，等待新存档核实队列。"
                )
            elif result["success"]:
                summary = (
                    "舰队目标编制调整已获房主确认；当前舰数已满足目标，"
                    "因此未发送增援请求，等待新存档核实模板。"
                )
            else:
                summary = (
                    f"舰队增援仅确认 {result['confirmed_protocol_steps']}/"
                    f"{result['requested_protocol_steps']} 个协议步骤，"
                    "已停止后续提交。"
                )
        else:
            raise FleetToolError(f"Unknown fleet tool: {name}")
        return result, summary
