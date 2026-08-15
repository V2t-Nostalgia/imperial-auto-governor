"""平台内置 Application 的唯一静态登记入口。"""

from iag.core.application_registry import (
    ApplicationManifest,
    ApplicationRegistry,
)


def builtin_application_registry() -> ApplicationRegistry:
    """返回当前平台内置的经济治理与舰队行动能力。"""

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
                display_name_zh="舰队行动",
                description_zh="读取舰队状态并执行玩家逐舰队授权的实验性命令。",
                agent_roles=("fleet_operator",),
                message_types=(
                    "fleet_assessment",
                    "fleet_order",
                    "escalation",
                ),
                action_types=("move_fleet", "attack_fleet"),
                required_platform_capabilities=(
                    "stellaris_fleet_state_v1",
                    "session_proxy_v1",
                ),
            ),
        ]
    )
