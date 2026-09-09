#!/usr/bin/env python3
"""Provider-neutral JSON LLM client used by the IAG planner."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any, TypeVar

from iag.applications.economy_governance.planner import SYSTEM_PROMPT
from iag.infrastructure.llm.anthropic_adapter import (
    anthropic_message_body,
    anthropic_message_to_assistant,
)
from iag.infrastructure.llm.model_pool import ModelEndpoint
from iag.infrastructure.llm.providers import (
    LLMTransport,
    api_headers,
    api_url,
    post_json,
    transport_from_endpoint,
)

__all__ = [
    "LLMPartialResponseError",
    "api_headers",
    "api_url",
    "async_call_model",
    "async_chat_completion_message",
    "call_model",
    "chat_completion_body",
    "chat_completion_message",
    "post_json",
    "request_body_overrides",
]


PROTECTED_REQUEST_KEYS = {
    "model",
    "messages",
    "input",
    "instructions",
    "tools",
    "tool_choice",
    "response_format",
    "text",
    "stream",
    "stream_options",
    "system",
}


class LLMPartialResponseError(RuntimeError):
    """A streamed request failed after visible output made replay unsafe."""


def request_body_overrides(
    request_options: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return JSON-safe provider parameters without exposing protocol fields."""
    options = request_options or {}
    value = options.get("request_body_overrides", {})
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("request_body_overrides must be a JSON object.")
    if len(value) > 64:
        raise ValueError("request_body_overrides contains too many keys.")
    forbidden = sorted(str(key) for key in value if str(key) in PROTECTED_REQUEST_KEYS)
    if forbidden:
        raise ValueError(
            "request_body_overrides cannot replace protected keys: "
            + ", ".join(forbidden)
        )
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(rendered) > 32_000:
        raise ValueError("request_body_overrides exceeds 32000 characters.")
    return json.loads(rendered)


def parse_model_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        cleaned = cleaned[first_newline + 1 :] if first_newline >= 0 else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    value = json.loads(cleaned.strip())
    if not isinstance(value, dict):
        raise ValueError("Model output must be a JSON object.")
    return value


def response_output_text(raw: dict[str, Any]) -> str:
    if raw.get("output_text"):
        return str(raw["output_text"])
    chunks: list[str] = []
    for item in raw.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(str(content.get("text", "")))
    return "".join(chunks)


def chat_completion_body(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    request_options: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one OpenAI-compatible chat request without leaking provider quirks."""
    options = request_options or {}
    body: dict[str, Any] = {
        "model": endpoint.model,
        "messages": [
            {
                key: value
                for key, value in message.items()
                if key != "anthropic_content"
            }
            for message in messages
        ],
    }
    overrides = request_body_overrides(options)
    body.update(overrides)
    thinking = options.get("thinking")
    thinking_enabled = (
        isinstance(thinking, dict)
        and str(thinking.get("type", "")).lower() == "enabled"
    )
    if isinstance(thinking, dict):
        body["thinking"] = thinking
    if options.get("reasoning_effort"):
        body["reasoning_effort"] = str(options["reasoning_effort"])
    if (
        not thinking_enabled
        and "temperature" not in overrides
        and options.get("temperature") is not None
    ):
        body["temperature"] = float(options["temperature"])
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if response_format:
        body["response_format"] = response_format
    return body


async def async_chat_completion_message(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    request_options: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    transport: LLMTransport | None = None,
    visible_delta_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Return one normalized assistant message for a persistent tool loop."""
    selected = transport or transport_from_endpoint(endpoint)
    if endpoint.provider == "anthropic_messages_compatible":
        body = anthropic_message_body(
            endpoint,
            messages,
            request_options=request_options,
            tools=tools,
            overrides=request_body_overrides(request_options),
        )
        if visible_delta_callback is not None:
            emitted = False

            def forward_anthropic(delta: str) -> None:
                nonlocal emitted
                if not delta:
                    return
                emitted = True
                visible_delta_callback(delta)

            stream_method = getattr(
                selected,
                "create_anthropic_message_stream",
                None,
            )
            if callable(stream_method):
                try:
                    raw = await stream_method(endpoint, body, forward_anthropic)
                except Exception as error:
                    if emitted:
                        raise LLMPartialResponseError(
                            "The model stream failed after visible output was "
                            "emitted; the request will not be replayed on another "
                            "endpoint."
                        ) from error
                    raise
                return anthropic_message_to_assistant(raw)
        method = getattr(selected, "create_anthropic_message", None)
        if not callable(method):
            raise RuntimeError(
                "The selected transport does not implement Anthropic Messages."
            )
        raw = await method(endpoint, body)
        message = anthropic_message_to_assistant(raw)
        if visible_delta_callback is not None and message["content"]:
            visible_delta_callback(str(message["content"]))
        return message
    if endpoint.provider != "chat_completions_compatible":
        raise ValueError(
            f"Provider {endpoint.provider!r} cannot run the persistent tool loop."
        )
    body = chat_completion_body(
        endpoint,
        messages,
        request_options=request_options,
        tools=tools,
    )
    if visible_delta_callback is not None:
        emitted = False

        def forward(delta: str) -> None:
            nonlocal emitted
            if not delta:
                return
            emitted = True
            visible_delta_callback(delta)

        stream_method = getattr(selected, "create_chat_completion_stream", None)
        if callable(stream_method):
            try:
                message = await stream_method(endpoint, body, forward)
            except Exception as error:
                if emitted:
                    raise LLMPartialResponseError(
                        "The model stream failed after visible output was emitted; "
                        "the request will not be replayed on another endpoint."
                    ) from error
                raise
            if not isinstance(message, dict):
                raise RuntimeError(
                    "Chat Completions stream returned no assistant message."
                )
            return message

    method = getattr(selected, "create_chat_completion", None)
    if not callable(method):
        raise NotImplementedError(
            "The selected transport does not implement Chat Completions."
        )
    raw = await method(endpoint, body)
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Chat Completions API returned no choices.")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("Chat Completions API returned no assistant message.")
    if visible_delta_callback is not None:
        content = str(message.get("content") or "")
        if content:
            visible_delta_callback(content)
    return message


async def async_call_model(
    endpoint: ModelEndpoint,
    decision_request: dict[str, Any],
    *,
    request_options: dict[str, Any] | None = None,
    transport: LLMTransport | None = None,
) -> dict[str, Any]:
    """Complete one planner request through the selected asynchronous transport."""
    provider = endpoint.provider
    selected = transport or transport_from_endpoint(endpoint)
    user_text = json.dumps(
        decision_request,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    if provider == "responses_compatible":
        body = {
            "model": endpoint.model,
            "instructions": SYSTEM_PROMPT,
            "input": user_text,
            "text": {"format": {"type": "json_object"}},
        }
        body.update(request_body_overrides(request_options))
        method = getattr(selected, "create_response", None)
        if not callable(method):
            raise NotImplementedError(
                "The selected transport does not implement Responses."
            )
        raw = await method(endpoint, body)
        output_text = response_output_text(raw)
        if not output_text:
            raise RuntimeError("Responses-compatible API returned no text.")
        return parse_model_json(output_text)

    if provider == "chat_completions_compatible":
        body = chat_completion_body(
            endpoint,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            request_options=request_options,
            response_format={"type": "json_object"},
        )
        method = getattr(selected, "create_chat_completion", None)
        if not callable(method):
            raise NotImplementedError(
                "The selected transport does not implement Chat Completions."
            )
        raw = await method(endpoint, body)
        return parse_model_json(
            str(raw["choices"][0]["message"]["content"])
        )

    if provider == "anthropic_messages_compatible":
        message = await async_chat_completion_message(
            endpoint,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            request_options=request_options,
            transport=selected,
        )
        content = str(message.get("content") or "")
        if not content:
            raise RuntimeError("Anthropic Messages API returned no text.")
        return parse_model_json(content)

    raise ValueError(f"Unsupported provider: {provider}")


_ResultT = TypeVar("_ResultT")


def _run_async_from_sync(awaitable: Coroutine[Any, Any, _ResultT]) -> _ResultT:
    """Run an async provider without forcing the current synchronous app to change."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    # A synchronous compatibility call made from an async host cannot nest its
    # running event loop.  Isolate this one request in a short-lived worker.
    result: Future[_ResultT] = Future()

    def worker() -> None:
        try:
            result.set_result(asyncio.run(awaitable))
        except BaseException as error:
            result.set_exception(error)

    thread = threading.Thread(target=worker, name="iag-llm-sdk", daemon=True)
    thread.start()
    return result.result()


def chat_completion_message(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    request_options: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    visible_delta_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Synchronous compatibility facade for the persistent tool loop."""
    return _run_async_from_sync(
        async_chat_completion_message(
            endpoint,
            messages,
            request_options=request_options,
            tools=tools,
            visible_delta_callback=visible_delta_callback,
        )
    )


def call_model(
    endpoint: ModelEndpoint,
    decision_request: dict[str, Any],
    *,
    request_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synchronous compatibility facade for the one-shot planner."""
    return _run_async_from_sync(
        async_call_model(
            endpoint,
            decision_request,
            request_options=request_options,
        )
    )
