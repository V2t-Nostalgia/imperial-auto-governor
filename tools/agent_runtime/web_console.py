#!/usr/bin/env python3
"""Local web console for IAG planning, calibration, telemetry, and execution."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import ssl
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from autonomy import autonomy_probe, coalesce_next_review_after_turn
from campaign_strategy import (
    activate_emergency,
    end_emergency,
    public_strategy_state,
    save_decade_plan,
)
from conversation_agent import ConversationAgent
from conversation_store import ConversationStore
from host_executor_protocol import (
    HOST_EXECUTOR_CAPABILITY,
    REQUIRED_HOST_EXECUTOR_APP_VERSION,
    HostExecutorProtocolError,
    record_host_ready,
    record_host_result,
    request_for_host_client,
)
from host_bridge_pairing import (
    PAIRED_HOST_BRIDGE_ARCHIVE,
    PUBLIC_HOST_BRIDGE_ARCHIVE,
    build_paired_host_bridge_archive,
    certificate_sha256,
    pairing_server_url,
)
from iag_agent import run_cycle
from iag_supervisor import (
    carrier_click_profile_path,
    carrier_intermediate_profile_path,
    carrier_navigation_profile_path,
    execute_run,
    runtime_path,
)
from model_client import request_body_overrides
from model_templates import apply_model_template, public_model_templates
from planner import CAPABILITIES_PATH, read_json
from port_discovery import discover_session
from fixed_click import (
    capture_calibration,
    commit_calibration,
    execute_fixed_click,
)
from save_ingest import (
    SaveIngestError,
    bearer_token_matches as upload_bearer_token_matches,
    maximum_source_save_lag_versions,
    read_campaign_manifest,
    read_manifest,
    receive_uploaded_save,
    resolve_current_save,
    review_interval_months,
    upload_token_path,
)
from web_research import DEFAULT_ALLOWED_DOMAINS


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "web"
CAPTURE_ID_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_[a-f0-9]{8}$")

DEFAULT_STRATEGIC_PROMPT_PATH = (
    ROOT / "strategy" / "grey_tempest_conversation_zh.md"
)
DEFAULT_STRATEGIC_PROMPT = DEFAULT_STRATEGIC_PROMPT_PATH.read_text(
    encoding="utf-8"
).strip()

class ConsoleError(RuntimeError):
    """An operator-facing console error."""


class RangeNotSatisfiable(ValueError):
    """Raised when an HTTP byte range cannot be served."""


CATALOG_GROUPS = (
    {
        "action_type": "build_building",
        "collection": "buildings",
        "target_field": "building_id",
        "label_zh": "建筑",
    },
    {
        "action_type": "build_district",
        "collection": "districts",
        "target_field": "district_type",
        "label_zh": "主区划",
    },
    {
        "action_type": "build_zone",
        "collection": "zones",
        "target_field": "zone_type",
        "label_zh": "区划特化",
    },
    {
        "action_type": "upgrade_building",
        "collection": "building_upgrades",
        "target_field": "to_building_id",
        "label_zh": "建筑升级",
    },
    {
        "action_type": "replace_building",
        "collection": "buildings",
        "target_field": "to_building_id",
        "label_zh": "建筑替换",
    },
)


def build_construction_catalog(
    capabilities: dict[str, Any],
    candidates: list[dict[str, Any]] | None,
    manifest: dict[str, Any] | None,
    calibration_steps: dict[str, Any],
) -> dict[str, Any]:
    """Build the reader-facing target catalog without inventing legal actions."""
    legal_counts: dict[tuple[str, str], int] = {}
    for candidate in candidates or []:
        action = candidate.get("action")
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("type", ""))
        group = next(
            (
                item
                for item in CATALOG_GROUPS
                if item["action_type"] == action_type
            ),
            None,
        )
        if group is None:
            continue
        object_id = str(action.get(str(group["target_field"]), ""))
        if object_id:
            key = (action_type, object_id)
            legal_counts[key] = legal_counts.get(key, 0) + 1

    selected_action = (manifest or {}).get("action")
    if not isinstance(selected_action, dict):
        selected_action = {}
    selected_type = str(selected_action.get("type", ""))
    carrier_definitions = capabilities.get("carriers", {})
    groups: list[dict[str, Any]] = []

    for group in CATALOG_GROUPS:
        action_type = str(group["action_type"])
        target_field = str(group["target_field"])
        selected_id = (
            str(selected_action.get(target_field, ""))
            if selected_type == action_type
            else ""
        )
        collection = capabilities.get(str(group["collection"]), {})
        entries: list[dict[str, Any]] = []
        if isinstance(collection, dict):
            for object_id, raw_value in collection.items():
                value = raw_value if isinstance(raw_value, dict) else {}
                enabled = value.get("enabled") is True
                legal_count = legal_counts.get((action_type, str(object_id)), 0)
                entries.append(
                    {
                        "object_id": str(object_id),
                        "label_zh": value.get("label_zh") or str(object_id),
                        "role": value.get("role"),
                        "enabled": enabled,
                        "planner_enabled": bool(
                            value.get("planner_enabled", enabled)
                        ),
                        "legal_candidate_count": legal_count,
                        "selected": str(object_id) == selected_id,
                        "verified_transport": value.get("verified_transport"),
                    }
                )
        elif isinstance(collection, list):
            for raw_value in collection:
                if not isinstance(raw_value, dict):
                    continue
                object_id = str(raw_value.get(target_field) or "")
                if not object_id:
                    continue
                enabled = raw_value.get("enabled") is True
                legal_count = legal_counts.get((action_type, object_id), 0)
                entries.append(
                    {
                        "object_id": object_id,
                        "source_object_id": raw_value.get("from_building_id"),
                        "label_zh": raw_value.get("label_zh") or object_id,
                        "role": raw_value.get("role"),
                        "enabled": enabled,
                        "planner_enabled": enabled,
                        "legal_candidate_count": legal_count,
                        "selected": object_id == selected_id,
                        "verified_transport": raw_value.get(
                            "verified_transport"
                        ),
                    }
                )
        entries.sort(
            key=lambda item: (
                not item["selected"],
                item["legal_candidate_count"] == 0,
                not item["planner_enabled"],
                not item["enabled"],
                str(item["label_zh"]),
            )
        )

        carrier = carrier_definitions.get(action_type, {})
        if not isinstance(carrier, dict):
            carrier = {}
        calibration = calibration_steps.get(action_type, {})
        groups.append(
            {
                "action_type": action_type,
                "label_zh": group["label_zh"],
                "selected_object_id": selected_id or None,
                "legal_candidate_count": sum(
                    item["legal_candidate_count"] for item in entries
                ),
                "carrier": {
                    **carrier,
                    "calibration": calibration,
                    "sequence_ready": bool(calibration.get("sequence_ready")),
                },
                "entries": entries,
            }
        )

    return {
        "schema": "iag.construction_catalog.v1",
        "selected_action_type": selected_type or None,
        "groups": groups,
    }


def parse_single_byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    """Parse one RFC 7233 byte range and return an inclusive interval."""
    if not value:
        return None
    if size <= 0:
        raise RangeNotSatisfiable("empty resource")

    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if match is None:
        raise RangeNotSatisfiable("unsupported byte range")

    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise RangeNotSatisfiable("empty byte range")

    if start_text:
        start = int(start_text)
        if start >= size:
            raise RangeNotSatisfiable("range starts beyond resource")
        end = int(end_text) if end_text else size - 1
        if end < start:
            raise RangeNotSatisfiable("range ends before it starts")
        return start, min(end, size - 1)

    suffix_length = int(end_text)
    if suffix_length <= 0:
        raise RangeNotSatisfiable("empty suffix range")
    return max(0, size - suffix_length), size - 1


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(temporary, mode)
    temporary.replace(path)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def optional_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return None
    return value if isinstance(value, dict) else None


def optional_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return ""


def basic_auth_matches(
    authorization: str | None,
    username: str,
    password: str,
) -> bool:
    if not authorization or not username or not password:
        return False
    scheme, separator, encoded = authorization.partition(" ")
    if separator != " " or scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    candidate_username, separator, candidate_password = decoded.partition(":")
    if separator != ":":
        return False
    return hmac.compare_digest(candidate_username, username) and hmac.compare_digest(
        candidate_password,
        password,
    )


def validate_bind_security(
    host: str,
    *,
    allow_lan: bool,
    has_tls: bool,
    auth_enabled: bool,
) -> bool:
    is_loopback = host in {"127.0.0.1", "::1", "localhost"}
    if not is_loopback and not allow_lan:
        raise ConsoleError("Refusing a non-loopback bind without --allow-lan.")
    if not is_loopback and not auth_enabled:
        raise ConsoleError("Refusing a LAN bind without configured Basic Auth.")
    if not is_loopback and not has_tls:
        raise ConsoleError("Refusing a plaintext LAN bind without TLS.")
    return is_loopback


def build_save_client_record(
    value: dict[str, Any],
    *,
    source_ip: str,
    seen_at: str | None = None,
    seen_epoch: float | None = None,
) -> dict[str, Any]:
    client_id = str(value.get("client_id", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", client_id):
        raise ConsoleError("客户端 ID 格式无效。")
    state = str(value.get("state", "")).strip()
    if state not in {"running", "stopped"}:
        raise ConsoleError("客户端状态必须是 running 或 stopped。")

    def bounded(name: str, limit: int = 160) -> str | None:
        raw = value.get(name)
        if raw is None:
            return None
        result = str(raw).strip()
        return result[:limit] if result else None

    raw_capabilities = value.get("capabilities", [])
    if raw_capabilities is None:
        raw_capabilities = []
    if not isinstance(raw_capabilities, list):
        raise ConsoleError("客户端 capabilities 必须是字符串数组。")
    capabilities = sorted(
        {
            str(capability).strip()[:80]
            for capability in raw_capabilities[:20]
            if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(capability).strip())
        }
    )

    return {
        "schema": "iag.save_client_status.v1",
        "client_id": client_id,
        "state": state,
        "source_ip": str(source_ip).strip()[:64],
        "hostname": bounded("hostname", 128),
        "app_version": bounded("app_version", 64),
        "campaign_id": bounded("campaign_id", 128),
        "campaign_label": bounded("campaign_label", 160),
        "last_game_date": bounded("last_game_date", 64),
        "last_success_at": bounded("last_success_at", 64),
        "capabilities": capabilities,
        "last_seen_at": seen_at or now_iso(),
        "last_seen_epoch": time.time() if seen_epoch is None else seen_epoch,
    }


class ConsoleService:
    def __init__(
        self,
        config_path: Path,
        *,
        tls_certificate_path: Path | None = None,
    ):
        self.config_path = config_path.resolve()
        self._config_lock = threading.RLock()
        self.config = read_json(self.config_path)
        self.runtime_root = Path(self.config["runtime_root"]).expanduser()
        self.operator_root = self.runtime_root / "operator"
        self.state_root = self.runtime_root / "state"
        self.tls_certificate_path = (
            tls_certificate_path.resolve()
            if tls_certificate_path is not None
            else None
        )
        self._host_bridge_pairing_lock = threading.Lock()
        self.capture_root = self.runtime_root / "calibration" / "captures"
        self.history_root = self.operator_root / "prompt_history"
        self.runs_root = runtime_path(
            self.config,
            "runs_root",
            self.runtime_root / "runs",
        )
        self.prompt_path = runtime_path(
            self.config,
            "operator_prompt_path",
            self.operator_root / "strategic_prompt.md",
        )
        self.instruction_path = runtime_path(
            self.config,
            "operator_instruction_path",
            self.operator_root / "current_instruction.md",
        )
        self.profile_paths = {
            action_type: carrier_click_profile_path(self.config, action_type)
            for action_type in (
                "build_building",
                "build_district",
                "build_zone",
                "upgrade_building",
                "replace_building",
            )
        }
        # Retain the legacy attribute for callers that only know buildings.
        self.profile_path = self.profile_paths["build_building"]
        self.navigation_profile_paths = {
            action_type: path
            for action_type in (
                "build_building",
                "build_zone",
                "upgrade_building",
                "replace_building",
            )
            if (
                path := carrier_navigation_profile_path(
                    self.config, action_type
                )
            ) is not None
        }
        self.intermediate_profile_paths = {
            action_type: path
            for action_type in self.profile_paths
            if (
                path := carrier_intermediate_profile_path(
                    self.config,
                    action_type,
                )
            ) is not None
        }
        self.telemetry_path = runtime_path(
            self.config,
            "interceptor_status_path",
            self.state_root / "interceptor_status.json",
        )
        self.passive_telemetry_path = runtime_path(
            self.config,
            "passive_telemetry_path",
            self.state_root / "passive_flow_status.json",
        )
        self.save_upload_token_path = upload_token_path(self.config)
        self.save_client_status_path = runtime_path(
            self.config,
            "save_client_status_path",
            self.state_root / "save_client_status.json",
        )
        self.save_client_timeout_seconds = max(
            int(self.config.get("save_client_heartbeat_timeout_seconds", 15)),
            5,
        )
        self.stop_path = self.state_root / "emergency_stop"
        self._conversation_lock = threading.RLock()
        self.conversation_db_path = runtime_path(
            self.config,
            "conversation_database",
            self.runtime_root / "conversation" / "campaign.sqlite3",
        )
        self.conversation_store = ConversationStore(
            self.conversation_db_path
        )
        self._activate_conversation(
            self.conversation_store.active_conversation_id(),
            seed=False,
        )
        self._autonomy_stop = threading.Event()
        self._autonomy_probe: dict[str, Any] = {
            "enabled": False,
            "mode": self._active_autonomy_mode(),
            "due": False,
            "reason": "starting",
        }
        self._job_lock = threading.Lock()
        self._save_upload_lock = threading.Lock()
        self._save_client_lock = threading.Lock()
        self._job: dict[str, Any] = {
            "state": "idle",
            "kind": None,
            "message": "",
            "started_at": None,
            "finished_at": None,
        }
        for path in (
            self.operator_root,
            self.state_root,
            self.capture_root,
            self.history_root,
            self.runs_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        if not self.prompt_path.exists():
            atomic_write_text(self.prompt_path, DEFAULT_STRATEGIC_PROMPT + "\n")
        if not self.instruction_path.exists():
            atomic_write_text(self.instruction_path, "")
        if not self.save_upload_token_path.exists():
            atomic_write_text(
                self.save_upload_token_path,
                secrets.token_urlsafe(32) + "\n",
                mode=0o600,
            )
        self._seed_conversation(self.conversation_store)
        if (
            self.conversation_store.conversation_id == "campaign"
            and self.conversation_store.get_state("latest_run_id", None) is None
        ):
            latest = self._latest_run_dir_global()
            if latest is not None:
                self.conversation_store.set_state("latest_run_id", latest.name)
        self._scheduler_thread = threading.Thread(
            target=self._autonomy_loop,
            name="iag-autonomy-scheduler",
            daemon=True,
        )
        self._scheduler_thread.start()

    def reload_config(self) -> None:
        with self._config_lock:
            self.config = read_json(self.config_path)

    def _initialize_conversation_defaults(
        self,
        store: ConversationStore,
    ) -> None:
        if store.get_state("autonomy_mode", None) is None:
            store.set_state(
                "autonomy_mode",
                str(self.config.get("autonomy_mode", "paused")),
            )
        if store.get_state("review_interval_months", None) is None:
            store.set_state(
                "review_interval_months",
                review_interval_months(self.config),
            )

    def _seed_conversation(self, store: ConversationStore) -> None:
        if store.public_state()["stored_messages"] != 0:
            return
        metadata = store.conversation_metadata()
        label = metadata.get("campaign_label") or metadata.get("title")
        store.append(
            "assistant",
            (
                f"战役会话“{label}”已经建立。我会只保留并使用这一局的战略要求、"
                "问答、长期规划与建设审计；读取当前数值前会核对绑定存档。"
            ),
            kind="assistant_message",
            visible=True,
        )

    def _activate_conversation(
        self,
        conversation_id: str,
        *,
        seed: bool = True,
    ) -> dict[str, Any]:
        store = ConversationStore(
            self.conversation_db_path,
            conversation_id=conversation_id,
        )
        metadata = store.set_active_conversation(conversation_id)
        self._initialize_conversation_defaults(store)
        self.conversation_store = store
        self.conversation_agent = ConversationAgent(self.config_path, store)
        if seed:
            self._seed_conversation(store)
        return metadata

    def _latest_run_dir_global(self) -> Path | None:
        if not self.runs_root.is_dir():
            return None
        directories = [
            path for path in self.runs_root.iterdir() if path.is_dir()
        ]
        return (
            max(directories, key=lambda path: path.stat().st_mtime)
            if directories
            else None
        )

    def _active_autonomy_mode(
        self,
        store: ConversationStore | None = None,
    ) -> str:
        selected = store or self.conversation_store
        return str(
            selected.get_state(
                "autonomy_mode",
                self.config.get("autonomy_mode", "paused"),
            )
        )

    def _active_review_interval(
        self,
        store: ConversationStore | None = None,
    ) -> int:
        selected = store or self.conversation_store
        value = selected.get_state(
            "review_interval_months",
            self.config.get("save_review_interval_months", 1),
        )
        return review_interval_months(
            {**self.config, "save_review_interval_months": value}
        )

    def _ensure_no_running_job(self) -> None:
        with self._job_lock:
            if self._job.get("state") == "running":
                raise ConsoleError(
                    "代理任务运行期间不能切换、归档或重新绑定战役会话。"
                )

    def campaign_binding_status(
        self,
        store: ConversationStore | None = None,
    ) -> dict[str, Any]:
        selected = store or self.conversation_store
        metadata = selected.conversation_metadata()
        manifest = read_manifest(self.config) or {}
        current_campaign_id = (
            str(manifest.get("campaign_id") or "").strip().lower() or None
        )
        current_campaign_label = (
            str(manifest.get("campaign_label") or "").strip() or None
        )
        bound_campaign_id = metadata.get("campaign_id")
        campaign_id_holder = (
            selected.conversation_for_campaign(current_campaign_id)
            if current_campaign_id
            else None
        )
        current_holder = (
            campaign_id_holder
            if (
                campaign_id_holder is not None
                and current_campaign_label is not None
                and campaign_id_holder.get("campaign_label")
                == current_campaign_label
            )
            else None
        )
        if current_campaign_id is None:
            state = "no_current_save"
            message = "尚未收到房主存档；会话不会被自动绑定。"
        elif current_holder is None:
            state = "unbound"
            if campaign_id_holder is not None:
                message = (
                    "当前存档与“"
                    + str(campaign_id_holder["title"])
                    + "”共享内部 ID，但存档标签不同。为避免串局，"
                    "必须手动选择目标会话。"
                )
            else:
                message = (
                    "当前上传存档尚未绑定任何会话。"
                    "请选择目标会话后手动确认。"
                )
        elif current_holder["conversation_id"] == metadata["conversation_id"]:
            state = "ready"
            message = "会话与当前上传存档一致，可以规划和执行。"
        else:
            state = "assigned_elsewhere"
            message = (
                "当前上传存档已绑定到“"
                + str(current_holder["title"])
                + "”，当前打开的是“"
                + str(metadata["title"])
                + "”。系统不会自动切换会话。"
            )
        return {
            "state": state,
            "message": message,
            "execution_allowed": state == "ready",
            "bound_campaign_id": bound_campaign_id,
            "current_campaign_id": current_campaign_id,
            "current_campaign_label": current_campaign_label,
            "active_conversation_id": metadata["conversation_id"],
            "active_conversation_title": metadata["title"],
            "current_bound_conversation_id": (
                current_holder.get("conversation_id")
                if current_holder
                else None
            ),
            "current_bound_conversation_title": (
                current_holder.get("title") if current_holder else None
            ),
            "matching_conversation_id": (
                current_holder.get("conversation_id")
                if current_holder
                else None
            ),
            "campaign_id_holder_conversation_id": (
                campaign_id_holder.get("conversation_id")
                if campaign_id_holder
                else None
            ),
            "campaign_id_holder_conversation_title": (
                campaign_id_holder.get("title")
                if campaign_id_holder
                else None
            ),
        }

    def _require_active_campaign(
        self,
        store: ConversationStore | None = None,
    ) -> Path:
        selected = store or self.conversation_store
        binding = self.campaign_binding_status(selected)
        if not binding["execution_allowed"]:
            raise ConsoleError(binding["message"])
        return resolve_current_save(
            self.config,
            expected_campaign_id=str(binding["bound_campaign_id"]),
        )

    def conversation_catalog_payload(
        self,
        *,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        active_id = self.conversation_store.conversation_id
        binding = self.campaign_binding_status()
        conversations = self.conversation_store.list_conversations(
            include_archived=include_archived
        )
        for item in conversations:
            campaign_id = item.get("campaign_id")
            manifest = (
                read_campaign_manifest(self.config, str(campaign_id))
                if campaign_id
                else None
            )
            if (
                manifest is not None
                and str(manifest.get("campaign_label") or "").strip()
                != str(item.get("campaign_label") or "").strip()
            ):
                manifest = None
            metadata = (manifest or {}).get("metadata") or {}
            item["last_game_date"] = metadata.get("date")
            item["last_save_received_at"] = (
                (manifest or {}).get("received_at")
            )
            item["active"] = item["conversation_id"] == active_id
            item["is_current_save"] = (
                bool(campaign_id)
                and campaign_id == binding.get("current_campaign_id")
                and item.get("campaign_label")
                == binding.get("current_campaign_label")
            )
        return {
            "schema": "iag.conversation_catalog.v1",
            "active_conversation_id": active_id,
            "conversations": conversations,
            "binding": binding,
        }

    def create_conversation(
        self,
        title: str,
        *,
        bind_current: bool,
    ) -> dict[str, Any]:
        with self._conversation_lock:
            self._ensure_no_running_job()
            manifest = read_manifest(self.config) or {}
            campaign_id = None
            campaign_label = None
            if bind_current:
                campaign_id = str(
                    manifest.get("campaign_id") or ""
                ).strip().lower()
                campaign_label = str(
                    manifest.get("campaign_label") or ""
                ).strip() or None
                if not campaign_id:
                    raise ConsoleError("尚未收到可绑定的房主存档。")
                existing = self.conversation_store.conversation_for_campaign(
                    campaign_id
                )
                if existing is not None:
                    raise ConsoleError(
                        "当前存档已有战役会话，请直接继续原会话。"
                    )
            selected_title = str(title).strip()
            if not selected_title:
                selected_title = (
                    campaign_label
                    or f"新战役 {len(self.conversation_store.list_conversations()) + 1}"
                )
            try:
                metadata = self.conversation_store.create_conversation(
                    selected_title,
                    campaign_id=campaign_id,
                    campaign_label=campaign_label,
                )
            except ValueError as error:
                raise ConsoleError(str(error)) from error
            self._activate_conversation(
                str(metadata["conversation_id"]),
                seed=True,
            )
            return self.conversation_catalog_payload(include_archived=True)

    def switch_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._conversation_lock:
            self._ensure_no_running_job()
            try:
                self._activate_conversation(conversation_id, seed=True)
            except (KeyError, ValueError) as error:
                raise ConsoleError(str(error)) from error
            return self.conversation_catalog_payload(include_archived=True)

    def rename_conversation(
        self,
        conversation_id: str,
        title: str,
    ) -> dict[str, Any]:
        with self._conversation_lock:
            self._ensure_no_running_job()
            try:
                self.conversation_store.rename_conversation(
                    conversation_id,
                    title,
                )
            except (KeyError, ValueError) as error:
                raise ConsoleError(str(error)) from error
            return self.conversation_catalog_payload(include_archived=True)

    def set_conversation_archived(
        self,
        conversation_id: str,
        archived: bool,
    ) -> dict[str, Any]:
        with self._conversation_lock:
            self._ensure_no_running_job()
            try:
                if not archived:
                    self.conversation_store.set_conversation_archived(
                        conversation_id,
                        False,
                    )
                elif (
                    conversation_id
                    == self.conversation_store.conversation_id
                ):
                    replacements = [
                        item
                        for item in self.conversation_store.list_conversations()
                        if item["conversation_id"] != conversation_id
                    ]
                    if not replacements:
                        raise ConsoleError(
                            "至少需要保留一条未归档的战役会话。"
                        )
                    self.conversation_store.set_conversation_archived(
                        conversation_id,
                        True,
                    )
                    self._activate_conversation(
                        str(replacements[0]["conversation_id"]),
                        seed=True,
                    )
                else:
                    self.conversation_store.set_conversation_archived(
                        conversation_id,
                        True,
                    )
            except (KeyError, ValueError) as error:
                raise ConsoleError(str(error)) from error
            return self.conversation_catalog_payload(include_archived=True)

    def set_current_campaign_binding(
        self,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        with self._conversation_lock:
            self._ensure_no_running_job()
            manifest = read_manifest(self.config) or {}
            campaign_id = str(
                manifest.get("campaign_id") or ""
            ).strip().lower()
            campaign_label = str(
                manifest.get("campaign_label") or ""
            ).strip() or None
            if not campaign_id:
                raise ConsoleError("尚未收到可绑定的房主存档。")
            selected_conversation = (
                str(conversation_id).strip()
                if conversation_id is not None
                else None
            )
            if selected_conversation == "":
                raise ConsoleError("conversation_id 不能为空字符串。")
            try:
                update = (
                    self.conversation_store.assign_campaign_to_conversation(
                        campaign_id,
                        selected_conversation,
                        campaign_label=campaign_label,
                    )
                )
            except (KeyError, ValueError) as error:
                raise ConsoleError(str(error)) from error
            result = self.conversation_catalog_payload(include_archived=True)
            result["binding_update"] = update
            return result

    def frontend_auth(self) -> dict[str, Any]:
        username = str(self.config.get("frontend_auth_username", "")).strip()
        password_path = runtime_path(
            self.config,
            "frontend_auth_password_file",
            self.runtime_root / "secrets" / "frontend_password",
        )
        password = optional_text(password_path).strip()
        return {
            "enabled": bool(username and password),
            "username": username,
            "password": password,
            "password_path": str(password_path),
        }

    def public_model_config(self) -> dict[str, Any]:
        key_file = self.config.get("api_key_file")
        env_name = str(self.config.get("api_key_env", "IAG_LLM_API_KEY"))
        configured = bool(os.environ.get(env_name))
        if key_file:
            configured = configured or Path(str(key_file)).expanduser().is_file()
        thinking = self.config.get("thinking")
        thinking_enabled = (
            isinstance(thinking, dict)
            and str(thinking.get("type", "")).lower() == "enabled"
        )
        return {
            "template_id": self.config.get("model_template_id", "custom"),
            "provider": self.config.get("provider", "responses_compatible"),
            "base_url": self.config.get("base_url", ""),
            "model": self.config.get("model", ""),
            "thinking_enabled": thinking_enabled,
            "reasoning_effort": self.config.get("reasoning_effort"),
            "temperature": self.config.get("temperature", 0.15),
            "timeout_seconds": self.config.get("timeout_seconds", 120),
            "request_body_overrides": request_body_overrides(self.config),
            "model_context_window_tokens": int(
                self.config.get("model_context_window_tokens", 128_000)
            ),
            "context_output_reserve_tokens": int(
                self.config.get("context_output_reserve_tokens", 8_192)
            ),
            "context_compression_enabled": bool(
                self.config.get("context_compression_enabled", True)
            ),
            "context_compression_trigger_percent": float(
                self.config.get("context_compression_trigger_percent", 80)
            ),
            "context_compression_target_percent": float(
                self.config.get("context_compression_target_percent", 35)
            ),
            "web_research_enabled": bool(
                self.config.get("web_research_enabled", False)
            ),
            "searxng_url": self.config.get(
                "searxng_url", "http://127.0.0.1:8080"
            ),
            "crawl4ai_url": self.config.get(
                "crawl4ai_url", "http://127.0.0.1:11235"
            ),
            "stellaris_wiki_api_url": self.config.get(
                "stellaris_wiki_api_url",
                "https://stellaris.paradoxwikis.com/api.php",
            ),
            "web_search_allowed_domains": self.config.get(
                "web_search_allowed_domains", DEFAULT_ALLOWED_DOMAINS
            ),
            "web_fetch_direct_fallback_enabled": bool(
                self.config.get("web_fetch_direct_fallback_enabled", False)
            ),
            "crawl4ai_api_token_configured": bool(
                self.config.get("crawl4ai_api_token_file")
                and runtime_path(
                    self.config,
                    "crawl4ai_api_token_file",
                    self.runtime_root / "secrets" / "crawl4ai_api_token",
                ).is_file()
            ),
            "tool_calling_enabled": bool(
                self.config.get("tool_calling_enabled", True)
            ),
            "conversation_supported": (
                self.config.get("provider") == "chat_completions_compatible"
                and bool(self.config.get("tool_calling_enabled", True))
            ),
            "api_key_configured": configured,
            "templates": public_model_templates(),
        }

    def save_model_config(self, value: dict[str, Any]) -> dict[str, Any]:
        template_id = str(value.get("template_id", "custom")).strip() or "custom"
        try:
            with self._config_lock:
                current = dict(self.config)
            updated = apply_model_template(current, template_id)
        except ValueError as error:
            raise ConsoleError(str(error)) from error
        if template_id == "custom":
            updated["provider"] = str(value.get("provider", "")).strip()
            updated["base_url"] = str(value.get("base_url", "")).strip()
            updated["model"] = str(value.get("model", "")).strip()
            updated["tool_calling_enabled"] = bool(
                value.get("tool_calling_enabled", True)
            )
            if bool(value.get("thinking_enabled", False)):
                updated["thinking"] = {"type": "enabled"}
                updated["reasoning_effort"] = str(
                    value.get("reasoning_effort", "high")
                )
            else:
                updated.pop("thinking", None)
                updated.pop("reasoning_effort", None)

        try:
            temperature = float(value.get("temperature", 0.15))
            timeout_seconds = int(value.get("timeout_seconds", 120))
            context_maximum = int(
                value.get("model_context_window_tokens", 128_000)
            )
            output_reserve = int(
                value.get("context_output_reserve_tokens", 8_192)
            )
            trigger_percent = float(
                value.get("context_compression_trigger_percent", 80)
            )
            target_percent = float(
                value.get("context_compression_target_percent", 35)
            )
        except (TypeError, ValueError) as error:
            raise ConsoleError("模型高级参数必须是有效数字。") from error
        if not 0 <= temperature <= 2:
            raise ConsoleError("Temperature 必须在 0 到 2 之间。")
        if not 10 <= timeout_seconds <= 900:
            raise ConsoleError("API 超时必须在 10 到 900 秒之间。")
        if not 8_192 <= context_maximum <= 2_000_000:
            raise ConsoleError("最大上下文必须在 8192 到 2000000 tokens 之间。")
        if not 512 <= output_reserve <= context_maximum // 2:
            raise ConsoleError("输出预留必须至少 512，且不超过最大上下文的一半。")
        if not 30 <= trigger_percent <= 98:
            raise ConsoleError("自动压缩触发比例必须在 30% 到 98% 之间。")
        if not 10 <= target_percent <= trigger_percent - 5:
            raise ConsoleError("压缩目标比例必须至少 10%，且比触发比例低至少 5%。")

        raw_overrides = value.get("request_body_overrides", {})
        if isinstance(raw_overrides, str):
            try:
                raw_overrides = json.loads(raw_overrides or "{}")
            except json.JSONDecodeError as error:
                raise ConsoleError(f"请求参数 Raw JSON 无效：{error}") from error
        candidate_for_validation = {
            **updated,
            "request_body_overrides": raw_overrides,
        }
        try:
            validated_overrides = request_body_overrides(
                candidate_for_validation
            )
        except ValueError as error:
            raise ConsoleError(str(error)) from error
        updated.update(
            {
                "temperature": temperature,
                "timeout_seconds": timeout_seconds,
                "request_body_overrides": validated_overrides,
                "model_context_window_tokens": context_maximum,
                "context_output_reserve_tokens": output_reserve,
                "context_compression_enabled": bool(
                    value.get("context_compression_enabled", True)
                ),
                "context_compression_trigger_percent": trigger_percent,
                "context_compression_target_percent": target_percent,
            }
        )
        service_fields = {
            "searxng_url": str(
                value.get("searxng_url", "http://127.0.0.1:8080")
            ).strip(),
            "crawl4ai_url": str(
                value.get("crawl4ai_url", "http://127.0.0.1:11235")
            ).strip(),
            "stellaris_wiki_api_url": str(
                value.get(
                    "stellaris_wiki_api_url",
                    "https://stellaris.paradoxwikis.com/api.php",
                )
            ).strip(),
        }
        for label, endpoint in service_fields.items():
            parsed = urlparse(endpoint)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ConsoleError(f"{label} 必须是无内嵌凭据的 HTTP(S) URL。")
        raw_domains = value.get(
            "web_search_allowed_domains", DEFAULT_ALLOWED_DOMAINS
        )
        if isinstance(raw_domains, str):
            raw_domains = re.split(r"[\s,;]+", raw_domains)
        if not isinstance(raw_domains, list):
            raise ConsoleError("网页来源白名单必须是域名列表。")
        allowed_domains = []
        for raw_domain in raw_domains:
            domain = str(raw_domain).strip().lower().rstrip(".")
            if not domain:
                continue
            if not re.fullmatch(r"[a-z0-9.-]{1,253}", domain):
                raise ConsoleError(f"无效的网页来源域名：{domain}")
            if domain not in allowed_domains:
                allowed_domains.append(domain)
        if not allowed_domains:
            raise ConsoleError("网页来源白名单至少需要一个域名。")
        if len(allowed_domains) > 64:
            raise ConsoleError("网页来源白名单最多 64 个域名。")
        updated.update(
            {
                **service_fields,
                "web_research_enabled": bool(
                    value.get("web_research_enabled", False)
                ),
                "web_search_allowed_domains": allowed_domains,
                "web_fetch_direct_fallback_enabled": bool(
                    value.get("web_fetch_direct_fallback_enabled", False)
                ),
            }
        )
        crawl_token = str(value.get("crawl4ai_api_token", "")).strip()
        if crawl_token:
            token_path = self.runtime_root / "secrets" / "crawl4ai_api_token"
            atomic_write_text(token_path, crawl_token + "\n", mode=0o600)
            updated["crawl4ai_api_token_file"] = str(token_path)
        provider = str(updated.get("provider", "")).strip()
        if provider not in {"responses_compatible", "chat_completions_compatible"}:
            raise ConsoleError("不支持的 API 协议类型。")
        base_url = str(updated.get("base_url", "")).strip()
        if not base_url.startswith(("http://", "https://")):
            raise ConsoleError("Base URL 必须以 http:// 或 https:// 开头。")
        model = str(updated.get("model", "")).strip()
        if not model:
            raise ConsoleError("模型名称不能为空。")
        api_key = str(value.get("api_key", "")).strip()
        if api_key:
            key_path = self.runtime_root / "secrets" / "llm_api_key"
            atomic_write_text(key_path, api_key + "\n", mode=0o600)
            updated["api_key_file"] = str(key_path)
        with self._config_lock:
            self.config = updated
            atomic_write_json(self.config_path, self.config)
            return self.public_model_config()

    def public_execution_settings(self) -> dict[str, Any]:
        policy = str(
            self.config.get(
                "inconclusive_rewrite_policy", "block_until_save"
            )
        )
        if policy not in {
            "block_until_save",
            "allow_serial_provisional",
        }:
            policy = "block_until_save"
        pending_state = self.conversation_store.get_state(
            "pending_execution_confirmations", []
        )
        if isinstance(pending_state, list):
            pending_count = len(pending_state)
        elif isinstance(pending_state, dict) and pending_state:
            pending_count = 1
        else:
            pending_count = 0
        return {
            "fixed_click_guard_enabled": bool(
                self.config.get("fixed_click_guard_enabled", True)
            ),
            "require_fresh_save_seconds": int(
                self.config.get("require_fresh_save_seconds", 900)
            ),
            "maximum_source_save_lag_versions": maximum_source_save_lag_versions(
                self.config
            ),
            "autonomy_require_fresh_save_seconds": int(
                self.config.get("autonomy_require_fresh_save_seconds", 900)
            ),
            "maximum_constructions_per_turn": int(
                self.config.get("maximum_constructions_per_turn", 3)
            ),
            "inconclusive_rewrite_policy": policy,
            "pending_confirmation_count": pending_count,
        }

    def save_execution_settings(self, value: dict[str, Any]) -> dict[str, Any]:
        try:
            manual_age = int(value.get("require_fresh_save_seconds", 900))
            autonomy_age = int(
                value.get("autonomy_require_fresh_save_seconds", 900)
            )
            maximum = int(value.get("maximum_constructions_per_turn", 3))
            maximum_lag = int(value.get("maximum_source_save_lag_versions", 2))
        except (TypeError, ValueError) as error:
            raise ConsoleError("执行门限必须是有效整数。") from error
        if not 0 <= manual_age <= 86_400:
            raise ConsoleError("执行存档时效必须在 0 到 86400 秒之间。")
        if not 0 <= autonomy_age <= 86_400:
            raise ConsoleError("自主巡检存档时效必须在 0 到 86400 秒之间。")
        if not 1 <= maximum <= 5:
            raise ConsoleError("单轮串行建设上限必须在 1 到 5 之间。")
        try:
            maximum_source_save_lag_versions(
                {"maximum_source_save_lag_versions": maximum_lag}
            )
        except SaveIngestError as error:
            raise ConsoleError(str(error)) from error
        policy = str(value.get("inconclusive_rewrite_policy", ""))
        if policy not in {
            "block_until_save",
            "allow_serial_provisional",
        }:
            raise ConsoleError("不支持的模糊回包处理策略。")
        self.config.update(
            {
                "fixed_click_guard_enabled": bool(
                    value.get("fixed_click_guard_enabled", True)
                ),
                "require_fresh_save_seconds": manual_age,
                "maximum_source_save_lag_versions": maximum_lag,
                "autonomy_require_fresh_save_seconds": autonomy_age,
                "maximum_constructions_per_turn": maximum,
                "inconclusive_rewrite_policy": policy,
            }
        )
        atomic_write_json(self.config_path, self.config)
        return {"saved": True, **self.public_execution_settings()}

    def _strategy_game_date(
        self,
        store: ConversationStore | None = None,
    ) -> str | None:
        selected = store or self.conversation_store
        binding = self.campaign_binding_status(selected)
        if binding.get("execution_allowed"):
            manifest = read_manifest(self.config) or {}
            metadata = manifest.get("metadata") or {}
            game_date = str(metadata.get("date") or "").strip()
            if game_date:
                return game_date
        decade = selected.get_state("decade_planning", {})
        if isinstance(decade, dict):
            return str(decade.get("last_game_date") or "").strip() or None
        return None

    def strategy_payload(self) -> dict[str, Any]:
        with self._conversation_lock:
            self.reload_config()
            return public_strategy_state(
                self.conversation_store,
                self._strategy_game_date(),
                renewal_lead_months=int(
                    self.config.get("decade_plan_renewal_lead_months", 12)
                ),
            )

    def save_decade_plan_text(
        self,
        text: str,
        target: str,
    ) -> dict[str, Any]:
        with self._conversation_lock:
            self._ensure_no_running_job()
            self.reload_config()
            self._require_active_campaign()
            try:
                decade = save_decade_plan(
                    self.conversation_store,
                    text=text,
                    game_date=self._strategy_game_date(),
                    target=target,
                    renewal_lead_months=int(
                        self.config.get(
                            "decade_plan_renewal_lead_months", 12
                        )
                    ),
                )
            except ValueError as error:
                raise ConsoleError(str(error)) from error
            return {
                "saved": True,
                "strategy": {
                    **self.strategy_payload(),
                    "decade": decade,
                },
            }

    def activate_strategic_emergency(
        self,
        title: str,
        directive: str,
    ) -> dict[str, Any]:
        with self._conversation_lock:
            self._ensure_no_running_job()
            try:
                emergency = activate_emergency(
                    self.conversation_store,
                    title=title,
                    directive=directive,
                    game_date=self._strategy_game_date(),
                )
            except ValueError as error:
                raise ConsoleError(str(error)) from error
            return {
                "saved": True,
                "emergency": emergency,
                "strategy": self.strategy_payload(),
            }

    def end_strategic_emergency(self) -> dict[str, Any]:
        with self._conversation_lock:
            self._ensure_no_running_job()
            emergency = end_emergency(
                self.conversation_store,
                game_date=self._strategy_game_date(),
            )
            return {
                "saved": True,
                "emergency": emergency,
                "strategy": self.strategy_payload(),
            }

    def save_prompt(self, text: str) -> dict[str, Any]:
        if not 1 <= len(text.strip()) <= 20000:
            raise ConsoleError("Prompt 必须包含 1 到 20000 个字符。")
        previous = optional_text(self.prompt_path)
        if previous and previous != text:
            digest = hashlib.sha256(previous.encode("utf-8")).hexdigest()[:10]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            atomic_write_text(
                self.history_root / f"{timestamp}_{digest}.md",
                previous,
            )
        atomic_write_text(self.prompt_path, text.rstrip() + "\n")
        return {"saved": True, "length": len(text), "saved_at": now_iso()}

    def save_instruction(self, text: str) -> dict[str, Any]:
        if len(text) > 8000:
            raise ConsoleError("本局指令不能超过 8000 个字符。")
        suffix = "\n" if text else ""
        atomic_write_text(self.instruction_path, text.rstrip() + suffix)
        history = self.operator_root / "instruction_history.jsonl"
        with history.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"timestamp": now_iso(), "instruction": text},
                    ensure_ascii=False,
                )
                + "\n"
            )
        return {"saved": True, "saved_at": now_iso()}

    def prompt_payload(self) -> dict[str, Any]:
        versions = sorted(
            (path.name for path in self.history_root.glob("*.md")),
            reverse=True,
        )[:20]
        return {"text": optional_text(self.prompt_path), "versions": versions}

    def latest_run_dir(self) -> Path | None:
        run_id = self.conversation_store.get_state("latest_run_id", None)
        if not isinstance(run_id, str) or not re.fullmatch(
            r"[0-9]{8}_[0-9]{6}(?:_[0-9]{2})?",
            run_id,
        ):
            return None
        run_dir = self.runs_root / run_id
        return run_dir if run_dir.is_dir() else None

    def run_summary(self) -> dict[str, Any] | None:
        run_dir = self.latest_run_dir()
        if run_dir is None:
            return None
        return {
            "run_id": run_dir.name,
            "path": str(run_dir),
            "snapshot": optional_json(run_dir / "snapshot.json"),
            "candidates": optional_json(run_dir / "candidates.json") or [],
            "local_rules": optional_json(run_dir / "local_rules.json"),
            "web_research": optional_json(run_dir / "web_research.json"),
            "plan": optional_json(run_dir / "plan.json"),
            "manifest": optional_json(run_dir / "execution_manifest.json"),
            "execution": optional_json(run_dir / "execution_result.json"),
        }

    def save_summary(self) -> dict[str, Any] | None:
        try:
            path = resolve_current_save(self.config)
        except (FileNotFoundError, PermissionError, SaveIngestError):
            return None
        manifest = read_manifest(self.config) or {}
        summary = {
            "path": str(path),
            "name": path.name,
            "modified_at": datetime.fromtimestamp(
                path.stat().st_mtime,
                tz=timezone.utc,
            ).astimezone().isoformat(timespec="seconds"),
            "size": path.stat().st_size,
        }
        if manifest:
            summary.update(
                {
                    "source": "host_upload",
                    "received_at": manifest.get("received_at"),
                    "source_id": manifest.get("source_id"),
                    "campaign_id": manifest.get("campaign_id"),
                    "campaign_label": manifest.get("campaign_label"),
                    "original_name": manifest.get("original_name"),
                    "sha256": manifest.get("sha256"),
                    "game_date": (manifest.get("metadata") or {}).get("date"),
                }
            )
        return summary

    def save_ingest_status(self) -> dict[str, Any]:
        manifest = read_manifest(self.config)
        token = optional_text(self.save_upload_token_path).strip()
        client = optional_json(self.save_client_status_path) or {}
        last_seen_epoch = float(client.get("last_seen_epoch", 0) or 0)
        heartbeat_age = max(time.time() - last_seen_epoch, 0) if last_seen_epoch else None
        connected = bool(
            client.get("state") == "running"
            and heartbeat_age is not None
            and heartbeat_age <= self.save_client_timeout_seconds
        )
        public_client = {
            "connected": connected,
            "state": client.get("state", "unknown"),
            "source_ip": client.get("source_ip"),
            "hostname": client.get("hostname"),
            "app_version": client.get("app_version"),
            "campaign_id": client.get("campaign_id"),
            "campaign_label": client.get("campaign_label"),
            "last_game_date": client.get("last_game_date"),
            "last_success_at": client.get("last_success_at"),
            "capabilities": client.get("capabilities", []),
            "host_executor_ready": (
                HOST_EXECUTOR_CAPABILITY in client.get("capabilities", [])
                and client.get("app_version")
                == REQUIRED_HOST_EXECUTOR_APP_VERSION
            ),
            "required_host_executor_app_version": (
                REQUIRED_HOST_EXECUTOR_APP_VERSION
            ),
            "last_seen_at": client.get("last_seen_at"),
            "heartbeat_age_seconds": round(heartbeat_age, 1) if heartbeat_age is not None else None,
        }
        return {
            "source_mode": self.config.get("save_source_mode", "host_upload"),
            "review_interval_months": self._active_review_interval(),
            "upload_ready": bool(token),
            "last_upload": manifest,
            "client": public_client,
        }

    def receive_save_client_heartbeat(
        self,
        value: dict[str, Any],
        *,
        source_ip: str,
    ) -> dict[str, Any]:
        record = build_save_client_record(value, source_ip=source_ip)
        with self._save_client_lock:
            atomic_write_json(self.save_client_status_path, record)
        executor_request = request_for_host_client(
            self.config,
            client_id=record["client_id"],
            source_ip=source_ip,
        )
        return {
            "accepted": True,
            "source_ip": source_ip,
            "observed_at": record["last_seen_at"],
            "connected": record["state"] == "running",
            "executor_request": executor_request,
        }

    def receive_host_executor_ready(
        self,
        value: dict[str, Any],
        *,
        source_ip: str,
    ) -> dict[str, Any]:
        return record_host_ready(
            self.config,
            value,
            client_id=str(value.get("client_id", "")),
            source_ip=source_ip,
        )

    def receive_host_executor_result(
        self,
        value: dict[str, Any],
        *,
        source_ip: str,
    ) -> dict[str, Any]:
        return record_host_result(
            self.config,
            value,
            client_id=str(value.get("client_id", "")),
            source_ip=source_ip,
        )

    def receive_save_upload(
        self,
        *,
        stream: Any,
        content_length: int,
        headers: Any,
    ) -> dict[str, Any]:
        self.reload_config()
        with self._save_upload_lock:
            return receive_uploaded_save(
                self.config,
                stream,
                content_length=content_length,
                headers=headers,
            )

    def upload_is_authorized(self, authorization: str | None) -> bool:
        token = optional_text(self.save_upload_token_path).strip()
        return upload_bearer_token_matches(authorization, token)

    def paired_host_bridge_archive(self, server_url: str) -> Path:
        """Build an authenticated runtime download paired to this Agent."""
        if self.tls_certificate_path is None:
            raise ConsoleError("Agent 未启用 TLS，不能生成房主执行桥配对包。")
        source = STATIC_ROOT / "downloads" / PUBLIC_HOST_BRIDGE_ARCHIVE
        token = optional_text(self.save_upload_token_path).strip()
        fingerprint = certificate_sha256(self.tls_certificate_path)
        cache_root = self.state_root / "paired_downloads"
        destination = cache_root / PAIRED_HOST_BRIDGE_ARCHIVE
        signature_path = cache_root / "host_bridge_pairing.sha256"
        source_stat = source.stat()
        signature = hashlib.sha256(
            "\0".join(
                (
                    str(source.resolve()),
                    str(source_stat.st_size),
                    str(source_stat.st_mtime_ns),
                    server_url,
                    fingerprint,
                    token,
                )
            ).encode("utf-8")
        ).hexdigest()
        with self._host_bridge_pairing_lock:
            if (
                destination.is_file()
                and optional_text(signature_path).strip() == signature
            ):
                return destination
            build_paired_host_bridge_archive(
                source,
                destination,
                server_url=server_url,
                certificate_fingerprint=fingerprint,
                upload_token=token,
            )
            atomic_write_text(signature_path, signature + "\n", mode=0o600)
        return destination

    def calibration_steps_payload(self) -> dict[str, Any]:
        navigation_enabled = bool(
            self.config.get("carrier_navigation_enabled", False)
        )
        value: dict[str, Any] = {}
        for action_type, command_path in self.profile_paths.items():
            command_profile = optional_json(command_path)
            open_path = self.navigation_profile_paths.get(action_type)
            open_required = navigation_enabled and open_path is not None
            open_profile = optional_json(open_path) if open_path is not None else None
            intermediate_path = self.intermediate_profile_paths.get(action_type)
            intermediate_required = (
                navigation_enabled and intermediate_path is not None
            )
            intermediate_profile = (
                optional_json(intermediate_path)
                if intermediate_path is not None
                else None
            )
            steps = {
                "open": {
                    "required": open_required,
                    "calibrated": bool(open_profile),
                    "path": str(open_path) if open_path is not None else None,
                    "profile": open_profile,
                },
                "command": {
                    "required": True,
                    "calibrated": bool(command_profile),
                    "path": str(command_path),
                    "profile": command_profile,
                },
            }
            if intermediate_path is not None:
                steps["replace"] = {
                    "required": intermediate_required,
                    "calibrated": bool(intermediate_profile),
                    "path": str(intermediate_path),
                    "profile": intermediate_profile,
                }
            required_step_count = (
                1 + int(open_required) + int(intermediate_required)
            )
            calibrated_step_count = int(bool(command_profile))
            if open_required:
                calibrated_step_count += int(bool(open_profile))
            if intermediate_required:
                calibrated_step_count += int(bool(intermediate_profile))
            value[action_type] = {
                "steps": steps,
                "required_step_count": required_step_count,
                "calibrated_step_count": calibrated_step_count,
                "sequence_ready": bool(command_profile)
                and (not open_required or bool(open_profile))
                and (
                    not intermediate_required
                    or bool(intermediate_profile)
                ),
            }
        return value

    def status(self) -> dict[str, Any]:
        self.reload_config()
        host_ip = str(self.config.get("host_ip", "")).strip() or None
        discovery = discover_session(
            self.config.get("process_names", ["stellaris"]),
            transport_process_names=self.config.get(
                "transport_process_names", ["steam"]
            ),
            host_ip=host_ip,
            telemetry_path=self.telemetry_path,
            passive_telemetry_path=self.passive_telemetry_path,
        )
        with self._conversation_lock:
            conversation_state = self.conversation_store.public_state()
            conversation_catalog = self.conversation_catalog_payload(
                include_archived=True
            )
            latest_run = self.run_summary()
            save_ingest = self.save_ingest_status()
            autonomy_mode = self._active_autonomy_mode()
            with self._job_lock:
                job = dict(self._job)
                autonomy_status = dict(self._autonomy_probe)
        calibration_steps = self.calibration_steps_payload()
        construction_catalog = build_construction_catalog(
            read_json(CAPABILITIES_PATH),
            (latest_run or {}).get("candidates"),
            (latest_run or {}).get("manifest"),
            calibration_steps,
        )
        return {
            "schema": "iag.console_status.v6",
            "observed_at": now_iso(),
            "network": discovery,
            "save": self.save_summary(),
            "save_ingest": save_ingest,
            "latest_run": latest_run,
            "calibration": optional_json(self.profile_path),
            "calibrations": {
                action_type: optional_json(profile_path)
                for action_type, profile_path in self.profile_paths.items()
            },
            "calibration_steps": calibration_steps,
            "construction_catalog": construction_catalog,
            "instruction": optional_text(self.instruction_path),
            "model": self.public_model_config(),
            "execution_settings": self.public_execution_settings(),
            "strategy": self.strategy_payload(),
            "conversation": conversation_state,
            "conversations": conversation_catalog,
            "campaign_binding": conversation_catalog["binding"],
            "autonomy": {
                **autonomy_status,
                "mode": autonomy_mode,
            },
            "job": job,
            "emergency_stop_requested": self.stop_path.is_file(),
        }

    def conversation_payload(
        self,
        *,
        after_id: int = 0,
        limit: int = 250,
    ) -> dict[str, Any]:
        with self._conversation_lock:
            store = self.conversation_store
            return {
                "schema": "iag.conversation.v2",
                "conversation_id": store.conversation_id,
                "messages": store.public_messages(
                    after_id=after_id,
                    limit=limit,
                ),
                "state": store.public_state(),
                "binding": self.campaign_binding_status(store),
            }

    def save_autonomy_settings(
        self,
        mode: str,
        interval_months: int,
    ) -> dict[str, Any]:
        if mode not in {"paused", "advisory", "execute"}:
            raise ConsoleError("自动巡检模式必须是 paused、advisory 或 execute。")
        with self._conversation_lock:
            self._ensure_no_running_job()
            self.reload_config()
            candidate_config = {
                **self.config,
                "save_review_interval_months": interval_months,
            }
            interval = review_interval_months(candidate_config)
            if mode == "execute":
                self._require_active_campaign()
            self.conversation_store.set_state("autonomy_mode", mode)
            self.conversation_store.set_state(
                "review_interval_months",
                interval,
            )
            next_review = self.conversation_store.get_state("next_review", None)
            if isinstance(next_review, dict):
                source_index = next_review.get("source_month_index")
                if source_index is not None:
                    next_review["next_review_months"] = interval
                    next_review["due_month_index"] = int(source_index) + interval
                    self.conversation_store.set_state("next_review", next_review)
            return {
                "saved": True,
                "conversation_id": self.conversation_store.conversation_id,
                "mode": mode,
                "review_interval_months": interval,
                "saved_at": now_iso(),
            }

    def _conversation_action(
        self,
        *,
        agent: ConversationAgent,
        store: ConversationStore,
        trigger: str,
        user_content: str | None,
        mode: str,
        source_identity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous_next_review = store.get_state("next_review", None)
        try:
            return agent.run_turn(
                trigger=trigger,
                user_content=user_content,
                autonomy_mode=mode,
            )
        except Exception as error:
            store.append(
                "system",
                f"{type(error).__name__}: {error}",
                kind="error",
                visible=True,
                metadata={"success": False, "trigger": trigger},
            )
            raise
        finally:
            if source_identity is not None:
                store.set_state(
                    "last_autonomy_source",
                    source_identity,
                )
            try:
                coalescing = coalesce_next_review_after_turn(
                    self.config,
                    store,
                    previous_next_review,
                )
            except Exception as error:
                coalescing = {
                    "changed": False,
                    "reason": f"coalescing_error:{type(error).__name__}",
                }
            store.set_state("last_review_coalescing", coalescing)

    def start_chat(self, text: str) -> dict[str, Any]:
        with self._conversation_lock:
            self.reload_config()
            store = self.conversation_store
            agent = self.conversation_agent
            mode = self._active_autonomy_mode(store)
            return self._start_job(
                "chat",
                lambda: self._conversation_action(
                    agent=agent,
                    store=store,
                    trigger="chat",
                    user_content=text,
                    mode=mode,
                ),
                conversation_id=store.conversation_id,
            )

    def start_review(self) -> dict[str, Any]:
        with self._conversation_lock:
            self.reload_config()
            store = self.conversation_store
            agent = self.conversation_agent
            self._require_active_campaign(store)
            mode = self._active_autonomy_mode(store)
            return self._start_job(
                "manual_review",
                lambda: self._conversation_action(
                    agent=agent,
                    store=store,
                    trigger="manual_review",
                    user_content=None,
                    mode=mode,
                ),
                conversation_id=store.conversation_id,
            )

    def _autonomy_loop(self) -> None:
        while True:
            try:
                self.reload_config()
                interval = max(
                    5,
                    min(int(self.config.get("autonomy_poll_seconds", 15)), 300),
                )
            except Exception:
                interval = 15
            if self._autonomy_stop.wait(interval):
                return
            try:
                self.reload_config()
                with self._conversation_lock:
                    store = self.conversation_store
                    agent = self.conversation_agent
                    probe = autonomy_probe(self.config, store)
                    with self._job_lock:
                        self._autonomy_probe = probe
                        busy = self._job.get("state") == "running"
                    if not probe.get("due") or busy:
                        continue
                    mode = self._active_autonomy_mode(store)
                    source_identity = probe.get("save")
                    self._start_job(
                        "autonomous",
                        lambda mode=mode, source_identity=source_identity: (
                            self._conversation_action(
                                agent=agent,
                                store=store,
                                trigger="autonomous",
                                user_content=None,
                                mode=mode,
                                source_identity=source_identity,
                            )
                        ),
                        conversation_id=store.conversation_id,
                    )
            except ConsoleError:
                continue
            except Exception as error:
                traceback.print_exc()
                with self._job_lock:
                    self._autonomy_probe = {
                        "enabled": True,
                        "mode": self._active_autonomy_mode(),
                        "due": False,
                        "reason": f"scheduler_error:{type(error).__name__}",
                    }

    def close(self) -> None:
        self._autonomy_stop.set()
        self._scheduler_thread.join(timeout=3)

    def capture(self) -> dict[str, Any]:
        return capture_calibration(
            self.capture_root,
            display=str(self.config.get("display", ":1")),
            xauthority=self.config.get("xauthority"),
            title_regex=str(self.config.get("stellaris_window_regex", "Stellaris")),
        )

    def calibration_profile_path(
        self,
        action_type: str,
        stage: str = "command",
    ) -> Path:
        if stage == "open":
            path = self.navigation_profile_paths.get(action_type)
            if path is None:
                raise ConsoleError(f"{action_type} 没有面板入口校准步骤。")
            return path
        if stage == "replace":
            path = self.intermediate_profile_paths.get(action_type)
            if path is None:
                raise ConsoleError(f"{action_type} 没有替换按钮校准步骤。")
            return path
        if stage != "command":
            raise ConsoleError(f"不支持的载体校准阶段：{stage}")
        try:
            return self.profile_paths[action_type]
        except KeyError as error:
            raise ConsoleError(f"不支持的载体校准类型：{action_type}") from error

    def commit_capture(
        self,
        capture_id: str,
        x_ratio: float,
        y_ratio: float,
        action_type: str = "build_building",
        stage: str = "command",
    ) -> dict[str, Any]:
        if not CAPTURE_ID_RE.fullmatch(capture_id):
            raise ConsoleError("无效的校准截图编号。")
        profile = commit_calibration(
            self.capture_root / f"{capture_id}.json",
            self.calibration_profile_path(action_type, stage),
            x_ratio=x_ratio,
            y_ratio=y_ratio,
        )
        return {**profile, "action_type": action_type, "stage": stage}

    def test_calibration(
        self,
        action_type: str = "build_building",
        stage: str = "command",
    ) -> dict[str, Any]:
        return execute_fixed_click(
            self.calibration_profile_path(action_type, stage),
            display=str(self.config.get("display", ":1")),
            xauthority=self.config.get("xauthority"),
            artifact_root=self.capture_root,
            move_only=True,
        )

    def capture_image(self, capture_id: str) -> Path:
        if not CAPTURE_ID_RE.fullmatch(capture_id):
            raise ConsoleError("无效的校准截图编号。")
        path = (self.capture_root / f"{capture_id}.png").resolve()
        if not path.is_file() or path.parent != self.capture_root.resolve():
            raise ConsoleError("校准截图不存在。")
        return path

    def _start_job(
        self,
        kind: str,
        action: Callable[[], Any],
        *,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        with self._job_lock:
            if self._job.get("state") == "running":
                raise ConsoleError("已有一个代理任务正在运行。")
            self._job = {
                "state": "running",
                "kind": kind,
                "conversation_id": conversation_id,
                "message": "",
                "started_at": now_iso(),
                "finished_at": None,
            }

        def worker() -> None:
            try:
                result = action()
                if isinstance(result, dict):
                    message = str(
                        result.get("final_content")
                        or result.get("run_id")
                        or result.get("schema")
                        or "completed"
                    )
                else:
                    message = str(result)
                state = "completed"
            except Exception as error:
                traceback.print_exc()
                message = f"{type(error).__name__}: {error}"
                state = "failed"
            with self._job_lock:
                self._job.update(
                    {
                        "state": state,
                        "message": message,
                        "finished_at": now_iso(),
                    }
                )

        threading.Thread(
            target=worker,
            name=f"iag-{kind}",
            daemon=True,
        ).start()
        return dict(self._job)

    def start_plan(self) -> dict[str, Any]:
        with self._conversation_lock:
            self.reload_config()
            store = self.conversation_store
            save_path = self._require_active_campaign(store)

            def plan_action() -> dict[str, Any]:
                run_dir = run_cycle(
                    self.config_path,
                    save_path=save_path,
                    run_root=self.runs_root,
                    plan_path=None,
                )
                store.set_state("latest_run_id", run_dir.name)
                return {"run_id": run_dir.name, "path": str(run_dir)}

            return self._start_job(
                "plan",
                plan_action,
                conversation_id=store.conversation_id,
            )

    def start_execute(self, run_id: str | None) -> dict[str, Any]:
        with self._conversation_lock:
            self.reload_config()
            store = self.conversation_store
            self._require_active_campaign(store)
            active_run_id = store.get_state("latest_run_id", None)
            selected_run_id = run_id or active_run_id
            if not isinstance(selected_run_id, str) or not re.fullmatch(
                r"[0-9]{8}_[0-9]{6}(?:_[0-9]{2})?",
                selected_run_id,
            ):
                raise ConsoleError("没有可执行的规划。")
            if active_run_id and selected_run_id != active_run_id:
                raise ConsoleError("该规划不属于当前战役会话。")
            run_dir = self.runs_root / selected_run_id
            if not run_dir.is_dir():
                raise ConsoleError("没有可执行的规划。")
            return self._start_job(
                "execute",
                lambda: execute_run(run_dir, self.config_path),
                conversation_id=store.conversation_id,
            )

    def emergency_stop(self) -> dict[str, Any]:
        atomic_write_text(self.stop_path, now_iso() + "\n")
        return {"requested": True, "requested_at": now_iso()}

    def clear_emergency_stop(self) -> dict[str, Any]:
        """Release the persistent execution interlock after work has stopped."""
        with self._job_lock:
            if self._job.get("state") == "running":
                raise ConsoleError(
                    "当前代理任务仍在运行；请等待其中止完成后再解除紧急停止。"
                )
            was_requested = self.stop_path.is_file()
            self.stop_path.unlink(missing_ok=True)
        return {
            "requested": False,
            "cleared": was_requested,
            "cleared_at": now_iso(),
        }


class ConsoleHandler(BaseHTTPRequestHandler):
    service: ConsoleService

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[iag-web] {self.address_string()} {format_string % args}")

    def send_bytes(
        self,
        payload: bytes,
        content_type: str,
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def is_authorized(self) -> bool:
        auth = self.service.frontend_auth()
        if not auth["enabled"]:
            return True
        return basic_auth_matches(
            self.headers.get("Authorization"),
            auth["username"],
            auth["password"],
        )

    def send_unauthorized(self) -> None:
        self.send_bytes(
            b'{"error":"authentication_required"}',
            "application/json; charset=utf-8",
            401,
            {"WWW-Authenticate": 'Basic realm="IAG Console", charset="UTF-8"'},
        )
    def send_upload_unauthorized(self) -> None:
        self.send_bytes(
            b'{"error":"upload_authentication_required"}',
            "application/json; charset=utf-8",
            401,
            {"WWW-Authenticate": 'Bearer realm="IAG Save Upload"'},
        )

    def send_json(self, value: Any, status: int = 200) -> None:
        self.send_bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def request_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ConsoleError("无效的请求长度。") from error
        if length > 100_000:
            raise ConsoleError("请求内容过大。")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ConsoleError("请求必须是 UTF-8 JSON。") from error
        if not isinstance(value, dict):
            raise ConsoleError("请求 JSON 必须是对象。")
        return value

    def static_file(self, relative: str) -> Path:
        target = (STATIC_ROOT / relative).resolve()
        if not target.is_file() or not target.is_relative_to(STATIC_ROOT.resolve()):
            raise FileNotFoundError(relative)
        return target

    def send_file(self, path: Path, *, head_only: bool = False) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"

        stat = path.stat()
        size = stat.st_size
        try:
            byte_range = parse_single_byte_range(self.headers.get("Range"), size)
        except RangeNotSatisfiable:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        start, end = byte_range if byte_range is not None else (0, size - 1)
        length = 0 if size == 0 else end - start + 1
        self.send_response(206 if byte_range is not None else 200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if byte_range is not None:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if path.suffix.lower() == ".zip":
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{path.name}"',
            )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'",
        )
        self.end_headers()
        if head_only or length == 0:
            return

        try:
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # Browsers commonly close a probe or superseded download connection.
            return

    def do_HEAD(self) -> None:
        if not self.is_authorized():
            self.send_unauthorized()
            return
        try:
            path = unquote(urlparse(self.path).path)
            if path == f"/downloads/{PAIRED_HOST_BRIDGE_ARCHIVE}":
                self.send_file(
                    self.service.paired_host_bridge_archive(
                        pairing_server_url(self.headers.get("Host", ""))
                    ),
                    head_only=True,
                )
                return
            if path.startswith("/api/calibration/image/"):
                capture_id = path.rsplit("/", 1)[-1]
                self.send_file(
                    self.service.capture_image(capture_id),
                    head_only=True,
                )
                return
            if path.startswith("/api/"):
                self.send_bytes(
                    b"",
                    "application/json; charset=utf-8",
                    status=405,
                    extra_headers={"Allow": "GET, POST"},
                )
                return
            relative = "index.html" if path == "/" else path.lstrip("/")
            self.send_file(self.static_file(relative), head_only=True)
        except FileNotFoundError:
            self.send_bytes(b"", "application/json; charset=utf-8", status=404)
        except Exception:
            self.send_bytes(b"", "application/json; charset=utf-8", status=500)

    def do_GET(self) -> None:
        if not self.is_authorized():
            self.send_unauthorized()
            return
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if path == f"/downloads/{PAIRED_HOST_BRIDGE_ARCHIVE}":
                self.send_file(
                    self.service.paired_host_bridge_archive(
                        pairing_server_url(self.headers.get("Host", ""))
                    )
                )
                return
            if path == "/api/status":
                self.send_json(self.service.status())
                return
            if path == "/api/prompt":
                self.send_json(self.service.prompt_payload())
                return
            if path == "/api/strategy":
                self.send_json(self.service.strategy_payload())
                return
            if path == "/api/conversations":
                include_archived = (
                    query.get("include_archived", ["0"])[0]
                    in {"1", "true", "yes"}
                )
                self.send_json(
                    self.service.conversation_catalog_payload(
                        include_archived=include_archived
                    )
                )
                return
            if path == "/api/conversation":
                try:
                    after_id = int(query.get("after_id", ["0"])[0])
                    limit = int(query.get("limit", ["250"])[0])
                except ValueError as error:
                    raise ConsoleError("无效的会话分页参数。") from error
                self.send_json(
                    self.service.conversation_payload(
                        after_id=after_id,
                        limit=limit,
                    )
                )
                return
            if path.startswith("/api/calibration/image/"):
                capture_id = path.rsplit("/", 1)[-1]
                self.send_file(self.service.capture_image(capture_id))
                return
            relative = "index.html" if path == "/" else path.lstrip("/")
            self.send_file(self.static_file(relative))
        except FileNotFoundError:
            self.send_json({"error": "not_found"}, status=404)
        except Exception as error:
            self.send_json({"error": str(error)}, status=500)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path in {
            "/api/host-executor/ready",
            "/api/host-executor/result",
        }:
            if not self.service.upload_is_authorized(
                self.headers.get("Authorization")
            ):
                self.send_upload_unauthorized()
                return
            try:
                value = self.request_json()
                if path.endswith("/ready"):
                    result = self.service.receive_host_executor_ready(
                        value,
                        source_ip=self.client_address[0],
                    )
                else:
                    result = self.service.receive_host_executor_result(
                        value,
                        source_ip=self.client_address[0],
                    )
                self.send_json(result)
            except (ConsoleError, HostExecutorProtocolError) as error:
                self.send_json({"error": str(error)}, status=409)
            except Exception as error:
                traceback.print_exc()
                self.send_json(
                    {"error": f"{type(error).__name__}: {error}"},
                    status=500,
                )
            return

        if path == "/api/save/client-heartbeat":
            if not self.service.upload_is_authorized(
                self.headers.get("Authorization")
            ):
                self.send_upload_unauthorized()
                return
            try:
                value = self.request_json()
                result = self.service.receive_save_client_heartbeat(
                    value,
                    source_ip=self.client_address[0],
                )
                self.send_json(result)
            except (ConsoleError, SaveIngestError) as error:
                self.send_json({"error": str(error)}, status=400)
            except Exception as error:
                traceback.print_exc()
                self.send_json(
                    {"error": f"{type(error).__name__}: {error}"},
                    status=500,
                )
            return

        if path == "/api/save/upload":
            if not self.service.upload_is_authorized(
                self.headers.get("Authorization")
            ):
                self.send_upload_unauthorized()
                return
            try:
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError as error:
                    raise SaveIngestError("无效的上传长度。") from error
                result = self.service.receive_save_upload(
                    stream=self.rfile,
                    content_length=content_length,
                    headers=self.headers,
                )
                self.send_json(
                    {"accepted": True, **result},
                    status=200 if result.get("duplicate") else 201,
                )
            except SaveIngestError as error:
                self.send_json({"error": str(error)}, status=400)
            except Exception as error:
                traceback.print_exc()
                self.send_json(
                    {"error": f"{type(error).__name__}: {error}"},
                    status=500,
                )
            return

        if not self.is_authorized():
            self.send_unauthorized()
            return
        try:
            value = self.request_json()
            if path == "/api/prompt":
                result = self.service.save_prompt(str(value.get("text", "")))
            elif path == "/api/instruction":
                result = self.service.save_instruction(str(value.get("text", "")))
            elif path == "/api/model":
                result = self.service.save_model_config(value)
            elif path == "/api/execution-settings":
                result = self.service.save_execution_settings(value)
            elif path == "/api/strategy/decade":
                result = self.service.save_decade_plan_text(
                    str(value.get("text", "")),
                    str(value.get("target", "current")),
                )
            elif path == "/api/strategy/emergency/activate":
                result = self.service.activate_strategic_emergency(
                    str(value.get("title", "")),
                    str(value.get("directive", "")),
                )
            elif path == "/api/strategy/emergency/end":
                result = self.service.end_strategic_emergency()
            elif path == "/api/conversations/create":
                result = self.service.create_conversation(
                    str(value.get("title", "")),
                    bind_current=bool(value.get("bind_current", False)),
                )
            elif path == "/api/conversations/switch":
                result = self.service.switch_conversation(
                    str(value.get("conversation_id", ""))
                )
            elif path == "/api/conversations/rename":
                result = self.service.rename_conversation(
                    str(value.get("conversation_id", "")),
                    str(value.get("title", "")),
                )
            elif path == "/api/conversations/archive":
                archived = value.get("archived")
                if not isinstance(archived, bool):
                    raise ConsoleError("archived 必须是布尔值。")
                result = self.service.set_conversation_archived(
                    str(value.get("conversation_id", "")),
                    archived,
                )
            elif path == "/api/conversations/bind-current":
                if "conversation_id" not in value:
                    raise ConsoleError(
                        "必须明确指定目标会话；使用 null 表示解除绑定。"
                    )
                raw_conversation_id = value.get("conversation_id")
                result = self.service.set_current_campaign_binding(
                    (
                        None
                        if raw_conversation_id is None
                        else str(raw_conversation_id)
                    )
                )
            elif path == "/api/conversation/message":
                result = self.service.start_chat(str(value.get("text", "")))
            elif path == "/api/conversation/review":
                result = self.service.start_review()
            elif path == "/api/autonomy":
                result = self.service.save_autonomy_settings(
                    str(value.get("mode", "")),
                    value.get("review_interval_months", 1),
                )
            elif path == "/api/calibration/capture":
                result = self.service.capture()
            elif path == "/api/calibration/commit":
                result = self.service.commit_capture(
                    str(value.get("capture_id", "")),
                    float(value.get("x_ratio")),
                    float(value.get("y_ratio")),
                    str(value.get("action_type", "build_building")),
                    str(value.get("stage", "command")),
                )
            elif path == "/api/calibration/test":
                result = self.service.test_calibration(
                    str(value.get("action_type", "build_building")),
                    str(value.get("stage", "command")),
                )
            elif path == "/api/cycle/plan":
                result = self.service.start_plan()
            elif path == "/api/cycle/execute":
                run_id = value.get("run_id")
                result = self.service.start_execute(str(run_id) if run_id else None)
            elif path == "/api/emergency-stop":
                result = self.service.emergency_stop()
            elif path == "/api/emergency-stop/clear":
                result = self.service.clear_emergency_stop()
            else:
                self.send_json({"error": "not_found"}, status=404)
                return
            self.send_json(result)
        except (ConsoleError, SaveIngestError) as error:
            self.send_json({"error": str(error)}, status=400)
        except Exception as error:
            traceback.print_exc()
            self.send_json(
                {"error": f"{type(error).__name__}: {error}"},
                status=500,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-lan", action="store_true")
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if bool(args.tls_cert) != bool(args.tls_key):
        raise ConsoleError("--tls-cert and --tls-key must be supplied together.")
    service = ConsoleService(
        args.config,
        tls_certificate_path=args.tls_cert,
    )
    validate_bind_security(
        args.host,
        allow_lan=args.allow_lan,
        has_tls=bool(args.tls_cert),
        auth_enabled=service.frontend_auth()["enabled"],
    )
    handler = type("BoundConsoleHandler", (ConsoleHandler,), {"service": service})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    scheme = "http"
    if args.tls_cert and args.tls_key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.tls_cert, args.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    print(f"IAG console listening on {scheme}://{args.host}:{args.port}")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
