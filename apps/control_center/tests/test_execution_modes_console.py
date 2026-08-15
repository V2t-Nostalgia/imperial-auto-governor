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
            )
        )
        self.assertEqual(result["execution_mode"], "session_proxy")
        self.assertTrue(result["experimental_fleet_tools_enabled"])
        reloaded = RuntimeConfig.load(self.path).snapshot().settings
        self.assertEqual(reloaded["execution_mode"], "session_proxy")


if __name__ == "__main__":
    unittest.main()
