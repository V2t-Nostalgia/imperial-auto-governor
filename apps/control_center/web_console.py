#!/usr/bin/env python3
"""Local web console for IAG planning, calibration, telemetry, and execution."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import ssl
import sys
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from apps.control_center.visible_reply_stream import VisibleReplyBroker
from iag.applications.economy_governance.conversation_agent import (
    ConversationAgent,
)
from iag.applications.economy_governance.iag_agent import run_cycle
from iag.applications.economy_governance.planner import (
    CAPABILITIES_PATH,
    read_json,
)
from iag.applications.fleet_operations.agent_tools import (
    FLEET_PERMISSIONS_KEY,
    normalized_permissions,
)
from iag.applications.registry import builtin_application_registry
from iag.applications.save_continuations import (
    continuation_public_summary,
    probe_save_continuations,
    run_save_continuations,
)
from iag.core.autonomy import (
    autonomy_probe,
    coalesce_next_review_after_turn,
    save_identity,
)
from iag.core.campaign_strategy import (
    activate_emergency,
    end_emergency,
    public_strategy_state,
    save_decade_plan,
)
from iag.core.conversation_store import ConversationStore
from iag.core.paths import economy_governance_root
from iag.infrastructure.llm.application_model_profile import (
    ApplicationModelProfile,
)
from iag.infrastructure.llm.model_client import request_body_overrides
from iag.infrastructure.llm.model_pool import ModelEndpoint, ModelPool
from iag.infrastructure.llm.model_pool_runtime import ModelPoolRuntime
from iag.infrastructure.llm.model_templates import (
    apply_model_template,
    public_model_templates,
)
from iag.infrastructure.llm.runtime_config import RuntimeConfig
from iag.infrastructure.research.web_research import DEFAULT_ALLOWED_DOMAINS
from iag.stellaris.execution.fixed_click import (
    capture_calibration,
    commit_calibration,
    execute_fixed_click,
)
from iag.stellaris.execution.host_bridge_pairing import (
    PAIRED_HOST_BRIDGE_ARCHIVE,
    PUBLIC_HOST_BRIDGE_ARCHIVE,
    build_paired_host_bridge_archive,
    certificate_sha256,
    pairing_server_url,
)
from iag.stellaris.execution.host_executor_protocol import (
    HOST_EXECUTOR_CAPABILITY,
    REQUIRED_HOST_EXECUTOR_APP_VERSION,
    HostExecutorProtocolError,
    record_host_ready,
    record_host_result,
    request_for_host_client,
)
from iag.stellaris.execution.iag_supervisor import (
    carrier_click_profile_path,
    carrier_intermediate_profile_path,
    carrier_navigation_profile_path,
    execute_run,
    runtime_path,
)
from iag.stellaris.execution.port_discovery import discover_session
from iag.stellaris.execution.protocol_compatibility_control import (
    ProtocolCompatibilityControl,
    ProtocolCompatibilityError,
)
from iag.stellaris.execution.session_proxy_controller import (
    SessionProxyController,
    SessionProxyError,
)
from iag.stellaris.state.fleet_profiles import extract_fleet_profiles
from iag.stellaris.state.planet_profiles import load_gamestate
from iag.stellaris.state.save_ingest import (
    SaveIngestError,
    maximum_source_save_lag_versions,
    read_campaign_manifest,
    read_manifest,
    receive_uploaded_save,
    resolve_current_save,
    review_interval_months,
    upload_token_path,
)
from iag.stellaris.state.save_ingest import (
    bearer_token_matches as upload_bearer_token_matches,
)

ROOT = Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
STATIC_ROOT = RESOURCE_ROOT / "web"
CAPTURE_ID_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_[a-f0-9]{8}$")

DEFAULT_STRATEGIC_PROMPT_PATH = (
    economy_governance_root()
    / "prompts"
    / "grey_tempest_conversation_zh.md"
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
        self.runtime_config = RuntimeConfig.load(self.config_path)
        runtime_snapshot = self.runtime_config.snapshot()
        self.application_registry = builtin_application_registry()
        self.config = runtime_snapshot.settings
        self.model_pool_runtimes = {
            pool.pool_id: ModelPoolRuntime(pool)
            for pool in runtime_snapshot.model_pools
        }
        self.model_pool_runtime = ModelPoolRuntime(runtime_snapshot.model_pool)
        self.runtime_root = Path(self.config["runtime_root"]).expanduser()
        self.operator_root = self.runtime_root / "operator"
        self.state_root = self.runtime_root / "state"
        self.tls_certificate_path = (
            tls_certificate_path.resolve()
            if tls_certificate_path is not None
            else None
        )
        self._host_bridge_pairing_lock = threading.Lock()
        self.visible_reply_broker = VisibleReplyBroker()
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
        self.overlay_access_token_path = runtime_path(
            self.config,
            "overlay_access_token_file",
            self.runtime_root / "secrets" / "overlay_access_token",
        )
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
        self._scheduler_wakeup = threading.Event()
        self._autonomy_probe: dict[str, Any] = {
            "enabled": False,
            "mode": self._active_autonomy_mode(),
            "due": False,
            "reason": "starting",
        }
        self._save_continuation_probe: dict[str, Any] = {
            "due": False,
            "blocks_autonomy": False,
            "reason": "starting",
        }
        self._job_lock = threading.Lock()
        self._save_upload_lock = threading.Lock()
        self._save_client_lock = threading.Lock()
        self._fleet_cache_lock = threading.Lock()
        self._fleet_cache_key: tuple[str, int, int] | None = None
        self._fleet_cache: dict[str, Any] | None = None
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
        if not self.overlay_access_token_path.exists():
            atomic_write_text(
                self.overlay_access_token_path,
                secrets.token_urlsafe(32) + "\n",
                mode=0o600,
            )
        self.protocol_compatibility = ProtocolCompatibilityControl(
            self.runtime_root,
            lambda: dict(self.config),
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
            snapshot = self.runtime_config.snapshot()
            self.config = snapshot.settings
            self.model_pool_runtime.replace_pool(snapshot.model_pool)

    def _sync_model_pool_runtimes(
        self,
        *,
        reset_health: bool = False,
    ) -> None:
        """Align transient health state with the persisted pool catalog."""
        snapshot = self.runtime_config.snapshot()
        previous = getattr(self, "model_pool_runtimes", {})
        next_runtimes: dict[str, ModelPoolRuntime] = {}
        for pool in snapshot.model_pools:
            runtime = previous.get(pool.pool_id)
            if runtime is None:
                runtime = ModelPoolRuntime(pool)
            else:
                runtime.replace_pool(pool, reset_health=reset_health)
            next_runtimes[pool.pool_id] = runtime
        self.model_pool_runtimes = next_runtimes
        self.model_pool_runtime.replace_pool(
            snapshot.model_pool,
            reset_health=reset_health,
        )
        self.config = snapshot.settings

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
        self.conversation_agent = ConversationAgent(
            self.runtime_config,
            store,
            model_pool_runtime=self.model_pool_runtime,
        )
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
        runtime = self.runtime_config.snapshot()
        endpoint = runtime.endpoint
        active_pool = runtime.model_pool
        active_profile = runtime.application_profile
        options = runtime.request_options
        config = runtime.settings
        public_pools: list[dict[str, Any]] = []
        health_catalog: dict[str, dict[str, Any]] = {}
        for pool in runtime.model_pools:
            pool_runtime = self.model_pool_runtimes.get(pool.pool_id)
            pool_health = (
                pool_runtime.status()
                if pool_runtime is not None
                else {
                    "pool_id": pool.pool_id,
                    "last_selected_endpoint_id": None,
                    "endpoints": [],
                }
            )
            health_catalog[pool.pool_id] = pool_health
            health_by_id = {
                str(item.get("endpoint_id")): item
                for item in pool_health.get("endpoints", [])
                if isinstance(item, dict)
            }
            public_endpoints = []
            for item in pool.endpoints:
                public_value = item.model_dump(
                    mode="json",
                    exclude={"api_key"},
                )
                public_value["base_url"] = str(item.base_url).rstrip("/")
                public_value["api_key_configured"] = item.api_key is not None
                public_value["health"] = health_by_id.get(
                    item.endpoint_id,
                    {"status": "unknown", "eligible": item.enabled},
                )
                public_endpoints.append(public_value)
            public_pools.append(
                {
                    "pool_id": pool.pool_id,
                    "display_name": pool.display_name,
                    "model_ids": list(pool.model_ids()),
                    "endpoints": public_endpoints,
                }
            )
        active_public_pool = next(
            item for item in public_pools if item["pool_id"] == active_pool.pool_id
        )
        active_endpoint_ids = {
            item.endpoint_id for item in active_pool.endpoints
        }
        active_public_endpoints = [
            item
            for item in active_public_pool["endpoints"]
            if item["endpoint_id"] in active_endpoint_ids
        ]
        active_pool_health = health_catalog.get(active_pool.pool_id, {})
        thinking = options.get("thinking")
        thinking_enabled = (
            isinstance(thinking, dict)
            and str(thinking.get("type", "")).lower() == "enabled"
        )
        return {
            "schema": "iag.model_configuration.v2",
            "applications": [
                manifest.model_dump(mode="json")
                for manifest in self.application_registry.all()
            ],
            "model_pools": public_pools,
            "application_model_profiles": [
                {
                    **profile.model_dump(
                        mode="json",
                        exclude={"application_options"},
                    ),
                    "application_options": {
                        key: value
                        for key, value in profile.application_options.items()
                        if key != "crawl4ai_api_token_file"
                    },
                    "crawl4ai_api_token_configured": bool(
                        profile.application_options.get(
                            "crawl4ai_api_token_file"
                        )
                    ),
                }
                for profile in runtime.application_model_profiles
            ],
            "application_model_bindings": runtime.application_model_bindings,
            "active_application_id": runtime.application_id,
            "active_profile_id": active_profile.profile_id,
            "template_id": config.get("model_template_id", "custom"),
            "model_pool": {
                "pool_id": active_pool.pool_id,
                "display_name": active_pool.display_name,
                "model_id": active_profile.model_id,
                "endpoints": active_public_endpoints,
            },
            "model_pool_health": active_pool_health,
            "model_pool_health_catalog": health_catalog,
            "endpoint_id": endpoint.endpoint_id,
            "priority": endpoint.priority,
            "enabled": endpoint.enabled,
            "model_transport": endpoint.model_transport,
            "provider": endpoint.provider,
            "base_url": str(endpoint.base_url).rstrip("/"),
            "model": endpoint.model,
            "thinking_enabled": thinking_enabled,
            "reasoning_effort": options.get("reasoning_effort"),
            "temperature": options.get("temperature", 0.15),
            "timeout_seconds": endpoint.timeout_seconds,
            "probe_timeout_seconds": endpoint.probe_timeout_seconds,
            "models_path": endpoint.models_path,
            "rate_limit_cooldown_seconds": (
                endpoint.rate_limit_cooldown_seconds
            ),
            "sdk_max_retries": endpoint.sdk_max_retries,
            "request_body_overrides": request_body_overrides(options),
            "model_context_window_tokens": endpoint.model_context_window_tokens,
            "context_output_reserve_tokens": endpoint.max_output_tokens,
            "context_compression_enabled": bool(
                config.get("context_compression_enabled", True)
            ),
            "context_compression_trigger_percent": float(
                config.get("context_compression_trigger_percent", 80)
            ),
            "context_compression_target_percent": float(
                config.get("context_compression_target_percent", 35)
            ),
            "web_research_enabled": bool(
                config.get("web_research_enabled", False)
            ),
            "searxng_url": config.get(
                "searxng_url", "http://127.0.0.1:8080"
            ),
            "crawl4ai_url": config.get(
                "crawl4ai_url", "http://127.0.0.1:11235"
            ),
            "stellaris_wiki_api_url": config.get(
                "stellaris_wiki_api_url",
                "https://stellaris.paradoxwikis.com/api.php",
            ),
            "web_search_allowed_domains": config.get(
                "web_search_allowed_domains", DEFAULT_ALLOWED_DOMAINS
            ),
            "web_fetch_direct_fallback_enabled": bool(
                config.get("web_fetch_direct_fallback_enabled", False)
            ),
            "crawl4ai_api_token_configured": bool(
                config.get("crawl4ai_api_token_file")
                and runtime_path(
                    config,
                    "crawl4ai_api_token_file",
                    self.runtime_root / "secrets" / "crawl4ai_api_token",
                ).is_file()
            ),
            "tool_calling_enabled": endpoint.supports_tools and bool(
                config.get("tool_calling_enabled", True)
            ),
            "conversation_supported": (
                any(
                    item.enabled
                    and item.provider == "chat_completions_compatible"
                    and item.supports_tools
                    for item in active_pool.endpoints
                )
                and bool(config.get("tool_calling_enabled", True))
            ),
            "api_key_configured": endpoint.api_key is not None,
            "configured_endpoint_count": sum(
                item.api_key is not None or item.auth_mode == "none"
                for pool in runtime.model_pools
                for item in pool.endpoints
            ),
            "configured_endpoint_total": sum(
                len(pool.endpoints) for pool in runtime.model_pools
            ),
            "templates": public_model_templates(),
        }

    def _save_model_config_legacy(self, value: dict[str, Any]) -> dict[str, Any]:
        template_id = str(value.get("template_id", "custom")).strip() or "custom"
        pool_payload = value.get("model_pool")
        pool_mode = isinstance(pool_payload, dict)
        try:
            current = self.public_model_config()
            updated = (
                dict(current)
                if pool_mode
                else apply_model_template(current, template_id)
            )
        except ValueError as error:
            raise ConsoleError(str(error)) from error
        if template_id == "custom" and not pool_mode:
            updated["model_transport"] = str(
                value.get("model_transport", "openai_sdk")
            ).strip()
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
            sdk_max_retries = int(value.get("sdk_max_retries", 2))
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
        if not 0 <= sdk_max_retries <= 10:
            raise ConsoleError("SDK 自动重试次数必须在 0 到 10 之间。")
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
                "sdk_max_retries": sdk_max_retries,
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
        runtime = self.runtime_config.snapshot()
        if pool_mode:
            raw_endpoints = pool_payload.get("endpoints")
            if not isinstance(raw_endpoints, list) or not raw_endpoints:
                raise ConsoleError("模型池至少需要一个端点。")
            if len(raw_endpoints) > 32:
                raise ConsoleError("一个模型池最多配置 32 个端点。")
            existing = {
                item.endpoint_id: item for item in runtime.model_pool.endpoints
            }
            parsed_endpoints: list[ModelEndpoint] = []
            endpoint_fields = set(ModelEndpoint.model_fields)
            for raw_endpoint in raw_endpoints:
                if not isinstance(raw_endpoint, dict):
                    raise ConsoleError("模型端点必须是 JSON 对象。")
                endpoint_id = str(
                    raw_endpoint.get("endpoint_id", "")
                ).strip()
                if not endpoint_id:
                    raise ConsoleError("模型端点标识不能为空。")
                previous = existing.get(endpoint_id)
                endpoint_value = (
                    previous.model_dump(mode="python")
                    if previous is not None
                    else {
                        "endpoint_id": endpoint_id,
                        "auth_mode": "bearer",
                        "api_key": None,
                        "chat_completions_path": "/chat/completions",
                        "responses_path": "/responses",
                        "models_path": "/models",
                        "extra_headers": {},
                        "api_key_header": "Authorization",
                        "api_key_prefix": "Bearer ",
                    }
                )
                endpoint_value.update(
                    {
                        key: raw_endpoint[key]
                        for key in endpoint_fields
                        if key in raw_endpoint and key != "api_key"
                    }
                )
                endpoint_value["endpoint_id"] = endpoint_id
                supplied_key = str(raw_endpoint.get("api_key", "")).strip()
                if supplied_key:
                    endpoint_value["api_key"] = supplied_key
                elif bool(raw_endpoint.get("clear_api_key", False)):
                    endpoint_value["api_key"] = None
                try:
                    parsed_endpoints.append(
                        ModelEndpoint.model_validate(endpoint_value)
                    )
                except ValueError as error:
                    raise ConsoleError(
                        f"端点 {endpoint_id} 配置无效：{error}"
                    ) from error
            try:
                model_pool = ModelPool(
                    pool_id=str(pool_payload.get("pool_id", "")).strip(),
                    endpoints=parsed_endpoints,
                )
            except ValueError as error:
                raise ConsoleError(f"模型池配置无效：{error}") from error
        else:
            model_transport = str(
                updated.get("model_transport", "openai_sdk")
            ).strip()
            if model_transport not in {"openai_sdk", "raw_http"}:
                raise ConsoleError("不支持的模型传输实现。")
            provider = str(updated.get("provider", "")).strip()
            if provider not in {
                "responses_compatible",
                "chat_completions_compatible",
            }:
                raise ConsoleError("不支持的 API 协议类型。")
            base_url = str(updated.get("base_url", "")).strip()
            if not base_url.startswith(("http://", "https://")):
                raise ConsoleError("Base URL 必须以 http:// 或 https:// 开头。")
            model = str(updated.get("model", "")).strip()
            if not model:
                raise ConsoleError("模型名称不能为空。")
            endpoint_value = runtime.endpoint.model_dump(mode="python")
            endpoint_value.update(
                {
                    "endpoint_id": (
                        runtime.endpoint.endpoint_id
                        if template_id == "custom"
                        else template_id
                    ),
                    "model_transport": model_transport,
                    "provider": provider,
                    "base_url": base_url,
                    "model": model,
                    "chat_completions_path": str(
                        updated.get(
                            "chat_completions_path",
                            runtime.endpoint.chat_completions_path,
                        )
                    ),
                    "responses_path": str(
                        updated.get(
                            "responses_path",
                            runtime.endpoint.responses_path,
                        )
                    ),
                    "supports_reasoning": bool(
                        updated.get(
                            "supports_reasoning",
                            runtime.endpoint.supports_reasoning,
                        )
                    ),
                    "model_context_window_tokens": context_maximum,
                    "max_output_tokens": output_reserve,
                    "timeout_seconds": timeout_seconds,
                    "sdk_max_retries": sdk_max_retries,
                    "supports_tools": bool(
                        updated.get("tool_calling_enabled", True)
                    ),
                }
            )
            api_key = str(value.get("api_key", "")).strip()
            if api_key:
                endpoint_value["api_key"] = api_key
            try:
                endpoint = ModelEndpoint.model_validate(endpoint_value)
                model_pool = ModelPool(
                    pool_id=runtime.model_pool.pool_id,
                    endpoints=[endpoint],
                )
            except ValueError as error:
                raise ConsoleError(str(error)) from error

        options = dict(runtime.request_options)
        options.update(
            {
                "temperature": temperature,
                "request_body_overrides": validated_overrides,
            }
        )
        thinking = updated.get("thinking")
        if isinstance(thinking, dict):
            options["thinking"] = thinking
            options["reasoning_effort"] = str(
                updated.get("reasoning_effort", "high")
            )
        else:
            options.pop("thinking", None)
            options.pop("reasoning_effort", None)

        application_changes = {
            "model_template_id": template_id,
            "tool_calling_enabled": bool(
                updated.get("tool_calling_enabled", True)
            ),
            "context_compression_enabled": bool(
                updated.get("context_compression_enabled", True)
            ),
            "context_compression_trigger_percent": trigger_percent,
            "context_compression_target_percent": target_percent,
            **service_fields,
            "web_research_enabled": bool(
                updated.get("web_research_enabled", False)
            ),
            "web_search_allowed_domains": allowed_domains,
            "web_fetch_direct_fallback_enabled": bool(
                updated.get("web_fetch_direct_fallback_enabled", False)
            ),
        }
        if crawl_token:
            application_changes["crawl4ai_api_token_file"] = updated[
                "crawl4ai_api_token_file"
            ]
        with self._config_lock:
            self.runtime_config.update(
                model_pool=model_pool,
                request_options=options,
                settings=application_changes,
            )
            self.runtime_config.save()
            snapshot = self.runtime_config.snapshot()
            self.config = snapshot.settings
            self.model_pool_runtime.replace_pool(
                snapshot.model_pool,
                reset_health=True,
            )
            return self.public_model_config()

    def _probe_model_pool_legacy(self, value: dict[str, Any]) -> dict[str, Any]:
        endpoint_id = str(value.get("endpoint_id", "")).strip()
        try:
            results = (
                [self.model_pool_runtime.probe(endpoint_id)]
                if endpoint_id
                else self.model_pool_runtime.probe_all()
            )
        except KeyError as error:
            raise ConsoleError(str(error)) from error
        return {
            "results": results,
            "model_pool_health": self.model_pool_runtime.status(),
        }

    def _parse_model_endpoint(
        self,
        raw_endpoint: dict[str, Any],
        previous: ModelEndpoint | None,
    ) -> ModelEndpoint:
        endpoint_id = str(raw_endpoint.get("endpoint_id", "")).strip()
        if not endpoint_id:
            raise ConsoleError("模型端点标识不能为空。")
        endpoint_value = (
            previous.model_dump(mode="python")
            if previous is not None
            else {
                "endpoint_id": endpoint_id,
                "display_name": endpoint_id,
                "model_id": str(raw_endpoint.get("model_id", "")).strip(),
                "model": str(raw_endpoint.get("model", "")).strip(),
                "model_transport": "openai_sdk",
                "provider": "chat_completions_compatible",
                "base_url": "https://api.example.com/v1",
                "supports_reasoning": False,
                "model_context_window_tokens": 128_000,
                "auth_mode": "bearer",
                "api_key": None,
                "max_output_tokens": 8_192,
                "priority": 0,
                "enabled": False,
                "supports_tools": True,
            }
        )
        endpoint_fields = set(ModelEndpoint.model_fields)
        endpoint_value.update(
            {
                key: raw_endpoint[key]
                for key in endpoint_fields
                if key in raw_endpoint and key != "api_key"
            }
        )
        endpoint_value["endpoint_id"] = endpoint_id
        supplied_key = str(raw_endpoint.get("api_key", "")).strip()
        if supplied_key:
            endpoint_value["api_key"] = supplied_key
        elif bool(raw_endpoint.get("clear_api_key", False)):
            endpoint_value["api_key"] = None
        try:
            return ModelEndpoint.model_validate(endpoint_value)
        except ValueError as error:
            raise ConsoleError(
                f"端点 {endpoint_id} 配置无效：{error}"
            ) from error

    def save_model_pools(self, value: dict[str, Any]) -> dict[str, Any]:
        raw_pools = value.get("model_pools")
        if not isinstance(raw_pools, list) or not raw_pools:
            raise ConsoleError("至少需要保留一个模型池。")
        if len(raw_pools) > 32:
            raise ConsoleError("最多保存 32 个模型池。")
        snapshot = self.runtime_config.snapshot()
        existing_pools = {pool.pool_id: pool for pool in snapshot.model_pools}
        parsed_pools: list[ModelPool] = []
        for raw_pool in raw_pools:
            if not isinstance(raw_pool, dict):
                raise ConsoleError("模型池必须是 JSON 对象。")
            pool_id = str(raw_pool.get("pool_id", "")).strip()
            display_name = str(raw_pool.get("display_name", "")).strip()
            raw_endpoints = raw_pool.get("endpoints", [])
            if not pool_id or not display_name:
                raise ConsoleError("模型池标识和玩家可见名称都不能为空。")
            if not isinstance(raw_endpoints, list):
                raise ConsoleError(f"模型池 {display_name} 的端点必须是数组。")
            if len(raw_endpoints) > 32:
                raise ConsoleError(f"模型池 {display_name} 最多配置 32 个端点。")
            previous_pool = existing_pools.get(pool_id)
            previous_endpoints = {
                endpoint.endpoint_id: endpoint
                for endpoint in (previous_pool.endpoints if previous_pool else [])
            }
            endpoints: list[ModelEndpoint] = []
            for raw_endpoint in raw_endpoints:
                if not isinstance(raw_endpoint, dict):
                    raise ConsoleError("模型端点必须是 JSON 对象。")
                endpoints.append(
                    self._parse_model_endpoint(
                        raw_endpoint,
                        previous_endpoints.get(
                            str(raw_endpoint.get("endpoint_id", "")).strip()
                        ),
                    )
                )
            try:
                parsed_pools.append(
                    ModelPool(
                        pool_id=pool_id,
                        display_name=display_name,
                        endpoints=endpoints,
                    )
                )
            except ValueError as error:
                raise ConsoleError(
                    f"模型池 {display_name} 配置无效：{error}"
                ) from error
        with self._config_lock:
            try:
                self.runtime_config.update(model_pools=parsed_pools)
            except ValueError as error:
                raise ConsoleError(str(error)) from error
            self.runtime_config.save()
            self._sync_model_pool_runtimes(reset_health=True)
            return self.public_model_config()

    def _profile_options(
        self,
        value: dict[str, Any],
        previous: ApplicationModelProfile | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        previous_request = dict(previous.request_options) if previous else {}
        previous_application = (
            dict(previous.application_options) if previous else {}
        )
        try:
            temperature = float(value.get("temperature", 0.15))
            trigger_percent = float(
                value.get("context_compression_trigger_percent", 80)
            )
            target_percent = float(
                value.get("context_compression_target_percent", 35)
            )
        except (TypeError, ValueError) as error:
            raise ConsoleError("Application 模型参数必须是有效数字。") from error
        if not 0 <= temperature <= 2:
            raise ConsoleError("Temperature 必须在 0 到 2 之间。")
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
        try:
            validated_overrides = request_body_overrides(
                {"request_body_overrides": raw_overrides}
            )
        except ValueError as error:
            raise ConsoleError(str(error)) from error
        request_options = {
            **previous_request,
            "temperature": temperature,
            "request_body_overrides": validated_overrides,
        }
        if bool(value.get("thinking_enabled", False)):
            request_options["thinking"] = {"type": "enabled"}
            request_options["reasoning_effort"] = str(
                value.get("reasoning_effort", "high")
            )
        else:
            request_options.pop("thinking", None)
            request_options.pop("reasoning_effort", None)

        service_fields = {
            "searxng_url": str(
                value.get(
                    "searxng_url",
                    previous_application.get(
                        "searxng_url", "http://127.0.0.1:8080"
                    ),
                )
            ).strip(),
            "crawl4ai_url": str(
                value.get(
                    "crawl4ai_url",
                    previous_application.get(
                        "crawl4ai_url", "http://127.0.0.1:11235"
                    ),
                )
            ).strip(),
            "stellaris_wiki_api_url": str(
                value.get(
                    "stellaris_wiki_api_url",
                    previous_application.get(
                        "stellaris_wiki_api_url",
                        "https://stellaris.paradoxwikis.com/api.php",
                    ),
                )
            ).strip(),
        }
        for label, service_url in service_fields.items():
            parsed = urlparse(service_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ConsoleError(
                    f"{label} 必须是无内嵌凭据的 HTTP(S) URL。"
                )
        raw_domains = value.get(
            "web_search_allowed_domains",
            previous_application.get(
                "web_search_allowed_domains", DEFAULT_ALLOWED_DOMAINS
            ),
        )
        if isinstance(raw_domains, str):
            raw_domains = re.split(r"[\s,;]+", raw_domains)
        if not isinstance(raw_domains, list):
            raise ConsoleError("网页来源白名单必须是域名列表。")
        allowed_domains: list[str] = []
        for raw_domain in raw_domains:
            domain = str(raw_domain).strip().lower().rstrip(".")
            if not domain:
                continue
            if not re.fullmatch(r"[a-z0-9.-]{1,253}", domain):
                raise ConsoleError(f"无效的网页来源域名：{domain}")
            if domain not in allowed_domains:
                allowed_domains.append(domain)
        if not allowed_domains or len(allowed_domains) > 64:
            raise ConsoleError("网页来源白名单需要 1 到 64 个域名。")
        application_options = {
            **previous_application,
            **service_fields,
            "model_template_id": "custom",
            "tool_calling_enabled": True,
            "context_compression_enabled": bool(
                value.get("context_compression_enabled", True)
            ),
            "context_compression_trigger_percent": trigger_percent,
            "context_compression_target_percent": target_percent,
            "web_research_enabled": bool(
                value.get("web_research_enabled", False)
            ),
            "web_search_allowed_domains": allowed_domains,
            "web_fetch_direct_fallback_enabled": bool(
                value.get("web_fetch_direct_fallback_enabled", False)
            ),
        }
        crawl_token = str(value.get("crawl4ai_api_token", "")).strip()
        if crawl_token:
            token_path = self.runtime_root / "secrets" / "crawl4ai_api_token"
            atomic_write_text(token_path, crawl_token + "\n", mode=0o600)
            application_options["crawl4ai_api_token_file"] = str(token_path)
        return request_options, application_options

    def save_application_model_profile(
        self,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        application_id = str(value.get("application_id", "")).strip()
        try:
            self.application_registry.get(application_id)
        except KeyError as error:
            raise ConsoleError(str(error)) from error
        profile_id = str(value.get("profile_id", "")).strip()
        display_name = str(value.get("display_name", "")).strip()
        pool_id = str(value.get("pool_id", "")).strip()
        model_id = str(value.get("model_id", "")).strip()
        if not all((profile_id, display_name, pool_id, model_id)):
            raise ConsoleError("配置名称、模型池和池内模型都不能为空。")
        snapshot = self.runtime_config.snapshot()
        previous = next(
            (
                item
                for item in snapshot.application_model_profiles
                if item.profile_id == profile_id
            ),
            None,
        )
        request_options, application_options = self._profile_options(
            value,
            previous,
        )
        try:
            profile = ApplicationModelProfile(
                profile_id=profile_id,
                display_name=display_name,
                application_id=application_id,
                pool_id=pool_id,
                model_id=model_id,
                request_options=request_options,
                application_options=application_options,
            )
        except ValueError as error:
            raise ConsoleError(f"Application 配置无效：{error}") from error
        profiles = [
            profile if item.profile_id == profile_id else item
            for item in snapshot.application_model_profiles
        ]
        if previous is None:
            if len(profiles) >= 128:
                raise ConsoleError("最多保存 128 份 Application 模型配置。")
            profiles.append(profile)
        bindings = dict(snapshot.application_model_bindings)
        bindings[application_id] = profile_id
        with self._config_lock:
            try:
                self.runtime_config.update(
                    application_model_profiles=profiles,
                    application_model_bindings=bindings,
                )
            except ValueError as error:
                raise ConsoleError(str(error)) from error
            self.runtime_config.save()
            self._sync_model_pool_runtimes(reset_health=False)
            return self.public_model_config()

    def delete_application_model_profile(
        self,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        profile_id = str(value.get("profile_id", "")).strip()
        snapshot = self.runtime_config.snapshot()
        target = next(
            (
                item
                for item in snapshot.application_model_profiles
                if item.profile_id == profile_id
            ),
            None,
        )
        if target is None:
            raise ConsoleError("找不到要删除的 Application 配置。")
        remaining = [
            item
            for item in snapshot.application_model_profiles
            if item.profile_id != profile_id
        ]
        fallback = next(
            (
                item
                for item in remaining
                if item.application_id == target.application_id
            ),
            None,
        )
        if fallback is None:
            raise ConsoleError("每个 Application 至少需要保留一份模型配置。")
        bindings = dict(snapshot.application_model_bindings)
        if bindings.get(target.application_id) == profile_id:
            bindings[target.application_id] = fallback.profile_id
        with self._config_lock:
            self.runtime_config.update(
                application_model_profiles=remaining,
                application_model_bindings=bindings,
            )
            self.runtime_config.save()
            self._sync_model_pool_runtimes(reset_health=False)
            return self.public_model_config()

    def save_model_config(self, value: dict[str, Any]) -> dict[str, Any]:
        """Compatibility route retained for pre-catalog web clients."""
        if isinstance(value.get("model_pools"), list):
            return self.save_model_pools(value)
        if isinstance(value.get("application_profile"), dict):
            return self.save_application_model_profile(
                dict(value["application_profile"])
            )
        raise ConsoleError("请刷新页面后使用新的模型配置面板。")

    def probe_model_pool(self, value: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.runtime_config.snapshot()
        pool_id = str(value.get("pool_id", "")).strip()
        if not pool_id:
            pool_id = snapshot.application_profile.pool_id
        endpoint_id = str(value.get("endpoint_id", "")).strip()
        pool_runtime = self.model_pool_runtimes.get(pool_id)
        if pool_runtime is None:
            raise ConsoleError(f"未知模型池：{pool_id}")
        try:
            results = (
                [pool_runtime.probe(endpoint_id)]
                if endpoint_id
                else pool_runtime.probe_all()
            )
        except KeyError as error:
            raise ConsoleError(str(error)) from error
        return {
            "pool_id": pool_id,
            "results": results,
            "model_pool_health": pool_runtime.status(),
        }

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
            "execution_mode": str(
                self.config.get("execution_mode", "carrier_click")
            ),
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
            "session_proxy_local_ip": str(
                self.config.get("session_proxy_local_ip", "")
            ),
            "session_proxy_host_ip": str(
                self.config.get("session_proxy_host_ip", "")
            ),
            "session_proxy_source_actor": int(
                self.config.get("session_proxy_source_actor", 0)
            ),
            "session_proxy_host_actor": int(
                self.config.get("session_proxy_host_actor", 1)
            ),
            "session_proxy_acknowledged": bool(
                self.config.get("session_proxy_acknowledged", False)
            ),
            "experimental_fleet_tools_enabled": bool(
                self.config.get("experimental_fleet_tools_enabled", False)
            ),
            "experimental_fleet_attack_enabled": bool(
                self.config.get("experimental_fleet_attack_enabled", False)
            ),
            "experimental_fleet_coordinate_tools_enabled": bool(
                self.config.get(
                    "experimental_fleet_coordinate_tools_enabled",
                    False,
                )
            ),
            "fleet_coordinate_max_abs": float(
                self.config.get("fleet_coordinate_max_abs", 1000.0)
            ),
            "experimental_ship_design_tools_enabled": bool(
                self.config.get(
                    "experimental_ship_design_tools_enabled",
                    False,
                )
            ),
            "experimental_fleet_reinforcement_tools_enabled": bool(
                self.config.get(
                    "experimental_fleet_reinforcement_tools_enabled",
                    False,
                )
            ),
            "maximum_fleet_reinforcement_increase": int(
                self.config.get("maximum_fleet_reinforcement_increase", 5)
            ),
            "experimental_new_fleet_tools_enabled": bool(
                self.config.get("experimental_new_fleet_tools_enabled", False)
            ),
            "maximum_new_fleet_initial_ships": int(
                self.config.get("maximum_new_fleet_initial_ships", 5)
            ),
            "experimental_research_tools_enabled": bool(
                self.config.get("experimental_research_tools_enabled", False)
            ),
            "experimental_research_reselection_enabled": bool(
                self.config.get(
                    "experimental_research_reselection_enabled",
                    False,
                )
            ),
        }

    def save_execution_settings(self, value: dict[str, Any]) -> dict[str, Any]:
        self._require_protocol_compatibility_idle()
        try:
            manual_age = int(value.get("require_fresh_save_seconds", 900))
            autonomy_age = int(
                value.get("autonomy_require_fresh_save_seconds", 900)
            )
            maximum = int(value.get("maximum_constructions_per_turn", 3))
            maximum_lag = int(value.get("maximum_source_save_lag_versions", 2))
            coordinate_limit = float(
                value.get("fleet_coordinate_max_abs", 1000.0)
            )
            reinforcement_limit = int(
                value.get("maximum_fleet_reinforcement_increase", 5)
            )
            new_fleet_limit = int(
                value.get("maximum_new_fleet_initial_ships", 5)
            )
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
        execution_mode = str(value.get("execution_mode", "carrier_click"))
        if execution_mode not in {"carrier_click", "session_proxy"}:
            raise ConsoleError("执行模式必须是点击载体或会话代理。")
        fleet_tools_enabled = bool(
            value.get("experimental_fleet_tools_enabled", False)
        )
        fleet_attack_enabled = bool(
            value.get("experimental_fleet_attack_enabled", False)
        )
        fleet_coordinate_enabled = bool(
            value.get("experimental_fleet_coordinate_tools_enabled", False)
        )
        ship_design_enabled = bool(
            value.get("experimental_ship_design_tools_enabled", False)
        )
        fleet_reinforcement_enabled = bool(
            value.get(
                "experimental_fleet_reinforcement_tools_enabled",
                False,
            )
        )
        new_fleet_enabled = bool(
            value.get("experimental_new_fleet_tools_enabled", False)
        )
        research_tools_enabled = bool(
            value.get("experimental_research_tools_enabled", False)
        )
        research_reselection_enabled = bool(
            value.get("experimental_research_reselection_enabled", False)
        )
        if fleet_tools_enabled and execution_mode != "session_proxy":
            raise ConsoleError("实验性舰队工具只能在会话代理模式下启用。")
        if fleet_attack_enabled and not fleet_tools_enabled:
            raise ConsoleError("启用攻击预检查前必须先启用实验性舰队工具。")
        if fleet_coordinate_enabled and not fleet_tools_enabled:
            raise ConsoleError("启用星系内坐标移动前必须先启用舰队工具。")
        if ship_design_enabled and execution_mode != "session_proxy":
            raise ConsoleError("实验性舰船设计工具只能在会话代理模式下启用。")
        if fleet_reinforcement_enabled and execution_mode != "session_proxy":
            raise ConsoleError("实验性舰队增援工具只能在会话代理模式下启用。")
        if new_fleet_enabled and execution_mode != "session_proxy":
            raise ConsoleError("实验性新建舰队工具只能在会话代理模式下启用。")
        if not 1 <= reinforcement_limit <= 20:
            raise ConsoleError("单次舰队目标编制增量上限必须在 1 到 20 之间。")
        if not 1 <= new_fleet_limit <= 20:
            raise ConsoleError("新舰队初始舰数上限必须在 1 到 20 之间。")
        if (
            not math.isfinite(coordinate_limit)
            or not 10 <= coordinate_limit <= 100_000
        ):
            raise ConsoleError("舰队星系内坐标边界必须在 10 到 100000 之间。")
        if research_tools_enabled and execution_mode != "session_proxy":
            raise ConsoleError("实验性科研工具只能在会话代理模式下启用。")
        if research_reselection_enabled and not research_tools_enabled:
            raise ConsoleError("允许中途换题前必须先启用实验性科研工具。")
        try:
            source_actor = int(value.get("session_proxy_source_actor", 0))
            host_actor = int(value.get("session_proxy_host_actor", 1))
        except (TypeError, ValueError) as error:
            raise ConsoleError("代理 actor 必须是整数。") from error
        if not 0 <= source_actor <= 0xFFFFFFFF:
            raise ConsoleError("合作端 actor 必须是 0 到 4294967295。")
        if not 1 <= host_actor <= 0xFFFFFFFF:
            raise ConsoleError("房主 actor 必须是 1 到 4294967295。")
        changes = {
            "execution_mode": execution_mode,
            "fixed_click_guard_enabled": bool(
                value.get("fixed_click_guard_enabled", True)
            ),
            "require_fresh_save_seconds": manual_age,
            "maximum_source_save_lag_versions": maximum_lag,
            "autonomy_require_fresh_save_seconds": autonomy_age,
            "maximum_constructions_per_turn": maximum,
            "inconclusive_rewrite_policy": policy,
            "session_proxy_local_ip": str(
                value.get("session_proxy_local_ip", "")
            ).strip(),
            "session_proxy_host_ip": str(
                value.get("session_proxy_host_ip", "")
            ).strip(),
            "session_proxy_source_actor": source_actor,
            "session_proxy_host_actor": host_actor,
            "session_proxy_acknowledged": bool(
                value.get("session_proxy_acknowledged", False)
            ),
            "experimental_fleet_tools_enabled": fleet_tools_enabled,
            "experimental_fleet_attack_enabled": fleet_attack_enabled,
            "experimental_fleet_coordinate_tools_enabled": (
                fleet_coordinate_enabled
            ),
            "fleet_coordinate_max_abs": coordinate_limit,
            "experimental_ship_design_tools_enabled": ship_design_enabled,
            "experimental_fleet_reinforcement_tools_enabled": (
                fleet_reinforcement_enabled
            ),
            "maximum_fleet_reinforcement_increase": reinforcement_limit,
            "experimental_new_fleet_tools_enabled": new_fleet_enabled,
            "maximum_new_fleet_initial_ships": new_fleet_limit,
            "experimental_research_tools_enabled": research_tools_enabled,
            "experimental_research_reselection_enabled": (
                research_reselection_enabled
            ),
        }
        with self._config_lock:
            self.runtime_config.update(settings=changes)
            self.runtime_config.save()
            self.config = self.runtime_config.snapshot().settings
        return {"saved": True, **self.public_execution_settings()}

    def session_proxy_status(self) -> dict[str, Any]:
        return SessionProxyController(self.config).status()

    def start_session_proxy(self) -> dict[str, Any]:
        self._require_protocol_compatibility_idle()
        self.reload_config()
        if str(self.config.get("execution_mode", "carrier_click")) != "session_proxy":
            raise ConsoleError("请先保存并选择会话代理执行模式。")
        try:
            return SessionProxyController(self.config).start()
        except SessionProxyError as error:
            raise ConsoleError(str(error)) from error

    def stop_session_proxy(self, *, room_exited: bool) -> dict[str, Any]:
        self._require_protocol_compatibility_idle()
        self.reload_config()
        try:
            return SessionProxyController(self.config).stop(
                room_exited=room_exited,
            )
        except SessionProxyError as error:
            raise ConsoleError(str(error)) from error

    def _protocol_compatibility_live_active(self) -> bool:
        suite = getattr(self, "protocol_compatibility", None)
        return bool(suite is not None and suite.live_active())

    def _require_protocol_compatibility_idle(self) -> None:
        if self._protocol_compatibility_live_active():
            raise ConsoleError(
                "协议兼容性验收正在接管会话代理；请先退房并结束验收。"
            )

    def protocol_compatibility_payload(self) -> dict[str, Any]:
        return self.protocol_compatibility.payload()

    def new_protocol_compatibility_plan(
        self,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self.protocol_compatibility.new_plan(
                game_version=str(value.get("game_version", "")),
                game_build=str(value.get("game_build", "")),
            )
        except ProtocolCompatibilityError as error:
            raise ConsoleError(str(error)) from error

    def save_protocol_compatibility_plan(
        self,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self.protocol_compatibility.save_plan(value.get("plan"))
        except ProtocolCompatibilityError as error:
            raise ConsoleError(str(error)) from error

    def check_protocol_compatibility(self) -> dict[str, Any]:
        try:
            return self.protocol_compatibility.offline_check()
        except ProtocolCompatibilityError as error:
            raise ConsoleError(str(error)) from error

    def start_protocol_compatibility_live(
        self,
        *,
        disposable_authorized: bool,
    ) -> dict[str, Any]:
        with self._conversation_lock:
            with self._job_lock:
                if self._job.get("state") == "running":
                    raise ConsoleError(
                        "已有代理任务正在运行；协议验收不会与模型任务并行。"
                    )
            self.reload_config()
            if self._active_autonomy_mode() != "paused":
                raise ConsoleError(
                    "开始协议验收前请把自主巡检切换为暂停。"
                )
            try:
                return self.protocol_compatibility.start_live(
                    disposable_authorized=disposable_authorized,
                )
            except (ProtocolCompatibilityError, SessionProxyError) as error:
                raise ConsoleError(str(error)) from error

    def confirm_protocol_compatibility_room(self) -> dict[str, Any]:
        try:
            return self.protocol_compatibility.confirm_room()
        except ProtocolCompatibilityError as error:
            raise ConsoleError(str(error)) from error

    def execute_protocol_compatibility_action(self) -> dict[str, Any]:
        try:
            return self.protocol_compatibility.execute_current()
        except ProtocolCompatibilityError as error:
            raise ConsoleError(str(error)) from error

    def record_protocol_compatibility_verdict(
        self,
        verdict: str,
    ) -> dict[str, Any]:
        try:
            return self.protocol_compatibility.record_verdict(verdict)
        except ProtocolCompatibilityError as error:
            raise ConsoleError(str(error)) from error

    def skip_protocol_compatibility_action(self) -> dict[str, Any]:
        try:
            return self.protocol_compatibility.skip_current()
        except ProtocolCompatibilityError as error:
            raise ConsoleError(str(error)) from error

    def finish_protocol_compatibility(
        self,
        *,
        room_exited: bool,
    ) -> dict[str, Any]:
        try:
            return self.protocol_compatibility.finish(
                room_exited=room_exited,
            )
        except ProtocolCompatibilityError as error:
            raise ConsoleError(str(error)) from error

    def reset_protocol_compatibility(self) -> dict[str, Any]:
        try:
            return self.protocol_compatibility.reset()
        except ProtocolCompatibilityError as error:
            raise ConsoleError(str(error)) from error

    def protocol_compatibility_report_path(self, format_name: str) -> Path:
        try:
            return self.protocol_compatibility.report_path(format_name)
        except ProtocolCompatibilityError as error:
            raise ConsoleError(str(error)) from error

    def fleet_payload(self) -> dict[str, Any]:
        """Return a cached save-backed fleet list with player permissions."""
        try:
            campaign_id = self.conversation_store.conversation_metadata().get(
                "campaign_id"
            )
            if not campaign_id:
                raise ConsoleError("当前战役会话尚未绑定存档。")
            path = resolve_current_save(
                self.config,
                expected_campaign_id=str(campaign_id),
            )
            stat = path.stat()
            cache_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
            with self._fleet_cache_lock:
                if cache_key != self._fleet_cache_key:
                    self._fleet_cache = extract_fleet_profiles(
                        load_gamestate(path)
                    )
                    self._fleet_cache_key = cache_key
                profile = dict(self._fleet_cache or {})
        except Exception as error:
            return {
                "schema": "iag.console_fleet_state.v1",
                "available": False,
                "error": str(error),
                "fleets": [],
            }

        permissions = normalized_permissions(
            self.conversation_store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        fleets: list[dict[str, Any]] = []
        for item in profile.get("fleets", []):
            if not isinstance(item, dict):
                continue
            if not item.get("player_controllable", False):
                continue
            permission = permissions.get(str(item.get("fleet_id")), {})
            fleets.append(
                {
                    **item,
                    "permission": {
                        "allow_move": bool(
                            permission.get("allow_move", False)
                        ),
                        "allow_attack": bool(
                            permission.get("allow_attack", False)
                        ),
                        "allow_reinforce": bool(
                            permission.get("allow_reinforce", False)
                        ),
                    },
                }
            )
        return {
            "schema": "iag.console_fleet_state.v1",
            "available": True,
            "game_date": profile.get("game_date"),
            "owner_country_id": profile.get("owner_country_id"),
            "fleet_count": len(fleets),
            "total_military_power": sum(
                float(item.get("military_power") or 0)
                for item in fleets
                if item.get("ship_class") == "shipclass_military"
            ),
            "fleets": fleets,
            "move_protocol_state": "live_verified_experimental",
            "attack_protocol_state": "paired_non_host_sample_required",
        }

    def save_fleet_permission(self, value: dict[str, Any]) -> dict[str, Any]:
        try:
            fleet_id = int(value.get("fleet_id"))
        except (TypeError, ValueError) as error:
            raise ConsoleError("fleet_id 必须是整数。") from error
        current = self.fleet_payload()
        owned_ids = {
            int(item["fleet_id"])
            for item in current.get("fleets", [])
            if isinstance(item, dict) and item.get("fleet_id") is not None
        }
        if fleet_id not in owned_ids:
            raise ConsoleError("该舰队不在最新同步存档的玩家舰队列表中。")
        permissions = normalized_permissions(
            self.conversation_store.get_state(FLEET_PERMISSIONS_KEY, {})
        )
        permissions[str(fleet_id)] = {
            "allow_move": bool(value.get("allow_move", False)),
            "allow_attack": bool(value.get("allow_attack", False)),
            "allow_reinforce": bool(value.get("allow_reinforce", False)),
        }
        self.conversation_store.set_state(FLEET_PERMISSIONS_KEY, permissions)
        return {"saved": True, "fleet_state": self.fleet_payload()}

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
            result = receive_uploaded_save(
                self.config,
                stream,
                content_length=content_length,
                headers=headers,
            )
        wakeup = getattr(self, "_scheduler_wakeup", None)
        if wakeup is not None:
            wakeup.set()
        return {
            **result,
            "save_continuation_wakeup": True,
        }

    def _current_continuation_store(self) -> ConversationStore | None:
        """Resolve the conversation explicitly bound to the latest host save."""
        manifest = read_manifest(self.config) or {}
        campaign_id = str(manifest.get("campaign_id") or "").strip().lower()
        campaign_label = str(
            manifest.get("campaign_label") or ""
        ).strip()
        if not campaign_id:
            return None
        holder = self.conversation_store.conversation_for_campaign(campaign_id)
        if holder is None or holder.get("archived"):
            return None
        if str(holder.get("campaign_label") or "").strip() != campaign_label:
            return None
        return ConversationStore(
            self.conversation_db_path,
            conversation_id=str(holder["conversation_id"]),
        )

    def _run_save_continuation_job(
        self,
        store: ConversationStore,
        source_identity: dict[str, Any],
    ) -> dict[str, Any]:
        """Run fixed code for one save event, without opening a model turn."""
        result = run_save_continuations(self.config, store)
        summary = continuation_public_summary(result)
        store.append(
            "system",
            summary,
            kind="save_continuation",
            visible=True,
            metadata={
                "success": result.get("state")
                not in {"failed", "needs_review"},
                "trigger": "save_continuation",
            },
        )
        if result.get("mutated_game"):
            # The current save predates the command that was just emitted.  Do
            # not spend a model turn auditing that now-stale snapshot.
            store.set_state("last_autonomy_source", source_identity)
        return result

    def upload_is_authorized(self, authorization: str | None) -> bool:
        token = optional_text(self.save_upload_token_path).strip()
        return upload_bearer_token_matches(authorization, token)

    def overlay_is_authorized(self, authorization: str | None) -> bool:
        token = optional_text(self.overlay_access_token_path).strip()
        return upload_bearer_token_matches(authorization, token)

    def overlay_bootstrap(self) -> dict[str, Any]:
        with self._conversation_lock:
            store = self.conversation_store
            metadata = store.conversation_metadata()
            stream = self.visible_reply_broker.snapshot(store.conversation_id)
            return {
                "schema": "iag.overlay_bootstrap.v1",
                "conversation": {
                    "conversation_id": store.conversation_id,
                    "title": metadata.get("title") or store.conversation_id,
                    "campaign_label": metadata.get("campaign_label"),
                },
                "messages": store.overlay_messages(limit=500),
                "stream": stream,
            }

    def overlay_start_chat(
        self,
        text: str,
        *,
        expected_conversation_id: str | None = None,
    ) -> dict[str, Any]:
        selected = str(expected_conversation_id or "").strip()
        with self._conversation_lock:
            active = self.conversation_store.conversation_id
            if selected and selected != active:
                raise ConsoleError(
                    "叠加层连接的战役会话已经切换，请等待界面自动刷新。"
                )
        return self.start_chat(text)

    def paired_host_bridge_archive(self, server_url: str) -> Path:
        """Build an authenticated runtime download paired to this Agent."""
        if self.tls_certificate_path is None:
            raise ConsoleError("Agent 未启用 TLS，不能生成房主执行桥配对包。")
        source = STATIC_ROOT / "downloads" / PUBLIC_HOST_BRIDGE_ARCHIVE
        token = optional_text(self.save_upload_token_path).strip()
        overlay_token = optional_text(self.overlay_access_token_path).strip()
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
                    overlay_token,
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
                overlay_access_token=overlay_token,
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
                continuation_status = dict(self._save_continuation_probe)
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
            "session_proxy": self.session_proxy_status(),
            "fleet_state": self.fleet_payload(),
            "strategy": self.strategy_payload(),
            "conversation": conversation_state,
            "conversations": conversation_catalog,
            "campaign_binding": conversation_catalog["binding"],
            "autonomy": {
                **autonomy_status,
                "mode": autonomy_mode,
            },
            "save_continuation": continuation_status,
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
        conversation_id = store.conversation_id
        self.visible_reply_broker.publish(
            conversation_id,
            "turn_started",
            {"trigger": trigger},
        )

        def publish_visible(event_type: str, payload: dict[str, Any]) -> None:
            self.visible_reply_broker.publish(
                conversation_id,
                event_type,
                payload,
            )

        try:
            return agent.run_turn(
                trigger=trigger,
                user_content=user_content,
                autonomy_mode=mode,
                visible_event_callback=publish_visible,
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
            self.visible_reply_broker.publish(
                conversation_id,
                "turn_finished",
                {"trigger": trigger},
            )
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
            self._scheduler_wakeup.wait(interval)
            self._scheduler_wakeup.clear()
            if self._autonomy_stop.is_set():
                return
            try:
                self.reload_config()
                with self._conversation_lock:
                    with self._job_lock:
                        busy = self._job.get("state") == "running"
                    if busy:
                        continue
                    if self._protocol_compatibility_live_active():
                        continue

                    continuation_store = self._current_continuation_store()
                    continuation_probe = (
                        probe_save_continuations(
                            self.config,
                            continuation_store,
                        )
                        if continuation_store is not None
                        else {
                            "schema": "iag.save_continuation_probe.v1",
                            "due": False,
                            "blocks_autonomy": False,
                            "reason": "current_save_unbound",
                        }
                    )
                    with self._job_lock:
                        self._save_continuation_probe = continuation_probe
                    ready = continuation_probe.get("ready")
                    if (
                        continuation_store is not None
                        and continuation_probe.get("due")
                        and isinstance(ready, dict)
                    ):
                        source = ready.get("save")
                        source = source if isinstance(source, dict) else {}
                        source_path = Path(str(source.get("path") or ""))
                        source_identity = save_identity(source_path)
                        self._start_job(
                            "save_continuation",
                            lambda store=continuation_store,
                            source_identity=source_identity: (
                                self._run_save_continuation_job(
                                    store,
                                    source_identity,
                                )
                            ),
                            conversation_id=(
                                continuation_store.conversation_id
                            ),
                        )
                        continue
                    if continuation_probe.get("blocks_autonomy"):
                        with self._job_lock:
                            self._autonomy_probe = {
                                "enabled": True,
                                "mode": self._active_autonomy_mode(),
                                "due": False,
                                "reason": "waiting_for_save_continuation",
                            }
                        continue

                    store = self.conversation_store
                    agent = self.conversation_agent
                    probe = autonomy_probe(self.config, store)
                    with self._job_lock:
                        self._autonomy_probe = probe
                    if not probe.get("due"):
                        continue
                    mode = self._active_autonomy_mode(store)
                    source_identity = probe.get("save")
                    self._start_job(
                        "autonomous",
                        lambda agent=agent,
                        store=store,
                        mode=mode,
                        source_identity=source_identity: (
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
        self._scheduler_wakeup.set()
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
        self._require_protocol_compatibility_idle()
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
            wakeup = getattr(self, "_scheduler_wakeup", None)
            if wakeup is not None:
                wakeup.set()

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
                    self.runtime_config.snapshot(),
                    save_path=save_path,
                    run_root=self.runs_root,
                    plan_path=None,
                    model_pool_runtime=self.model_pool_runtime,
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
                lambda: execute_run(
                    run_dir,
                    self.runtime_config.snapshot().settings,
                ),
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

    def send_overlay_unauthorized(self) -> None:
        self.send_bytes(
            b'{"error":"overlay_authentication_required"}',
            "application/json; charset=utf-8",
            401,
            {"WWW-Authenticate": 'Bearer realm="IAG Game Overlay"'},
        )

    def send_overlay_events(
        self,
        conversation_id: str,
        after_sequence: int,
    ) -> None:
        events = self.service.visible_reply_broker.wait_after(
            conversation_id,
            after_sequence,
            timeout_seconds=25,
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            if not events:
                self.wfile.write(b"event: keepalive\ndata: {}\n\n")
            for event in events:
                payload = json.dumps(
                    event.as_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self.wfile.write(f"id: {event.sequence}\n".encode("ascii"))
                self.wfile.write(f"event: {event.event_type}\n".encode("ascii"))
                self.wfile.write(b"data: " + payload + b"\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            self.close_connection = True

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

    def send_file(
        self,
        path: Path,
        *,
        head_only: bool = False,
        download_name: str | None = None,
    ) -> None:
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
        if download_name:
            safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", download_name)
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{safe_name}"',
            )
        elif path.suffix.lower() == ".zip":
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
        except ConsoleError:
            self.send_bytes(
                b"",
                "application/json; charset=utf-8",
                status=400,
            )
        except FileNotFoundError:
            self.send_bytes(b"", "application/json; charset=utf-8", status=404)
        except Exception:
            self.send_bytes(b"", "application/json; charset=utf-8", status=500)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if path in {"/api/overlay/bootstrap", "/api/overlay/events"}:
                if not self.service.overlay_is_authorized(
                    self.headers.get("Authorization")
                ):
                    self.send_overlay_unauthorized()
                    return
                if path == "/api/overlay/bootstrap":
                    self.send_json(self.service.overlay_bootstrap())
                    return
                try:
                    after_sequence = int(query.get("after", ["0"])[0])
                except ValueError as error:
                    raise ConsoleError("无效的叠加层事件游标。") from error
                conversation_id = str(
                    query.get("conversation_id", [""])[0]
                ).strip()
                if not conversation_id:
                    raise ConsoleError("叠加层事件请求缺少 conversation_id。")
                self.send_overlay_events(conversation_id, after_sequence)
                return
            if not self.is_authorized():
                self.send_unauthorized()
                return
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
            if path == "/api/protocol-compatibility":
                self.send_json(
                    self.service.protocol_compatibility_payload()
                )
                return
            if path == "/api/protocol-compatibility/report":
                format_name = str(query.get("format", ["json"])[0])
                report_path = self.service.protocol_compatibility_report_path(
                    format_name
                )
                self.send_file(
                    report_path,
                    download_name=(
                        f"protocol-compatibility-{report_path.parent.name}-"
                        f"{report_path.name}"
                    ),
                )
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
        except ConsoleError as error:
            self.send_json({"error": str(error)}, status=400)
        except FileNotFoundError:
            self.send_json({"error": "not_found"}, status=404)
        except Exception as error:
            self.send_json({"error": str(error)}, status=500)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/api/overlay/message":
            if not self.service.overlay_is_authorized(
                self.headers.get("Authorization")
            ):
                self.send_overlay_unauthorized()
                return
            try:
                value = self.request_json()
                result = self.service.overlay_start_chat(
                    str(value.get("text", "")),
                    expected_conversation_id=(
                        str(value.get("conversation_id", "")) or None
                    ),
                )
                self.send_json(result, status=202)
            except ConsoleError as error:
                self.send_json({"error": str(error)}, status=409)
            except Exception as error:
                traceback.print_exc()
                self.send_json(
                    {"error": f"{type(error).__name__}: {error}"},
                    status=500,
                )
            return
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
            elif path == "/api/model/pools":
                result = self.service.save_model_pools(value)
            elif path == "/api/model/application-profile":
                result = self.service.save_application_model_profile(value)
            elif path == "/api/model/application-profile/delete":
                result = self.service.delete_application_model_profile(value)
            elif path == "/api/model/probe":
                result = self.service.probe_model_pool(value)
            elif path == "/api/execution-settings":
                result = self.service.save_execution_settings(value)
            elif path == "/api/session-proxy/start":
                result = self.service.start_session_proxy()
            elif path == "/api/session-proxy/stop":
                result = self.service.stop_session_proxy(
                    room_exited=bool(value.get("room_exited", False))
                )
            elif path == "/api/protocol-compatibility/plan/new":
                result = self.service.new_protocol_compatibility_plan(value)
            elif path == "/api/protocol-compatibility/plan/save":
                result = self.service.save_protocol_compatibility_plan(value)
            elif path == "/api/protocol-compatibility/offline-check":
                result = self.service.check_protocol_compatibility()
            elif path == "/api/protocol-compatibility/live/start":
                result = self.service.start_protocol_compatibility_live(
                    disposable_authorized=bool(
                        value.get("disposable_authorized", False)
                    )
                )
            elif path == "/api/protocol-compatibility/room/confirm":
                result = self.service.confirm_protocol_compatibility_room()
            elif path == "/api/protocol-compatibility/action/execute":
                result = self.service.execute_protocol_compatibility_action()
            elif path == "/api/protocol-compatibility/action/verdict":
                result = self.service.record_protocol_compatibility_verdict(
                    str(value.get("verdict", ""))
                )
            elif path == "/api/protocol-compatibility/action/skip":
                result = self.service.skip_protocol_compatibility_action()
            elif path == "/api/protocol-compatibility/finish":
                result = self.service.finish_protocol_compatibility(
                    room_exited=bool(value.get("room_exited", False))
                )
            elif path == "/api/protocol-compatibility/reset":
                result = self.service.reset_protocol_compatibility()
            elif path == "/api/fleet-permission":
                result = self.service.save_fleet_permission(value)
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
