"""Declarative command catalog and reports for protocol compatibility checks.

This module intentionally contains no packet I/O.  The live diagnostic runner
uses the existing session proxy so transport discovery, serial allocation and
reliable-stream translation continue to have one implementation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


PLAN_SCHEMA = "iag.protocol_compatibility_plan.v1"
CATALOG_SCHEMA = "iag.protocol_command_catalog.v1"
REPORT_SCHEMA = "iag.protocol_compatibility_report.v1"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class ProtocolCommandSpec:
    """One independently testable session-proxy action."""

    action: str
    group: str
    title: str
    wire_family_hex: str
    verification_state: str
    risk: str
    required_target_fields: tuple[str, ...]
    setup: str
    operator_check: str
    example_target: Mapping[str, Any]


COMMAND_SPECS = (
    ProtocolCommandSpec(
        action="build_building",
        group="construction",
        title="建设建筑",
        wire_family_hex="b43d01000300",
        verification_state="live_verified",
        risk="state_change",
        required_target_fields=(
            "build_queue_id",
            "colony_id",
            "zone_id",
            "building_id",
        ),
        setup="准备一个有空槽、资源充足且建设队列可用的殖民地。",
        operator_check="确认目标建筑进入预期区划的建设队列。",
        example_target={
            "context_822c": 0,
            "build_queue_id": "<BUILD_QUEUE_ID>",
            "colony_id": "<COLONY_ID>",
            "zone_id": "<ZONE_ID>",
            "building_id": "building_research_lab_1",
        },
    ),
    ProtocolCommandSpec(
        action="build_district",
        group="construction",
        title="建设基础区划",
        wire_family_hex="b43d01000300",
        verification_state="live_verified",
        risk="state_change",
        required_target_fields=("build_queue_id", "colony_id", "district_type"),
        setup="准备一个仍有对应容量且建设队列可用的殖民地。",
        operator_check="确认目标基础区划进入建设队列。",
        example_target={
            "context_822c": 0,
            "build_queue_id": "<BUILD_QUEUE_ID>",
            "colony_id": "<COLONY_ID>",
            "district_type": "district_generator",
        },
    ),
    ProtocolCommandSpec(
        action="build_zone",
        group="construction",
        title="特化区域",
        wire_family_hex="b43d01000300",
        verification_state="live_verified",
        risk="state_change",
        required_target_fields=(
            "build_queue_id",
            "colony_id",
            "district_id",
            "slot_selector",
            "zone_type",
        ),
        setup="准备一个可特化的区域槽与足够资源。",
        operator_check="确认指定槽变为目标特化区域。",
        example_target={
            "context_822c": 0,
            "build_queue_id": "<BUILD_QUEUE_ID>",
            "colony_id": "<COLONY_ID>",
            "district_id": "<DISTRICT_ID>",
            "slot_selector": "<SLOT_SELECTOR>",
            "zone_type": "zone_research_engineering",
        },
    ),
    ProtocolCommandSpec(
        action="move_fleet",
        group="fleet_movement",
        title="舰队跨对象移动",
        wire_family_hex="d32c01000300",
        verification_state="live_verified",
        risk="state_change",
        required_target_fields=(
            "source_fleet_object",
            "destination_tag_hex",
            "destination_object",
        ),
        setup="选择一支允许 AI 调用、未失踪且能够抵达安全目的地的舰队。",
        operator_check="确认舰队获得正确目的地命令且房间继续同步。",
        example_target={
            "source_fleet_object": "<FLEET_OBJECT_ID>",
            "destination_tag_hex": "0c3a01001400",
            "destination_object": "<DESTINATION_OBJECT_ID>",
        },
    ),
    ProtocolCommandSpec(
        action="move_fleet_to_coordinate",
        group="fleet_movement",
        title="舰队星系内坐标移动",
        wire_family_hex="4f2c01000300",
        verification_state="paired_capture",
        risk="state_change",
        required_target_fields=(
            "source_fleet_object",
            "x_fixed",
            "y_fixed",
            "system_origin",
        ),
        setup="选择安全星系内的一支舰队和可见空白坐标。",
        operator_check="确认舰队在当前星系内向指定位置移动。",
        example_target={
            "source_fleet_object": "<FLEET_OBJECT_ID>",
            "x_fixed": "<X_TIMES_100000>",
            "y_fixed": "<Y_TIMES_100000>",
            "system_origin": "<SYSTEM_ORIGIN_ID>",
        },
    ),
    ProtocolCommandSpec(
        action="start_research",
        group="research",
        title="开始科研",
        wire_family_hex="062d01000300",
        verification_state="paired_capture",
        risk="state_change",
        required_target_fields=("technology_id",),
        setup="准备一个当前研究分支中真实可选的科技。",
        operator_check="确认该科技成为当前研究项目。",
        example_target={
            "context_822c": 0,
            "technology_id": "<AVAILABLE_TECHNOLOGY_ID>",
        },
    ),
    ProtocolCommandSpec(
        action="stop_research",
        group="research",
        title="停止科研",
        wire_family_hex="453301000300",
        verification_state="paired_capture",
        risk="destructive_state_change",
        required_target_fields=("technology_id",),
        setup="准备一个允许在可丢弃存档中取消的当前研究项目。",
        operator_check="确认该研究停止，随后不要继续正式游玩此测试存档。",
        example_target={
            "context_822c": 0,
            "technology_id": "<ACTIVE_TECHNOLOGY_ID>",
        },
    ),
    ProtocolCommandSpec(
        action="build_ship",
        group="ship_production",
        title="船坞直接建造舰船",
        wire_family_hex="b43d01000300",
        verification_state="paired_capture",
        risk="state_change",
        required_target_fields=(
            "build_queue_id",
            "design_id",
            "destination_object",
        ),
        setup="准备拥有船坞、资源充足且队列可用的恒星基地。",
        operator_check="确认正确设计进入指定船坞队列。",
        example_target={
            "context_822c": 0,
            "build_queue_id": "<STARBASE_BUILD_QUEUE_ID>",
            "design_id": "<SHIP_DESIGN_ID>",
            "upgrade_id": 4294967295,
            "growth_stage": 0,
            "destination_tag_hex": "0c3a01001400",
            "destination_object": "<STARBASE_OBJECT_ID>",
        },
    ),
    ProtocolCommandSpec(
        action="create_ship_design",
        group="ship_design",
        title="创建舰船设计",
        wire_family_hex="fb2d01000300",
        verification_state="paired_capture",
        risk="state_change",
        required_target_fields=("blueprint",),
        setup="从最新存档/工具生成完整蓝图，并使用不会覆盖现有设计的新名称。",
        operator_check="确认新设计出现且原设计仍被保留。",
        example_target={
            "blueprint": {
                "replace_with_complete_blueprint": "<SHIP_BLUEPRINT_OBJECT>"
            }
        },
    ),
    ProtocolCommandSpec(
        action="create_fleet_template",
        group="fleet_manager",
        title="创建空舰队模板",
        wire_family_hex="7e3b01000300",
        verification_state="paired_capture",
        risk="state_change_requires_fresh_save",
        required_target_fields=(),
        setup="确保测试后会等待新存档，以差分确认确定性分配的模板 ID。",
        operator_check="确认 Fleet Manager 出现一个新空模板；记录月底存档。",
        example_target={"context_822c": 0},
    ),
    ProtocolCommandSpec(
        action="add_fleet_template_ship",
        group="fleet_manager",
        title="舰队模板目标数量加一",
        wire_family_hex="5f3b01000300",
        verification_state="paired_capture",
        risk="state_change",
        required_target_fields=("fleet_template_id", "design_id"),
        setup="选择已有模板与玩家拥有的设计，并记录修改前目标数量。",
        operator_check="确认目标设计数量恰好增加一。",
        example_target={
            "context_822c": 0,
            "fleet_template_id": "<FLEET_TEMPLATE_ID>",
            "design_id": "<SHIP_DESIGN_ID>",
            "upgrade_id": 4294967295,
            "growth_stage": 0,
        },
    ),
    ProtocolCommandSpec(
        action="remove_fleet_template_ship",
        group="fleet_manager",
        title="舰队模板目标数量减一",
        wire_family_hex="603b01000300",
        verification_state="paired_capture",
        risk="destructive_state_change",
        required_target_fields=("fleet_template_id", "design_id"),
        setup="选择目标数量至少为一的测试模板与设计。",
        operator_check="确认目标设计数量恰好减少一。",
        example_target={
            "fleet_template_id": "<FLEET_TEMPLATE_ID>",
            "design_id": "<SHIP_DESIGN_ID>",
            "upgrade_id": 4294967295,
            "growth_stage": 0,
        },
    ),
    ProtocolCommandSpec(
        action="reinforce_fleet_stage_1",
        group="fleet_manager",
        title="舰队增援第一阶段",
        wire_family_hex="123b01000300",
        verification_state="paired_capture",
        risk="state_change",
        required_target_fields=("fleet_template_id",),
        setup="先把模板目标数量调高，并确保帝国有足够资源与船坞。",
        operator_check="确认第一阶段获权威回包；它本身不证明舰船已开工。",
        example_target={
            "context_822c": 0,
            "fleet_template_id": "<FLEET_TEMPLATE_ID>",
        },
    ),
    ProtocolCommandSpec(
        action="reinforce_fleet_stage_2",
        group="fleet_manager",
        title="舰队增援第二阶段",
        wire_family_hex="f23b01000300",
        verification_state="paired_capture",
        risk="state_change",
        required_target_fields=("fleet_template_id",),
        setup="仅在同一模板的第一阶段刚刚成功后执行。",
        operator_check="确认增援请求成立，并在随后存档中核对队列。",
        example_target={
            "context_822c": 0,
            "fleet_template_id": "<FLEET_TEMPLATE_ID>",
        },
    ),
)

COMMAND_SPEC_BY_ACTION = {spec.action: spec for spec in COMMAND_SPECS}
SUPPORTED_SESSION_PROXY_ACTIONS = tuple(spec.action for spec in COMMAND_SPECS)

# These targets exercise every deterministic command builder without touching a
# live game.  They belong to the shipped diagnostic runtime rather than the test
# package because frozen Windows builds also run this preflight from the control
# panel.
OFFLINE_FIXTURE_TARGETS: dict[str, dict[str, Any]] = {
    "build_building": {
        "context_822c": 0,
        "build_queue_id": 11,
        "colony_id": 12,
        "zone_id": 13,
        "building_id": "building_research_lab_1",
    },
    "build_district": {
        "context_822c": 0,
        "build_queue_id": 11,
        "colony_id": 12,
        "district_type": "district_generator",
    },
    "build_zone": {
        "context_822c": 0,
        "build_queue_id": 11,
        "colony_id": 12,
        "district_id": 13,
        "slot_selector": 1,
        "zone_type": "zone_research_engineering",
    },
    "move_fleet": {
        "source_fleet_object": 21,
        "destination_tag_hex": "0c3a01001400",
        "destination_object": 22,
    },
    "move_fleet_to_coordinate": {
        "source_fleet_object": 21,
        "x_fixed": 125000,
        "y_fixed": -250000,
        "system_origin": 23,
    },
    "start_research": {
        "context_822c": 0,
        "technology_id": "tech_shields_2",
    },
    "stop_research": {
        "context_822c": 0,
        "technology_id": "tech_shields_2",
    },
    "build_ship": {
        "context_822c": 0,
        "build_queue_id": 31,
        "design_id": 32,
        "upgrade_id": 0xFFFFFFFF,
        "growth_stage": 0,
        "destination_tag_hex": "0c3a01001400",
        "destination_object": 33,
    },
    "create_ship_design": {
        "blueprint": {
            "context_822c": 0,
            "name": "IAG_COMPAT_TEST",
            "entity": "screen",
            "graphical_culture": "mammalian_01",
            "growth_stages": [
                {
                    "ship_size": "corvette",
                    "parent": 0xFFFFFFFF,
                    "sections": [
                        {
                            "template": "CORVETTE_MID_M1S1",
                            "slot": "mid",
                            "components": [
                                {
                                    "slot": "SMALL_GUN_01",
                                    "component_id": "RED_LASER",
                                }
                            ],
                        }
                    ],
                    "required_components": [],
                }
            ],
        }
    },
    "create_fleet_template": {"context_822c": 0},
    "add_fleet_template_ship": {
        "context_822c": 0,
        "fleet_template_id": 41,
        "design_id": 42,
        "upgrade_id": 0xFFFFFFFF,
        "growth_stage": 0,
    },
    "remove_fleet_template_ship": {
        "fleet_template_id": 41,
        "design_id": 42,
        "upgrade_id": 0xFFFFFFFF,
        "growth_stage": 0,
    },
    "reinforce_fleet_stage_1": {
        "context_822c": 0,
        "fleet_template_id": 41,
    },
    "reinforce_fleet_stage_2": {
        "context_822c": 0,
        "fleet_template_id": 41,
    },
}


def catalog_document() -> dict[str, Any]:
    return {
        "schema": CATALOG_SCHEMA,
        "commands": [asdict(spec) for spec in COMMAND_SPECS],
    }


def new_plan_document(*, game_version: str, game_build: str = "") -> dict[str, Any]:
    """Return a safe plan template; every mutating scenario starts disabled."""
    return {
        "schema": PLAN_SCHEMA,
        "game_version": game_version,
        "game_build": game_build,
        "notes": (
            "Use a disposable, fully authorized co-op save. Fill targets from the "
            "current save, enable only prepared scenarios, and acknowledge each "
            "side effect before a live run."
        ),
        "scenarios": [
            {
                "id": spec.action,
                "action": spec.action,
                "enabled": False,
                "acknowledge_side_effects": False,
                "target": dict(spec.example_target),
                "setup": spec.setup,
                "operator_check": spec.operator_check,
            }
            for spec in COMMAND_SPECS
        ],
    }


def validate_plan_document(
    value: Any,
    *,
    require_live_acknowledgements: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != PLAN_SCHEMA:
        raise ValueError(f"Plan schema must be {PLAN_SCHEMA}.")
    if not str(value.get("game_version", "")).strip():
        raise ValueError("Plan game_version is required.")
    scenarios = value.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("Plan scenarios must be a non-empty list.")
    seen_ids: set[str] = set()
    seen_actions: set[str] = set()
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise ValueError(f"Scenario {index} must be an object.")
        scenario_id = str(scenario.get("id", "")).strip()
        action = str(scenario.get("action", "")).strip()
        if not scenario_id or scenario_id in seen_ids:
            raise ValueError(f"Scenario {index} has a missing or duplicate id.")
        seen_ids.add(scenario_id)
        if action not in COMMAND_SPEC_BY_ACTION:
            raise ValueError(
                f"Scenario {scenario_id} has unsupported action {action!r}."
            )
        if action in seen_actions:
            raise ValueError(f"Plan contains duplicate action {action!r}.")
        seen_actions.add(action)
        if not isinstance(scenario.get("enabled", False), bool):
            raise ValueError(f"Scenario {scenario_id} enabled must be boolean.")
        target = scenario.get("target")
        if not isinstance(target, dict):
            raise ValueError(f"Scenario {scenario_id} target must be an object.")
        if scenario.get("template_record_hex"):
            try:
                bytes.fromhex(str(scenario["template_record_hex"]))
            except ValueError as error:
                raise ValueError(
                    f"Scenario {scenario_id} template_record_hex is invalid."
                ) from error
        if (
            require_live_acknowledgements
            and scenario.get("enabled")
            and scenario.get("acknowledge_side_effects") is not True
        ):
            raise ValueError(
                f"Scenario {scenario_id} is enabled but its side effects are not "
                "acknowledged."
            )
    if require_live_acknowledgements:
        enabled_by_action = {
            str(item["action"]): item
            for item in scenarios
            if item.get("enabled") is True
        }
        second_stage = enabled_by_action.get("reinforce_fleet_stage_2")
        if second_stage is not None:
            first_stage = enabled_by_action.get("reinforce_fleet_stage_1")
            if first_stage is None:
                raise ValueError(
                    "reinforce_fleet_stage_2 requires an enabled stage 1 scenario."
                )
            if first_stage["target"].get("fleet_template_id") != (
                second_stage["target"].get("fleet_template_id")
            ):
                raise ValueError(
                    "Reinforcement stages must target the same fleet template."
                )
    return value


def load_plan(
    path: Path,
    *,
    require_live_acknowledgements: bool = False,
) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return validate_plan_document(
        value,
        require_live_acknowledgements=require_live_acknowledgements,
    )


def target_fingerprint(target: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        target,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def classify_network_result(result: Mapping[str, Any]) -> str:
    """Locate the first observable failure stage in a proxy result."""
    outcome = str(result.get("outcome", ""))
    acknowledged = result.get("host_acknowledged_inserted_bytes") is True
    retagged = result.get("response_retagged") is True
    if outcome == "confirmed" and retagged:
        return "network_confirmed"
    if outcome == "confirmed":
        return "confirmation_metadata_incomplete"
    if outcome == "response_timeout" and acknowledged:
        return "authoritative_response_not_matched"
    if outcome == "response_timeout":
        return "host_ack_or_authoritative_response_missing"
    return "proxy_result_unrecognized"


def new_report_document(
    *,
    run_id: str,
    mode: str,
    platform_version: str,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "run_id": run_id,
        "mode": mode,
        "platform_version": platform_version,
        "game_version": str(plan.get("game_version", "")),
        "game_build": str(plan.get("game_build", "")),
        "game_install": None,
        "started_at": now_iso(),
        "finished_at": None,
        "status": "running",
        "offline_tests": None,
        "flow": None,
        "scenarios": [],
        "baseline_comparison": [],
        "notes": [],
    }


def compare_report_documents(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Report stable, target-independent regressions against a known-good run."""
    changes: list[dict[str, Any]] = []
    for field in ("platform_version", "game_version", "game_build"):
        before = baseline.get(field)
        after = current.get(field)
        if before != after:
            changes.append(
                {
                    "field": field,
                    "before": before,
                    "after": after,
                }
            )
    old_install = baseline.get("game_install")
    new_install = current.get("game_install")
    if isinstance(old_install, dict) and isinstance(new_install, dict):
        field = "launcher_settings_sha256"
        if old_install.get(field) != new_install.get(field):
            changes.append(
                {
                    "field": f"game_install.{field}",
                    "before": old_install.get(field),
                    "after": new_install.get(field),
                }
            )
    old_by_id = {
        str(item.get("id")): item
        for item in baseline.get("scenarios", [])
        if isinstance(item, dict)
    }
    for item in current.get("scenarios", []):
        if not isinstance(item, dict):
            continue
        old = old_by_id.get(str(item.get("id")))
        if old is None:
            changes.append({"id": item.get("id"), "change": "new_scenario"})
            continue
        for field in ("wire_family_hex", "network_stage", "operator_verdict"):
            before = old.get(field)
            after = item.get(field)
            if before != after:
                changes.append(
                    {
                        "id": item.get("id"),
                        "field": field,
                        "before": before,
                        "after": after,
                    }
                )
        if item.get("target_sha256") == old.get("target_sha256"):
            for field in ("record_length", "inserted_length", "record_sha256"):
                before = old.get(field)
                after = item.get(field)
                if before != after:
                    changes.append(
                        {
                            "id": item.get("id"),
                            "field": field,
                            "before": before,
                            "after": after,
                        }
                    )
    return changes


def render_report_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Stellaris 协议兼容性验收报告",
        "",
        f"- 运行：`{report.get('run_id', '')}`",
        f"- 平台：`{report.get('platform_version', '')}`",
        f"- 游戏：`{report.get('game_version', '')}` "
        f"(`{report.get('game_build', '') or 'build 未填写'}`)",
        f"- 模式：`{report.get('mode', '')}`",
        f"- 状态：`{report.get('status', '')}`",
        "",
        "| 动作 | 命令族 | 离线构包 | 网络阶段 | 玩家观察 |",
        "|---|---|---|---|---|",
    ]
    for item in report.get("scenarios", []):
        if not isinstance(item, dict):
            continue
        lines.append(
            "| `{}` | `{}` | `{}` | `{}` | `{}` |".format(
                item.get("action", ""),
                item.get("wire_family_hex", ""),
                item.get("offline_status", ""),
                item.get("network_stage", "not_run"),
                item.get("operator_verdict", "not_checked"),
            )
        )
    changes = report.get("baseline_comparison", [])
    lines.extend(["", "## 基线差异", ""])
    if changes:
        for change in changes:
            lines.append(f"- `{json.dumps(change, ensure_ascii=False)}`")
    else:
        lines.append("未发现可比较字段的变化，或未提供基线报告。")
    notes = report.get("notes", [])
    if notes:
        lines.extend(["", "## 备注", ""])
        lines.extend(f"- {note}" for note in notes)
    return "\n".join(lines) + "\n"


def write_report(directory: Path, report: Mapping[str, Any]) -> None:
    """Atomically persist both machine-readable and review-friendly reports."""
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "report.json"
    temporary = json_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(json_path)
    markdown_path = directory / "report.md"
    temporary_markdown = markdown_path.with_suffix(".md.tmp")
    temporary_markdown.write_text(
        render_report_markdown(report),
        encoding="utf-8",
    )
    temporary_markdown.replace(markdown_path)
