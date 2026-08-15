#!/usr/bin/env python3
"""Persistent ten-year planning and player-controlled emergency state."""

from __future__ import annotations

import re
import uuid
from typing import Any

from iag.core.conversation_store import ConversationStore, now_iso


DECADE_STATE_KEY = "decade_planning"
EMERGENCY_STATE_KEY = "strategic_emergency"
GAME_DATE_RE = re.compile(r"^(\d{1,6})\.(\d{1,2})\.(\d{1,2})$")


def game_date_parts(game_date: Any) -> tuple[int, int, int] | None:
    match = GAME_DATE_RE.fullmatch(str(game_date or ""))
    if not match:
        return None
    year, month, day = (int(value) for value in match.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return year, month, day


def game_month_index(game_date: Any) -> int | None:
    parts = game_date_parts(game_date)
    if parts is None:
        return None
    return parts[0] * 12 + parts[1] - 1


def _empty_decade_state(renewal_lead_months: int) -> dict[str, Any]:
    return {
        "schema": "iag.decade_planning.v1",
        "current": None,
        "next_draft": None,
        "renewal_lead_months": renewal_lead_months,
        "renewal_open": False,
        "status": "awaiting_first_plan",
        "last_game_date": None,
        "updated_at": now_iso(),
    }


def _empty_emergency_state() -> dict[str, Any]:
    return {
        "schema": "iag.strategic_emergency.v1",
        "active": False,
        "title": "",
        "directive": "",
        "activated_at": None,
        "activated_game_date": None,
        "ended_at": None,
        "ended_game_date": None,
    }


def _plan_record(
    text: str,
    *,
    start_year: int,
    created_at: str | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    return {
        "plan_id": plan_id or f"decade_{start_year}_{uuid.uuid4().hex[:8]}",
        "start_year": start_year,
        "end_year": start_year + 9,
        "text": text.strip(),
        "created_at": created_at or now_iso(),
        "updated_at": now_iso(),
    }


def synchronize_decade_state(
    store: ConversationStore,
    game_date: str | None,
    *,
    renewal_lead_months: int = 12,
) -> dict[str, Any]:
    """Advance plan periods from authoritative save time, never wall-clock time."""
    lead = max(1, min(int(renewal_lead_months), 36))
    raw = store.get_state(DECADE_STATE_KEY, None)
    state = dict(raw) if isinstance(raw, dict) else _empty_decade_state(lead)
    state.setdefault("schema", "iag.decade_planning.v1")
    state.setdefault("current", None)
    state.setdefault("next_draft", None)
    state["renewal_lead_months"] = lead
    state["last_game_date"] = game_date

    current = state.get("current") if isinstance(state.get("current"), dict) else None
    next_draft = (
        state.get("next_draft")
        if isinstance(state.get("next_draft"), dict)
        else None
    )
    current_index = game_month_index(game_date)
    if current is None:
        state["status"] = "awaiting_first_plan"
        state["renewal_open"] = False
    elif current_index is None:
        state["status"] = "active_date_unknown"
        state["renewal_open"] = False
    else:
        end_index = int(current["end_year"]) * 12 + 11
        if current_index > end_index and next_draft is not None:
            promoted = _plan_record(
                str(next_draft.get("text") or ""),
                start_year=int(current["end_year"]) + 1,
                created_at=str(next_draft.get("created_at") or now_iso()),
                plan_id=str(next_draft.get("plan_id") or "") or None,
            )
            promoted["activated_at"] = now_iso()
            state["current"] = promoted
            state["next_draft"] = None
            current = promoted
            end_index = int(current["end_year"]) * 12 + 11
        if current_index > end_index:
            state["status"] = "expired_waiting_next_plan"
            state["renewal_open"] = True
        else:
            state["renewal_open"] = current_index >= end_index - lead + 1
            state["status"] = (
                "renewal_open" if state["renewal_open"] else "active"
            )
    state["updated_at"] = now_iso()
    store.set_state(DECADE_STATE_KEY, state)
    return state


def save_decade_plan(
    store: ConversationStore,
    *,
    text: str,
    game_date: str | None,
    target: str,
    renewal_lead_months: int = 12,
) -> dict[str, Any]:
    content = str(text).strip()
    if not 1 <= len(content) <= 12_000:
        raise ValueError("十年计划必须包含 1 到 12000 个字符。")
    parts = game_date_parts(game_date)
    if parts is None:
        raise ValueError("当前绑定存档没有可识别的游戏日期。")
    state = synchronize_decade_state(
        store,
        game_date,
        renewal_lead_months=renewal_lead_months,
    )
    if target == "current":
        current = state.get("current")
        if isinstance(current, dict):
            state["current"] = _plan_record(
                content,
                start_year=int(current["start_year"]),
                created_at=str(current.get("created_at") or now_iso()),
                plan_id=str(current.get("plan_id") or "") or None,
            )
        else:
            state["current"] = _plan_record(content, start_year=parts[0])
    elif target == "next":
        if not bool(state.get("renewal_open")):
            raise ValueError("下一份十年计划尚未进入开放编写期。")
        current = state.get("current")
        start_year = (
            int(current["end_year"]) + 1
            if isinstance(current, dict)
            else parts[0]
        )
        previous = state.get("next_draft")
        state["next_draft"] = _plan_record(
            content,
            start_year=start_year,
            created_at=(
                str(previous.get("created_at") or now_iso())
                if isinstance(previous, dict)
                else None
            ),
            plan_id=(
                str(previous.get("plan_id") or "") or None
                if isinstance(previous, dict)
                else None
            ),
        )
    else:
        raise ValueError("十年计划目标必须是 current 或 next。")
    store.set_state(DECADE_STATE_KEY, state)
    return synchronize_decade_state(
        store,
        game_date,
        renewal_lead_months=renewal_lead_months,
    )


def emergency_state(store: ConversationStore) -> dict[str, Any]:
    value = store.get_state(EMERGENCY_STATE_KEY, None)
    return dict(value) if isinstance(value, dict) else _empty_emergency_state()


def activate_emergency(
    store: ConversationStore,
    *,
    title: str,
    directive: str,
    game_date: str | None,
) -> dict[str, Any]:
    selected_title = str(title).strip()
    selected_directive = str(directive).strip()
    if not 1 <= len(selected_title) <= 120:
        raise ValueError("紧急事件标题必须包含 1 到 120 个字符。")
    if not 1 <= len(selected_directive) <= 8_000:
        raise ValueError("紧急事件指令必须包含 1 到 8000 个字符。")
    value = {
        "schema": "iag.strategic_emergency.v1",
        "active": True,
        "title": selected_title,
        "directive": selected_directive,
        "activated_at": now_iso(),
        "activated_game_date": game_date,
        "ended_at": None,
        "ended_game_date": None,
    }
    store.set_state(EMERGENCY_STATE_KEY, value)
    return value


def end_emergency(
    store: ConversationStore,
    *,
    game_date: str | None,
) -> dict[str, Any]:
    value = emergency_state(store)
    value["active"] = False
    value["ended_at"] = now_iso()
    value["ended_game_date"] = game_date
    store.set_state(EMERGENCY_STATE_KEY, value)
    return value


def public_strategy_state(
    store: ConversationStore,
    game_date: str | None,
    *,
    renewal_lead_months: int = 12,
) -> dict[str, Any]:
    decade = synchronize_decade_state(
        store,
        game_date,
        renewal_lead_months=renewal_lead_months,
    )
    emergency = emergency_state(store)
    return {
        "schema": "iag.campaign_strategy.v1",
        "game_date": game_date,
        "decade": decade,
        "emergency": emergency,
        "decade_suspended": bool(emergency.get("active")),
    }


def render_strategy_context(value: dict[str, Any]) -> str:
    decade = value.get("decade", {})
    emergency = value.get("emergency", {})
    current = decade.get("current")
    next_draft = decade.get("next_draft")
    lines = [
        "# 战役战略状态板",
        "",
        f"当前已知游戏日期：{value.get('game_date') or '未知'}",
    ]
    if emergency.get("active"):
        lines.extend(
            [
                "",
                "## 紧急事件状态：已启用",
                f"事件：{emergency.get('title') or '未命名紧急事件'}",
                str(emergency.get("directive") or ""),
                "",
                "紧急事件由玩家显式启用，优先级高于十年计划。十年计划当前暂停执行，",
                "但不会被删除；只有玩家在前端结束紧急状态后才恢复。不要自行宣布紧急状态结束。",
            ]
        )
    else:
        lines.extend(["", "## 紧急事件状态：未启用"])
    lines.extend(["", "## 当前十年计划"])
    if isinstance(current, dict):
        lines.append(f"周期：{current['start_year']} - {current['end_year']}")
        lines.append(f"状态：{decade.get('status')}")
        lines.append(str(current.get("text") or ""))
    else:
        lines.append("尚未建立。当前回合只能按玩家会话要求和帝国长期增长原则行事。")
    if decade.get("renewal_open"):
        lines.extend(["", "## 下一十年计划编写窗口：已开放"])
        if isinstance(next_draft, dict):
            lines.append(f"草案周期：{next_draft['start_year']} - {next_draft['end_year']}")
            lines.append(str(next_draft.get("text") or ""))
        else:
            lines.append("玩家尚未保存下一周期草案。")
    lines.extend(
        [
            "",
            "本状态板由本地系统按绑定存档日期维护，每次模型开始工作前都必须重新阅读。",
            "不得用较早会话摘要覆盖这里的当前计划或紧急状态。",
        ]
    )
    return "\n".join(lines).strip()
