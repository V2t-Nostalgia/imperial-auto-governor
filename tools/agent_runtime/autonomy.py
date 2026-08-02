#!/usr/bin/env python3
"""Save-driven scheduling helpers for unattended IAG reviews."""

from __future__ import annotations

import time
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
