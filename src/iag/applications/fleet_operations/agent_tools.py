"""Model tools for save-backed, player-authorized fleet movement."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from iag.core.conversation_store import ConversationStore
from iag.core.resource_ledger import (
    ResourceReservationError,
    ResourceReservationLedger,
)
from iag.stellaris.execution.session_proxy_controller import (
    SessionProxyController,
    SessionProxyError,
)
from iag.stellaris.game_knowledge import detect_game_root
from iag.stellaris.state.campaign_routes import CampaignRoutePlanner
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
from iag.stellaris.state.invasion_profiles import (
    extract_invasion_profiles,
    selected_army_landing,
    selected_army_recruitment,
    selected_orbital_bombardment,
)
from iag.stellaris.state.planet_profiles import load_gamestate
from iag.stellaris.state.save_ingest import resolve_current_save
from iag.stellaris.state.ship_profiles import (
    customize_ship_design,
    extract_ship_profiles,
    ship_design_options,
)
from iag.stellaris.state.world_snapshot import WorldSnapshot, WorldStateService

FLEET_PERMISSIONS_KEY = "fleet_permissions"
FLEET_FULL_DELEGATION_KEY = "fleet_full_delegation"
PENDING_NEW_FLEET_KEY = "pending_new_fleet_creation"
PENDING_CAMPAIGN_KEY = "pending_campaign_route"
PENDING_ARMY_RECRUITMENT_KEY = "pending_army_recruitment"
CAMPAIGN_ROUTE_USAGE_KEY = "campaign_route_usage"


INSPECT_CAMPAIGN_ROUTES_TOOL = {
    "type": "function",
    "function": {
        "name": "inspect_campaign_routes",
        "description": (
            "对一支已选军用舰队前往交战目标星系执行有界多目标 Pareto 路径搜索。"
            "返回多条完整星系路径、关闭边境、恒星基地与行星抑制器成本，并依据"
            "当前舰队部署和历史路线给出确定性的探索/拥堵排序；不会发包。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fleet_id": {"type": "integer"},
                "target_system_id": {"type": "integer"},
            },
            "required": ["fleet_id", "target_system_id"],
            "additionalProperties": False,
        },
    },
}


INSPECT_CAMPAIGN_DEPLOYMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "inspect_campaign_deployment",
        "description": (
            "读取全局战区部署：各敌对目标战线的已知敌军与已投入军力、当前/历史"
            "走廊拥堵、未覆盖的前线或占领区驻防点，以及按相对军力生成的主攻、"
            "战列线和守备建议。每条战线还给出空间军力安全门槛、缺口与候选增援"
            "编组；只有合计达到安全线的原子 task_force_package 才获得进攻分配。"
            "用于先分配舰队角色再检查具体路线；不会发包。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


PREPARE_CAMPAIGN_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_campaign_route",
        "description": (
            "选择 inspect_campaign_routes 返回的一条路线并准备战役第一步。之后每份"
            "新同步存档由固定续接器重新验证并最多推进一步；模型不能自行跳过抑制器、"
            "边境、舰队授权或守军检查。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fleet_id": {"type": "integer"},
                "target_system_id": {"type": "integer"},
                "route_id": {"type": "string"},
                "ground_policy": {
                    "type": "string",
                    "enum": [
                        "bombard_then_land",
                        "land_when_advantaged",
                        "bombard_only",
                    ],
                },
                "bombardment_stance": {
                    "type": "string",
                    "enum": ["selective", "indiscriminate"],
                },
                "transport_fleet_id": {"type": "integer"},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": [
                "fleet_id",
                "target_system_id",
                "route_id",
                "ground_policy",
                "bombardment_stance",
                "reason",
            ],
            "additionalProperties": False,
        },
    },
}

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
            "开启移动权限；目标星系必须有存档可验证的 d32c 目标对象。此工具只用于"
            "重新部署，不能代替攻击命令。"
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
            "准备一条 6b33 舰队攻击命令。目标必须来自最新存档中当前传感器"
            "确认的敌对舰队，或玩家战略地图已知的交战国恒星基地；来源舰队必须"
            "列在目标的 reachable_from_fleet_ids 中并获得逐舰队攻击授权。目标位于"
            "其他星系时也仍然使用本工具。固定层会合计目标星系全部已知敌军和"
            "已在场／正在抵达的友军；低于玩家空间军力安全系数时拒绝发包。"
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

INSPECT_INVASION_TOOL = {
    "type": "function",
    "function": {
        "name": "inspect_invasion_state",
        "description": (
            "读取最新同步存档中的敌方战争殖民地、逐舰队可达性、轨道轰炸姿态、"
            "运输舰队登陆状态，以及固定程序生成的机器人陆军招募候选。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

PREPARE_BOMBARDMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_orbital_bombardment",
        "description": (
            "准备轨道轰炸：先设置已验证的轰炸姿态，再令已授权军用舰队进入"
            "最新存档确认可达的敌方殖民地轨道。两个协议步骤会原子化连续执行。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fleet_id": {"type": "integer"},
                "target_planet_id": {"type": "integer"},
                "stance": {
                    "type": "string",
                    "enum": ["selective", "indiscriminate", "raiding"],
                },
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["fleet_id", "target_planet_id", "stance", "reason"],
            "additionalProperties": False,
        },
    },
}

PREPARE_ARMY_LANDING_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_army_landing",
        "description": (
            "准备运输舰队登陆。来源必须是最新存档中空闲、未失踪且获玩家授权的"
            "运输舰队；目标必须是该舰队当前可达的交战国殖民地。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "transport_fleet_id": {"type": "integer"},
                "target_planet_id": {"type": "integer"},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["transport_fleet_id", "target_planet_id", "reason"],
            "additionalProperties": False,
        },
    },
}

PREPARE_ARMY_RECRUITMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "prepare_army_recruitment",
        "description": (
            "准备机器人进攻部队招募。candidate_id 必须来自 inspect_invasion_state；"
            "count 会展开为同一行动锁内逐条确认的普通招募命令，任一失败即停止。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 5},
                "reason": {"type": "string", "maxLength": 300},
            },
            "required": ["candidate_id", "count", "reason"],
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
            "读取玩家当前可见舰船设计、组件槽位、直接船坞队列和在建舰船。"
            "自动设计关闭后遗留的隐藏自动生成模板不会暴露给模型。"
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
            "按需读取一份玩家当前可见舰船设计可用的区段、必需组件和槽位候选。"
            "隐藏自动生成模板不能作为设计锚点。先不传 "
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
                "new_name": {
                    "type": "string",
                    "maxLength": 48,
                    "description": (
                        "新设计的唯一字面名称。玩家未指定名称时由舰队 Agent "
                        "根据舰型与职责自主拟定；仅使用 ASCII 字母、数字、空格、"
                        "点、下划线或连字符。"
                    ),
                },
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
            "allow_bombardment": bool(permission.get("allow_bombardment", False)),
            "allow_land_armies": bool(permission.get("allow_land_armies", False)),
        }
    return result


FLEET_PERMISSION_FIELDS = (
    "allow_move",
    "allow_attack",
    "allow_reinforce",
    "allow_repair",
    "allow_upgrade",
    "allow_automation",
    "allow_build_starbase",
    "allow_bombardment",
    "allow_land_armies",
)


def fleet_label(fleet: dict[str, Any]) -> str:
    return str(
        fleet.get("display_name_hint")
        or fleet.get("planet_display_name_hint")
        or fleet.get("name_key")
        or fleet.get("planet_name_key")
        or fleet.get("fleet_id")
        or fleet.get("planet_id")
    )


def has_callable_fleet_permission(fleet: dict[str, Any]) -> bool:
    """Return whether any player-authorized capability is callable right now."""
    permission = fleet.get("permission", {})
    if not isinstance(permission, dict):
        return False
    military_default = fleet.get("ai_callable_now") is True
    checks = (
        ("allow_move", fleet.get("move_callable_now", military_default)),
        ("allow_attack", fleet.get("attack_callable_now", military_default)),
        (
            "allow_reinforce",
            fleet.get("reinforcement_callable_now", military_default),
        ),
        ("allow_repair", fleet.get("maintenance_callable_now", False)),
        ("allow_upgrade", fleet.get("maintenance_callable_now", False)),
        ("allow_automation", fleet.get("civilian_callable_now", False)),
        ("allow_build_starbase", fleet.get("civilian_callable_now", False)),
        ("allow_bombardment", fleet.get("attack_callable_now", False)),
        ("allow_land_armies", fleet.get("landing_callable_now", False)),
    )
    return any(
        permission.get(name) is True and callable_now for name, callable_now in checks
    )


class FleetToolbox:
    """One model-turn view over fleet state and one prepared order."""

    # inspect_ship_state is intentionally absent: it may reconcile and persist
    # a newly allocated fleet template after a fresh save arrives.
    parallel_read_tools: ClassVar[frozenset[str]] = frozenset(
        {
            "inspect_fleet_state",
            "inspect_invasion_state",
            "inspect_expansion_state",
            "inspect_campaign_deployment",
            "inspect_campaign_routes",
            "inspect_ship_design_options",
        }
    )

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
            "inspect_invasion_state",
            "prepare_orbital_bombardment",
            "prepare_army_landing",
            "prepare_army_recruitment",
            "inspect_campaign_deployment",
            "inspect_campaign_routes",
            "prepare_campaign_route",
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
    ) -> None:
        self.config = dict(config)
        self.store = store
        self.allow_execute = allow_execute
        self.world_snapshot = world_snapshot
        self.world_state_service = world_state_service
        self.game_root = detect_game_root(self.config)
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
        self.invasion_enabled = bool(
            self.config.get("experimental_invasion_tools_enabled", False)
        )
        self.maximum_army_recruitment_count = max(
            1,
            min(int(self.config.get("maximum_army_recruitment_batch", 5)), 5),
        )
        self.campaign_ground_force_ratio = max(
            1.0,
            min(float(self.config.get("campaign_ground_force_ratio", 1.25)), 5.0),
        )
        self.campaign_space_force_ratio = max(
            1.0,
            min(float(self.config.get("campaign_space_force_ratio", 1.20)), 5.0),
        )
        self.campaign_bombardment_threshold = max(
            0.0,
            min(
                float(self.config.get("campaign_bombardment_threshold", 50.0)),
                100.0,
            ),
        )
        self.auto_authorize_recruited_transport_fleets = bool(
            self.config.get("auto_authorize_recruited_transport_fleets", False)
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
        self._campaign_profile_cache: dict[str, Any] | None = None

    def _full_delegation(self) -> bool:
        return self.store.get_state(FLEET_FULL_DELEGATION_KEY, False) is True

    def _permission_granted(
        self,
        fleet_id: int | str | None,
        field: str,
        permissions: dict[str, dict[str, bool]] | None = None,
    ) -> bool:
        if fleet_id is None:
            return False
        if self._full_delegation() and field.startswith("allow_"):
            return True
        if field not in FLEET_PERMISSION_FIELDS:
            return False
        selected = permissions
        if selected is None:
            selected = normalized_permissions(
                self.store.get_state(FLEET_PERMISSIONS_KEY, {})
            )
        return selected.get(str(fleet_id), {}).get(field, False) is True

    def _rendered_permission(
        self,
        fleet_id: int | str | None,
        permissions: dict[str, dict[str, bool]],
    ) -> dict[str, bool]:
        return {
            field: self._permission_granted(fleet_id, field, permissions)
            for field in FLEET_PERMISSION_FIELDS
        }

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
        if self.invasion_enabled:
            value.extend(
                [
                    INSPECT_INVASION_TOOL,
                    PREPARE_BOMBARDMENT_TOOL,
                    PREPARE_ARMY_LANDING_TOOL,
                    PREPARE_ARMY_RECRUITMENT_TOOL,
                ]
            )
            if self.enabled and self.attack_enabled:
                value.extend(
                    [
                        INSPECT_CAMPAIGN_DEPLOYMENT_TOOL,
                        INSPECT_CAMPAIGN_ROUTES_TOOL,
                        PREPARE_CAMPAIGN_TOOL,
                    ]
                )
        if self.allow_execute and (
            self.enabled
            or self.maintenance_enabled
            or self.civilian_ship_enabled
            or self.colonization_enabled
            or self.starbase_enabled
            or self.invasion_enabled
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
            raise FleetToolError("当前战役会话尚未绑定房主存档。")
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
        snapshot = self._pinned_snapshot()
        if snapshot is not None:
            return snapshot.path, snapshot.fleet_profile()
        path = self._save_path()
        return path, extract_fleet_profiles(load_gamestate(path))

    def _ship_profile(self) -> tuple[Path, dict[str, Any]]:
        snapshot = self._pinned_snapshot()
        if snapshot is not None:
            return snapshot.path, snapshot.ship_profile(game_root=self.game_root)
        path = self._save_path()
        return path, extract_ship_profiles(
            load_gamestate(path),
            game_root=self.game_root,
        )

    def _expansion_profile(self) -> tuple[Path, dict[str, Any]]:
        snapshot = self._pinned_snapshot()
        if snapshot is not None:
            if self.game_root is None:
                raise FleetToolError("未找到 Stellaris 安装目录。")
            return snapshot.path, snapshot.expansion_profile(
                game_root=self.game_root,
                minimum_habitability=self.minimum_colonization_habitability,
            )
        path = self._save_path()
        return path, extract_expansion_profiles(
            load_gamestate(path),
            game_root=self.game_root,
            minimum_habitability=self.minimum_colonization_habitability,
        )

    def _invasion_profile(self) -> tuple[Path, dict[str, Any]]:
        snapshot = self._pinned_snapshot()
        if snapshot is not None:
            return snapshot.path, snapshot.invasion_profile(
                game_root=self.game_root,
                maximum_recruitment_count=self.maximum_army_recruitment_count,
            )
        path = self._save_path()
        return path, extract_invasion_profiles(
            load_gamestate(path),
            game_root=self.game_root,
            maximum_recruitment_count=self.maximum_army_recruitment_count,
        )

    def _campaign_profiles(
        self,
    ) -> tuple[
        Path,
        dict[str, Any],
        dict[str, Any],
        CampaignRoutePlanner,
    ]:
        snapshot = self._pinned_snapshot()
        if snapshot is not None:
            commitments = self._campaign_deployment_commitments()
            fleet_profile = snapshot.fleet_profile()
            invasion_profile = snapshot.invasion_profile(
                game_root=self.game_root,
                maximum_recruitment_count=self.maximum_army_recruitment_count,
            )
            planner = snapshot.campaign_planner(
                game_root=self.game_root,
                maximum_recruitment_count=self.maximum_army_recruitment_count,
                deployment_commitments=commitments,
                minimum_space_force_ratio=self.campaign_space_force_ratio,
            )
            return snapshot.path, fleet_profile, invasion_profile, planner
        path = self._save_path()
        save_hash = sha256_file(path)
        commitments = self._campaign_deployment_commitments()
        commitment_hash = hashlib.sha256(
            json.dumps(
                commitments,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        cache = self._campaign_profile_cache
        if (
            cache is not None
            and cache.get("path") == path
            and cache.get("save_hash") == save_hash
            and cache.get("commitment_hash") == commitment_hash
        ):
            return (
                path,
                cache["fleet_profile"],
                cache["invasion_profile"],
                cache["planner"],
            )
        text = load_gamestate(path)
        fleet_profile = extract_fleet_profiles(text)
        invasion_profile = extract_invasion_profiles(
            text,
            game_root=self.game_root,
            maximum_recruitment_count=self.maximum_army_recruitment_count,
            fleet_profile=fleet_profile,
        )
        planner = CampaignRoutePlanner(
            text,
            game_root=self.game_root,
            fleet_profile=fleet_profile,
            invasion_profile=invasion_profile,
            maximum_recruitment_count=self.maximum_army_recruitment_count,
            deployment_commitments=commitments,
            minimum_space_force_ratio=self.campaign_space_force_ratio,
        )
        self._campaign_profile_cache = {
            "path": path,
            "save_hash": save_hash,
            "commitment_hash": commitment_hash,
            "fleet_profile": fleet_profile,
            "invasion_profile": invasion_profile,
            "planner": planner,
        }
        return path, fleet_profile, invasion_profile, planner

    def _campaign_deployment_commitments(self) -> list[dict[str, Any]]:
        raw_usage = self.store.get_state(CAMPAIGN_ROUTE_USAGE_KEY, [])
        usage = (
            [dict(item) for item in raw_usage if isinstance(item, dict)]
            if isinstance(raw_usage, list)
            else []
        )
        pending = self.store.get_state(PENDING_CAMPAIGN_KEY)
        if isinstance(pending, dict) and pending.get("selected_path_system_ids"):
            pending_id = str(pending.get("campaign_plan_id") or "")
            usage = [
                item
                for item in usage
                if str(item.get("campaign_plan_id") or "") != pending_id
            ]
            usage.append(
                {
                    "campaign_plan_id": pending_id,
                    "fleet_id": pending.get("fleet_id"),
                    "target_system_id": pending.get("target_system_id"),
                    "path_system_ids": pending.get("selected_path_system_ids"),
                    "route_type": pending.get("route_type"),
                    "status": pending.get("status") or "active",
                }
            )
        return usage[-64:]

    def _record_campaign_route_usage(
        self,
        plan: dict[str, Any],
        *,
        status: str,
    ) -> None:
        if not plan.get("selected_path_system_ids"):
            return
        plan_id = str(plan.get("campaign_plan_id") or "")
        raw = self.store.get_state(CAMPAIGN_ROUTE_USAGE_KEY, [])
        usage = (
            [dict(item) for item in raw if isinstance(item, dict)]
            if isinstance(raw, list)
            else []
        )
        usage = [
            item for item in usage if str(item.get("campaign_plan_id") or "") != plan_id
        ]
        usage.append(
            {
                "campaign_plan_id": plan_id,
                "fleet_id": int(plan["fleet_id"]),
                "target_system_id": int(plan["target_system_id"]),
                "path_system_ids": [
                    int(value) for value in plan["selected_path_system_ids"]
                ],
                "route_type": str(plan.get("route_type") or ""),
                "status": status,
                "updated_at": now_iso(),
                "last_action_game_date": plan.get("last_action_game_date"),
            }
        )
        self.store.set_state(CAMPAIGN_ROUTE_USAGE_KEY, usage[-64:])

    def _resource_ledger(self) -> ResourceReservationLedger:
        return ResourceReservationLedger(self.store)

    @staticmethod
    def _country_stockpile(profile: dict[str, Any]) -> dict[str, Any]:
        value = profile.get("country_stockpile", {})
        return dict(value) if isinstance(value, dict) else {}

    def _resource_status(
        self,
        path: Path,
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self._resource_ledger().status(
                source_save_sha256=sha256_file(path),
                stockpile=self._country_stockpile(profile),
            )
        except ResourceReservationError as error:
            raise FleetToolError(str(error)) from error

    def _campaign_landing_assessment(
        self,
        *,
        target: dict[str, Any],
        transport: dict[str, Any] | None,
    ) -> dict[str, Any]:
        defenders = target.get("defending_armies", {})
        defenders = defenders if isinstance(defenders, dict) else {}
        attackers = (
            transport.get("transport_armies", {}) if isinstance(transport, dict) else {}
        )
        attackers = attackers if isinstance(attackers, dict) else {}
        attacker_health = float(attackers.get("current_health_total") or 0.0)
        defender_health = float(defenders.get("current_health_total") or 0.0)
        required_health = defender_health * self.campaign_ground_force_ratio
        adequate = defender_health <= 0 or attacker_health >= required_health
        return {
            "adequate": adequate,
            "attacker_army_count": int(attackers.get("army_count") or 0),
            "defender_army_count": int(defenders.get("army_count") or 0),
            "attacker_current_health_total": attacker_health,
            "defender_current_health_total": defender_health,
            "required_attacker_health_total": required_health,
            "safety_ratio": self.campaign_ground_force_ratio,
            "authority": (
                "save_army_health_pool_comparison_not_full_combat_simulation"
            ),
        }

    @staticmethod
    def _transport_army_count(fleet: dict[str, Any]) -> int:
        armies = fleet.get("transport_armies", {})
        parsed_count = (
            int(armies.get("army_count") or 0) if isinstance(armies, dict) else 0
        )
        return max(int(fleet.get("ship_count") or 0), parsed_count)

    def _campaign_ground_step(
        self,
        *,
        plan: dict[str, Any],
        target: dict[str, Any],
        invasion_profile: dict[str, Any],
        permissions: dict[str, dict[str, bool]],
    ) -> dict[str, Any]:
        fleet_id = int(plan["fleet_id"])
        source = next(
            (
                item
                for item in invasion_profile.get("fleets", [])
                if int(item["fleet_id"]) == fleet_id
            ),
            None,
        )
        transport_id = plan.get("transport_fleet_id")
        transport = next(
            (
                item
                for item in invasion_profile.get("fleets", [])
                if transport_id is not None
                and int(item["fleet_id"]) == int(transport_id)
            ),
            None,
        )
        assessment = self._campaign_landing_assessment(
            target=target,
            transport=transport,
        )
        landing_authorized = bool(
            transport is not None
            and self._permission_granted(
                transport_id,
                "allow_land_armies",
                permissions,
            )
        )
        landing_ready = bool(
            landing_authorized
            and transport.get("landing_callable_now", False)
            and assessment["adequate"]
        )
        damage = float(target.get("bombardment_damage") or 0.0)
        ground_policy = str(plan["ground_policy"])
        prefer_landing = ground_policy == "land_when_advantaged" or (
            ground_policy == "bombard_then_land"
            and damage >= self.campaign_bombardment_threshold
        )
        if prefer_landing and landing_ready:
            return {
                "state": "ready",
                "action": "land_armies",
                "order": selected_army_landing(
                    invasion_profile,
                    transport_fleet_id=int(transport_id),
                    target_planet_id=int(target["planet_id"]),
                ),
                "landing_assessment": assessment,
            }

        if (
            ground_policy != "bombard_only"
            and transport is None
            and damage >= (self.campaign_bombardment_threshold)
        ):
            return {
                "state": "waiting_for_transport_fleet",
                "mutated_game": False,
                "landing_assessment": assessment,
            }
        if (
            ground_policy != "bombard_only"
            and transport is not None
            and not landing_authorized
            and damage >= self.campaign_bombardment_threshold
        ):
            return {
                "state": "waiting_for_transport_permission",
                "mutated_game": False,
                "transport_fleet_id": int(transport_id),
                "landing_assessment": assessment,
            }
        if (
            ground_policy != "bombard_only"
            and transport is not None
            and landing_authorized
            and damage >= self.campaign_bombardment_threshold
            and not landing_ready
        ):
            return {
                "state": "waiting_for_stronger_transport",
                "mutated_game": False,
                "transport_fleet_id": int(transport_id),
                "landing_assessment": assessment,
            }
        if source is None or not source.get("attack_callable_now", False):
            return {
                "state": "waiting_for_military_fleet",
                "mutated_game": False,
                "landing_assessment": assessment,
            }
        if not self._permission_granted(
            fleet_id,
            "allow_bombardment",
            permissions,
        ):
            return {
                "state": "waiting_for_bombardment_permission",
                "mutated_game": False,
                "landing_assessment": assessment,
            }
        if ground_policy == "bombard_only" and damage >= (
            self.campaign_bombardment_threshold
        ):
            return {
                "state": "waiting_for_inhibitor_disable_or_manual_occupation",
                "mutated_game": False,
                "bombardment_damage": damage,
                "landing_assessment": assessment,
            }
        return {
            "state": "ready",
            "action": "orbital_bombardment",
            "order": selected_orbital_bombardment(
                invasion_profile,
                fleet_id=fleet_id,
                target_planet_id=int(target["planet_id"]),
                stance=str(plan["bombardment_stance"]),
            ),
            "landing_assessment": assessment,
        }

    @staticmethod
    def _campaign_space_force_gate(
        route: dict[str, Any],
        system_id: int,
    ) -> dict[str, Any] | None:
        assessment = route.get("force_assessment", {})
        if not isinstance(assessment, dict):
            return None
        engagement = next(
            (
                item
                for item in assessment.get("engagements", [])
                if isinstance(item, dict)
                and int(item.get("system_id", -1)) == int(system_id)
            ),
            None,
        )
        if engagement is None or engagement.get("hard_gate_satisfied", False):
            return None
        status = str(engagement.get("status") or "force_assessment_unavailable")
        return {
            "state": (
                "waiting_for_space_reinforcements"
                if status == "reinforcement_required"
                else "waiting_for_enemy_force_intelligence"
            ),
            "mutated_game": False,
            "blocking_system_id": int(system_id),
            "space_force_assessment": engagement,
            "additional_military_power_required": float(
                engagement.get("additional_military_power_required") or 0.0
            ),
        }

    def _campaign_next_step(
        self,
        *,
        plan: dict[str, Any],
        route: dict[str, Any],
        fleet_profile: dict[str, Any],
        invasion_profile: dict[str, Any],
    ) -> dict[str, Any]:
        fleet_id = int(plan["fleet_id"])
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        source = next(
            (
                item
                for item in fleet_profile.get("fleets", [])
                if int(item["fleet_id"]) == fleet_id
            ),
            None,
        )
        if source is None:
            return {"state": "source_fleet_missing", "mutated_game": False}

        blockers = list(route.get("blockers", []))
        objectives = blockers[0] if blockers else route.get("target_objectives", {})
        starbases = list(
            objectives.get(
                "starbase_sources" if blockers else "hostile_starbases",
                [],
            )
        )
        if starbases:
            if not self._permission_granted(
                fleet_id,
                "allow_attack",
                permissions,
            ):
                return {
                    "state": "waiting_for_attack_permission",
                    "mutated_game": False,
                }
            if not source.get("attack_callable_now", False):
                return {"state": "waiting_for_military_fleet", "mutated_game": False}
            target = min(starbases, key=lambda item: int(item["fleet_id"]))
            force_gate = self._campaign_space_force_gate(
                route,
                int(target["system_id"]),
            )
            if force_gate is not None:
                return force_gate
            return {
                "state": "ready",
                "action": "attack_fleet",
                "order": selected_attack(
                    fleet_profile,
                    fleet_id,
                    int(target["fleet_id"]),
                ),
            }

        planetary = list(
            objectives.get(
                "planetary_sources" if blockers else "hostile_colonies",
                [],
            )
        )
        if planetary:
            target = min(planetary, key=lambda item: int(item["planet_id"]))
            return self._campaign_ground_step(
                plan=plan,
                target=target,
                invasion_profile=invasion_profile,
                permissions=permissions,
            )
        if blockers:
            return {
                "state": "unknown_inhibitor_source_needs_review",
                "mutated_game": False,
                "blocking_system_id": blockers[0].get("system_id"),
            }

        mobile_targets = list(objectives.get("hostile_mobile_fleets", []))
        if mobile_targets:
            if not self._permission_granted(
                fleet_id,
                "allow_attack",
                permissions,
            ):
                return {
                    "state": "waiting_for_attack_permission",
                    "mutated_game": False,
                }
            if not source.get("attack_callable_now", False):
                return {"state": "waiting_for_military_fleet", "mutated_game": False}
            target = min(
                mobile_targets,
                key=lambda item: (
                    -float(item.get("military_power") or 0.0),
                    int(item["fleet_id"]),
                ),
            )
            force_gate = self._campaign_space_force_gate(
                route,
                int(target["system_id"]),
            )
            if force_gate is not None:
                return force_gate
            return {
                "state": "ready",
                "action": "attack_fleet",
                "order": selected_attack(
                    fleet_profile,
                    fleet_id,
                    int(target["fleet_id"]),
                ),
            }
        return {"state": "completed", "mutated_game": False}

    def inspect_campaign_deployment(
        self,
        _arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.enabled or not self.attack_enabled or not self.invasion_enabled:
            raise FleetToolError("战区部署需要同时启用舰队移动、攻击与入侵工具。")
        path, fleet_profile, _invasion, planner = self._campaign_profiles()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if self._full_delegation():
            authorized = {
                int(fleet["fleet_id"])
                for fleet in fleet_profile.get("fleets", [])
                if isinstance(fleet, dict) and fleet.get("fleet_id") is not None
            }
        else:
            authorized = {
                int(fleet_id)
                for fleet_id, grants in permissions.items()
                if grants.get("allow_move", False) and grants.get("allow_attack", False)
            }
        return {
            **planner.deployment_summary(authorized_fleet_ids=authorized),
            "source_save": str(path),
            "source_save_sha256": sha256_file(path),
            "resource_reservations": self._resource_status(path, fleet_profile),
            "authorization_rule": (
                "Full delegation authorizes every otherwise callable fleet."
                if self._full_delegation()
                else "Only fleets with both allow_move and allow_attack appear in "
                "role recommendations; garrison coverage still reflects every "
                "observed player military fleet."
            ),
        }

    def inspect_campaign_routes(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled or not self.attack_enabled or not self.invasion_enabled:
            raise FleetToolError("战役路线需要同时启用舰队移动、攻击与入侵工具。")
        path, fleet_profile, _invasion, planner = self._campaign_profiles()
        try:
            result = planner.plan(
                fleet_id=int(arguments["fleet_id"]),
                target_system_id=int(arguments["target_system_id"]),
            )
        except (TypeError, ValueError) as error:
            raise FleetToolError(str(error)) from error
        return {
            **result,
            "source_save": str(path),
            "source_save_sha256": sha256_file(path),
            "resource_reservations": self._resource_status(path, fleet_profile),
        }

    def prepare_campaign_route(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled or not self.attack_enabled or not self.invasion_enabled:
            raise FleetToolError("战役路线需要同时启用舰队移动、攻击与入侵工具。")
        pending = self.store.get_state(PENDING_CAMPAIGN_KEY)
        if isinstance(pending, dict):
            raise FleetToolError("当前战役已有一条路线正在执行或等待新存档。")
        ground_policy = str(arguments.get("ground_policy") or "")
        if ground_policy not in {
            "bombard_then_land",
            "land_when_advantaged",
            "bombard_only",
        }:
            raise FleetToolError("不支持的战役地面行动策略。")
        bombardment_stance = str(arguments.get("bombardment_stance") or "")
        if bombardment_stance not in {"selective", "indiscriminate"}:
            raise FleetToolError("战役轰炸姿态必须是选择性或无差别轰炸。")
        path, fleet_profile, invasion_profile, planner = self._campaign_profiles()
        try:
            route_profile = planner.plan(
                fleet_id=int(arguments["fleet_id"]),
                target_system_id=int(arguments["target_system_id"]),
            )
            route = CampaignRoutePlanner.select_route(
                route_profile,
                str(arguments["route_id"]),
            )
        except (TypeError, ValueError) as error:
            raise FleetToolError(str(error)) from error
        plan_id = "campaign_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        plan = {
            "schema": "iag.pending_campaign_route.v1",
            "campaign_plan_id": plan_id,
            "status": "prepared",
            "fleet_id": int(arguments["fleet_id"]),
            "target_system_id": int(arguments["target_system_id"]),
            "selected_route_id": str(route["route_id"]),
            "route_type": str(route["route_type"]),
            "selected_path_system_ids": list(route["path_system_ids"]),
            "ground_policy": ground_policy,
            "bombardment_stance": bombardment_stance,
            "transport_fleet_id": (
                int(arguments["transport_fleet_id"])
                if arguments.get("transport_fleet_id") is not None
                else None
            ),
            "reason": str(arguments["reason"]).strip(),
            "created_at": now_iso(),
            "source_save_sha256": sha256_file(path),
            "source_game_date": fleet_profile.get("game_date"),
        }
        current_route = {
            **route,
            "target_objectives": route_profile["target_objectives"],
        }
        try:
            step = self._campaign_next_step(
                plan=plan,
                route=current_route,
                fleet_profile=fleet_profile,
                invasion_profile=invasion_profile,
            )
        except ValueError as error:
            raise FleetToolError(str(error)) from error
        plan["current_step"] = step
        if step.get("state") != "ready":
            plan["status"] = str(step.get("state") or "needs_review")
            plan["last_evaluated_save_sha256"] = sha256_file(path)
            plan["updated_at"] = now_iso()
            if plan["status"] == "completed":
                self.store.set_state("last_campaign_route", plan)
            elif plan["status"] not in {
                "waiting_for_space_reinforcements",
                "waiting_for_enemy_force_intelligence",
            }:
                self.store.set_state(PENDING_CAMPAIGN_KEY, plan)
            return {
                **plan,
                "execution_required": False,
                "run_id": None,
            }
        self.prepared = {
            "run_id": plan_id,
            "action": str(step["action"]),
            "reason": plan["reason"],
            "order": dict(step["order"]),
            "campaign_plan": plan,
            "source_save_sha256": sha256_file(path),
            "source_game_date": fleet_profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return {
            **plan,
            "run_id": plan_id,
            "execution_required": True,
            "prepared_action": step["action"],
        }

    def inspect_ships(self) -> dict[str, Any]:
        path, profile = self._ship_profile()
        pending = self._pending_new_fleet_status()
        public_profile = dict(profile)
        country_designs = list(public_profile.get("designs", []))
        public_profile["designs"] = [
            design
            for design in country_designs
            if isinstance(design, dict) and design.get("player_visible", True)
        ]
        public_profile["country_design_record_count"] = len(country_designs)
        public_profile["hidden_autogenerated_design_count"] = sum(
            isinstance(design, dict) and design.get("player_visible") is False
            for design in country_designs
        )
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
        if not self._permission_granted(
            fleet_id,
            "allow_reinforce",
            permissions,
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

    def reconcile_pending_army_recruitment_from_save(self) -> dict[str, Any]:
        """Resolve the transport fleet created by a verified recruitment batch."""
        raw = self.store.get_state(PENDING_ARMY_RECRUITMENT_KEY)
        if not isinstance(raw, dict):
            return {
                "schema": "iag.save_continuation.army_recruitment.v1",
                "state": "idle",
                "mutated_game": False,
            }
        path, profile = self._invasion_profile()
        current_hash = sha256_file(path)
        baseline = {int(value) for value in raw.get("baseline_transport_fleet_ids", [])}
        baseline_rows = raw.get("baseline_transport_fleets", [])
        baseline_counts = {
            int(item["fleet_id"]): int(item.get("army_count") or 0)
            for item in baseline_rows
            if isinstance(item, dict) and item.get("fleet_id") is not None
        }
        transports: list[dict[str, Any]] = []
        for fleet in profile.get("fleets", []):
            if fleet.get("ship_class") != "shipclass_transport":
                continue
            fleet_id = int(fleet["fleet_id"])
            current_count = self._transport_army_count(fleet)
            if fleet_id not in baseline or (
                fleet_id in baseline_counts
                and current_count > baseline_counts[fleet_id]
            ):
                transports.append(fleet)
        pending = {
            **raw,
            "last_checked_save_sha256": current_hash,
            "last_checked_game_date": profile.get("game_date"),
            "updated_at": now_iso(),
        }
        if not transports:
            pending["phase"] = "waiting_for_transport_formation"
            self.store.set_state(PENDING_ARMY_RECRUITMENT_KEY, pending)
            return {
                "schema": "iag.save_continuation.army_recruitment.v1",
                "state": "waiting_for_transport_formation",
                "mutated_game": False,
                "game_date": profile.get("game_date"),
            }
        if len(transports) != 1:
            pending["phase"] = "needs_review"
            pending["candidate_transport_fleet_ids"] = sorted(
                int(fleet["fleet_id"]) for fleet in transports
            )
            self.store.set_state(PENDING_ARMY_RECRUITMENT_KEY, pending)
            return {
                "schema": "iag.save_continuation.army_recruitment.v1",
                "state": "needs_review",
                "mutated_game": False,
                "candidate_transport_fleet_ids": pending[
                    "candidate_transport_fleet_ids"
                ],
            }

        transport = transports[0]
        transport_id = int(transport["fleet_id"])
        auto_authorized = False
        if self.auto_authorize_recruited_transport_fleets:
            permissions = normalized_permissions(
                self.store.get_state(FLEET_PERMISSIONS_KEY, {})
            )
            permission = dict(permissions.get(str(transport_id), {}))
            permission["allow_land_armies"] = True
            permissions[str(transport_id)] = permission
            self.store.set_state(FLEET_PERMISSIONS_KEY, permissions)
            auto_authorized = True
        expected = int(raw.get("expected_army_count") or 0)
        observed_army_count = self._transport_army_count(transport)
        baseline_army_count = baseline_counts.get(transport_id, 0)
        recruited_army_count = max(observed_army_count - baseline_army_count, 0)
        complete = recruited_army_count >= expected
        resolved = {
            **pending,
            "phase": "confirmed_in_save" if complete else "transport_forming",
            "transport_fleet_id": transport_id,
            "transport_ship_count": int(transport.get("ship_count") or 0),
            "observed_army_count": observed_army_count,
            "baseline_army_count": baseline_army_count,
            "recruited_army_count": recruited_army_count,
            "transport_power": transport.get("transport_power"),
            "transport_armies": transport.get("transport_armies"),
            "auto_authorized_for_landing": auto_authorized,
        }
        self.store.set_state("last_army_recruitment", resolved)
        self.store.set_state(
            PENDING_ARMY_RECRUITMENT_KEY,
            None if complete else resolved,
        )
        campaign = self.store.get_state(PENDING_CAMPAIGN_KEY)
        if (
            isinstance(campaign, dict)
            and campaign.get("campaign_plan_id") == raw.get("campaign_plan_id")
            and (
                campaign.get("transport_fleet_id") is None
                or campaign.get("status")
                in {"waiting_for_transport_fleet", "waiting_for_stronger_transport"}
            )
        ):
            campaign["transport_fleet_id"] = transport_id
            campaign["updated_at"] = now_iso()
            self.store.set_state(PENDING_CAMPAIGN_KEY, campaign)
        return {
            "schema": "iag.save_continuation.army_recruitment.v1",
            "state": resolved["phase"],
            "mutated_game": False,
            "transport_fleet_id": transport_id,
            "transport_ship_count": resolved["transport_ship_count"],
            "recruited_army_count": recruited_army_count,
            "auto_authorized_for_landing": auto_authorized,
            "game_date": profile.get("game_date"),
        }

    def continue_pending_campaign_from_save(self) -> dict[str, Any]:
        """Advance at most one save-backed campaign action."""
        if not self.allow_execute:
            raise FleetToolError("当前运行策略不允许自动续接战役。")
        raw = self.store.get_state(PENDING_CAMPAIGN_KEY)
        if not isinstance(raw, dict):
            return {
                "schema": "iag.save_continuation.campaign_route.v1",
                "state": "idle",
                "mutated_game": False,
            }
        path, fleet_profile, invasion_profile, planner = self._campaign_profiles()
        current_hash = sha256_file(path)
        if current_hash == str(raw.get("last_action_save_sha256") or ""):
            return {
                "schema": "iag.save_continuation.campaign_route.v1",
                "state": "waiting_for_fresh_save",
                "mutated_game": False,
                "game_date": fleet_profile.get("game_date"),
            }
        plan = {
            **raw,
            "last_evaluated_save_sha256": current_hash,
            "last_evaluated_game_date": fleet_profile.get("game_date"),
            "updated_at": now_iso(),
        }
        try:
            route_profile = planner.plan(
                fleet_id=int(plan["fleet_id"]),
                target_system_id=int(plan["target_system_id"]),
            )
        except ValueError as error:
            if "no save-backed active-war objective" in str(error):
                plan["status"] = "completed"
                plan["completion_reason"] = "target_system_has_no_hostile_objective"
                self.store.set_state("last_campaign_route", plan)
                self.store.set_state(PENDING_CAMPAIGN_KEY, None)
                self._record_campaign_route_usage(plan, status="completed")
                return {
                    "schema": "iag.save_continuation.campaign_route.v1",
                    "state": "completed",
                    "mutated_game": False,
                    "target_system_id": plan["target_system_id"],
                }
            plan["status"] = "needs_review"
            plan["error"] = str(error)
            self.store.set_state(PENDING_CAMPAIGN_KEY, plan)
            return {
                "schema": "iag.save_continuation.campaign_route.v1",
                "state": "needs_review",
                "mutated_game": False,
                "error": str(error),
            }
        route = CampaignRoutePlanner.resume_route(
            route_profile,
            previous_route_id=str(plan.get("selected_route_id") or "") or None,
            previous_path_system_ids=[
                int(value) for value in plan.get("selected_path_system_ids", [])
            ],
            previous_route_type=str(plan.get("route_type") or "") or None,
        )
        if route is None:
            plan["status"] = str(route_profile.get("status") or "needs_review")
            plan["route_diagnostics"] = {
                "border_access_blockers": route_profile.get(
                    "border_access_blockers", []
                )
            }
            self.store.set_state(PENDING_CAMPAIGN_KEY, plan)
            return {
                "schema": "iag.save_continuation.campaign_route.v1",
                "state": plan["status"],
                "mutated_game": False,
            }
        plan.update(
            {
                "selected_route_id": route["route_id"],
                "route_type": route["route_type"],
                "selected_path_system_ids": list(route["path_system_ids"]),
            }
        )
        route = {**route, "target_objectives": route_profile["target_objectives"]}
        try:
            step = self._campaign_next_step(
                plan=plan,
                route=route,
                fleet_profile=fleet_profile,
                invasion_profile=invasion_profile,
            )
        except ValueError as error:
            plan["status"] = "needs_review"
            plan["error"] = str(error)
            self.store.set_state(PENDING_CAMPAIGN_KEY, plan)
            return {
                "schema": "iag.save_continuation.campaign_route.v1",
                "state": "needs_review",
                "mutated_game": False,
                "error": str(error),
            }
        plan["current_step"] = step
        if step.get("state") != "ready":
            plan["status"] = str(step.get("state") or "needs_review")
            if plan["status"] == "completed":
                self.store.set_state("last_campaign_route", plan)
                self.store.set_state(PENDING_CAMPAIGN_KEY, None)
                self._record_campaign_route_usage(plan, status="completed")
            else:
                self.store.set_state(PENDING_CAMPAIGN_KEY, plan)
            return {
                "schema": "iag.save_continuation.campaign_route.v1",
                "state": plan["status"],
                "mutated_game": False,
                "current_step": step,
                "game_date": fleet_profile.get("game_date"),
            }

        run_id = "campaign_continue_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": str(step["action"]),
            "reason": str(plan.get("reason") or "save-backed campaign continuation"),
            "order": dict(step["order"]),
            "campaign_plan": plan,
            "source_save_sha256": current_hash,
            "source_game_date": fleet_profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        execution = self.execute({"run_id": run_id})
        confirmed_steps = int(execution.get("confirmed_protocol_steps") or 0)
        mutated_game = bool(execution.get("success") or confirmed_steps > 0)
        return {
            "schema": "iag.save_continuation.campaign_route.v1",
            "state": "executed" if execution.get("success") else "partially_executed",
            "mutated_game": mutated_game,
            "action": execution.get("action"),
            "fleet_id": execution.get("fleet_id"),
            "target_system_id": plan.get("target_system_id"),
            "game_date": fleet_profile.get("game_date"),
            "execution": execution,
        }

    def execute_ship_action(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.allow_execute:
            raise FleetToolError("当前运行策略不允许自动执行。")
        run_id = str(arguments.get("run_id", ""))
        if self.prepared is None or run_id != self.prepared["run_id"]:
            raise FleetToolError("run_id 必须来自本回合刚准备的舰船动作。")
        action = str(self.prepared.get("action") or "")
        resource_reservation_id: str | None = None
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
                if not self._permission_granted(
                    fleet_id,
                    "allow_reinforce",
                    permissions,
                ):
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
            resource_reservation_id = f"fleet_operations:{run_id}"
            try:
                self._resource_ledger().reserve(
                    reservation_id=resource_reservation_id,
                    application_id="fleet_operations",
                    action=action,
                    source_save_sha256=sha256_file(profile_path),
                    source_game_date=profile.get("game_date"),
                    stockpile=self._country_stockpile(profile),
                    exclusive=True,
                )
            except ResourceReservationError as error:
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
            if confirmed_steps == 0:
                self._resource_ledger().release(resource_reservation_id)
                resource_reservation_id = None
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
                "resource_reservation_id": resource_reservation_id,
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
            fleet_view = {
                key: value for key, value in fleet.items() if key != "ship_ids"
            }
            fleets.append(
                {
                    **fleet_view,
                    "permission": self._rendered_permission(
                        fleet["fleet_id"],
                        permissions,
                    ),
                }
            )
        return {
            **profile,
            "source_save": str(path),
            "source_save_sha256": sha256_file(path),
            "resource_reservations": self._resource_status(path, profile),
            "fleets": fleets,
            "full_delegation": self._full_delegation(),
            "attack_protocol_state": (
                "save_backed_war_targets_inhibitor_routes_and_paired_6b33_verified"
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

    def inspect_invasion(self) -> dict[str, Any]:
        path, profile = self._invasion_profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        fleets: list[dict[str, Any]] = []
        for fleet in profile["fleets"]:
            if not (
                fleet.get("bombardment_verified_family", False)
                or fleet.get("landing_verified_family", False)
            ):
                continue
            rendered_permission = self._rendered_permission(
                fleet["fleet_id"],
                permissions,
            )
            fleets.append(
                {
                    **{key: value for key, value in fleet.items() if key != "ship_ids"},
                    "permission": {
                        "allow_bombardment": rendered_permission["allow_bombardment"],
                        "allow_land_armies": rendered_permission["allow_land_armies"],
                    },
                }
            )
        return {
            **profile,
            "source_save": str(path),
            "source_save_sha256": sha256_file(path),
            "resource_reservations": self._resource_status(path, profile),
            "fleets": fleets,
            "full_delegation": self._full_delegation(),
            "invasion_tools_enabled": self.invasion_enabled,
            "protocol_state": (
                "d62d_plus_d32c_bombardment_6f33_landing_b43d_recruitment"
            ),
        }

    def inspect_expansion(self) -> dict[str, Any]:
        path, profile = self._expansion_profile()
        return {
            **profile,
            "source_save": str(path),
            "source_save_sha256": sha256_file(path),
            "resource_reservations": self._resource_status(path, profile),
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
        if not self._permission_granted(fleet_id, "allow_move", permissions):
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
        if not self._permission_granted(fleet_id, "allow_move", permissions):
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

    def _direct_attack_force_assessment(
        self,
        profile: dict[str, Any],
        *,
        fleet_id: int,
        target_fleet_id: int,
    ) -> dict[str, Any]:
        source = next(
            (
                item
                for item in profile.get("fleets", [])
                if int(item["fleet_id"]) == int(fleet_id)
            ),
            None,
        )
        target = next(
            (
                item
                for item in profile.get("hostile_targets", [])
                if int(item["fleet_id"]) == int(target_fleet_id)
            ),
            None,
        )
        if source is None or target is None or target.get("system_id") is None:
            raise FleetToolError("无法从最新存档重建攻击军力评估。")
        target_system_id = int(target["system_id"])
        hostile_by_id = {
            int(item["fleet_id"]): item
            for item in profile.get("hostile_targets", [])
            if item.get("system_id") is not None
            and int(item["system_id"]) == target_system_id
        }
        known_hostile_power = sum(
            float(item.get("military_power") or 0.0)
            for item in hostile_by_id.values()
            if item.get("military_power") is not None
        )
        unknown_hostiles = sorted(
            hostile_id
            for hostile_id, item in hostile_by_id.items()
            if item.get("military_power") is None
        )
        supporting: list[dict[str, Any]] = []
        for fleet in profile.get("fleets", []):
            support_id = int(fleet["fleet_id"])
            if support_id == fleet_id or not fleet.get("attack_verified_family", False):
                continue
            movement = fleet.get("movement", {})
            current = movement.get("current_system_id")
            destination = movement.get("target_system_id")
            already_present = bool(
                current is not None
                and int(current) == target_system_id
                and fleet.get("availability") not in {"MIA", "UNAVAILABLE"}
            )
            incoming = bool(
                destination is not None
                and int(destination) == target_system_id
                and fleet.get("attack_callable_now", False)
            )
            if not already_present and not incoming:
                continue
            supporting.append(
                {
                    "fleet_id": support_id,
                    "military_power": float(fleet.get("military_power") or 0.0),
                    "support_authority": (
                        "currently_in_target_system"
                        if already_present
                        else "save_movement_target_system"
                    ),
                }
            )
        source_power = float(source.get("military_power") or 0.0)
        support_power = sum(item["military_power"] for item in supporting)
        projected_power = source_power + support_power
        required_power = known_hostile_power * self.campaign_space_force_ratio
        shortfall = max(required_power - projected_power, 0.0)
        if shortfall > 0:
            status = "reinforcement_required"
        elif unknown_hostiles:
            status = "opposition_power_unknown"
        else:
            status = "adequate_known_force"
        return {
            "status": status,
            "hard_gate_satisfied": status == "adequate_known_force",
            "target_system_id": target_system_id,
            "target_fleet_id": target_fleet_id,
            "source_fleet_id": fleet_id,
            "source_fleet_military_power": source_power,
            "supporting_fleets": supporting,
            "supporting_military_power": support_power,
            "known_hostile_fleet_ids": sorted(hostile_by_id),
            "unknown_hostile_power_fleet_ids": unknown_hostiles,
            "known_hostile_military_power": known_hostile_power,
            "minimum_force_ratio": self.campaign_space_force_ratio,
            "minimum_recommended_friendly_power": required_power,
            "projected_friendly_military_power": projected_power,
            "known_force_ratio": (
                round(projected_power / known_hostile_power, 4)
                if known_hostile_power > 0
                else None
            ),
            "additional_military_power_required": shortfall,
        }

    @staticmethod
    def _require_attack_force(assessment: dict[str, Any]) -> None:
        status = str(assessment.get("status") or "")
        if status == "adequate_known_force":
            return
        if status == "reinforcement_required":
            shortfall = float(
                assessment.get("additional_military_power_required") or 0.0
            )
            raise FleetToolError(
                "目标星系的已知敌军超过当前攻击编组安全门槛；"
                f"至少还需 {shortfall:.2f} 军力，拒绝发送单舰队攻击。"
            )
        raise FleetToolError(
            "目标星系仍有军力未知的敌对目标，无法证明攻击编组达到安全门槛。"
        )

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
        if not self._permission_granted(fleet_id, "allow_attack", permissions):
            raise FleetToolError(f"玩家没有授权调用舰队 {fleet_id} 进行攻击。")
        try:
            order = selected_attack(profile, fleet_id, target_fleet_id)
        except ValueError as error:
            raise FleetToolError(str(error)) from error
        force_assessment = self._direct_attack_force_assessment(
            profile,
            fleet_id=fleet_id,
            target_fleet_id=target_fleet_id,
        )
        self._require_attack_force(force_assessment)
        run_id = "fleet_attack_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": "attack_fleet",
            "reason": reason,
            "order": order,
            "space_force_assessment": force_assessment,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_orbital_bombardment(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.invasion_enabled:
            raise FleetToolError("玩家没有启用实验性入侵与陆军工具。")
        fleet_id = int(arguments["fleet_id"])
        path, profile = self._invasion_profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not self._permission_granted(
            fleet_id,
            "allow_bombardment",
            permissions,
        ):
            raise FleetToolError(f"玩家没有授权舰队 {fleet_id} 执行轨道轰炸。")
        try:
            order = selected_orbital_bombardment(
                profile,
                fleet_id=fleet_id,
                target_planet_id=int(arguments["target_planet_id"]),
                stance=str(arguments["stance"]),
            )
        except (TypeError, ValueError) as error:
            raise FleetToolError(str(error)) from error
        run_id = "bombardment_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": "orbital_bombardment",
            "reason": str(arguments["reason"]).strip(),
            "order": order,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_army_landing(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.invasion_enabled:
            raise FleetToolError("玩家没有启用实验性入侵与陆军工具。")
        fleet_id = int(arguments["transport_fleet_id"])
        path, profile = self._invasion_profile()
        permissions = normalized_permissions(
            self.store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        if not self._permission_granted(
            fleet_id,
            "allow_land_armies",
            permissions,
        ):
            raise FleetToolError(f"玩家没有授权运输舰队 {fleet_id} 执行登陆。")
        try:
            order = selected_army_landing(
                profile,
                transport_fleet_id=fleet_id,
                target_planet_id=int(arguments["target_planet_id"]),
            )
        except (TypeError, ValueError) as error:
            raise FleetToolError(str(error)) from error
        run_id = "army_landing_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": "land_armies",
            "reason": str(arguments["reason"]).strip(),
            "order": order,
            "source_save_sha256": sha256_file(path),
            "source_game_date": profile.get("game_date"),
            "prepared_at": now_iso(),
        }
        return dict(self.prepared)

    def prepare_army_recruitment(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.invasion_enabled:
            raise FleetToolError("玩家没有启用实验性入侵与陆军工具。")
        path, profile = self._invasion_profile()
        candidate_id = str(arguments["candidate_id"])
        try:
            order = selected_army_recruitment(
                profile,
                candidate_id=candidate_id,
                count=int(arguments["count"]),
            )
        except (TypeError, ValueError) as error:
            raise FleetToolError(str(error)) from error
        campaign = self.store.get_state(PENDING_CAMPAIGN_KEY)
        campaign_plan_id = (
            str(campaign.get("campaign_plan_id"))
            if isinstance(campaign, dict)
            and campaign.get("status")
            in {"waiting_for_transport_fleet", "waiting_for_stronger_transport"}
            and campaign.get("campaign_plan_id")
            else None
        )
        run_id = "army_recruitment_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.prepared = {
            "run_id": run_id,
            "action": "recruit_armies",
            "reason": str(arguments["reason"]).strip(),
            "candidate_id": candidate_id,
            "count": int(arguments["count"]),
            "order": order,
            "baseline_transport_fleet_ids": sorted(
                int(fleet["fleet_id"])
                for fleet in profile.get("fleets", [])
                if fleet.get("ship_class") == "shipclass_transport"
            ),
            "baseline_transport_fleets": [
                {
                    "fleet_id": int(fleet["fleet_id"]),
                    "army_count": self._transport_army_count(fleet),
                }
                for fleet in profile.get("fleets", [])
                if fleet.get("ship_class") == "shipclass_transport"
            ],
            "campaign_plan_id": campaign_plan_id,
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
        if not self._permission_granted(fleet_id, "allow_repair", permissions):
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
        if not self._permission_granted(fleet_id, "allow_upgrade", permissions):
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
        if not self._permission_granted(
            fleet_id,
            "allow_automation",
            permissions,
        ):
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
        if not self._permission_granted(
            fleet_id,
            "allow_build_starbase",
            permissions,
        ):
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
        current_path: Path
        if action == "move_fleet":
            if not self.enabled:
                raise FleetToolError("玩家在执行前关闭了实验性舰队工具。")
            current_path, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not self._permission_granted(
                fleet_id,
                "allow_move",
                permissions,
            ):
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
            current_path, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not self._permission_granted(
                fleet_id,
                "allow_move",
                permissions,
            ):
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
            current_path, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not self._permission_granted(
                fleet_id,
                "allow_attack",
                permissions,
            ):
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
            if not isinstance(self.prepared.get("campaign_plan"), dict):
                force_assessment = self._direct_attack_force_assessment(
                    profile,
                    fleet_id=fleet_id,
                    target_fleet_id=target_fleet_id,
                )
                self._require_attack_force(force_assessment)
                self.prepared["space_force_assessment"] = force_assessment
            destination_system_id = refreshed["hostile_target"].get("system_id")
        elif action == "orbital_bombardment":
            if not self.invasion_enabled:
                raise FleetToolError("玩家在执行前关闭了实验性入侵与陆军工具。")
            current_path, profile = self._invasion_profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not self._permission_granted(
                fleet_id,
                "allow_bombardment",
                permissions,
            ):
                raise FleetToolError("玩家在执行前撤销了该舰队的轰炸权限。")
            try:
                refreshed = selected_orbital_bombardment(
                    profile,
                    fleet_id=fleet_id,
                    target_planet_id=int(
                        self.prepared["order"]["hostile_colony"]["planet_id"]
                    ),
                    stance=str(self.prepared["order"]["stance"]),
                )
            except ValueError as error:
                raise FleetToolError(str(error)) from error
            destination_system_id = refreshed["hostile_colony"].get("system_id")
        elif action == "land_armies":
            if not self.invasion_enabled:
                raise FleetToolError("玩家在执行前关闭了实验性入侵与陆军工具。")
            current_path, profile = self._invasion_profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not self._permission_granted(
                fleet_id,
                "allow_land_armies",
                permissions,
            ):
                raise FleetToolError("玩家在执行前撤销了该运输舰队的登陆权限。")
            try:
                refreshed = selected_army_landing(
                    profile,
                    transport_fleet_id=fleet_id,
                    target_planet_id=int(
                        self.prepared["order"]["hostile_colony"]["planet_id"]
                    ),
                )
            except ValueError as error:
                raise FleetToolError(str(error)) from error
            destination_system_id = refreshed["hostile_colony"].get("system_id")
        elif action == "recruit_armies":
            if not self.invasion_enabled:
                raise FleetToolError("玩家在执行前关闭了实验性入侵与陆军工具。")
            current_path, profile = self._invasion_profile()
            try:
                refreshed = selected_army_recruitment(
                    profile,
                    candidate_id=str(self.prepared["candidate_id"]),
                    count=int(self.prepared["count"]),
                )
            except ValueError as error:
                raise FleetToolError(str(error)) from error
            destination_system_id = refreshed["recruitment_starbase"].get("system_id")
        elif action == "repair_fleet":
            if not self.maintenance_enabled:
                raise FleetToolError("玩家在执行前关闭了舰队维修与升级工具。")
            current_path, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not self._permission_granted(
                fleet_id,
                "allow_repair",
                permissions,
            ):
                raise FleetToolError("玩家在执行前撤销了该舰队的维修权限。")
            try:
                refreshed = selected_fleet_repair(profile, fleet_id)
            except ValueError as error:
                raise FleetToolError(str(error)) from error
        elif action == "upgrade_fleet":
            if not self.maintenance_enabled:
                raise FleetToolError("玩家在执行前关闭了舰队维修与升级工具。")
            current_path, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not self._permission_granted(
                fleet_id,
                "allow_upgrade",
                permissions,
            ):
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
            current_path, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not self._permission_granted(
                fleet_id,
                "allow_automation",
                permissions,
            ):
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
            current_path, profile = self._profile()
            fleet_id = int(self.prepared["order"]["source_fleet"]["fleet_id"])
            if not self._permission_granted(
                fleet_id,
                "allow_build_starbase",
                permissions,
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
            current_path, profile = self._expansion_profile()
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
            current_path, profile = self._expansion_profile()
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
        sequence_action = action in {
            "orbital_bombardment",
            "land_armies",
            "recruit_armies",
        }
        resource_reservation_id: str | None = None
        resource_costs: dict[str, Any] = {}
        exclusive_cost = action in {
            "build_starbase",
            "order_colony_ship_and_colonize",
            "upgrade_starbase",
            "set_starbase_module",
            "set_starbase_building",
            "upgrade_fleet",
        }
        if action == "recruit_armies":
            resource_costs = dict(refreshed.get("total_cost", {}))
        current_save_sha256 = sha256_file(current_path)
        if resource_costs or exclusive_cost:
            resource_reservation_id = f"fleet_operations:{run_id}"
            try:
                self._resource_ledger().reserve(
                    reservation_id=resource_reservation_id,
                    application_id="fleet_operations",
                    action=action,
                    source_save_sha256=current_save_sha256,
                    source_game_date=profile.get("game_date"),
                    stockpile=self._country_stockpile(profile),
                    costs=resource_costs,
                    exclusive=exclusive_cost,
                )
            except ResourceReservationError as error:
                raise FleetToolError(str(error)) from error
        try:
            controller = SessionProxyController(self.config)
            if sequence_action:
                result = controller.arm_sequence_and_wait(
                    steps=[dict(step) for step in refreshed["protocol_sequence"]],
                    request_id=run_id,
                )
            else:
                result = controller.arm_and_wait(
                    action=action,
                    target=dict(refreshed["target"]),
                    request_id=run_id,
                )
        except SessionProxyError as error:
            if resource_reservation_id is not None:
                self._resource_ledger().release(resource_reservation_id)
            raise FleetToolError(str(error)) from error
        success = result.get("outcome") == "confirmed"
        confirmed_steps = int(result.get("confirmed_steps") or 0)
        if resource_reservation_id is not None and not success and confirmed_steps == 0:
            self._resource_ledger().release(resource_reservation_id)
        if not success and not sequence_action:
            raise FleetToolError(str(result.get("error") or "命令未获房主权威确认。"))
        fact = {
            "run_id": run_id,
            "action": action,
            "fleet_id": fleet_id,
            "destination_system_id": destination_system_id,
            "recorded_at": now_iso(),
            "proxy_result": result,
        }
        if action in {"orbital_bombardment", "land_armies", "recruit_armies"}:
            fact_key = "last_invasion_execution"
        elif action in {
            "order_colony_ship_and_colonize",
            "upgrade_starbase",
            "set_starbase_module",
            "set_starbase_building",
        }:
            fact_key = "last_expansion_execution"
        else:
            fact_key = "last_fleet_execution"
        self.store.set_state(fact_key, fact)
        campaign_plan = self.prepared.get("campaign_plan")
        if isinstance(campaign_plan, dict) and (success or confirmed_steps > 0):
            campaign_plan = {
                **campaign_plan,
                "status": "awaiting_fresh_save",
                "last_action": action,
                "last_action_save_sha256": current_save_sha256,
                "last_action_game_date": profile.get("game_date"),
                "last_execution_run_id": run_id,
                "last_confirmation": {
                    "success": success,
                    "confirmed_steps": confirmed_steps,
                },
                "updated_at": now_iso(),
            }
            self.store.set_state(PENDING_CAMPAIGN_KEY, campaign_plan)
            self._record_campaign_route_usage(campaign_plan, status="active")
        if action == "recruit_armies" and (success or confirmed_steps > 0):
            self.store.set_state(
                PENDING_ARMY_RECRUITMENT_KEY,
                {
                    "schema": "iag.pending_army_recruitment.v1",
                    "phase": "awaiting_transport_save",
                    "recruitment_run_id": run_id,
                    "expected_army_count": int(self.prepared.get("count") or 0),
                    "baseline_transport_fleet_ids": list(
                        self.prepared.get("baseline_transport_fleet_ids", [])
                    ),
                    "last_action_save_sha256": current_save_sha256,
                    "last_action_game_date": profile.get("game_date"),
                    "campaign_plan_id": (
                        self.prepared.get("campaign_plan_id")
                        or (
                            campaign_plan.get("campaign_plan_id")
                            if isinstance(campaign_plan, dict)
                            else None
                        )
                    ),
                    "created_at": now_iso(),
                },
            )
        self.prepared = None
        return {
            "schema": "iag.tool_result.fleet_execution.v1",
            "success": success,
            "run_id": run_id,
            "fleet_id": fleet_id,
            "destination_system_id": destination_system_id,
            "action": action,
            "order": refreshed,
            "confirmation": result,
            "resource_reservation_id": resource_reservation_id,
            "confirmed_protocol_steps": confirmed_steps,
            "requested_protocol_steps": (
                len(refreshed.get("protocol_sequence", [])) if sequence_action else 1
            ),
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
                has_callable_fleet_permission(fleet) for fleet in result["fleets"]
            )
            summary = (
                f"已读取 {len(result['fleets'])} 支玩家舰队；"
                f"{authorized} 支已获至少一项 AI 权限，"
                f"其中 {callable_now} 支当前可调用。"
            )
        elif name == "inspect_invasion_state":
            result = self.inspect_invasion()
            summary = (
                f"已读取 {len(result['hostile_colonies'])} 个当前可达敌方殖民地、"
                f"{len(result['blocked_hostile_colonies'])} 个受阻目标和 "
                f"{len(result['army_recruitment_candidates'])} 个陆军招募候选。"
            )
        elif name == "inspect_campaign_deployment":
            result = self.inspect_campaign_deployment(arguments)
            uncovered = sum(
                item.get("coverage_state") == "uncovered"
                for item in result["garrison_candidates"]
            )
            summary = (
                f"已读取 {len(result['fronts'])} 个战争目标战线和 "
                f"{len(result['garrison_candidates'])} 个驻防点；"
                f"其中 {uncovered} 个尚无驻守舰队。"
            )
        elif name == "inspect_campaign_routes":
            result = self.inspect_campaign_routes(arguments)
            summary = (
                f"已为舰队 {arguments['fleet_id']} 生成 "
                f"{result['route_count']} 条非支配战役路线；"
                f"状态为 {result['status']}。"
            )
        elif name == "prepare_campaign_route":
            result = self.prepare_campaign_route(arguments)
            if result["execution_required"]:
                summary = (
                    f"已准备战役 {result['campaign_plan_id']} 的第一步 "
                    f"{result['prepared_action']}；后续将按新存档逐步续接。"
                )
            else:
                summary = (
                    f"战役 {result['campaign_plan_id']} 当前状态为 "
                    f"{result['status']}，本份存档未发包。"
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
        elif name == "prepare_orbital_bombardment":
            result = self.prepare_orbital_bombardment(arguments)
            target = result["order"]["hostile_colony"]
            summary = (
                f"已准备舰队 {fleet_label(result['order']['source_fleet'])} 以 "
                f"{result['order']['stance']} 姿态轰炸 "
                f"{fleet_label(target)}；尚未发包。"
            )
        elif name == "prepare_army_landing":
            result = self.prepare_army_landing(arguments)
            summary = (
                f"已准备运输舰队 {fleet_label(result['order']['source_fleet'])} 登陆 "
                f"{fleet_label(result['order']['hostile_colony'])}；尚未发包。"
            )
        elif name == "prepare_army_recruitment":
            result = self.prepare_army_recruitment(arguments)
            summary = (
                f"已准备在恒星基地 "
                f"{result['order']['recruitment_starbase']['starbase_index']} 招募 "
                f"{result['order']['count']} 支 {result['order']['army_type']}；尚未发包。"
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
            if result["success"]:
                summary = (
                    f"{result['action']} 已获房主权威确认；"
                    "最终游戏状态仍以后续新存档为准。"
                )
            else:
                confirmation = result["confirmation"]
                summary = (
                    f"{result['action']} 仅确认 "
                    f"{confirmation.get('confirmed_steps', 0)}/"
                    f"{confirmation.get('requested_steps', 0)} 个协议步骤；"
                    "已停止余下步骤并等待新存档。"
                )
        elif name == "inspect_ship_state":
            result = self.inspect_ships()
            summary = (
                f"已读取 {len(result['designs'])} 份玩家可见舰船设计和 "
                f"{len(result['shipyards'])} 个直接船坞队列；"
                f"排除 {result.get('hidden_autogenerated_design_count', 0)} 份"
                "自动设计关闭后遗留的隐藏模板。"
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
