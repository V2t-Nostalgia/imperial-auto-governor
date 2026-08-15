#!/usr/bin/env python3
"""Contract tests for model-pool configuration and explicit persistence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from iag.infrastructure.llm.model_pool import ModelEndpoint, ModelPool
from iag.infrastructure.llm.runtime_config import RuntimeConfig


def endpoint_document(
    endpoint_id: str = "primary",
    *,
    model: str = "model-a",
    model_id: str | None = None,
    priority: int = 0,
    api_key: str = "local-secret",
) -> dict[str, object]:
    return {
        "endpoint_id": endpoint_id,
        "display_name": endpoint_id,
        "model_id": model_id or model,
        "model": model,
        "model_transport": "openai_sdk",
        "provider": "chat_completions_compatible",
        "base_url": "https://model.example/v1",
        "supports_reasoning": True,
        "model_context_window_tokens": 64_000,
        "auth_mode": "bearer",
        "api_key": api_key,
        "max_output_tokens": 8_000,
        "priority": priority,
        "enabled": True,
        "supports_tools": True,
    }


def legacy_config_document() -> dict[str, object]:
    return {
        "endpoint": endpoint_document(),
        "request_options": {"temperature": 0.2},
        "runtime_root": "runtime",
    }


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "agent_config.json"
        self.path.write_text(
            json.dumps(legacy_config_document(), ensure_ascii=False),
            encoding="utf-8",
        )
        self.runtime = RuntimeConfig.load(self.path)

    def test_legacy_endpoint_migrates_only_when_explicitly_saved(self) -> None:
        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot.model_pool.pool_id, "default")
        self.assertEqual(snapshot.endpoint.model, "model-a")
        self.assertIn("endpoint", json.loads(self.path.read_text()))

        self.runtime.save()
        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("endpoint", persisted)
        self.assertNotIn("model_pool", persisted)
        self.assertEqual(persisted["model_pools"][0]["pool_id"], "default")
        self.assertEqual(
            persisted["model_pools"][0]["endpoints"][0]["api_key"],
            "local-secret",
        )
        self.assertEqual(
            persisted["application_model_bindings"]["economy_governance"],
            "economy-default",
        )

    def test_pool_update_is_live_but_persistence_is_explicit(self) -> None:
        secondary = ModelEndpoint.model_validate(
            endpoint_document(
                "secondary",
                model="model-b",
                model_id="model-a",
                priority=1,
                api_key="secondary-secret",
            )
        )
        pool = ModelPool(
            pool_id="deepseek",
            endpoints=[self.runtime.snapshot().endpoint, secondary],
        )
        self.runtime.update(
            model_pool=pool,
            request_options={"temperature": 0.7},
            settings={"runtime_root": "new-runtime"},
        )

        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot.model_pool.pool_id, "deepseek")
        self.assertEqual(len(snapshot.model_pool.endpoints), 2)
        catalog_pool = next(
            item for item in snapshot.model_pools if item.pool_id == "deepseek"
        )
        self.assertEqual(len(catalog_pool.endpoints), 2)
        self.assertEqual(snapshot.request_options["temperature"], 0.7)
        self.assertEqual(snapshot.settings["runtime_root"], "new-runtime")
        self.assertIn("endpoint", json.loads(self.path.read_text()))

        self.runtime.save()
        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        persisted_pool = next(
            item
            for item in persisted["model_pools"]
            if item["pool_id"] == "deepseek"
        )
        self.assertEqual(
            persisted_pool["endpoints"][1]["api_key"],
            "secondary-secret",
        )

    def test_invalid_endpoint_does_not_change_runtime(self) -> None:
        before = self.runtime.snapshot()
        invalid = before.endpoint.model_copy(
            update={"max_output_tokens": 100_000}
        )
        with self.assertRaises(ValidationError):
            self.runtime.update(endpoint=invalid)

        self.assertEqual(self.runtime.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
