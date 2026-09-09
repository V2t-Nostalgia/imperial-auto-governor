"""平台内置 Application 的唯一静态登记入口。"""

from iag.core.application_registry import (
    ApplicationManifest,
    ApplicationRegistry,
)


def builtin_application_registry() -> ApplicationRegistry:
    """返回当前平台内置的经济、科研与舰队能力。"""

    return ApplicationRegistry(
        [
            ApplicationManifest(
                schema_version="iag.application_manifest.v1",
                application_id="economy_governance",
                version="0.5.9",
                display_name_zh="帝国经济总管",
                description_zh="管理殖民地建设、资源平衡与经济发展。",
                agent_roles=("economy_governor",),
                message_types=(
                    "domain_assessment",
                    "action_intent",
                    "resource_request",
                    "escalation",
                ),
                action_types=(
                    "build_building",
                    "build_district",
                    "build_zone",
                    "upgrade_building",
                    "replace_building",
                ),
                required_platform_capabilities=(
                    "stellaris_save_state_v1",
                    "host_inbound_rewrite_v1",
                ),
            ),
            ApplicationManifest(
                schema_version="iag.application_manifest.v1",
                application_id="fleet_operations",
                version="0.5.9",
                display_name_zh="舰队与扩张行动",
                description_zh=(
                    "管理玩家授权的舰队、民用船、殖民与恒星基地命令。"
                ),
                agent_roles=("fleet_operator",),
                message_types=(
                    "fleet_assessment",
                    "fleet_order",
                    "escalation",
                ),
                action_types=(
                    "move_fleet",
                    "move_fleet_to_coordinate",
                    "attack_fleet",
                    "configure_ship_automation",
                    "build_starbase",
                    "order_colony_ship_and_colonize",
                    "upgrade_starbase",
                    "set_starbase_module",
                    "set_starbase_building",
                    "create_ship_design",
                    "create_new_fleet",
                    "reinforce_fleet_to_target",
                ),
                required_platform_capabilities=(
                    "stellaris_fleet_state_v1",
                    "stellaris_expansion_state_v1",
                    "session_proxy_v1",
                ),
            ),
            ApplicationManifest(
                schema_version="iag.application_manifest.v1",
                application_id="research_strategy",
                version="0.5.9",
                display_name_zh="科研战略",
                description_zh="读取合法科技候选并执行实验性科研选择。",
                agent_roles=("research_director",),
                message_types=(
                    "research_assessment",
                    "research_selection",
                    "escalation",
                ),
                action_types=("start_research", "stop_research"),
                required_platform_capabilities=(
                    "stellaris_research_state_v1",
                    "session_proxy_v1",
                ),
            ),
        ]
    )
