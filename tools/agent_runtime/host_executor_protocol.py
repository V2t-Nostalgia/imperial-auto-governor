#!/usr/bin/env python3
"""File-backed coordination for the Windows host-side packet interceptor."""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


HOST_EXECUTOR_CAPABILITY = "host_inbound_rewrite_v1"
REQUIRED_HOST_EXECUTOR_APP_VERSION = "2026.08.02-hostbridge8"
VERIFIED_CARRIER_BY_ACTION = {
    "build_building": "building_upc_construction_command_relay",
    "build_district": "district_generator",
    "build_zone": "zone_research_engineering",
    "upgrade_building": "building_upc_upgrade_command_relay_target",
}
SUPPORTED_CARRIERS_BY_ACTION = {
    "build_building": {
        "building_upc_construction_command_relay",
        "building_research_lab_1",
    },
    "build_district": {"district_generator"},
    "build_zone": {"zone_research_engineering"},
    "upgrade_building": {
        "building_upc_upgrade_command_relay_target",
        "building_research_lab_2",
    },
}
_STATE_LOCK = threading.RLock()


class HostExecutorProtocolError(RuntimeError):
    """Raised when the host execution bridge is unavailable or inconsistent."""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def runtime_path(
    config: Mapping[str, Any],
    key: str,
    default: Path,
) -> Path:
    value = Path(str(config.get(key, default))).expanduser()
    if value.is_absolute():
        return value
    return Path(str(config["runtime_root"])).expanduser() / value


def protocol_root(config: Mapping[str, Any]) -> Path:
    runtime_root = Path(str(config["runtime_root"])).expanduser()
    return runtime_path(
        config,
        "host_executor_state_root",
        runtime_root / "state" / "host_executor",
    )


def save_client_status_path(config: Mapping[str, Any]) -> Path:
    runtime_root = Path(str(config["runtime_root"])).expanduser()
    return runtime_path(
        config,
        "save_client_status_path",
        runtime_root / "state" / "save_client_status.json",
    )


def optional_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def request_directory(config: Mapping[str, Any], request_id: str) -> Path:
    if not request_id or any(character not in "0123456789_abcdef" for character in request_id):
        raise HostExecutorProtocolError("Invalid host executor request ID.")
    return protocol_root(config) / "requests" / request_id


def active_pointer_path(config: Mapping[str, Any]) -> Path:
    return protocol_root(config) / "active.json"


def read_active_request(config: Mapping[str, Any]) -> dict[str, Any] | None:
    pointer = optional_json(active_pointer_path(config))
    if not pointer:
        return None
    request_id = str(pointer.get("request_id", ""))
    try:
        directory = request_directory(config, request_id)
    except HostExecutorProtocolError:
        return None
    request = optional_json(directory / "request.json")
    if not request or request.get("request_id") != request_id:
        return None
    if (directory / "result.json").is_file() or (directory / "cancelled.json").is_file():
        return None
    expires_epoch = float(request.get("expires_epoch", 0) or 0)
    if expires_epoch <= time.time():
        return None
    return request


def connected_host_client(
    config: Mapping[str, Any],
    *,
    expected_host_ip: str,
) -> dict[str, Any]:
    client = optional_json(save_client_status_path(config)) or {}
    timeout_seconds = max(
        float(config.get("save_client_heartbeat_timeout_seconds", 15)),
        5,
    )
    age = max(time.time() - float(client.get("last_seen_epoch", 0) or 0), 0)
    if client.get("state") != "running" or age > timeout_seconds:
        raise HostExecutorProtocolError(
            "The Windows host bridge is not connected. No carrier click was issued."
        )
    if str(client.get("source_ip", "")) != expected_host_ip:
        raise HostExecutorProtocolError(
            "The connected save client does not match the selected Stellaris host."
        )
    capabilities = client.get("capabilities")
    if (
        not isinstance(capabilities, list)
        or HOST_EXECUTOR_CAPABILITY not in capabilities
    ):
        raise HostExecutorProtocolError(
            "The connected Windows uploader is too old for host-side packet "
            "rewriting. Install the Host Bridge build and run it as administrator; "
            "no carrier click was issued."
        )
    app_version = str(client.get("app_version", ""))
    if app_version != REQUIRED_HOST_EXECUTOR_APP_VERSION:
        raise HostExecutorProtocolError(
            "The connected Windows Host Bridge does not include the verified "
            "same-family construction rewrites. Install "
            f"{REQUIRED_HOST_EXECUTOR_APP_VERSION}; no carrier click was issued."
        )
    client_id = str(client.get("client_id", ""))
    if not client_id:
        raise HostExecutorProtocolError("The Windows host bridge has no client ID.")
    return client


def local_ipv4_for_remote(remote_ip: str) -> str:
    address = ipaddress.ip_address(remote_ip)
    if address.version != 4:
        raise HostExecutorProtocolError("The host bridge currently supports IPv4 only.")
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((str(address), 9))
        local_ip = probe.getsockname()[0]
    except OSError as error:
        raise HostExecutorProtocolError(
            f"Unable to determine the co-op client address for {remote_ip}."
        ) from error
    finally:
        probe.close()
    if local_ip in {"0.0.0.0", "127.0.0.1"}:
        raise HostExecutorProtocolError(
            "The discovered co-op client address is not routable to the host."
        )
    return local_ip


def begin_host_execution(
    config: Mapping[str, Any],
    *,
    run_id: str,
    manifest: Mapping[str, Any],
    host_ip: str,
    peer_ip: str,
) -> dict[str, Any]:
    client = connected_host_client(config, expected_host_ip=host_ip)
    timeout_seconds = max(
        int(config.get("interceptor_timeout_seconds", 120)),
        15,
    )
    request_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(6)
    action = manifest.get("action")
    carrier = manifest.get("carrier")
    if not isinstance(action, dict) or not isinstance(carrier, dict):
        raise HostExecutorProtocolError(
            "The execution manifest is missing its action or carrier."
        )
    action_type = str(action.get("type", ""))
    supported_carriers = SUPPORTED_CARRIERS_BY_ACTION.get(action_type)
    if supported_carriers is None:
        raise HostExecutorProtocolError(
            f"The Windows host bridge does not support action {action_type}."
        )
    selected_carrier = str(carrier.get("command", ""))
    if selected_carrier not in supported_carriers:
        raise HostExecutorProtocolError(
            f"Action {action_type} must use one of the supported carriers: "
            f"{', '.join(sorted(supported_carriers))}."
        )
    request = {
        "schema": "iag.host_executor_request.v1",
        "request_id": request_id,
        "run_id": run_id,
        "created_at": now_iso(),
        "created_epoch": time.time(),
        "expires_epoch": time.time() + timeout_seconds + 45,
        "expected_client_id": client["client_id"],
        "host_ip": host_ip,
        "peer_ip": peer_ip,
        "carrier": {
            "command": selected_carrier,
            "required_direction": "inbound_to_host",
        },
        "action": dict(action),
        "safety": {
            "one_shot": True,
            "rewrite_direction": "inbound_to_host",
            "preserve_udp_payload_length": True,
            "preserve_command_count": True,
            "preserve_carrier_serial": True,
            "require_authoritative_host_confirmation": True,
            "drop_unrewritable_carrier": True,
        },
        "timeout_seconds": timeout_seconds,
        "observe_seconds": max(int(config.get("observe_seconds", 15)), 3),
    }
    with _STATE_LOCK:
        active = read_active_request(config)
        if active is not None:
            raise HostExecutorProtocolError(
                f"Host executor request {active['request_id']} is already active."
            )
        directory = request_directory(config, request_id)
        directory.mkdir(parents=True, exist_ok=False)
        atomic_write_json(directory / "request.json", request)
        atomic_write_json(
            active_pointer_path(config),
            {
                "schema": "iag.host_executor_active.v1",
                "request_id": request_id,
                "run_id": run_id,
                "created_at": request["created_at"],
            },
        )
    return request


def request_for_host_client(
    config: Mapping[str, Any],
    *,
    client_id: str,
    source_ip: str,
) -> dict[str, Any] | None:
    with _STATE_LOCK:
        request = read_active_request(config)
        if request is None:
            return None
        if request.get("expected_client_id") != client_id:
            return None
        if request.get("host_ip") != source_ip:
            return None
        directory = request_directory(config, str(request["request_id"]))
        return {
            **request,
            "ready_received": (directory / "ready.json").is_file(),
        }


def _validate_host_response(
    config: Mapping[str, Any],
    value: Mapping[str, Any],
    *,
    client_id: str,
    source_ip: str,
) -> tuple[dict[str, Any], Path]:
    request_id = str(value.get("request_id", ""))
    active = read_active_request(config)
    if active is None or active.get("request_id") != request_id:
        raise HostExecutorProtocolError(
            "The host executor request is no longer active."
        )
    if active.get("expected_client_id") != client_id:
        raise HostExecutorProtocolError("The host executor client ID does not match.")
    if active.get("host_ip") != source_ip:
        raise HostExecutorProtocolError("The host executor source IP does not match.")
    return active, request_directory(config, request_id)


def record_host_ready(
    config: Mapping[str, Any],
    value: Mapping[str, Any],
    *,
    client_id: str,
    source_ip: str,
) -> dict[str, Any]:
    with _STATE_LOCK:
        request, directory = _validate_host_response(
            config,
            value,
            client_id=client_id,
            source_ip=source_ip,
        )
        ready = {
            "schema": "iag.host_executor_ready.v1",
            "request_id": request["request_id"],
            "client_id": client_id,
            "received_at": now_iso(),
            "received_epoch": time.time(),
            "elevated": value.get("elevated") is True,
            "interceptor_open": value.get("interceptor_open") is True,
            "filter": str(value.get("filter", ""))[:1000],
            "process_id": int(value.get("process_id", 0) or 0),
        }
        if not ready["elevated"] or not ready["interceptor_open"]:
            raise HostExecutorProtocolError(
                "The Windows host interceptor did not prove that its elevated "
                "WinDivert handle is open."
            )
        atomic_write_json(directory / "ready.json", ready)
        return {"accepted": True, "request_id": request["request_id"]}


def record_host_result(
    config: Mapping[str, Any],
    value: Mapping[str, Any],
    *,
    client_id: str,
    source_ip: str,
) -> dict[str, Any]:
    with _STATE_LOCK:
        request, directory = _validate_host_response(
            config,
            value,
            client_id=client_id,
            source_ip=source_ip,
        )
        result = {
            "schema": "iag.host_executor_result.v1",
            "request_id": request["request_id"],
            "client_id": client_id,
            "received_at": now_iso(),
            "received_epoch": time.time(),
            "success": value.get("success") is True,
            "carrier_seen": value.get("carrier_seen") is True,
            "rewritten": value.get("rewritten") is True,
            "authoritative_confirmation": (
                value.get("authoritative_confirmation") is True
            ),
            "phase": str(value.get("phase", ""))[:160],
            "error": str(value.get("error", ""))[:2000],
            "telemetry": (
                dict(value.get("telemetry"))
                if isinstance(value.get("telemetry"), dict)
                else {}
            ),
        }
        atomic_write_json(directory / "result.json", result)
        return {"accepted": True, "request_id": request["request_id"]}


def _wait_for_document(
    config: Mapping[str, Any],
    request_id: str,
    filename: str,
    *,
    timeout_seconds: float,
    stop_path: Path | None = None,
) -> dict[str, Any]:
    directory = request_directory(config, request_id)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        value = optional_json(directory / filename)
        if value is not None:
            return value
        cancellation = optional_json(directory / "cancelled.json")
        if cancellation is not None:
            raise HostExecutorProtocolError(
                str(cancellation.get("reason") or "Host execution was cancelled.")
            )
        if stop_path is not None and stop_path.is_file():
            raise HostExecutorProtocolError(
                "Emergency stop requested before host execution completed."
            )
        if filename == "ready.json":
            result = optional_json(directory / "result.json")
            if result is not None:
                raise HostExecutorProtocolError(
                    str(result.get("error") or result.get("phase") or (
                        "The Windows host bridge failed before READY."
                    ))
                )
        time.sleep(0.1)
    raise HostExecutorProtocolError(
        f"Timed out waiting for Windows host executor {filename}."
    )


def wait_for_host_ready(
    config: Mapping[str, Any],
    request_id: str,
    *,
    timeout_seconds: float,
    stop_path: Path | None = None,
) -> dict[str, Any]:
    return _wait_for_document(
        config,
        request_id,
        "ready.json",
        timeout_seconds=timeout_seconds,
        stop_path=stop_path,
    )


def wait_for_host_result(
    config: Mapping[str, Any],
    request_id: str,
    *,
    timeout_seconds: float,
    stop_path: Path | None = None,
) -> dict[str, Any]:
    return _wait_for_document(
        config,
        request_id,
        "result.json",
        timeout_seconds=timeout_seconds,
        stop_path=stop_path,
    )


def cancel_host_execution(
    config: Mapping[str, Any],
    request_id: str,
    reason: str,
) -> None:
    with _STATE_LOCK:
        directory = request_directory(config, request_id)
        if directory.is_dir() and not (directory / "result.json").is_file():
            atomic_write_json(
                directory / "cancelled.json",
                {
                    "schema": "iag.host_executor_cancelled.v1",
                    "request_id": request_id,
                    "cancelled_at": now_iso(),
                    "reason": reason[:2000],
                },
            )
        pointer = optional_json(active_pointer_path(config))
        if pointer and pointer.get("request_id") == request_id:
            active_pointer_path(config).unlink(missing_ok=True)


def complete_host_execution(
    config: Mapping[str, Any],
    request_id: str,
) -> None:
    with _STATE_LOCK:
        pointer = optional_json(active_pointer_path(config))
        if pointer and pointer.get("request_id") == request_id:
            active_pointer_path(config).unlink(missing_ok=True)
