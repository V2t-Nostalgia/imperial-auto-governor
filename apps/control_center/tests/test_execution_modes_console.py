from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from apps.control_center.web_console import ConsoleError, ConsoleService
from iag.core.conversation_store import ConversationStore
from iag.infrastructure.llm.runtime_config import RuntimeConfig


def settings_payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "execution_mode": "carrier_click",
        "fixed_click_guard_enabled": True,
        "require_fresh_save_seconds": 900,
        "maximum_source_save_lag_versions": 2,
        "autonomy_require_fresh_save_seconds": 900,
        "maximum_constructions_per_turn": 3,
        "inconclusive_rewrite_policy": "block_until_save",
        "session_proxy_local_ip": "",
        "session_proxy_host_ip": "192.0.2.20",
        "session_proxy_source_actor": 0,
        "session_proxy_host_actor": 1,
        "session_proxy_acknowledged": False,
        "experimental_fleet_tools_enabled": False,
        "experimental_fleet_attack_enabled": False,
        "experimental_fleet_coordinate_tools_enabled": False,
        "fleet_coordinate_max_abs": 1000,
        "experimental_ship_design_tools_enabled": False,
        "experimental_fleet_reinforcement_tools_enabled": False,
        "maximum_fleet_reinforcement_increase": 5,
        "experimental_new_fleet_tools_enabled": False,
        "maximum_new_fleet_initial_ships": 5,
        "experimental_research_tools_enabled": False,
        "experimental_research_reselection_enabled": False,
    }
    value.update(overrides)
    return value


class ExecutionModeConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "agent_config.json"
        self.path.write_text(
            json.dumps(
                {
                    "runtime_root": str(self.root / "runtime"),
                    "model_pool": {
                        "pool_id": "test-pool",
                        "display_name": "Test Pool",
                        "endpoints": [
                            {
                                "endpoint_id": "test-endpoint",
                                "display_name": "Test Endpoint",
                                "model_id": "test-model",
                                "model": "test-model",
                                "model_transport": "openai_sdk",
                                "provider": "chat_completions_compatible",
                                "base_url": "https://model.example.test/v1",
                                "api_key": "test-only-key",
                                "supports_reasoning": False,
                                "model_context_window_tokens": 32000,
                                "max_output_tokens": 4000,
                                "priority": 0,
                                "enabled": True,
                                "supports_tools": True,
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        runtime = RuntimeConfig.load(self.path)
        service = ConsoleService.__new__(ConsoleService)
        service.runtime_config = runtime
        service._config_lock = threading.RLock()
        service.config = runtime.snapshot().settings
        service.conversation_store = ConversationStore(
            self.root / "conversation.sqlite3"
        )
        self.service = service

    def test_new_configuration_prefers_session_proxy(self) -> None:
        self.assertEqual(
            self.service.public_execution_settings()["execution_mode"],
            "session_proxy",
        )

    def test_fleet_tools_require_session_proxy_mode(self) -> None:
        with self.assertRaisesRegex(ConsoleError, "只能在会话代理"):
            self.service.save_execution_settings(
                settings_payload(experimental_fleet_tools_enabled=True)
            )

    def test_attack_precheck_requires_master_fleet_switch(self) -> None:
        with self.assertRaisesRegex(ConsoleError, "必须先启用"):
            self.service.save_execution_settings(
                settings_payload(
                    execution_mode="session_proxy",
                    experimental_fleet_attack_enabled=True,
                )
            )

    def test_proxy_and_fleet_settings_persist_together(self) -> None:
        result = self.service.save_execution_settings(
            settings_payload(
                execution_mode="session_proxy",
                session_proxy_acknowledged=True,
                experimental_fleet_tools_enabled=True,
                experimental_fleet_attack_enabled=True,
                experimental_fleet_coordinate_tools_enabled=True,
                experimental_ship_design_tools_enabled=True,
                experimental_fleet_reinforcement_tools_enabled=True,
                maximum_fleet_reinforcement_increase=7,
                experimental_new_fleet_tools_enabled=True,
                maximum_new_fleet_initial_ships=4,
                experimental_research_tools_enabled=True,
            )
        )
        self.assertEqual(result["execution_mode"], "session_proxy")
        self.assertTrue(result["experimental_fleet_tools_enabled"])
        self.assertTrue(result["experimental_fleet_coordinate_tools_enabled"])
        self.assertTrue(result["experimental_ship_design_tools_enabled"])
        self.assertTrue(
            result["experimental_fleet_reinforcement_tools_enabled"]
        )
        self.assertEqual(result["maximum_fleet_reinforcement_increase"], 7)
        self.assertTrue(result["experimental_new_fleet_tools_enabled"])
        self.assertEqual(result["maximum_new_fleet_initial_ships"], 4)
        self.assertTrue(result["experimental_research_tools_enabled"])
        reloaded = RuntimeConfig.load(self.path).snapshot().settings
        self.assertEqual(reloaded["execution_mode"], "session_proxy")

    def test_reinforcement_limit_is_bounded(self) -> None:
        with self.assertRaisesRegex(ConsoleError, "目标编制增量"):
            self.service.save_execution_settings(
                settings_payload(maximum_fleet_reinforcement_increase=21)
            )

    def test_reinforcement_requires_session_proxy_mode(self) -> None:
        with self.assertRaisesRegex(ConsoleError, "舰队增援工具"):
            self.service.save_execution_settings(
                settings_payload(
                    experimental_fleet_reinforcement_tools_enabled=True,
                )
            )

    def test_new_fleet_requires_session_proxy_mode(self) -> None:
        with self.assertRaisesRegex(ConsoleError, "新建舰队工具"):
            self.service.save_execution_settings(
                settings_payload(experimental_new_fleet_tools_enabled=True)
            )

    def test_new_fleet_limit_is_bounded(self) -> None:
        with self.assertRaisesRegex(ConsoleError, "初始舰数"):
            self.service.save_execution_settings(
                settings_payload(maximum_new_fleet_initial_ships=21)
            )

    def test_reinforcement_permission_is_saved_per_fleet(self) -> None:
        self.service.fleet_payload = lambda: {"fleets": [{"fleet_id": 7}]}
        result = self.service.save_fleet_permission(
            {
                "fleet_id": 7,
                "allow_move": False,
                "allow_attack": False,
                "allow_reinforce": True,
            }
        )
        self.assertTrue(result["saved"])
        permissions = self.service.conversation_store.get_state(
            "fleet_permissions",
            {},
        )
        self.assertTrue(permissions["7"]["allow_reinforce"])

    def test_research_reselection_requires_research_tools(self) -> None:
        with self.assertRaisesRegex(ConsoleError, "科研工具"):
            self.service.save_execution_settings(
                settings_payload(
                    execution_mode="session_proxy",
                    experimental_research_reselection_enabled=True,
                )
            )

    def test_coordinate_move_requires_master_fleet_switch(self) -> None:
        with self.assertRaisesRegex(ConsoleError, "舰队工具"):
            self.service.save_execution_settings(
                settings_payload(
                    execution_mode="session_proxy",
                    experimental_fleet_coordinate_tools_enabled=True,
                )
            )

    def test_live_protocol_suite_freezes_execution_settings(self) -> None:
        self.service.protocol_compatibility = type(
            "ActiveProtocolSuite",
            (),
            {"live_active": lambda _self: True},
        )()
        with self.assertRaisesRegex(ConsoleError, "协议兼容性验收"):
            self.service.save_execution_settings(settings_payload())


if __name__ == "__main__":
    unittest.main()
