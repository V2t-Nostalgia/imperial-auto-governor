#!/usr/bin/env python3
"""Contract tests for the OpenAI SDK and raw HTTP LLM transports."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

import httpx
from openai import AsyncOpenAI

from iag.infrastructure.llm.model_client import (
    LLMPartialResponseError,
    _run_async_from_sync,
    async_call_model,
    async_chat_completion_message,
    chat_completion_body,
)
from iag.infrastructure.llm.model_pool import ModelEndpoint
from iag.infrastructure.llm.providers import (
    OpenAISDKTransport,
    RawHTTPTransport,
    resolve_api_key,
    transport_from_endpoint,
)


def base_endpoint(**overrides: Any) -> ModelEndpoint:
    value: dict[str, Any] = {
        "endpoint_id": "test-endpoint",
        "model": "test-model",
        "model_transport": "openai_sdk",
        "provider": "chat_completions_compatible",
        "base_url": "https://model.example/v1",
        "supports_reasoning": True,
        "model_context_window_tokens": 64_000,
        "auth_mode": "bearer",
        "api_key": "test-secret",
        "max_output_tokens": 8_000,
        "priority": 0,
        "enabled": True,
        "supports_tools": True,
        "chat_completions_path": "/chat/completions",
        "responses_path": "/responses",
        "timeout_seconds": 30,
        "sdk_max_retries": 0,
    }
    value.update(overrides)
    return ModelEndpoint.model_validate(value)


def base_options(**overrides: Any) -> dict[str, Any]:
    value = {"temperature": 0.2, "request_body_overrides": {}}
    value.update(overrides)
    return value


class ApiKeyResolutionTests(unittest.TestCase):
    def test_endpoint_key_is_unwrapped(self) -> None:
        endpoint = ModelEndpoint(
            model="test-model",
            model_transport="openai_sdk",
            provider="chat_completions_compatible",
            base_url="https://model.example/v1",
            supports_reasoning=True,
            model_context_window_tokens=64_000,
            auth_mode="bearer",
            api_key="endpoint-secret",
            endpoint_id="test-endpoint",
            max_output_tokens=8_000,
            priority=0,
            enabled=True,
            supports_tools=True,
        )

        self.assertEqual(resolve_api_key(endpoint), "endpoint-secret")


class StubTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.chat_calls: list[dict[str, Any]] = []
        self.response_calls: list[dict[str, Any]] = []

    async def create_chat_completion(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        self.chat_calls.append(body)
        return self.response

    async def create_response(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        self.response_calls.append(body)
        return self.response


class ModelClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_callback_receives_visible_content_only(self) -> None:
        class StreamingTransport(StubTransport):
            async def create_chat_completion_stream(
                self,
                endpoint: ModelEndpoint,
                body: dict[str, Any],
                visible_delta_callback: Any,
            ) -> dict[str, Any]:
                visible_delta_callback("first ")
                visible_delta_callback("second")
                return {
                    "role": "assistant",
                    "content": "first second",
                    "reasoning_content": "private",
                }

        visible: list[str] = []
        message = await async_chat_completion_message(
            base_endpoint(),
            [{"role": "user", "content": "hello"}],
            transport=StreamingTransport({}),
            visible_delta_callback=visible.append,
        )

        self.assertEqual(visible, ["first ", "second"])
        self.assertEqual(message["content"], "first second")
        self.assertEqual(message["reasoning_content"], "private")

    async def test_partial_stream_failure_is_marked_unsafe_to_replay(self) -> None:
        class BrokenStreamingTransport(StubTransport):
            async def create_chat_completion_stream(
                self,
                endpoint: ModelEndpoint,
                body: dict[str, Any],
                visible_delta_callback: Any,
            ) -> dict[str, Any]:
                visible_delta_callback("already visible")
                raise ConnectionError("stream ended")

        with self.assertRaises(LLMPartialResponseError):
            await async_chat_completion_message(
                base_endpoint(),
                [{"role": "user", "content": "hello"}],
                transport=BrokenStreamingTransport({}),
                visible_delta_callback=lambda _delta: None,
            )

    async def test_sdk_preserves_deepseek_reasoning_and_tool_calls(self) -> None:
        observed: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["url"] = str(request.url)
            observed["authorization"] = request.headers.get("authorization")
            observed["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "completion-1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "deepseek-test",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "reasoning_content": "internal reasoning",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "inspect_empire_state",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )

        def client_factory(**kwargs: Any) -> AsyncOpenAI:
            return AsyncOpenAI(
                **kwargs,
                http_client=httpx.AsyncClient(
                    transport=httpx.MockTransport(handler)
                ),
            )

        endpoint = base_endpoint()
        options = base_options(
            thinking={"type": "enabled"},
            reasoning_effort="max",
            request_body_overrides={"top_p": 0.9},
        )
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "inspect_empire_state",
                    "description": "Read state.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        message = await async_chat_completion_message(
            endpoint,
            [{"role": "user", "content": "inspect"}],
            request_options=options,
            tools=tools,
            transport=OpenAISDKTransport(client_factory),
        )

        self.assertEqual(observed["url"], "https://model.example/v1/chat/completions")
        self.assertEqual(observed["authorization"], "Bearer test-secret")
        self.assertEqual(observed["body"]["thinking"], {"type": "enabled"})
        self.assertEqual(observed["body"]["top_p"], 0.9)
        self.assertNotIn("temperature", observed["body"])
        self.assertEqual(message["reasoning_content"], "internal reasoning")
        self.assertEqual(
            message["tool_calls"][0]["function"]["name"],
            "inspect_empire_state",
        )

    async def test_responses_protocol_uses_injected_transport(self) -> None:
        transport = StubTransport(
            {
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": '{"actions":[]}'},
                        ]
                    }
                ]
            }
        )
        result = await async_call_model(
            base_endpoint(provider="responses_compatible"),
            {"state": "ok"},
            transport=transport,
        )
        self.assertEqual(result, {"actions": []})
        self.assertEqual(len(transport.response_calls), 1)
        self.assertEqual(transport.chat_calls, [])

    async def test_sdk_serializes_responses_protocol(self) -> None:
        observed: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["url"] = str(request.url)
            observed["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "response-1",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "model": "test-model",
                    "output": [
                        {
                            "id": "message-1",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"actions":[]}',
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "parallel_tool_calls": True,
                    "tool_choice": "auto",
                    "tools": [],
                },
            )

        def client_factory(**kwargs: Any) -> AsyncOpenAI:
            return AsyncOpenAI(
                **kwargs,
                http_client=httpx.AsyncClient(
                    transport=httpx.MockTransport(handler)
                ),
            )

        endpoint = base_endpoint(provider="responses_compatible")
        options = base_options(
            request_body_overrides={"max_output_tokens": 128},
        )
        result = await async_call_model(
            endpoint,
            {"state": "ok"},
            request_options=options,
            transport=OpenAISDKTransport(client_factory),
        )
        self.assertEqual(result, {"actions": []})
        self.assertEqual(observed["url"], "https://model.example/v1/responses")
        self.assertEqual(observed["body"]["max_output_tokens"], 128)
        self.assertEqual(
            observed["body"]["text"],
            {"format": {"type": "json_object"}},
        )

    async def test_raw_http_transport_uses_exact_configured_endpoint(self) -> None:
        observed: dict[str, Any] = {}

        def post_function(
            url: str,
            headers: dict[str, str],
            body: dict[str, Any],
            timeout: int,
        ) -> dict[str, Any]:
            observed.update(
                url=url,
                headers=headers,
                body=body,
                timeout=timeout,
            )
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        endpoint = base_endpoint(
            model_transport="raw_http",
            base_url="http://local.example/api",
            chat_completions_path="/custom/generate",
            auth_mode="none",
            api_key=None,
        )
        raw = await RawHTTPTransport(post_function).create_chat_completion(
            endpoint,
            {"model": "test-model", "messages": []},
        )
        self.assertEqual(observed["url"], "http://local.example/api/custom/generate")
        self.assertNotIn("Authorization", observed["headers"])
        self.assertEqual(raw["choices"][0]["message"]["content"], "ok")

    async def test_sdk_rejects_arbitrary_path_instead_of_silent_fallback(self) -> None:
        endpoint = base_endpoint(chat_completions_path="/custom/generate")
        with self.assertRaisesRegex(ValueError, "use 'raw_http'"):
            await OpenAISDKTransport().create_chat_completion(
                endpoint,
                {"model": "test-model", "messages": []},
            )

    def test_transport_selection_is_explicit(self) -> None:
        self.assertIsInstance(
            transport_from_endpoint(base_endpoint()),
            OpenAISDKTransport,
        )
        self.assertIsInstance(
            transport_from_endpoint(
                base_endpoint(model_transport="raw_http")
            ),
            RawHTTPTransport,
        )
        invalid = base_endpoint().model_copy(
            update={"model_transport": "automatic_fallback"}
        )
        with self.assertRaisesRegex(ValueError, "Unsupported model_transport"):
            transport_from_endpoint(invalid)

    def test_provider_overrides_are_sent_outside_protocol_fields(self) -> None:
        body = chat_completion_body(
            base_endpoint(),
            [{"role": "user", "content": "hello"}],
            request_options=base_options(
                request_body_overrides={"top_p": 0.8}
            ),
        )
        self.assertEqual(body["top_p"], 0.8)
        self.assertEqual(body["temperature"], 0.2)

    async def test_sync_bridge_is_safe_inside_an_existing_event_loop(self) -> None:
        self.assertEqual(
            _run_async_from_sync(asyncio.sleep(0, result="ok")),
            "ok",
        )


if __name__ == "__main__":
    unittest.main()
