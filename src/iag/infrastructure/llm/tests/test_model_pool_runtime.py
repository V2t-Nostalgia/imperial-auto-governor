#!/usr/bin/env python3
"""Routing tests for cheap-first model endpoints and conservative replay."""

from __future__ import annotations

import unittest

from iag.infrastructure.llm.endpoint_probe import EndpointProbeResult
from iag.infrastructure.llm.model_pool import (
    TOOL_CALL_PROTOCOLS,
    ModelEndpoint,
    ModelPool,
)
from iag.infrastructure.llm.model_pool_runtime import ModelPoolRuntime
from iag.infrastructure.llm.providers import LLMHTTPError, LLMTimeoutError


def endpoint(endpoint_id: str, priority: int) -> ModelEndpoint:
    return ModelEndpoint(
        endpoint_id=endpoint_id,
        model="logical-model",
        model_transport="openai_sdk",
        provider="chat_completions_compatible",
        base_url=f"https://{endpoint_id}.example.test/v1",
        supports_reasoning=True,
        model_context_window_tokens=64_000,
        auth_mode="bearer",
        api_key=f"{endpoint_id}-key",
        max_output_tokens=8_000,
        priority=priority,
        enabled=True,
        supports_tools=True,
        rate_limit_cooldown_seconds=600,
    )


class ModelPoolRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock_value = 1_000.0
        self.runtime = ModelPoolRuntime(
            ModelPool(
                pool_id="logical-model",
                endpoints=[endpoint("expensive", 20), endpoint("cheap", 0)],
            ),
            clock=lambda: self.clock_value,
        )

    def test_candidates_are_sorted_by_configured_priority(self) -> None:
        self.assertEqual(
            [item.endpoint_id for item in self.runtime.candidates()],
            ["cheap", "expensive"],
        )

    def test_tool_protocol_filter_accepts_openai_and_anthropic(self) -> None:
        anthropic = ModelEndpoint(
            endpoint_id="anthropic",
            model="claude-test",
            model_transport="anthropic_sdk",
            provider="anthropic_messages_compatible",
            base_url="https://api.anthropic.com",
            supports_reasoning=True,
            model_context_window_tokens=64_000,
            auth_mode="bearer",
            api_key="anthropic-key",
            api_key_header="x-api-key",
            api_key_prefix="",
            messages_path="/v1/messages",
            max_output_tokens=8_000,
            priority=10,
            enabled=True,
            supports_tools=True,
        )
        runtime = ModelPoolRuntime(
            ModelPool(
                pool_id="mixed",
                endpoints=[endpoint("openai", 20), anthropic],
            )
        )

        self.assertEqual(
            [
                item.endpoint_id
                for item in runtime.candidates(provider=TOOL_CALL_PROTOCOLS)
            ],
            ["anthropic", "openai"],
        )

    def test_rate_limit_uses_next_endpoint_and_cools_first(self) -> None:
        calls: list[str] = []

        def operation(candidate: ModelEndpoint) -> str:
            calls.append(candidate.endpoint_id)
            if candidate.endpoint_id == "cheap":
                raise LLMHTTPError(429, "quota", retry_after_seconds=120)
            return "ok"

        self.assertEqual(self.runtime.execute(operation), "ok")
        self.assertEqual(calls, ["cheap", "expensive"])
        status = self.runtime.status()
        cheap = next(
            item for item in status["endpoints"] if item["endpoint_id"] == "cheap"
        )
        self.assertEqual(cheap["status"], "rate_limited")
        self.assertFalse(cheap["eligible"])
        self.assertEqual(status["last_selected_endpoint_id"], "expensive")

    def test_ambiguous_server_error_is_not_replayed(self) -> None:
        calls: list[str] = []

        def operation(candidate: ModelEndpoint) -> None:
            calls.append(candidate.endpoint_id)
            raise LLMHTTPError(503, "unknown provider state")

        with self.assertRaises(LLMHTTPError):
            self.runtime.execute(operation)
        self.assertEqual(calls, ["cheap"])

    def test_timeout_is_not_replayed(self) -> None:
        calls: list[str] = []

        def operation(candidate: ModelEndpoint) -> None:
            calls.append(candidate.endpoint_id)
            raise LLMTimeoutError("delivery state unknown")

        with self.assertRaises(LLMTimeoutError):
            self.runtime.execute(operation)
        self.assertEqual(calls, ["cheap"])

    def test_probe_status_is_exposed_without_chat_request(self) -> None:
        observed: list[str] = []

        def probe(candidate: ModelEndpoint) -> EndpointProbeResult:
            observed.append(candidate.endpoint_id)
            return EndpointProbeResult(
                endpoint_id=candidate.endpoint_id,
                status="available",
                detail="listed",
                checked_at="2026-08-10T00:00:00+00:00",
                probe_url="https://cheap.example.test/v1/models",
                http_status=200,
                model_found=True,
                discovered_model_count=2,
            )

        runtime = ModelPoolRuntime(
            self.runtime.pool(),
            probe_function=probe,
            clock=lambda: self.clock_value,
        )
        runtime.probe("cheap")
        self.assertEqual(observed, ["cheap"])
        cheap = next(
            item
            for item in runtime.status()["endpoints"]
            if item["endpoint_id"] == "cheap"
        )
        self.assertEqual(cheap["status"], "available")
        self.assertEqual(cheap["discovered_model_count"], 2)
        self.assertEqual(
            cheap["probe_url"],
            "https://cheap.example.test/v1/models",
        )


if __name__ == "__main__":
    unittest.main()
