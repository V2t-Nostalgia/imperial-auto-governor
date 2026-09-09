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
from iag.stellaris.game_knowledge import detect_game_root
from iag.stellaris.state.expansion_profiles import (
    extract_expansion_profiles,
    selected_colonization,
    selected_starbase_operation,
)
from iag.stellaris.state.fleet_profiles import (
    extract_fleet_profiles,
    resolve_created_fleet_template,
    selected_attack,
    selected_construction_ship_starbase,
    selected_coordinate_move,
    selected_fleet_reinforcement,
    selected_fleet_repair,
    selected_fleet_upgrade,
    selected_move,
    selected_new_fleet_reinforcement,
    selected_ship_automation,
)
from iag.stellaris.state.planet_profiles import load_gamestate
from iag.stellaris.state.save_ingest import resolve_current_save
from iag.stellaris.state.ship_profiles import (
    customize_ship_design,
    extract_ship_profiles,
    ship_design_options,
)

FLEET_PERMISSIONS_KEY = "fleet_permissions"
PENDING_NEW_FLEET_KEY = "pending_new_fleet_creation"

INSPECT_FLEETS_TOOL = {
    "type": "function",
    "function": {
        "name": "inspect_fleet_state",
        "description": (
            "读取最新同步存档中的玩家舰队和民用船、军力、位置、忙碌/交战/MIA "
            "状态、舰船船体/装甲/护盾剩余百分比的舰队平均数与中位数，以及玩家"
            "逐船队授予的移动、攻击、维修、升级、增援、自动化与建站权限。"
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
            "准备一条 6b33 舰队攻击命令。目标必须由最新存档中的玩家 hostile "
            "情报与当前全局舰队位置唯一匹配，来源舰队还必须获得逐舰队攻击授权。"
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

PREPARE_REPAIR_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_fleet_repair",
        "description": (
            "准备一条 8f32 返港维修命令。来源必须是最新存档确认受损、空闲、"
            "未失踪、未交战，并获玩家逐舰队维修授权的军用舰队。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fleet_id": {"type": "integer"},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["fleet_id", "reason"],
            "additionalProperties": False,
        },
    },
}

PREPARE_UPGRADE_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_fleet_upgrade",
        "description": (
            "准备一条 8f2f 舰队升级命令。来源舰队必须有存档确认的升级设计，"
            "目标必须是 inspect_fleet_state 返回的己方船坞建设队列。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fleet_id": {"type": "integer"},
                "shipyard_build_queue_id": {"type": "integer"},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["fleet_id", "shipyard_build_queue_id", "reason"],
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
            "执行本回合刚准备的舰队、民用船、殖民或恒星基地命令。执行前会重新"
            "读取存档、重建同一候选、复查玩家权限，并要求会话代理锁定本局流量。"
        ),
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
}

PREPARE_SHIP_AUTOMATION_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_ship_automation",
        "description": (
            "为一艘玩家已授权的科研船或工程船配置 8f32 自动化。只接受该船型"
            "已经实机验证的选项；由内阁科学官带队时会拒绝星界裂隙自动化。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fleet_id": {"type": "integer"},
                "options": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                },
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["fleet_id", "options", "reason"],
            "additionalProperties": False,
        },
    },
}

PREPARE_CONSTRUCTION_STARBASE_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_construction_ship_starbase",
        "description": (
            "准备工程船 e02c 前哨建设命令。首版只允许最新存档中已完全勘探、"
            "无恒星基地、无可见敌军并与工程船当前位置直接相邻的星系。"
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

INSPECT_EXPANSION_TOOL = {
    "type": "function",
    "function": {
        "name": "inspect_expansion_state",
        "description": (
            "读取存档和匹配版本原版规则生成殖民与恒星基地候选，包括宜居度依据、"
            "殖民物种/设计/来源，以及恒星基地队列、等级、模块和建筑槽。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

PREPARE_COLONIZATION_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_colonization",
        "description": (
            "从 inspect_expansion_state 返回的候选中准备 3d37 订购殖民船并殖民。"
            "只支持已经差分验证的 col_city 与 col_mining 初始规划。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "designation": {
                    "type": "string",
                    "enum": ["col_city", "col_mining"],
                },
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["candidate_id", "designation", "reason"],
            "additionalProperties": False,
        },
    },
}

PREPARE_STARBASE_OPERATION_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_starbase_operation",
        "description": (
            "从 inspect_expansion_state 返回的候选中准备恒星基地升级、模块或建筑"
            "操作。已占用槽替换还要求玩家单独开启高影响替换开关。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["candidate_id", "reason"],
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

INSPECT_SHIP_DESIGN_OPTIONS_TOOL = {
    "type": "function",
    "function": {
        "name": "inspect_ship_design_options",
        "description": (
            "按需读取一份玩家舰船设计可用的区段、必需组件和槽位候选。先不传 "
            "section_template 查看区段与必需组件；选定区段后再传 section_template，"
            "只展开该区段的合法组件，避免把整个组件库塞入上下文。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_design_id": {"type": "integer"},
                "section_template": {"type": "string"},
            },
            "required": ["source_design_id"],
            "additionalProperties": False,
        },
    },
}

PREPARE_SHIP_DESIGN_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_ship_design",
        "description": (
            "以最新存档中一份玩家军舰设计为锚点创建新设计，可替换多个区段、"
            "填充或替换区段组件、替换反应堆/超空间引擎/推进器/传感器/战斗电脑，"
            "以及舰型具备的舰船光环，并设置自动升级。所有 ID 必须先由 "
            "inspect_ship_design_options 返回；"
            "原设计不会被覆盖。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_design_id": {"type": "integer"},
                "new_name": {"type": "string", "maxLength": 48},
                "component_replacements": {
                    "type": "array",
                    "maxItems": 32,
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
                "section_replacements": {
                    "type": "array",
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "section_slot": {"type": "string"},
                            "section_template": {"type": "string"},
                            "components": {
                                "type": "array",
                                "maxItems": 32,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "component_slot": {"type": "string"},
                                        "component_id": {"type": "string"},
                                    },
                                    "required": ["component_slot", "component_id"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": [
                            "section_slot",
                            "section_template",
                            "components",
                        ],
                        "additionalProperties": False,
                    },
                },
                "required_component_replacements": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "component_set": {"type": "string"},
                            "component_id": {"type": "string"},
                        },
                        "required": ["component_set", "component_id"],
                        "additionalProperties": False,
                    },
                },
                "upgrade_components_automatically": {"type": "boolean"},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": [
                "source_design_id",
                "new_name",
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
            "管理器的 123b 选中舰队协议请求增援。每次增加目标编制均会独立等待"
            "房主确认。"
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
            "重新读取存档；模板增量和选中舰队增援逐项确认，首个失败后立即停止。"
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
            "allow_reinforce": bool(permission.get("allow_reinforce", False)),
            "allow_repair": bool(permission.get("allow_repair", False)),
            "allow_upgrade": bool(permission.get("allow_upgrade", False)),
            "allow_automation": bool(permission.get("allow_automation", False)),
            "allow_build_starbase": bool(permission.get("allow_build_starbase", False)),
        }
    return result


def fleet_label(fleet: dict[str, Any]) -> str:
    return str(
        fleet.get("display_name_hint") or fleet.get("name_key") or fleet.get("fleet_id")
    )


class FleetToolbox:
    """One model-turn view over fleet state and one prepared order."""

    tool_names: ClassVar[frozenset[str]] = frozenset(
        {
            "inspect_fleet_state",
            "inspect_expansion_state",
            "prepare_fleet_move",
            "prepare_fleet_coordinate_move",
            "prepare_fleet_attack",
            "prepare_fleet_repair",
            "prepare_fleet_upgrade",
            "prepare_ship_automation",
            "prepare_construction_ship_starbase",
            "prepare_colonization",
            "prepare_starbase_operation",
            "execute_prepared_fleet_order",
            "inspect_ship_state",
            "inspect_ship_design_options",
            "prepare_ship_design",
            "prepare_ship_design_clone",
            "prepare_fleet_reinforcement",
            "prepare_new_fleet",
            "execute_prepared_ship_action",
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
        self.enabled = bool(self.config.get("experimental_fleet_tools_enabled", False))
        self.attack_enabled = bool(
            self.config.get("experimental_fleet_attack_enabled", False)
        )
        self.maintenance_enabled = bool(
            self.config.get(
                "experimental_fleet_maintenance_tools_enabled",
                False,
            )
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
        self.civilian_ship_enabled = bool(
            self.config.get("experimental_civilian_ship_tools_enabled", False)
        )
        self.colonization_enabled = bool(
            self.config.get("experimental_colonization_tools_enabled", False)
        )
        self.starbase_enabled = bool(
            self.config.get("experimental_starbase_tools_enabled", False)
        )
        self.starbase_replacement_enabled = bool(
            self.config.get(
                "experimental_starbase_replacement_enabled",
                False,
            )
        )
        self.minimum_colonization_habitability = float(
            self.config.get("minimum_colonization_habitability", 0.30)
        )
        if not 0 <= self.minimum_colonization_habitability <= 1:
            raise FleetToolError(
                "minimum_colonization_habitability 必须在 0 到 1 之间。"
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
        if self.maintenance_enabled:
            if not self.enabled:
                value.append(INSPECT_FLEETS_TOOL)
            value.extend([PREPARE_REPAIR_TOOL, PREPARE_UPGRADE_TOOL])
        if self.civilian_ship_enabled:
            if not self.enabled:
                value.append(INSPECT_FLEETS_TOOL)
            value.extend(
                [
                    PREPARE_SHIP_AUTOMATION_TOOL,
                    PREPARE_CONSTRUCTION_STARBASE_TOOL,
                ]
            )
        if self.colonization_enabled or self.starbase_enabled:
            value.append(INSPECT_EXPANSION_TOOL)
        if self.colonization_enabled:
            value.append(PREPARE_COLONIZATION_TOOL)
        if self.starbase_enabled:
            value.append(PREPARE_STARBASE_OPERATION_TOOL)
        if self.allow_execute and (
            self.enabled
            or self.maintenance_enabled
            or self.civilian_ship_enabled
            or self.colonization_enabled
            or self.starbase_enabled
        ):
            value.append(EXECUTE_FLEET_TOOL)
        if (
            self.ship_design_enabled
            or self.fleet_reinforcement_enabled
            or self.new_fleet_enabled
        ):
            value.append(INSPECT_SHIPS_TOOL)
        if self.ship_design_enabled:
            value.extend([INSPECT_SHIP_DESIGN_OPTIONS_TOOL, PREPARE_SHIP_DESIGN_TOOL])
        if self.fleet_reinforcement_enabled:
            if not self.enabled:
                value.append(INSPECT_FLEETS_TOOL)
            value.append(PREPARE_FLEET_REINFORCEMENT_TOOL)
        if self.new_fleet_enabled:
            value.append(PREPARE_NEW_FLEET_TOOL)
        if self.allow_execute and (
            self.ship_design_enabled
            or self.fleet_reinforcement_enabled
            or self.new_fleet_enabled
        ):
            value.append(EXECUTE_SHIP_TOOL)
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for schema in value:
            name = str(schema["function"]["name"])
            if name not in seen:
                unique.append(schema)
                seen.add(name)
        return unique

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
        return path, extract_ship_profiles(
            load_gamestate(path),
            game_root=detect_game_root(self.config),
        )

    def _expansion_profile(self) -> tuple[Path, dict[str, Any]]:
        path = self._save_path()
        return path, extract_expansion_profiles(
            load_gamestate(path),
            game_root=detect_game_root(self.config),
            minimum_habitability=self.minimum_colonization_habitability,
        )

    def inspect_ships(self) -> dict[str, Any]:
        path, profile = self._ship_profile()
        pending = self._pending_new_fleet_status()
        public_profile = dict(profile)
        component_choice_count = len(public_profile.pop("component_choice_index", []))
        return {
            **public_profile,
            "source_save": str(path),
            "source_save_sha256": sha256_file(path),
            "component_choice_count": component_choice_count,
            "component_choice_hint": (
                "Use inspect_ship_design_options to expand one source design and "
                "one optional section at a time."
            ),
            "maximum_fleet_reinforcement_increase": self.maximum_target_increase,
            "ship_design_enabled": self.ship_design_enabled,
            "fleet_reinforcement_enabled": self.fleet_reinforcement_enabled,
            "new_fleet_enabled": self.new_fleet_enabled,
            "maximum_new_fleet_initial_ships": self.maximum_new_fleet_ships,
            "pending_new_fleet_creation": pending,
        }

    def inspect_ship_design_options(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.ship_design_enabled:
            raise FleetToolError("玩家没有启用实验性舰船设计工具。")
        _path, profile = self._ship_profile()
        game_root = detect_game_root(self.config)
        if game_root is None:
            raise FleetToolError("未找到 Stellaris 安装目录，无法验证舰船设计规则。")
        raw_section = arguments.get("section_template")
        section_template = str(raw_section).strip() if raw_section is not None else None
        try:
            return ship_design_options(
                profile,
                source_design_id=int(arguments["source_design_id"]),
                game_root=game_root,
                section_template=section_template or None,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise FleetToolError(str(error)) from error

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
        game_root = detect_game_root(self.config)
        if game_root is None:
            raise FleetToolError("未找到 Stellaris 安装目录，无法验证舰船设计规则。")

        def object_array(name: str) -> list[dict[str, Any]]:
            raw = arguments.get(name, [])
            if not isinstance(raw, list) or any(
                not isinstance(item, dict) for item in raw
            ):
                raise FleetToolError(f"{name} 必须是对象数组。")
            return [dict(item) for item in raw]

        component_replacements = object_array("component_replacements")
        section_replacements = object_array("section_replacements")
        required_replacements = object_array("required_component_replacements")
        automatic_upgrade = arguments.get("upgrade_components_automatically")
        try:
            blueprint = customize_ship_design(
                profile,
                source_design_id=int(arguments["source_design_id"]),
                new_name=str(arguments["new_name"]).strip(),
                component_replacements=component_replacements,
                section_replacements=section_replacements,
                required_component_replacements=required_replacements,
                upgrade_components_automatically=automatic_upgrade,
                game_root=game_root,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise FleetToolError(str(error)) from error
        run_id = "ship_design_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": "create_ship_design",
            "reason": str(arguments["reason"]).strip(),
            "source_design_id": int(arguments["source_design_id"]),
            "new_name": str(arguments["new_name"]).strip(),
            "component_replacements_input": component_replacements,
            "section_replacements_input": section_replacements,
            "required_component_replacements_input": required_replacements,
            "upgrade_components_automatically_input": automatic_upgrade,
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
        if not permissions.get(str(fleet_id), {}).get("allow_reinforce", False):
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
        run_id = "fleet_reinforce_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
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
            raise FleetToolError("新舰队初始数量超出玩家配置的单次上限。")
        baseline_template_ids = sorted(
            int(item["fleet_template_id"])
            for item in profile.get("fleet_templates", [])
        )
        run_id = "new_fleet_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
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
        run_id = "new_fleet_config_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
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
            and pending.get("last_continuation_attempt_save_sha256") == current_hash
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
                "last_continuation_attempt_game_date": status.get("current_game_date"),
                "last_continuation_attempted_at": now_iso(),
            }
        )
        self.store.set_state(PENDING_NEW_FLEET_KEY, pending)

        if status.get("resolution_error"):
            pending["last_continuation_error"] = str(status["resolution_error"])
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
            execution = self.execute_ship_action({"run_id": prepared["run_id"]})
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
            "state": ("executed" if execution.get("success") else "partially_executed"),
            "mutated_game": confirmed_steps > 0,
            "phase": "awaiting_fleet_save",
            "game_date": status.get("current_game_date"),
            "fleet_template_id": execution.get("fleet_template_id"),
            "fleet_id": execution.get("fleet_id"),
            "confirmed_protocol_steps": confirmed_steps,
            "requested_protocol_steps": execution.get("requested_protocol_steps"),
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
                game_root = detect_game_root(self.config)
                if game_root is None:
                    raise ValueError(
                        "未找到 Stellaris 安装目录，无法在执行前复验舰船设计。"
                    )
                blueprint = customize_ship_design(
                    profile,
                    source_design_id=int(self.prepared["source_design_id"]),
                    new_name=str(self.prepared["new_name"]),
                    component_replacements=list(
                        self.prepared["component_replacements_input"]
                    ),
                    section_replacements=list(
                        self.prepared["section_replacements_input"]
                    ),
                    required_component_replacements=list(
                        self.prepared["required_component_replacements_input"]
                    ),
                    upgrade_components_automatically=self.prepared.get(
                        "upgrade_components_automatically_input"
                    ),
                    game_root=game_root,
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
            if current_template_ids != list(self.prepared["baseline_template_ids"]):
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
                    raise FleetToolError("玩家在执行前关闭了实验性新建舰队工具。")
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
                if not permissions.get(str(fleet_id), {}).get("allow_reinforce", False):
                    raise FleetToolError("玩家在执行前撤销了该舰队的增援权限。")
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
            confirmed_increase = confirmed_actions.count("add_fleet_template_ship")
            reinforcement_required = "reinforce_selected_fleet" in sequence
            reinforcement_confirmed = (
                reinforcement_required
                and "reinforce_selected_fleet" in confirmed_actions
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
                        "resolved_template_id": int(refreshed["fleet_template_id"]),
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
            fleet_view = {
                key: value for key, value in fleet.items() if key != "ship_ids"
            }
            fleets.append(
                {
                    **fleet_view,
                    "permission": {
                        "allow_move": bool(permission.get("allow_move", False)),
                        "allow_attack": bool(permission.get("allow_attack", False)),
                        "allow_reinforce": bool(
                            permission.get("allow_reinforce", False)
                        ),
                        "allow_repair": bool(permission.get("allow_repair", False)),
                        "allow_upgrade": bool(permission.get("allow_upgrade", False)),
                        "allow_automation": bool(
                            permission.get("allow_automation", False)
                        ),
                        "allow_build_starbase": bool(
                            permission.get("allow_build_starbase", False)
                        ),
                    },
                }
            )
        return {
            **profile,
            "source_save": str(path),
            "source_save_sha256": sha256_file(path),
            "fleets": fleets,
            "attack_protocol_state": (
                "save_backed_hostile_mapping_and_paired_6b33_verified"
            ),
            "civilian_automation_protocol_state": ("paired_8f32_verified_options_only"),
            "construction_starbase_protocol_state": (
                "paired_e02c_adjacent_surveyed_targets_only"
            ),
            "coordinate_protocol_state": (
                "paired_non_host_samples_verified_experimental"
            ),
            "coordinate_limit": self.coordinate_limit,
            "fleet_maintenance_enabled": self.maintenance_enabled,
            "reinforcement_protocol_state": (
                "selected_fleet_123b_paired_and_save_backed"
            ),
            "fleet_maintenance_protocol_state": (
                "repair_8f32_and_upgrade_8f2f_paired_and_save_backed"
            ),
        }

    def inspect_expansion(self) -> dict[str, Any]:
        path, profile = self._expansion_profile()
        return {
            **profile,
            "source_save": str(path),
            "source_save_sha256": sha256_file(path),
            "colonization_enabled": self.colonization_enabled,
            "starbase_operations_enabled": self.starbase_enabled,
            "starbase_replacement_enabled": (self.starbase_replacement_enabled),
            "minimum_colonization_habitability": (
                self.minimum_colonization_habitability
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
        if not self.enabled or not self.attack_enabled:
            raise FleetToolError("玩家没有启用实验性舰队攻击工具。")
        fleet_id = int(arguments["fleet_id"])
        target_fleet_id = int(arguments["target_fleet_id"])
        reason = str(arguments["reason"]).strip()
        path, profile = self._profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not permissions.get(str(fleet_id), {}).get("allow_attack", False):
            raise FleetToolError(f"玩家没有授权调用舰队 {fleet_id} 进行攻击。")
        try:
            order = selected_attack(profile, fleet_id, target_fleet_id)
        except ValueError as error:
            raise FleetToolError(str(error)) from error
        run_id = "fleet_attack_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": "attack_fleet",
            "reason": reason,
            "order": order,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_fleet_repair(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.maintenance_enabled:
            raise FleetToolError("玩家没有启用实验性舰队维修与升级工具。")
        fleet_id = int(arguments["fleet_id"])
        path, profile = self._profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not permissions.get(str(fleet_id), {}).get("allow_repair", False):
            raise FleetToolError(f"玩家没有授权舰队 {fleet_id} 返港维修。")
        try:
            order = selected_fleet_repair(profile, fleet_id)
        except ValueError as error:
            raise FleetToolError(str(error)) from error
        run_id = "fleet_repair_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": "repair_fleet",
            "reason": str(arguments["reason"]).strip(),
            "order": order,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_fleet_upgrade(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.maintenance_enabled:
            raise FleetToolError("玩家没有启用实验性舰队维修与升级工具。")
        fleet_id = int(arguments["fleet_id"])
        queue_id = int(arguments["shipyard_build_queue_id"])
        path, profile = self._profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not permissions.get(str(fleet_id), {}).get("allow_upgrade", False):
            raise FleetToolError(f"玩家没有授权舰队 {fleet_id} 执行升级。")
        try:
            order = selected_fleet_upgrade(profile, fleet_id, queue_id)
        except ValueError as error:
            raise FleetToolError(str(error)) from error
        run_id = "fleet_upgrade_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": "upgrade_fleet",
            "reason": str(arguments["reason"]).strip(),
            "order": order,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_ship_automation(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.civilian_ship_enabled:
            raise FleetToolError("玩家没有启用实验性民用船工具。")
        fleet_id = int(arguments["fleet_id"])
        options = arguments.get("options")
        if not isinstance(options, list):
            raise FleetToolError("options 必须是数组。")
        path, profile = self._profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not permissions.get(str(fleet_id), {}).get("allow_automation", False):
            raise FleetToolError(f"玩家没有授权调用民用船 {fleet_id} 的自动化。")
        try:
            order = selected_ship_automation(profile, fleet_id, options)
        except (TypeError, ValueError) as error:
            raise FleetToolError(str(error)) from error
        run_id = "ship_automation_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": "configure_ship_automation",
            "reason": str(arguments["reason"]).strip(),
            "order": order,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_construction_starbase(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.civilian_ship_enabled:
            raise FleetToolError("玩家没有启用实验性民用船工具。")
        fleet_id = int(arguments["fleet_id"])
        destination_system_id = int(arguments["destination_system_id"])
        path, profile = self._profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not permissions.get(str(fleet_id), {}).get("allow_build_starbase", False):
            raise FleetToolError(f"玩家没有授权工程船 {fleet_id} 建造恒星基地。")
        try:
            order = selected_construction_ship_starbase(
                profile,
                fleet_id,
                destination_system_id,
            )
        except ValueError as error:
            raise FleetToolError(str(error)) from error
        run_id = "construction_starbase_" + datetime.now(UTC).strftime(
            "%Y%m%d_%H%M%S_%f"
        )
        self.prepared = {
            "run_id": run_id,
            "action": "build_starbase",
            "reason": str(arguments["reason"]).strip(),
            "order": order,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_colonization(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.colonization_enabled:
            raise FleetToolError("玩家没有启用实验性自动殖民工具。")
        candidate_id = str(arguments["candidate_id"])
        designation = str(arguments["designation"])
        path, profile = self._expansion_profile()
        try:
            order = selected_colonization(
                profile,
                candidate_id=candidate_id,
                designation=designation,
            )
        except ValueError as error:
            raise FleetToolError(str(error)) from error
        run_id = "colonization_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": "order_colony_ship_and_colonize",
            "reason": str(arguments["reason"]).strip(),
            "candidate_id": candidate_id,
            "designation": designation,
            "order": order,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_starbase_operation(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.starbase_enabled:
            raise FleetToolError("玩家没有启用实验性恒星基地工具。")
        candidate_id = str(arguments["candidate_id"])
        path, profile = self._expansion_profile()
        try:
            order = selected_starbase_operation(
                profile,
                candidate_id=candidate_id,
                allow_replacement=self.starbase_replacement_enabled,
            )
        except ValueError as error:
            raise FleetToolError(str(error)) from error
        run_id = "starbase_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": str(order["action"]),
            "reason": str(arguments["reason"]).strip(),
            "candidate_id": candidate_id,
            "order": order,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.allow_execute:
            raise FleetToolError("当前运行策略不允许自动执行。")
        run_id = str(arguments.get("run_id", ""))
        if self.prepared is None or run_id != self.prepared["run_id"]:
            raise FleetToolError("run_id 必须来自本回合刚准备的舰队命令。")
        action = str(self.prepared["action"])
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        fleet_id: int | None = None
        destination_system_id: int | None = None
        if action == "move_fleet":
            if not self.enabled:
                raise FleetToolError("玩家在执行前关闭了实验性舰队工具。")
            _, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not permissions.get(str(fleet_id), {}).get("allow_move", False):
                raise FleetToolError("玩家在执行前撤销了该舰队的移动权限。")
            destination_system_id = int(
                self.prepared["order"]["destination_system"]["system_id"]
            )
            try:
                refreshed = selected_move(profile, fleet_id, destination_system_id)
            except ValueError as error:
                raise FleetToolError(str(error)) from error
        elif action == "move_fleet_to_coordinate":
            if not self.enabled or not self.coordinate_enabled:
                raise FleetToolError("玩家在执行前关闭了星系内坐标移动。")
            _, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not permissions.get(str(fleet_id), {}).get("allow_move", False):
                raise FleetToolError("玩家在执行前撤销了该舰队的移动权限。")
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
        elif action == "attack_fleet":
            if not self.enabled or not self.attack_enabled:
                raise FleetToolError("玩家在执行前关闭了舰队攻击工具。")
            _, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not permissions.get(str(fleet_id), {}).get("allow_attack", False):
                raise FleetToolError("玩家在执行前撤销了该舰队的攻击权限。")
            target_fleet_id = int(self.prepared["order"]["hostile_target"]["fleet_id"])
            try:
                refreshed = selected_attack(
                    profile,
                    fleet_id,
                    target_fleet_id,
                )
            except ValueError as error:
                raise FleetToolError(str(error)) from error
            destination_system_id = refreshed["hostile_target"].get("system_id")
        elif action == "repair_fleet":
            if not self.maintenance_enabled:
                raise FleetToolError("玩家在执行前关闭了舰队维修与升级工具。")
            _, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not permissions.get(str(fleet_id), {}).get("allow_repair", False):
                raise FleetToolError("玩家在执行前撤销了该舰队的维修权限。")
            try:
                refreshed = selected_fleet_repair(profile, fleet_id)
            except ValueError as error:
                raise FleetToolError(str(error)) from error
        elif action == "upgrade_fleet":
            if not self.maintenance_enabled:
                raise FleetToolError("玩家在执行前关闭了舰队维修与升级工具。")
            _, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not permissions.get(str(fleet_id), {}).get("allow_upgrade", False):
                raise FleetToolError("玩家在执行前撤销了该舰队的升级权限。")
            queue_id = int(self.prepared["order"]["target"]["shipyard_build_queue_id"])
            try:
                refreshed = selected_fleet_upgrade(profile, fleet_id, queue_id)
            except ValueError as error:
                raise FleetToolError(str(error)) from error
            destination_system_id = refreshed["destination_shipyard"].get("system_id")
        elif action == "configure_ship_automation":
            if not self.civilian_ship_enabled:
                raise FleetToolError("玩家在执行前关闭了民用船工具。")
            _, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not permissions.get(str(fleet_id), {}).get("allow_automation", False):
                raise FleetToolError("玩家在执行前撤销了该民用船的自动化权限。")
            try:
                refreshed = selected_ship_automation(
                    profile,
                    fleet_id,
                    list(self.prepared["order"]["options"]),
                )
            except (TypeError, ValueError) as error:
                raise FleetToolError(str(error)) from error
        elif action == "build_starbase":
            if not self.civilian_ship_enabled:
                raise FleetToolError("玩家在执行前关闭了民用船工具。")
            _, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not permissions.get(str(fleet_id), {}).get(
                "allow_build_starbase", False
            ):
                raise FleetToolError("玩家在执行前撤销了工程船建站权限。")
            destination_system_id = int(
                self.prepared["order"]["destination_system"]["system_id"]
            )
            try:
                refreshed = selected_construction_ship_starbase(
                    profile,
                    fleet_id,
                    destination_system_id,
                )
            except ValueError as error:
                raise FleetToolError(str(error)) from error
        elif action == "order_colony_ship_and_colonize":
            if not self.colonization_enabled:
                raise FleetToolError("玩家在执行前关闭了自动殖民工具。")
            _, profile = self._expansion_profile()
            try:
                refreshed = selected_colonization(
                    profile,
                    candidate_id=str(self.prepared["candidate_id"]),
                    designation=str(self.prepared["designation"]),
                )
            except ValueError as error:
                raise FleetToolError(str(error)) from error
            destination_system_id = refreshed["target_planet"].get("system_id")
        elif action in {
            "upgrade_starbase",
            "set_starbase_module",
            "set_starbase_building",
        }:
            if not self.starbase_enabled:
                raise FleetToolError("玩家在执行前关闭了恒星基地工具。")
            _, profile = self._expansion_profile()
            try:
                refreshed = selected_starbase_operation(
                    profile,
                    candidate_id=str(self.prepared["candidate_id"]),
                    allow_replacement=self.starbase_replacement_enabled,
                )
            except ValueError as error:
                raise FleetToolError(str(error)) from error
            destination_system_id = next(
                (
                    item.get("system_id")
                    for item in profile.get("starbases", [])
                    if int(item["starbase_index"])
                    == int(refreshed["target"]["starbase_object"])
                ),
                None,
            )
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
            raise FleetToolError(str(result.get("error") or "命令未获房主权威确认。"))
        fact = {
            "run_id": run_id,
            "action": action,
            "fleet_id": fleet_id,
            "destination_system_id": destination_system_id,
            "recorded_at": now_iso(),
            "proxy_result": result,
        }
        fact_key = (
            "last_expansion_execution"
            if action
            in {
                "order_colony_ship_and_colonize",
                "upgrade_starbase",
                "set_starbase_module",
                "set_starbase_building",
            }
            else "last_fleet_execution"
        )
        self.store.set_state(fact_key, fact)
        self.prepared = None
        return {
            "schema": "iag.tool_result.fleet_execution.v1",
            "success": True,
            "run_id": run_id,
            "fleet_id": fleet_id,
            "destination_system_id": destination_system_id,
            "action": action,
            "order": refreshed,
            "confirmation": result,
        }

    def dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        if name == "inspect_fleet_state":
            result = self.inspect()
            authorized = sum(
                any(fleet.get("permission", {}).values()) for fleet in result["fleets"]
            )
            callable_now = sum(
                (
                    fleet.get("ai_callable_now") is True
                    or fleet.get("civilian_callable_now") is True
                )
                and any(fleet.get("permission", {}).values())
                for fleet in result["fleets"]
            )
            summary = (
                f"已读取 {len(result['fleets'])} 支玩家舰队；"
                f"{authorized} 支已获至少一项 AI 权限，"
                f"其中 {callable_now} 支当前可调用。"
            )
        elif name == "prepare_fleet_move":
            result = self.prepare_move(arguments)
            source = result["order"]["source_fleet"]
            destination = result["order"]["destination_system"]
            destination_label = str(
                destination.get("display_name_hint")
                or destination.get("name_key")
                or destination["system_id"]
            )
            summary = (
                f"已准备舰队 {fleet_label(source)} 移动到 "
                f"{destination_label}；尚未发包。"
            )
        elif name == "prepare_fleet_coordinate_move":
            result = self.prepare_coordinate_move(arguments)
            source = result["order"]["source_fleet"]
            destination = result["order"]["destination_coordinate"]
            summary = (
                f"已准备舰队 {fleet_label(source)} 在星系 "
                f"{destination['system_origin']} 内移动到 "
                f"({destination['x']}, {destination['y']})；尚未发包。"
            )
        elif name == "prepare_fleet_attack":
            result = self.prepare_attack(arguments)
            source = result["order"]["source_fleet"]
            target = result["order"]["hostile_target"]
            summary = (
                f"已准备舰队 {fleet_label(source)} 攻击敌对舰队 "
                f"{fleet_label(target)}；尚未发包。"
            )
        elif name == "prepare_fleet_repair":
            result = self.prepare_fleet_repair(arguments)
            source = result["order"]["source_fleet"]
            summary = f"已准备舰队 {fleet_label(source)} 返港维修；尚未发包。"
        elif name == "prepare_fleet_upgrade":
            result = self.prepare_fleet_upgrade(arguments)
            source = result["order"]["source_fleet"]
            shipyard = result["order"]["destination_shipyard"]
            destination = (
                shipyard.get("system_display_name_hint")
                or shipyard.get("system_name_key")
                or shipyard.get("system_id")
            )
            summary = (
                f"已准备舰队 {fleet_label(source)} 前往 {destination} 升级；尚未发包。"
            )
        elif name == "prepare_ship_automation":
            result = self.prepare_ship_automation(arguments)
            source = result["order"]["source_fleet"]
            summary = (
                f"已准备民用船 {fleet_label(source)} 的自动化选项："
                f"{', '.join(result['order']['options'])}；尚未发包。"
            )
        elif name == "prepare_construction_ship_starbase":
            result = self.prepare_construction_starbase(arguments)
            source = result["order"]["source_fleet"]
            destination = result["order"]["destination_system"]
            summary = (
                f"已准备工程船 {fleet_label(source)} 在 "
                f"{fleet_label(destination)} 建造恒星基地；尚未发包。"
            )
        elif name == "inspect_expansion_state":
            result = self.inspect_expansion()
            summary = (
                f"已生成 {len(result['colonization_candidates'])} 个殖民候选和 "
                f"{len(result['starbase_operation_candidates'])} 个恒星基地候选。"
            )
        elif name == "prepare_colonization":
            result = self.prepare_colonization(arguments)
            target = result["order"]["target_planet"]
            summary = (
                f"已准备殖民 {fleet_label(target)}，初始规划为 "
                f"{result['designation']}；尚未发包。"
            )
        elif name == "prepare_starbase_operation":
            result = self.prepare_starbase_operation(arguments)
            summary = (
                f"已准备恒星基地操作 {result['action']}："
                f"{result['candidate_id']}；尚未发包。"
            )
        elif name == "execute_prepared_fleet_order":
            result = self.execute(arguments)
            summary = (
                f"{result['action']} 已获房主权威确认；最终游戏状态仍以后续新存档为准。"
            )
        elif name == "inspect_ship_state":
            result = self.inspect_ships()
            summary = (
                f"已读取 {len(result['designs'])} 份玩家舰船设计和 "
                f"{len(result['shipyards'])} 个直接船坞队列。"
            )
        elif name == "inspect_ship_design_options":
            result = self.inspect_ship_design_options(arguments)
            if result.get("selected_section"):
                selected = result["selected_section"]
                summary = (
                    f"已展开区段 {selected['section_template']} 的 "
                    f"{len(selected['component_slots'])} 个组件槽位。"
                )
            else:
                summary = (
                    f"已读取 {len(result['section_options'])} 个可用区段和 "
                    f"{len(result['required_component_options'])} 组必需组件。"
                )
        elif name in {"prepare_ship_design", "prepare_ship_design_clone"}:
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
