#!/usr/bin/env python3
"""Save-driven scheduling helpers for unattended IAG reviews."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_tools import game_month_index
from conversation_store import ConversationStore
from extract_game_state import load_save_metadata
from save_ingest import SaveIngestError, resolve_current_save


def save_identity(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "modified_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def coalesce_next_review_after_turn(
    config: dict[str, Any],
    store: ConversationStore,
    previous_next_review: Any,
) -> dict[str, Any]:
    """Skip review boundaries crossed while the preceding model turn was active."""
    next_review = store.get_state("next_review", None)
    if not isinstance(next_review, dict) or next_review == previous_next_review:
        return {"changed": False, "reason": "review_schedule_unchanged"}
    campaign_id = store.conversation_metadata().get("campaign_id")
    if not campaign_id:
        return {"changed": False, "reason": "campaign_unbound"}
    try:
        path = resolve_current_save(
            config,
            expected_campaign_id=str(campaign_id),
        )
        game_date = load_save_metadata(path).get("date")
    except (FileNotFoundError, PermissionError, SaveIngestError):
        return {"changed": False, "reason": "save_unavailable"}

    current_index = game_month_index(game_date)
    try:
        due_index = int(next_review.get("due_month_index"))
        interval = int(next_review.get("next_review_months"))
    except (TypeError, ValueError):
        return {"changed": False, "reason": "schedule_incomplete"}
    if current_index is None or interval <= 0 or due_index > current_index:
        return {
            "changed": False,
            "reason": "next_boundary_still_future",
            "current_month_index": current_index,
            "due_month_index": due_index,
        }

    original_due = due_index
    skipped = 0
    while due_index <= current_index:
        due_index += interval
        skipped += 1
    next_review.update(
        {
            "due_month_index": due_index,
            "coalesced_at": datetime.now(timezone.utc)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "coalesced_through_game_date": game_date,
            "coalesced_from_due_month_index": original_due,
            "coalesced_missed_intervals": skipped,
        }
    )
    store.set_state("next_review", next_review)
    return {
        "changed": True,
        "reason": "missed_boundaries_coalesced",
        "game_date": game_date,
        "current_month_index": current_index,
        "previous_due_month_index": original_due,
        "due_month_index": due_index,
        "skipped_intervals": skipped,
    }


def autonomy_probe(
    config: dict[str, Any],
    store: ConversationStore,
) -> dict[str, Any]:
    mode = str(
        store.get_state("autonomy_mode", config.get("autonomy_mode", "paused"))
    )
    result: dict[str, Any] = {
        "enabled": mode in {"advisory", "execute"},
        "mode": mode,
        "due": False,
        "reason": "paused",
        "save": None,
        "game_date": None,
    }
    if not result["enabled"]:
        return result
    campaign_id = store.conversation_metadata().get("campaign_id")
    if not campaign_id:
        result["reason"] = "campaign_unbound"
        return result
    try:
        path = resolve_current_save(
            config,
            expected_campaign_id=str(campaign_id),
        )
    except (FileNotFoundError, PermissionError, SaveIngestError) as error:
        result["reason"] = f"save_unavailable:{type(error).__name__}"
        return result

    identity = save_identity(path)
    result["save"] = identity
    if identity == store.get_state("last_autonomy_source", None):
        result["reason"] = "already_reviewed_save"
        return result

    maximum_age = int(config.get("autonomy_require_fresh_save_seconds", 900))
    age = max(time.time() - path.stat().st_mtime, 0.0)
    if maximum_age > 0 and age > maximum_age:
        result["reason"] = "save_too_old"
        result["save_age_seconds"] = age
        return result

    metadata = load_save_metadata(path)
    game_date = metadata.get("date")
    result["game_date"] = game_date
    current_index = game_month_index(game_date)
    next_review = store.get_state("next_review", {}) or {}
    due_index = next_review.get("due_month_index")
    if due_index is not None and current_index is not None:
        if current_index < int(due_index):
            result["reason"] = "waiting_for_game_month"
            result["due_month_index"] = int(due_index)
            result["current_month_index"] = current_index
            return result

    result["due"] = True
    result["reason"] = "new_due_save"
    return result
