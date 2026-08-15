#!/usr/bin/env python3
"""Reconstruct one OpenAI-compatible assistant message from stream chunks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

VisibleDeltaCallback = Callable[[str], None]


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        rendered = dump(mode="json")
        if isinstance(rendered, dict):
            return rendered
    return {}


def _text_fragment(value: Any) -> str:
    """Accept the string form plus the typed text fragments used by some relays."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
            continue
        mapped = _mapping(item)
        text = mapped.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


class ChatCompletionAccumulator:
    """Aggregate content, private reasoning, and fragmented function calls.

    Only ordinary assistant content is forwarded to ``visible_delta_callback``.
    Provider reasoning remains available in the final protocol message but is
    deliberately kept out of the game overlay event stream.
    """

    def __init__(
        self,
        visible_delta_callback: VisibleDeltaCallback | None = None,
    ) -> None:
        self._visible_delta_callback = visible_delta_callback
        self._content: list[str] = []
        self._reasoning: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None

    @property
    def emitted_visible_content(self) -> bool:
        return bool(self._content)

    def ingest(self, chunk: Any) -> None:
        value = _mapping(chunk)
        choices = value.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        choice = _mapping(choices[0])
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None:
            self.finish_reason = str(finish_reason)
        delta = _mapping(choice.get("delta"))
        if not delta:
            return

        content = _text_fragment(delta.get("content"))
        if content:
            self._content.append(content)
            if self._visible_delta_callback is not None:
                self._visible_delta_callback(content)

        reasoning = _text_fragment(
            delta.get("reasoning_content", delta.get("reasoning"))
        )
        if reasoning:
            self._reasoning.append(reasoning)

        raw_calls = delta.get("tool_calls")
        if not isinstance(raw_calls, list):
            return
        for fallback_index, raw_call in enumerate(raw_calls):
            call = _mapping(raw_call)
            raw_index = call.get("index", fallback_index)
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                index = fallback_index
            aggregate = self._tool_calls.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            if call.get("id"):
                aggregate["id"] += str(call["id"])
            if call.get("type"):
                aggregate["type"] = str(call["type"])
            function = _mapping(call.get("function"))
            if function.get("name"):
                aggregate["function"]["name"] += str(function["name"])
            arguments = function.get("arguments")
            if arguments is not None:
                aggregate["function"]["arguments"] += str(arguments)

    def message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(self._content),
        }
        if self._reasoning:
            message["reasoning_content"] = "".join(self._reasoning)
        if self._tool_calls:
            message["tool_calls"] = [
                self._tool_calls[index] for index in sorted(self._tool_calls)
            ]
        return message
