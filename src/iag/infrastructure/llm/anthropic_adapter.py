"""Translate IAG's provider-neutral tool loop to Anthropic Messages."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from iag.infrastructure.llm.model_pool import ModelEndpoint


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return str(value or "")
    return "".join(
        str(block.get("text") or "")
        for block in value
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _tool_input(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    if not isinstance(value, str):
        raise TypeError("Tool arguments must be a JSON object.")
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("Tool arguments are invalid JSON.") from error
    if not isinstance(parsed, dict):
        raise TypeError("Tool arguments must decode to a JSON object.")
    return parsed


def _append_message(
    output: list[dict[str, Any]],
    role: str,
    blocks: list[dict[str, Any]],
) -> None:
    if not blocks:
        return
    if output and output[-1]["role"] == role:
        output[-1]["content"].extend(blocks)
        return
    output.append({"role": role, "content": blocks})


def anthropic_messages(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert OpenAI-style persisted history without losing tool pairing."""
    system_parts: list[str] = []
    output: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role == "system":
            text = _text_content(message.get("content"))
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "").strip()
            if not call_id:
                raise ValueError("Anthropic tool results require tool_call_id.")
            _append_message(
                output,
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": _text_content(message.get("content")),
                    }
                ],
            )
            continue
        if role not in {"user", "assistant"}:
            raise ValueError(f"Unsupported Anthropic message role: {role}")
        native_content = message.get("anthropic_content")
        if role == "assistant" and isinstance(native_content, list):
            if not all(isinstance(block, dict) for block in native_content):
                raise TypeError("Anthropic replay content must contain objects.")
            _append_message(output, role, deepcopy(native_content))
            continue
        blocks: list[dict[str, Any]] = []
        content = _text_content(message.get("content"))
        if content:
            blocks.append({"type": "text", "text": content})
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                call_id = str(call.get("id") or "").strip()
                name = str(function.get("name") or "").strip()
                if not call_id or not name:
                    raise ValueError("Anthropic tool calls require an ID and name.")
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": _tool_input(function.get("arguments", "{}")),
                    }
                )
        _append_message(output, role, blocks)
    system = "\n\n".join(system_parts) or None
    return system, output


def anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict):
            raise TypeError("Anthropic only accepts function tool schemas here.")
        name = str(function.get("name") or "").strip()
        schema = function.get("parameters")
        if not name or not isinstance(schema, dict):
            raise ValueError("Each Anthropic tool requires a name and JSON schema.")
        converted.append(
            {
                "name": name,
                "description": str(function.get("description") or ""),
                "input_schema": deepcopy(schema),
            }
        )
    return converted


def anthropic_message_body(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    request_options: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a native Messages API request from IAG's common conversation."""
    options = request_options or {}
    system, converted_messages = anthropic_messages(messages)
    body: dict[str, Any] = {
        "model": endpoint.model,
        "max_tokens": endpoint.max_output_tokens,
        "messages": converted_messages,
    }
    if system:
        body["system"] = system
    body.update(deepcopy(overrides or {}))
    thinking = options.get("thinking")
    if isinstance(thinking, dict):
        rendered_thinking = deepcopy(thinking)
        if (
            rendered_thinking.get("type") == "enabled"
            and "budget_tokens" not in rendered_thinking
        ):
            maximum = int(body["max_tokens"])
            if maximum <= 1024:
                raise ValueError(
                    "Anthropic extended thinking requires max_tokens above 1024."
                )
            rendered_thinking["budget_tokens"] = min(16_000, maximum - 1)
        body["thinking"] = rendered_thinking
    elif options.get("temperature") is not None and "temperature" not in body:
        body["temperature"] = float(options["temperature"])
    if tools:
        body["tools"] = anthropic_tools(tools)
        body["tool_choice"] = {"type": "auto"}
    return body


def anthropic_message_to_assistant(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize Anthropic content blocks for the existing IAG tool loop."""
    content = raw.get("content")
    if not isinstance(content, list):
        raise TypeError("Anthropic Messages API returned invalid content.")
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    replay_blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = str(block.get("text") or "")
            text_parts.append(text)
            replay_blocks.append({"type": "text", "text": text})
        elif block_type == "thinking":
            thinking = str(block.get("thinking") or "")
            signature = str(block.get("signature") or "")
            thinking_parts.append(thinking)
            replay = {"type": "thinking", "thinking": thinking}
            if signature:
                replay["signature"] = signature
            replay_blocks.append(replay)
        elif block_type == "redacted_thinking":
            replay_blocks.append(
                {
                    "type": "redacted_thinking",
                    "data": str(block.get("data") or ""),
                }
            )
        elif block_type == "tool_use":
            call_id = str(block.get("id") or "").strip()
            name = str(block.get("name") or "").strip()
            if not call_id or not name:
                raise RuntimeError("Anthropic returned an incomplete tool call.")
            arguments = block.get("input", {})
            if not isinstance(arguments, dict):
                raise RuntimeError("Anthropic tool input is not a JSON object.")
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
            )
            replay_blocks.append(
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": name,
                    "input": deepcopy(arguments),
                }
            )
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts),
    }
    if thinking_parts:
        message["reasoning_content"] = "".join(thinking_parts)
    if tool_calls:
        message["tool_calls"] = tool_calls
    if replay_blocks:
        message["anthropic_content"] = replay_blocks
    return message
