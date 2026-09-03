"""Deterministic continuations triggered by newly synchronized saves.

The model records a durable intent once.  A continuation handler may then
resolve save-assigned identifiers and advance exactly one mutating protocol
phase without opening another model turn.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iag.applications.fleet_operations.agent_tools import (
    PENDING_NEW_FLEET_KEY,
    FleetToolbox,
    sha256_file,
)
from iag.core.conversation_store import ConversationStore
from iag.stellaris.state.extract_game_state import load_save_metadata
from iag.stellaris.state.save_ingest import (
    SaveIngestError,
    read_campaign_manifest,
    resolve_current_save,
)

ATTEMPTS_STATE_KEY = "save_continuation_attempts"
LAST_RESULT_STATE_KEY = "last_save_continuation"


@dataclass(frozen=True)
class SaveContinuationHandler:
    """One built-in, deterministic save-dependent state machine."""

    name: str
    probe: Callable[[dict[str, Any], ConversationStore], dict[str, Any]]
    run: Callable[[dict[str, Any], ConversationStore], dict[str, Any]]


def _current_save_reference(
    config: dict[str, Any],
    store: ConversationStore,
) -> dict[str, Any]:
    campaign_id = store.conversation_metadata().get("campaign_id")
    if not campaign_id:
        raise ValueError("当前战役会话尚未绑定房主存档。")
    path = resolve_current_save(
        config,
        expected_campaign_id=str(campaign_id),
    )
    manifest = read_campaign_manifest(config, str(campaign_id)) or {}
    manifest_path = str(manifest.get("stored_path") or "")
    manifest_sha256 = str(manifest.get("sha256") or "")
    if (
        manifest_sha256
        and manifest_path
        and Path(manifest_path).resolve() == path.resolve()
    ):
        digest = manifest_sha256
        game_date = (manifest.get("metadata") or {}).get("date")
    else:
        digest = sha256_file(path)
        game_date = load_save_metadata(path).get("date")
    return {
        "path": str(path.resolve()),
        "sha256": digest,
        "game_date": game_date,
    }


def _new_fleet_probe(
    config: dict[str, Any],
    store: ConversationStore,
) -> dict[str, Any]:
    raw = store.get_state(PENDING_NEW_FLEET_KEY)
    if not isinstance(raw, dict):
        return {
            "handler": "new_fleet_creation",
            "pending": False,
            "due": False,
            "blocks_autonomy": False,
            "reason": "no_pending_workflow",
        }

    creation_run_id = str(raw.get("creation_run_id") or "unknown")
    continuation_id = f"new_fleet_creation:{creation_run_id}"
    result: dict[str, Any] = {
        "handler": "new_fleet_creation",
        "continuation_id": continuation_id,
        "pending": True,
        "due": False,
        "blocks_autonomy": False,
        "phase": raw.get("phase"),
        "reason": "not_ready",
    }
    try:
        source = _current_save_reference(config, store)
    except (
        FileNotFoundError,
        PermissionError,
        SaveIngestError,
        TypeError,
        ValueError,
    ) as error:
        result["reason"] = f"save_unavailable:{type(error).__name__}"
        result["error"] = str(error)
        return result
    result["save"] = source

    current_hash = str(source["sha256"])
    last_action_hash = str(raw.get("last_action_save_sha256") or "")
    if current_hash == last_action_hash:
        result["reason"] = "waiting_for_fresh_save"
        result["blocks_autonomy"] = True
        return result

    attempts = store.get_state(ATTEMPTS_STATE_KEY, {})
    attempts = attempts if isinstance(attempts, dict) else {}
    if str(attempts.get(continuation_id) or "") == current_hash:
        result["reason"] = "already_attempted_for_save"
        return result

    if str(raw.get("phase") or "") not in {
        "awaiting_template_save",
        "configuration_partial",
        "awaiting_fleet_save",
    }:
        result["reason"] = "unsupported_workflow_phase"
        return result
    if str(store.get_state("autonomy_mode", "paused")) != "execute":
        result["reason"] = "execution_not_authorized"
        return result
    if str(config.get("execution_mode", "carrier_click")) != "session_proxy":
        result["reason"] = "session_proxy_not_selected"
        return result
    if not bool(config.get("experimental_new_fleet_tools_enabled", False)):
        result["reason"] = "new_fleet_tools_disabled"
        return result
    runtime_root = Path(str(config["runtime_root"])).expanduser()
    if (runtime_root / "state" / "emergency_stop").is_file():
        result["reason"] = "emergency_stop_active"
        result["blocks_autonomy"] = True
        return result

    result["due"] = True
    result["blocks_autonomy"] = True
    result["reason"] = "fresh_save_ready"
    return result


def _run_new_fleet(
    config: dict[str, Any],
    store: ConversationStore,
) -> dict[str, Any]:
    return FleetToolbox(
        config,
        store,
        allow_execute=True,
    ).continue_pending_new_fleet_from_save()


BUILTIN_HANDLERS = (
    SaveContinuationHandler(
        name="new_fleet_creation",
        probe=_new_fleet_probe,
        run=_run_new_fleet,
    ),
)


def probe_save_continuations(
    config: dict[str, Any],
    store: ConversationStore,
    *,
    handlers: tuple[SaveContinuationHandler, ...] = BUILTIN_HANDLERS,
) -> dict[str, Any]:
    """Return the first ready continuation and whether an LLM turn must wait."""
    probes = [handler.probe(config, store) for handler in handlers]
    ready = next((item for item in probes if item.get("due")), None)
    return {
        "schema": "iag.save_continuation_probe.v1",
        "due": ready is not None,
        "blocks_autonomy": any(
            bool(item.get("blocks_autonomy")) for item in probes
        ),
        "ready": ready,
        "handlers": probes,
    }


def run_save_continuations(
    config: dict[str, Any],
    store: ConversationStore,
    *,
    handlers: tuple[SaveContinuationHandler, ...] = BUILTIN_HANDLERS,
) -> dict[str, Any]:
    """Advance at most one game-mutating continuation for the current save."""
    probe = probe_save_continuations(config, store, handlers=handlers)
    ready = probe.get("ready")
    if not isinstance(ready, dict):
        result = {
            "schema": "iag.save_continuation_run.v1",
            "state": "idle",
            "mutated_game": False,
            "probe": probe,
        }
        store.set_state(LAST_RESULT_STATE_KEY, result)
        return result

    handler_name = str(ready["handler"])
    handler = next(item for item in handlers if item.name == handler_name)
    continuation_id = str(ready["continuation_id"])
    source = dict(ready["save"])
    attempts = store.get_state(ATTEMPTS_STATE_KEY, {})
    attempts = dict(attempts) if isinstance(attempts, dict) else {}
    attempts[continuation_id] = str(source["sha256"])
    store.set_state(ATTEMPTS_STATE_KEY, attempts)

    try:
        outcome = handler.run(config, store)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        outcome = {
            "schema": "iag.save_continuation.error.v1",
            "state": "failed",
            "mutated_game": False,
            "error": f"{type(error).__name__}: {error}",
        }
    result = {
        "schema": "iag.save_continuation_run.v1",
        "state": str(outcome.get("state") or "unknown"),
        "handler": handler_name,
        "continuation_id": continuation_id,
        "source_save": source,
        "mutated_game": bool(outcome.get("mutated_game", False)),
        "outcome": outcome,
    }
    store.set_state(LAST_RESULT_STATE_KEY, result)
    return result


def continuation_public_summary(result: dict[str, Any]) -> str:
    """Render a concise audit line without asking the model to restate it."""
    state = str(result.get("state") or "unknown")
    outcome = result.get("outcome")
    outcome = outcome if isinstance(outcome, dict) else {}
    if state == "executed":
        return (
            "新存档已唯一解析新舰队模板，固定续接器已写入初始编制并"
            "请求增援；等待下一份存档确认实体舰队。"
        )
    if state == "partially_executed":
        return (
            "固定续接器只完成了部分新舰队协议步骤，已停止；"
            "下一份存档将按权威状态继续。"
        )
    if state == "confirmed_in_save":
        return (
            f"新存档已确认新舰队成立（舰队 ID "
            f"{outcome.get('fleet_id') or '待分配'}）。"
        )
    if state == "needs_review":
        return "存档续接无法唯一解析目标，已停止且没有猜测字段。"
    if state == "failed":
        return "存档续接执行失败，已锁定本份存档以避免重复发包。"
    return f"存档续接状态：{state}。"
