#!/usr/bin/env python3
"""Provider-neutral JSON LLM client used by the IAG planner."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from planner import SYSTEM_PROMPT


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
}


def request_body_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """Return JSON-safe provider parameters without exposing protocol fields."""
    value = config.get("request_body_overrides", {})
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


def api_headers(config: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    headers.update(
        {
            str(key): str(value)
            for key, value in config.get("extra_headers", {}).items()
        }
    )
    auth_mode = str(config.get("auth_mode", "bearer"))
    if auth_mode == "none":
        return headers
    if auth_mode != "bearer":
        raise ValueError(f"Unsupported auth_mode: {auth_mode}")
    env_name = str(config.get("api_key_env", "IAG_LLM_API_KEY"))
    api_key = os.environ.get(env_name)
    key_file = config.get("api_key_file")
    if not api_key and key_file:
        api_key = Path(str(key_file)).expanduser().read_text(
            encoding="utf-8"
        ).strip()
    if not api_key:
        raise RuntimeError(f"Neither {env_name} nor api_key_file provides an API key.")
    headers[str(config.get("api_key_header", "Authorization"))] = (
        str(config.get("api_key_prefix", "Bearer ")) + api_key
    )
    return headers


def api_url(config: dict[str, Any], path_key: str, default: str) -> str:
    base = str(config["base_url"]).rstrip("/")
    path = str(config.get(path_key, default))
    return base + (path if path.startswith("/") else "/" + path)


def post_json(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
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
        raise RuntimeError(f"LLM API HTTP {error.code}: {detail}") from error
    if not isinstance(value, dict):
        raise RuntimeError("LLM API returned a non-object JSON response.")
    return value


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
    config: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one OpenAI-compatible chat request without leaking provider quirks."""
    body: dict[str, Any] = {
        "model": str(config["model"]),
        "messages": messages,
    }
    overrides = request_body_overrides(config)
    body.update(overrides)
    thinking = config.get("thinking")
    thinking_enabled = (
        isinstance(thinking, dict)
        and str(thinking.get("type", "")).lower() == "enabled"
    )
    if isinstance(thinking, dict):
        body["thinking"] = thinking
    if config.get("reasoning_effort"):
        body["reasoning_effort"] = str(config["reasoning_effort"])
    if (
        not thinking_enabled
        and "temperature" not in overrides
        and config.get("temperature") is not None
    ):
        body["temperature"] = float(config["temperature"])
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if response_format:
        body["response_format"] = response_format
    return body


def chat_completion_message(
    config: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the raw assistant message, including DeepSeek reasoning/tool fields."""
    raw = post_json(
        api_url(config, "chat_completions_path", "/chat/completions"),
        api_headers(config),
        chat_completion_body(config, messages, tools=tools),
        int(config.get("timeout_seconds", 120)),
    )
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Chat Completions API returned no choices.")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("Chat Completions API returned no assistant message.")
    return message


def call_model(    config: dict[str, Any],
    decision_request: dict[str, Any],
) -> dict[str, Any]:
    provider = str(config.get("provider", "responses_compatible"))
    model = str(config["model"])
    timeout = int(config.get("timeout_seconds", 120))
    user_text = json.dumps(
        decision_request,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    if provider == "responses_compatible":
        body = {
            "model": model,
            "instructions": SYSTEM_PROMPT,
            "input": user_text,
            "text": {"format": {"type": "json_object"}},
        }
        body.update(request_body_overrides(config))
        raw = post_json(
            api_url(config, "responses_path", "/responses"),
            api_headers(config),
            body,
            timeout,
        )
        output_text = response_output_text(raw)
        if not output_text:
            raise RuntimeError("Responses-compatible API returned no text.")
        return parse_model_json(output_text)

    if provider == "chat_completions_compatible":
        body = chat_completion_body(
            config,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            response_format={"type": "json_object"},
        )
        raw = post_json(
            api_url(config, "chat_completions_path", "/chat/completions"),
            api_headers(config),
            body,
            timeout,
        )
        return parse_model_json(
            str(raw["choices"][0]["message"]["content"])
        )

    raise ValueError(f"Unsupported provider: {provider}")
