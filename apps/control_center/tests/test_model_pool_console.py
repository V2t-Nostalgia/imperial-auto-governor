#!/usr/bin/env python3
"""Narrow integration tests for model-pool console persistence."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from apps.control_center.web_console import ConsoleService
from iag.applications.registry import builtin_application_registry
from iag.infrastructure.llm.model_pool_runtime import ModelPoolRuntime
from iag.infrastructure.llm.runtime_config import RuntimeConfig


def endpoint(
    endpoint_id: str,
    priority: int,
    api_key: str,
) -> dict[str, object]:
    return {
        "endpoint_id": endpoint_id,
        "display_name": endpoint_id.title(),
        "model_id": "deepseek-chat",
        "model": "deepseek-test",
        "model_transport": "openai_sdk",
        "provider": "chat_completions_compatible",
        "base_url": f"https://{endpoint_id}.example.test/v1",
        "supports_reasoning": True,
        "model_context_window_tokens": 64_000,
        "auth_mode": "bearer",
        "api_key": api_key,
        "max_output_tokens": 8_000,
        "priority": priority,
        "enabled": True,
        "supports_tools": True,
        "models_path": "/models",
        "timeout_seconds": 120,
        "probe_timeout_seconds": 10,
        "rate_limit_cooldown_seconds": 300,
        "sdk_max_retries": 2,
    }


class ModelPoolConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "agent_config.json"
        self.path.write_text(
            json.dumps(
                {
                    "model_pool": {
                        "pool_id": "deepseek",
                        "display_name": "DeepSeek 主池",
                        "endpoints": [endpoint("official", 0, "official-secret")],
                    },
                    "request_options": {
                        "temperature": 0.2,
                        "request_body_overrides": {},
                    },
                    "runtime_root": str(self.root / "runtime"),
                    "tool_calling_enabled": True,
                }
            ),
            encoding="utf-8",
        )
        runtime = RuntimeConfig.load(self.path)
        service = ConsoleService.__new__(ConsoleService)
        service.runtime_config = runtime
        service.application_registry = builtin_application_registry()
        service.model_pool_runtime = ModelPoolRuntime(
            runtime.snapshot().model_pool
        )
        service.model_pool_runtimes = {
            pool.pool_id: ModelPoolRuntime(pool)
            for pool in runtime.snapshot().model_pools
        }
        service._config_lock = threading.RLock()
        service.config = runtime.snapshot().settings
        service.runtime_root = self.root / "runtime"
        service.runtime_root.mkdir(parents=True, exist_ok=True)
        self.service = service

    def test_save_two_endpoints_preserves_existing_key_and_hides_secrets(self) -> None:
        current = self.service.public_model_config()
        official = current["model_pools"][0]["endpoints"][0]
        relay = endpoint("relay", 1, "relay-secret")
        payload = {
            "model_pools": [{
                "pool_id": "deepseek",
                "display_name": "玩家命名的 DeepSeek 池",
                "endpoints": [official, relay],
            }],
        }

        public = self.service.save_model_pools(payload)

        self.assertEqual(len(public["model_pool"]["endpoints"]), 2)
        self.assertEqual(
            public["model_pools"][0]["display_name"],
            "玩家命名的 DeepSeek 池",
        )
        self.assertNotIn("api_key", public["model_pool"]["endpoints"][0])
        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        keys = {
            item["endpoint_id"]: item["api_key"]
            for item in persisted["model_pools"][0]["endpoints"]
        }
        self.assertEqual(keys["official"], "official-secret")
        self.assertEqual(keys["relay"], "relay-secret")
        reloaded = RuntimeConfig.load(self.path).snapshot()
        self.assertEqual(len(reloaded.model_pool.endpoints), 2)

    def test_named_application_profile_binds_logical_model(self) -> None:
        public = self.service.save_application_model_profile(
            {
                "profile_id": "economy-cheap",
                "display_name": "经济治理低成本配置",
                "application_id": "economy_governance",
                "pool_id": "deepseek",
                "model_id": "deepseek-chat",
                "thinking_enabled": True,
                "reasoning_effort": "high",
                "temperature": 0.3,
                "context_compression_enabled": True,
                "context_compression_trigger_percent": 80,
                "context_compression_target_percent": 35,
                "request_body_overrides": {"top_p": 0.9},
                "web_research_enabled": False,
                "searxng_url": "http://127.0.0.1:8080",
                "crawl4ai_url": "http://127.0.0.1:11235",
                "stellaris_wiki_api_url": (
                    "https://stellaris.paradoxwikis.com/api.php"
                ),
                "web_search_allowed_domains": [
                    "stellaris.paradoxwikis.com"
                ],
                "web_fetch_direct_fallback_enabled": False,
            }
        )

        self.assertEqual(public["active_profile_id"], "economy-cheap")
        self.assertEqual(
            public["application_model_bindings"]["economy_governance"],
            "economy-cheap",
        )
        profile = next(
            item
            for item in public["application_model_profiles"]
            if item["profile_id"] == "economy-cheap"
        )
        self.assertEqual(profile["display_name"], "经济治理低成本配置")
        self.assertEqual(profile["model_id"], "deepseek-chat")
        serialized = json.dumps(public)
        self.assertNotIn("official-secret", serialized)
        self.assertNotIn("relay-secret", serialized)

    def test_unreferenced_empty_pool_can_be_saved(self) -> None:
        current_pool = self.service.public_model_config()["model_pools"][0]

        public = self.service.save_model_pools(
            {
                "model_pools": [
                    current_pool,
                    {
                        "pool_id": "player-created",
                        "display_name": "玩家新建模型池",
                        "endpoints": [],
                    },
                ]
            }
        )

        created = next(
            pool
            for pool in public["model_pools"]
            if pool["pool_id"] == "player-created"
        )
        self.assertEqual(created["display_name"], "玩家新建模型池")
        self.assertEqual(created["endpoints"], [])

    def test_existing_endpoint_parameters_can_be_changed(self) -> None:
        current_pool = self.service.public_model_config()["model_pools"][0]
        current_endpoint = current_pool["endpoints"][0]
        current_endpoint.update(
            {
                "display_name": "DeepSeek 官方低优先级",
                "priority": 27,
                "base_url": "https://edited.example.test/v1",
                "timeout_seconds": 240,
                "sdk_max_retries": 5,
                "model_context_window_tokens": 128_000,
            }
        )

        self.service.save_model_pools({"model_pools": [current_pool]})
        endpoint_after_reload = RuntimeConfig.load(self.path).snapshot().endpoint

        self.assertEqual(endpoint_after_reload.display_name, "DeepSeek 官方低优先级")
        self.assertEqual(endpoint_after_reload.priority, 27)
        self.assertEqual(
            str(endpoint_after_reload.base_url),
            "https://edited.example.test/v1",
        )
        self.assertEqual(endpoint_after_reload.timeout_seconds, 240)
        self.assertEqual(endpoint_after_reload.sdk_max_retries, 5)
        self.assertEqual(endpoint_after_reload.model_context_window_tokens, 128_000)
        self.assertEqual(endpoint_after_reload.api_key.get_secret_value(), "official-secret")

    def test_endpoint_model_replacement_migrates_referencing_profiles(self) -> None:
        current_pool = self.service.public_model_config()["model_pools"][0]
        current_endpoint = current_pool["endpoints"][0]
        current_endpoint.update(
            {
                "model_id": "replacement-model",
                "model": "replacement-provider-model",
                "base_url": "https://replacement.example.test/v1",
            }
        )

        public = self.service.save_model_pools({"model_pools": [current_pool]})

        profile = next(
            item
            for item in public["application_model_profiles"]
            if item["profile_id"] == "economy-default"
        )
        self.assertEqual(profile["model_id"], "replacement-model")
        self.assertEqual(
            public["profile_migrations"],
            [
                {
                    "profile_id": "economy-default",
                    "pool_id": "deepseek",
                    "from_model_id": "deepseek-chat",
                    "to_model_id": "replacement-model",
                }
            ],
        )
        reloaded = RuntimeConfig.load(self.path).snapshot()
        self.assertEqual(reloaded.application_profile.model_id, "replacement-model")
        self.assertEqual(reloaded.endpoint.model, "replacement-provider-model")

    def test_application_can_select_second_logical_model_in_pool(self) -> None:
        current_pool = self.service.public_model_config()["model_pools"][0]
        reasoner = endpoint("reasoner", 1, "reasoner-secret")
        reasoner["model_id"] = "deepseek-reasoner"
        reasoner["model"] = "deepseek-reasoner-provider-name"
        current_pool["endpoints"].append(reasoner)
        self.service.save_model_pools({"model_pools": [current_pool]})

        public = self.service.save_application_model_profile(
            {
                "profile_id": "economy-reasoning",
                "display_name": "经济治理推理配置",
                "application_id": "economy_governance",
                "pool_id": "deepseek",
                "model_id": "deepseek-reasoner",
            }
        )

        self.assertEqual(public["model_pool"]["model_id"], "deepseek-reasoner")
        self.assertEqual(len(public["model_pool"]["endpoints"]), 1)
        self.assertEqual(
            public["model_pool"]["endpoints"][0]["model"],
            "deepseek-reasoner-provider-name",
        )

    def test_anthropic_endpoint_can_replace_the_active_tool_route(self) -> None:
        current_pool = self.service.public_model_config()["model_pools"][0]
        anthropic = endpoint("anthropic-official", 0, "anthropic-secret")
        anthropic.update(
            {
                "display_name": "Anthropic Official",
                "model": "claude-sonnet-4-6",
                "model_transport": "anthropic_sdk",
                "provider": "anthropic_messages_compatible",
                "base_url": "https://api.anthropic.com",
                "messages_path": "/v1/messages",
                "models_path": "/v1/models",
                "api_key_header": "x-api-key",
                "api_key_prefix": "",
                "extra_headers": {"anthropic-version": "2023-06-01"},
            }
        )
        current_pool["endpoints"] = [anthropic]

        public = self.service.save_model_pools(
            {"model_pools": [current_pool]}
        )

        saved = public["model_pool"]["endpoints"][0]
        self.assertEqual(saved["model_transport"], "anthropic_sdk")
        self.assertEqual(saved["provider"], "anthropic_messages_compatible")
        self.assertEqual(saved["messages_path"], "/v1/messages")
        self.assertTrue(public["conversation_supported"])
        self.assertNotIn("anthropic-secret", json.dumps(public))

    def test_specialist_profile_can_explicitly_follow_economy_model(self) -> None:
        public = self.service.save_application_model_profile(
            {
                "profile_id": "fleet-shared",
                "display_name": "舰队沿用经济模型",
                "application_id": "fleet_operations",
                "pool_id": "deepseek",
                "model_id": "deepseek-chat",
                "inherit_model_from_application_id": "economy_governance",
            }
        )

        profile = next(
            item
            for item in public["application_model_profiles"]
            if item["profile_id"] == "fleet-shared"
        )
        self.assertEqual(
            profile["inherit_model_from_application_id"],
            "economy_governance",
        )
        route = next(
            item
            for item in self.service.application_agent_routes()
            if item["application_id"] == "fleet_operations"
        )
        self.assertEqual(route["binding_mode"], "inherited")
        self.assertEqual(route["model_source_profile_id"], "economy-default")


if __name__ == "__main__":
    unittest.main()
