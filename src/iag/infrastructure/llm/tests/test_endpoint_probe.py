#!/usr/bin/env python3
"""Tests for no-completion endpoint reachability probes."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from typing import Any

from iag.infrastructure.llm.endpoint_probe import probe_endpoint
from iag.infrastructure.llm.model_pool import ModelEndpoint


def endpoint(**updates: Any) -> ModelEndpoint:
    value = {
        "endpoint_id": "probe-target",
        "model": "deepseek-v4-pro",
        "model_transport": "openai_sdk",
        "provider": "chat_completions_compatible",
        "base_url": "https://api.example.test/v1",
        "supports_reasoning": True,
        "model_context_window_tokens": 128_000,
        "auth_mode": "bearer",
        "api_key": "test-key",
        "max_output_tokens": 8_192,
        "priority": 0,
        "enabled": True,
        "supports_tools": True,
    }
    value.update(updates)
    return ModelEndpoint.model_validate(value)


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self, _maximum: int) -> bytes:
        return self.payload


class EndpointProbeTests(unittest.TestCase):
    def test_openrouter_style_models_response_confirms_model(self) -> None:
        observed: dict[str, Any] = {}

        def opener(request: Any, *, timeout: int) -> FakeResponse:
            observed["url"] = request.full_url
            observed["authorization"] = request.headers.get("Authorization")
            observed["timeout"] = timeout
            return FakeResponse(
                {
                    "data": [
                        {
                            "id": "deepseek-v4-pro",
                            "context_length": 262_144,
                            "supported_parameters": ["tools", "temperature"],
                        }
                    ]
                }
            )

        result = probe_endpoint(endpoint(), opener=opener)

        self.assertEqual(result.status, "available")
        self.assertTrue(result.model_found)
        self.assertEqual(result.discovered_model_count, 1)
        self.assertEqual(observed["url"], "https://api.example.test/v1/models")
        self.assertEqual(observed["authorization"], "Bearer test-key")
        self.assertEqual(observed["timeout"], 10)

    def test_reachable_endpoint_without_model_is_not_disabled(self) -> None:
        result = probe_endpoint(
            endpoint(),
            opener=lambda *_args, **_kwargs: FakeResponse(
                {"object": "list", "data": [{"id": "another-model"}]}
            ),
        )

        self.assertEqual(result.status, "reachable_unconfirmed")
        self.assertFalse(result.model_found)

    def test_429_retains_retry_after(self) -> None:
        def opener(*_args: Any, **_kwargs: Any) -> FakeResponse:
            raise urllib.error.HTTPError(
                "https://api.example.test/v1/models",
                429,
                "rate limited",
                {"Retry-After": "123"},
                io.BytesIO(b'{"error":"quota"}'),
            )

        result = probe_endpoint(endpoint(), opener=opener)

        self.assertEqual(result.status, "rate_limited")
        self.assertEqual(result.retry_after_seconds, 123)

    def test_null_models_path_explicitly_disables_probe(self) -> None:
        result = probe_endpoint(endpoint(models_path=None))
        self.assertEqual(result.status, "probe_unsupported")

    def test_anthropic_probe_uses_native_authentication_headers(self) -> None:
        observed: dict[str, Any] = {}

        def opener(request: Any, *, timeout: int) -> FakeResponse:
            observed["url"] = request.full_url
            observed["api_key"] = request.headers.get("X-api-key")
            observed["version"] = request.headers.get("Anthropic-version")
            observed["timeout"] = timeout
            return FakeResponse(
                {"data": [{"id": "claude-sonnet-4-6", "type": "model"}]}
            )

        result = probe_endpoint(
            endpoint(
                model="claude-sonnet-4-6",
                model_transport="anthropic_sdk",
                provider="anthropic_messages_compatible",
                base_url="https://api.anthropic.com",
                api_key_header="x-api-key",
                api_key_prefix="",
                extra_headers={"anthropic-version": "2023-06-01"},
                messages_path="/v1/messages",
                models_path="/v1/models",
            ),
            opener=opener,
        )

        self.assertEqual(result.status, "available")
        self.assertEqual(observed["url"], "https://api.anthropic.com/v1/models")
        self.assertEqual(observed["api_key"], "test-key")
        self.assertEqual(observed["version"], "2023-06-01")
        self.assertEqual(observed["timeout"], 10)


if __name__ == "__main__":
    unittest.main()
