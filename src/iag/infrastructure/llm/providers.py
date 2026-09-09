#!/usr/bin/env python3
"""Network transports for supported model APIs.

Application protocols are deliberately separate from their network
transports. Official OpenAI and Anthropic SDKs cover their native APIs; raw
HTTP remains available for compatibility diagnostics and unusual endpoints.
"""

from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, ClassVar, Protocol

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from iag.infrastructure.llm.chat_stream import (
    ChatCompletionAccumulator,
    VisibleDeltaCallback,
)
from iag.infrastructure.llm.model_pool import ModelEndpoint


class LLMHTTPError(RuntimeError):
    """HTTP failure retaining the fields needed by model-pool routing."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(f"LLM API HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds


class LLMConnectionError(RuntimeError):
    """The request failed before a usable HTTP response was received."""


class LLMTimeoutError(RuntimeError):
    """The request timed out after its delivery state became ambiguous."""


def _numeric_retry_after(headers: Any) -> int | None:
    if headers is None:
        return None
    value = headers.get("Retry-After")
    text = str(value or "").strip()
    return max(0, int(text)) if text.isdigit() else None


class ChatCompletionTransport(Protocol):
    """A transport capable of OpenAI-compatible chat completions."""

    async def create_chat_completion(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def create_chat_completion_stream(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
        visible_delta_callback: VisibleDeltaCallback,
    ) -> dict[str, Any]: ...


class ResponsesTransport(Protocol):
    """A transport capable of OpenAI-compatible Responses calls."""

    async def create_response(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
    ) -> dict[str, Any]: ...


class AnthropicMessagesTransport(Protocol):
    """A transport capable of Anthropic-compatible Messages calls."""

    async def create_anthropic_message(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
    ) -> dict[str, Any]: ...


LLMTransport = (
    ChatCompletionTransport
    | ResponsesTransport
    | AnthropicMessagesTransport
)


def resolve_api_key(endpoint: ModelEndpoint) -> str | None:
    """Return the endpoint API key, or None for unauthenticated endpoints."""
    if endpoint.auth_mode == "none":
        return None
    if endpoint.api_key is None:
        raise RuntimeError(
            f"Endpoint {endpoint.endpoint_id!r} has no API key."
        )
    api_key = endpoint.api_key.get_secret_value().strip()
    if not api_key:
        raise RuntimeError(
            f"Endpoint {endpoint.endpoint_id!r} has an empty API key."
        )
    return api_key


def _extra_headers(endpoint: ModelEndpoint) -> dict[str, str]:
    """Return a mutable copy of the endpoint's validated headers."""
    return dict(endpoint.extra_headers)


def _drop_header(headers: dict[str, str], name: str) -> None:
    """Remove all case variants before writing an authoritative auth header."""
    lowered = name.lower()
    for key in list(headers):
        if key.lower() == lowered:
            headers.pop(key)


def api_headers(endpoint: ModelEndpoint) -> dict[str, str]:
    """Build headers for the explicit raw HTTP transport."""
    headers = {"Content-Type": "application/json"}
    headers.update(_extra_headers(endpoint))
    api_key = resolve_api_key(endpoint)
    if api_key is None:
        return headers
    header_name = endpoint.api_key_header
    _drop_header(headers, header_name)
    headers[header_name] = endpoint.api_key_prefix + api_key
    return headers


def api_url(endpoint: ModelEndpoint, path: str) -> str:
    """Build an exact endpoint URL for the raw HTTP transport."""
    base = str(endpoint.base_url).rstrip("/")
    return base + (path if path.startswith("/") else "/" + path)


def post_json(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    """POST a JSON object using only the standard library."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1500]
        raise LLMHTTPError(
            int(error.code),
            detail,
            retry_after_seconds=_numeric_retry_after(error.headers),
        ) from error
    except (socket.timeout, TimeoutError) as error:
        raise LLMTimeoutError(
            f"LLM API timed out: {type(error).__name__}: {error}"
        ) from error
    except urllib.error.URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            raise LLMTimeoutError(
                f"LLM API timed out: {type(error.reason).__name__}: {error.reason}"
            ) from error
        raise LLMConnectionError(
            f"LLM API connection failed: {type(error).__name__}: {error}"
        ) from error
    except OSError as error:
        raise LLMConnectionError(
            f"LLM API connection failed: {type(error).__name__}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise RuntimeError("LLM API returned a non-object JSON response.")
    return value


def stream_chat_completion(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: int,
    visible_delta_callback: VisibleDeltaCallback,
) -> dict[str, Any]:
    """Read a Chat Completions SSE response with the standard library."""
    request_headers = dict(headers)
    request_headers["Accept"] = "text/event-stream"
    request_body = dict(body)
    request_body["stream"] = True
    request = urllib.request.Request(
        url,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    accumulator = ChatCompletionAccumulator(visible_delta_callback)

    def consume_event(data_lines: list[str]) -> bool:
        if not data_lines:
            return False
        payload = "\n".join(data_lines).strip()
        data_lines.clear()
        if not payload:
            return False
        if payload == "[DONE]":
            return True
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError as error:
            raise RuntimeError("LLM stream returned invalid SSE JSON.") from error
        accumulator.ingest(chunk)
        return False

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data_lines: list[str] = []
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="strict").rstrip("\r\n")
                if not line:
                    if consume_event(data_lines):
                        break
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            else:
                consume_event(data_lines)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1500]
        raise LLMHTTPError(
            int(error.code),
            detail,
            retry_after_seconds=_numeric_retry_after(error.headers),
        ) from error
    except (socket.timeout, TimeoutError) as error:
        raise LLMTimeoutError(
            f"LLM API stream timed out: {type(error).__name__}: {error}"
        ) from error
    except urllib.error.URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            raise LLMTimeoutError(
                f"LLM API stream timed out: {type(error.reason).__name__}: "
                f"{error.reason}"
            ) from error
        raise LLMConnectionError(
            f"LLM API stream connection failed: {type(error).__name__}: {error}"
        ) from error
    except UnicodeDecodeError as error:
        raise RuntimeError("LLM stream is not valid UTF-8.") from error
    except OSError as error:
        raise LLMConnectionError(
            f"LLM API stream connection failed: {type(error).__name__}: {error}"
        ) from error
    return accumulator.message()


def _sdk_base_url(
    endpoint: ModelEndpoint,
    path: str,
    endpoint_suffix: str,
) -> str:
    """Derive the SDK base URL from an endpoint-specific API path."""
    base = str(endpoint.base_url).rstrip("/")
    normalized_path = path if path.startswith("/") else "/" + path

    if not normalized_path.endswith(endpoint_suffix):
        raise ValueError(
            f"Endpoint {endpoint.endpoint_id!r} path must end with "
            f"{endpoint_suffix!r} when using an SDK transport; "
            "use 'raw_http' for an arbitrary endpoint path."
        )

    prefix = normalized_path[: -len(endpoint_suffix)].rstrip("/")
    return base + prefix


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if not callable(dump):
        raise RuntimeError("Model SDK returned an unsupported response object.")
    rendered = dump(mode="json")
    if not isinstance(rendered, dict):
        raise RuntimeError("Model SDK returned a non-object response.")
    return rendered


class OpenAISDKTransport:
    """OpenAI-compatible transport backed by the official asynchronous SDK."""

    _CHAT_TYPED_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "model",
            "messages",
            "tools",
            "tool_choice",
            "response_format",
            "temperature",
            "reasoning_effort",
        }
    )
    _RESPONSES_TYPED_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"model", "instructions", "input", "text"}
    )

    def __init__(
        self,
        client_factory: Callable[..., Any] = AsyncOpenAI,
    ) -> None:
        self._client_factory = client_factory

    def _new_client(
        self,
        endpoint: ModelEndpoint,
        *,
        path: str,
        endpoint_suffix: str,
    ) -> Any:
        headers = _extra_headers(endpoint)
        key = resolve_api_key(endpoint)
        header_name = endpoint.api_key_header
        key_prefix = endpoint.api_key_prefix
        standard_bearer = (
            header_name.lower() == "authorization" and key_prefix == "Bearer "
        )
        if key is None:
            # AsyncOpenAI requires a non-empty client key.  A blank override keeps
            # genuinely unauthenticated local endpoints free of a bearer token.
            sdk_key = "iag-no-auth"
            _drop_header(headers, "Authorization")
            headers["Authorization"] = ""
        elif standard_bearer:
            sdk_key = key
            _drop_header(headers, "Authorization")
        else:
            sdk_key = "iag-custom-auth"
            _drop_header(headers, "Authorization")
            _drop_header(headers, header_name)
            headers["Authorization"] = ""
            headers[header_name] = key_prefix + key
        return self._client_factory(
            api_key=sdk_key,
            base_url=_sdk_base_url(
                endpoint,
                path,
                endpoint_suffix,
            ),
            timeout=endpoint.timeout_seconds,
            max_retries=endpoint.sdk_max_retries,
            default_headers=headers or None,
        )

    @staticmethod
    async def _close(client: Any) -> None:
        close = getattr(client, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result

    async def create_chat_completion(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        client = self._new_client(
            endpoint,
            path=endpoint.chat_completions_path,
            endpoint_suffix="/chat/completions",
        )
        typed = {
            key: value
            for key, value in body.items()
            if key in self._CHAT_TYPED_KEYS
        }
        extra = {
            key: value
            for key, value in body.items()
            if key not in self._CHAT_TYPED_KEYS
        }
        try:
            value = await client.chat.completions.create(
                **typed,
                extra_body=extra or None,
            )
            return _model_dump(value)
        finally:
            await self._close(client)


    async def create_chat_completion_stream(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
        visible_delta_callback: VisibleDeltaCallback,
    ) -> dict[str, Any]:
        client = self._new_client(
            endpoint,
            path=endpoint.chat_completions_path,
            endpoint_suffix="/chat/completions",
        )
        typed = {
            key: value
            for key, value in body.items()
            if key in self._CHAT_TYPED_KEYS
        }
        extra = {
            key: value
            for key, value in body.items()
            if key not in self._CHAT_TYPED_KEYS
        }
        accumulator = ChatCompletionAccumulator(visible_delta_callback)
        try:
            stream = await client.chat.completions.create(
                **typed,
                extra_body=extra or None,
                stream=True,
            )
            async for chunk in stream:
                accumulator.ingest(chunk)
            return accumulator.message()
        finally:
            await self._close(client)

    async def create_response(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        client = self._new_client(
            endpoint,
            path=endpoint.responses_path,
            endpoint_suffix="/responses",
        )
        typed = {
            key: value
            for key, value in body.items()
            if key in self._RESPONSES_TYPED_KEYS
        }
        extra = {
            key: value
            for key, value in body.items()
            if key not in self._RESPONSES_TYPED_KEYS
        }
        try:
            value = await client.responses.create(
                **typed,
                extra_body=extra or None,
            )
            return _model_dump(value)
        finally:
            await self._close(client)


class AnthropicSDKTransport:
    """Anthropic Messages transport backed by the official async SDK."""

    _TYPED_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "cache_control",
            "container",
            "inference_geo",
            "max_tokens",
            "messages",
            "metadata",
            "model",
            "output_config",
            "service_tier",
            "stop_sequences",
            "system",
            "thinking",
            "tool_choice",
            "tools",
            "user_profile_id",
            "workspace_id",
        }
    )

    def __init__(
        self,
        client_factory: Callable[..., Any] = AsyncAnthropic,
    ) -> None:
        self._client_factory = client_factory

    def _new_client(self, endpoint: ModelEndpoint) -> Any:
        key = resolve_api_key(endpoint)
        if key is None:
            raise ValueError(
                "anthropic_sdk requires an API key; use raw_http for a "
                "no-auth compatible endpoint"
            )
        return self._client_factory(
            api_key=key,
            base_url=_sdk_base_url(
                endpoint,
                endpoint.messages_path,
                "/v1/messages",
            ),
            timeout=endpoint.timeout_seconds,
            max_retries=endpoint.sdk_max_retries,
            default_headers=_extra_headers(endpoint) or None,
        )

    @staticmethod
    def _arguments(
        body: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        typed = {
            key: value
            for key, value in body.items()
            if key in AnthropicSDKTransport._TYPED_KEYS
        }
        extra = {
            key: value
            for key, value in body.items()
            if key not in AnthropicSDKTransport._TYPED_KEYS
        }
        return typed, extra

    async def create_anthropic_message(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        client = self._new_client(endpoint)
        typed, extra = self._arguments(body)
        try:
            value = await client.messages.create(
                **typed,
                extra_body=extra or None,
            )
            return _model_dump(value)
        finally:
            await OpenAISDKTransport._close(client)

    async def create_anthropic_message_stream(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
        visible_delta_callback: VisibleDeltaCallback,
    ) -> dict[str, Any]:
        client = self._new_client(endpoint)
        typed, extra = self._arguments(body)
        try:
            manager = client.messages.stream(
                **typed,
                extra_body=extra or None,
            )
            async with manager as stream:
                async for delta in stream.text_stream:
                    if delta:
                        visible_delta_callback(str(delta))
                value = await stream.get_final_message()
            return _model_dump(value)
        finally:
            await OpenAISDKTransport._close(client)


class RawHTTPTransport:
    """Compatibility transport preserving the previous exact HTTP behavior."""

    def __init__(
        self,
        post_function: Callable[
            [str, dict[str, str], dict[str, Any], int],
            dict[str, Any],
        ] = post_json,
    ) -> None:
        self._post_function = post_function

    async def _post(
        self,
        endpoint: ModelEndpoint,
        path: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._post_function,
            api_url(endpoint, path),
            api_headers(endpoint),
            body,
            endpoint.timeout_seconds,
        )

    async def create_chat_completion(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._post(
            endpoint,
            endpoint.chat_completions_path,
            body,
        )

    async def create_chat_completion_stream(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
        visible_delta_callback: VisibleDeltaCallback,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            stream_chat_completion,
            api_url(endpoint, endpoint.chat_completions_path),
            api_headers(endpoint),
            body,
            endpoint.timeout_seconds,
            visible_delta_callback,
        )

    async def create_response(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._post(
            endpoint,
            endpoint.responses_path,
            body,
        )

    async def create_anthropic_message(
        self,
        endpoint: ModelEndpoint,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._post(endpoint, endpoint.messages_path, body)


def transport_from_endpoint(endpoint: ModelEndpoint) -> LLMTransport:
    """Select a transport explicitly; never repeat a failed request implicitly."""
    if endpoint.model_transport == "openai_sdk":
        return OpenAISDKTransport()
    if endpoint.model_transport == "anthropic_sdk":
        return AnthropicSDKTransport()
    if endpoint.model_transport == "raw_http":
        return RawHTTPTransport()
    raise ValueError(f"Unsupported model_transport: {endpoint.model_transport}")
