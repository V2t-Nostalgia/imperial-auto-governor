#!/usr/bin/env python3
"""Token-budgeted conversation replay with loss-aware rolling summaries."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable

from iag.core.conversation_store import ConversationStore, now_iso
from iag.infrastructure.llm.model_pool import ModelEndpoint

CJK_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
)


def estimate_text_tokens(value: Any) -> int:
    """Conservative tokenizer-independent estimate for mixed Chinese/JSON text."""
    text = str(value or "")
    if not text:
        return 0
    cjk = len(CJK_RE.findall(text))
    remainder = CJK_RE.sub("", text)
    non_space = sum(1 for char in remainder if not char.isspace())
    whitespace = len(remainder) - non_space
    return max(1, cjk + math.ceil(non_space / 3.5) + math.ceil(whitespace / 12))


def estimate_message_tokens(message: dict[str, Any]) -> int:
    return 6 + estimate_text_tokens(
        json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    )


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(item) for item in messages)


def _without_reasoning(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        copy = dict(message)
        copy.pop("reasoning_content", None)
        result.append(copy)
    return result


def _truncate_to_tokens(text: str, maximum_tokens: int) -> str:
    if estimate_text_tokens(text) <= maximum_tokens:
        return text.strip()
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_text_tokens(text[:middle]) <= maximum_tokens:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "\n[摘要因本地预算截断]"


def _summary_prompt(
    existing_summary: str,
    messages: list[dict[str, Any]],
    target_tokens: int,
) -> list[dict[str, Any]]:
    payload = json.dumps(
        _without_reasoning(messages),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return [
        {
            "role": "system",
            "content": (
                "你是持久战役会话的事实压缩器。输出中文概况，不执行工具，不给新建议。"
                "保留玩家明确要求及其撤销、殖民地分工、长期目标、已确认动作、待存档核验"
                "动作、失败原因、未决问题、关键日期和仍有用的对象标识。删除寒暄、重复表格"
                "和已经被后续消息推翻的临时判断。不得把计划或暂定执行写成已完成，不得编造。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"请把下面内容压缩为不超过约 {target_tokens} tokens 的概况版上文。\n\n"
                "已有滚动摘要：\n"
                + (existing_summary or "（无）")
                + "\n\n新增历史：\n"
                + payload
            ),
        },
    ]


def build_context_messages(
    store: ConversationStore,
    config: dict[str, Any],
    *,
    endpoint: ModelEndpoint,
    request_options: dict[str, Any],
    system_prompt: str,
    tool_schemas: list[dict[str, Any]],
    completion_fn: Callable[..., dict[str, Any]],
    application_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build one bounded model context, compacting only the replayed copy."""
    maximum = endpoint.model_context_window_tokens
    reserve = max(512, min(endpoint.max_output_tokens, maximum // 2))
    usable = maximum - reserve
    trigger_percent = max(
        30.0,
        min(float(config.get("context_compression_trigger_percent", 80)), 98.0),
    )
    target_percent = max(
        10.0,
        min(float(config.get("context_compression_target_percent", 35)), trigger_percent - 5),
    )
    enabled = bool(config.get("context_compression_enabled", True))
    minimum_recent = max(2, min(int(config.get("context_compression_min_recent_segments", 8)), 64))
    summary_key = (
        f"context_summary:{application_id}"
        if application_id
        else "context_summary"
    )
    summary_state = store.get_state(summary_key, {})
    if not isinstance(summary_state, dict):
        summary_state = {}
    through_id = int(summary_state.get("through_message_id") or 0)
    summary_text = str(summary_state.get("content") or "").strip()
    segments = store.protocol_segments(
        after_id=through_id,
        application_id=application_id,
    )
    system_tokens = estimate_text_tokens(system_prompt) + estimate_text_tokens(tool_schemas) + 24

    def segment_tokens(segment: dict[str, Any]) -> int:
        return estimate_messages_tokens(segment["messages"])

    history_tokens = sum(segment_tokens(item) for item in segments)
    summary_tokens = estimate_text_tokens(summary_text)
    input_tokens = system_tokens + summary_tokens + history_tokens
    utilization = input_tokens / max(usable, 1) * 100
    compression_error: str | None = None
    compacted_now = False

    if enabled and utilization >= trigger_percent and len(segments) > minimum_recent:
        target_total = max(int(maximum * target_percent / 100), 2_048)
        recent_budget = max(int(target_total * 0.55) - system_tokens, 512)
        recent: list[dict[str, Any]] = []
        recent_tokens = 0
        for segment in reversed(segments):
            cost = segment_tokens(segment)
            if len(recent) >= minimum_recent and recent_tokens + cost > recent_budget:
                break
            recent.append(segment)
            recent_tokens += cost
        recent.reverse()
        prefix_count = len(segments) - len(recent)
        if prefix_count > 0:
            prefix = segments[:prefix_count]
            prefix_messages = [
                message
                for segment in prefix
                for message in segment["messages"]
            ]
            summary_budget = max(
                384,
                min(target_total - system_tokens - recent_tokens, int(maximum * 0.3)),
            )
            try:
                summary_options = dict(request_options)
                overrides = dict(
                    summary_options.get("request_body_overrides", {})
                )
                overrides.setdefault("max_tokens", summary_budget)
                summary_options["request_body_overrides"] = overrides
                response = completion_fn(
                    endpoint,
                    _summary_prompt(summary_text, prefix_messages, summary_budget),
                    request_options=summary_options,
                    tools=None,
                )
                generated = str(response.get("content") or "").strip()
                if not generated:
                    raise RuntimeError("摘要模型没有返回正文。")
                summary_text = _truncate_to_tokens(generated, summary_budget)
                through_id = int(prefix[-1]["last_id"])
                summary_state = {
                    "schema": "iag.context_summary.v1",
                    "through_message_id": through_id,
                    "content": summary_text,
                    "generated_at": now_iso(),
                    "source_segment_count": len(prefix),
                    "estimated_summary_tokens": estimate_text_tokens(summary_text),
                    "target_percent": target_percent,
                }
                store.set_state(summary_key, summary_state)
                segments = recent
                compacted_now = True
            except Exception as error:  # A failed summary must not erase history.
                compression_error = f"{type(error).__name__}: {error}"

    summary_message = (
        {
            "role": "system",
            "content": (
                "# 概况版较早会话\n\n"
                + summary_text
                + "\n\n以上是本地保存的滚动摘要；当前战略状态板和较新的原始消息优先。"
            ),
        }
        if summary_text
        else None
    )
    base_tokens = system_tokens + (
        estimate_message_tokens(summary_message) if summary_message else 0
    )
    selected: list[dict[str, Any]] = []
    selected_tokens = 0
    for segment in reversed(segments):
        cost = segment_tokens(segment)
        if selected and base_tokens + selected_tokens + cost > usable:
            break
        if not selected and base_tokens + cost > usable:
            continue
        selected.append(segment)
        selected_tokens += cost
    selected.reverse()
    omitted_segments = len(segments) - len(selected)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if summary_message:
        messages.append(summary_message)
    if omitted_segments:
        messages.append(
            {
                "role": "system",
                "content": (
                    f"本次上下文仍因硬预算省略了 {omitted_segments} 个较早协议组。"
                    "不要猜测其中内容；可要求玩家把必须长期保留的要求写入十年计划。"
                ),
            }
        )
    for segment in selected:
        messages.extend(segment["messages"])

    final_estimate = estimate_messages_tokens(messages) + estimate_text_tokens(tool_schemas)
    stats = {
        "schema": "iag.context_window.v1",
        "maximum_tokens": maximum,
        "output_reserve_tokens": reserve,
        "usable_input_tokens": usable,
        "estimated_input_tokens_before": input_tokens,
        "estimated_input_tokens_after": final_estimate,
        "utilization_percent_before": round(utilization, 2),
        "compression_enabled": enabled,
        "compression_trigger_percent": trigger_percent,
        "compression_target_percent": target_percent,
        "compacted_now": compacted_now,
        "summary_through_message_id": through_id or None,
        "summary_estimated_tokens": estimate_text_tokens(summary_text),
        "included_segments": len(selected),
        "omitted_segments": omitted_segments,
        # Keep the pre-token-budget API fields for older frontends and tests.
        "stored_messages": sum(len(item["messages"]) for item in segments),
        "included_messages": sum(len(item["messages"]) for item in selected),
        "omitted_messages": sum(
            len(item["messages"]) for item in segments[:omitted_segments]
        ),
        "compression_error": compression_error,
    }
    return messages, stats
