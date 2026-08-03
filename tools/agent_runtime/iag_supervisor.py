#!/usr/bin/env python3
"""Execute one planned IAG carrier click through a guarded interceptor."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from host_executor_protocol import (
    HostExecutorProtocolError,
    begin_host_execution,
    cancel_host_execution,
    complete_host_execution,
    local_ipv4_for_remote,
    optional_json,
    save_client_status_path,
    wait_for_host_ready,
    wait_for_host_result,
)
from fixed_click import execute_fixed_click
from port_discovery import discover_session
from save_ingest import (
    SaveIngestError,
    maximum_source_save_lag_versions,
    read_manifest,
    resolve_current_save,
    save_manifest_revision,
)


ROOT = Path(__file__).resolve().parent
INTERCEPTOR = ROOT / "iag_linux_interceptor.py"


class SupervisorError(RuntimeError):
    """Raised when a one-shot execution cannot be started or verified safely."""


class StaleSourceSaveError(SupervisorError):
    """Raised before any click when a plan is no longer bound to the latest save."""


def host_result_awaits_save_confirmation(
    host_result: dict[str, Any],
    telemetry: dict[str, Any],
) -> bool:
    """Return true when rewriting succeeded but packet echo was inconclusive."""
    if not (
        telemetry.get("carrier_seen") is True
        and telemetry.get("rewritten") is True
        and telemetry.get("authoritative_confirmation") is not True
    ):
        return False
    phase = str(host_result.get("phase") or "")
    error = str(host_result.get("error") or "")
    return (
        phase == "rewritten_without_authoritative_confirmation"
        or "未看到房主权威广播" in error
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as error:
        raise SupervisorError(f"Required file does not exist: {path}") from error
    if not isinstance(value, dict):
        raise SupervisorError(f"{path} must contain a JSON object.")
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_path(config: dict[str, Any], key: str, default: Path) -> Path:
    value = Path(config.get(key, default)).expanduser()
    if value.is_absolute():
        return value
    return Path(config["runtime_root"]).expanduser() / value


def carrier_click_profile_path(
    config: dict[str, Any],
    action_type: str,
) -> Path:
    runtime_root = Path(config["runtime_root"]).expanduser()
    default_names = {
        "build_building": "carrier_click.json",
        "build_district": "carrier_district_click.json",
        "build_zone": "carrier_zone_click.json",
        "upgrade_building": "carrier_upgrade_click.json",
        "replace_building": "carrier_replacement_click.json",
    }
    if action_type not in default_names:
        raise SupervisorError(f"Unsupported carrier action {action_type}.")
    profiles = config.get("carrier_click_profiles")
    value = profiles.get(action_type) if isinstance(profiles, dict) else None
    if not value and action_type == "build_building":
        value = config.get("carrier_click_profile")
    path = Path(value or (Path("calibration") / default_names[action_type]))
    if path.is_absolute():
        return path.expanduser()
    return runtime_root / path


def carrier_navigation_profile_path(
    config: dict[str, Any],
    action_type: str,
) -> Path | None:
    """Return the optional panel-opening click profile for one carrier."""
    default_names = {
        "build_building": "carrier_building_open.json",
        "build_zone": "carrier_zone_open.json",
        "upgrade_building": "carrier_upgrade_open.json",
        "replace_building": "carrier_replacement_open.json",
    }
    if action_type == "build_district":
        return None
    if action_type not in default_names:
        raise SupervisorError(f"Unsupported carrier action {action_type}.")
    runtime_root = Path(config["runtime_root"]).expanduser()
    profiles = config.get("carrier_navigation_profiles")
    value = profiles.get(action_type) if isinstance(profiles, dict) else None
    path = Path(value or (Path("calibration") / default_names[action_type]))
    if path.is_absolute():
        return path.expanduser()
    return runtime_root / path


def carrier_intermediate_profile_path(
    config: dict[str, Any],
    action_type: str,
) -> Path | None:
    """Return an action-specific click between panel opening and command."""
    if action_type != "replace_building":
        return None
    runtime_root = Path(config["runtime_root"]).expanduser()
    profiles = config.get("carrier_intermediate_profiles")
    value = profiles.get(action_type) if isinstance(profiles, dict) else None
    path = Path(
        value
        or (Path("calibration") / "carrier_replacement_button.json")
    )
    if path.is_absolute():
        return path.expanduser()
    return runtime_root / path


def carrier_click_sequence_paths(
    config: dict[str, Any],
    action_type: str,
) -> list[Path]:
    """Return guarded clicks in the exact order used for one carrier action."""
    command_profile = carrier_click_profile_path(config, action_type)
    if not bool(config.get("carrier_navigation_enabled", False)):
        return [command_profile]
    navigation_profile = carrier_navigation_profile_path(config, action_type)
    if navigation_profile is None:
        return [command_profile]
    intermediate_profile = carrier_intermediate_profile_path(
        config,
        action_type,
    )
    return [
        profile
        for profile in (
            navigation_profile,
            intermediate_profile,
            command_profile,
        )
        if profile is not None
    ]


def execute_carrier_click_sequence(
    profile_paths: list[Path],
    *,
    config: dict[str, Any],
    artifact_root: Path,
) -> dict[str, Any]:
    """Execute a guarded, fixed-coordinate carrier sequence after READY."""
    results: list[dict[str, Any]] = []
    delay = max(float(config.get("carrier_click_step_delay_seconds", 0.45)), 0.0)
    click_timing = {
        "guard_enabled": bool(config.get("fixed_click_guard_enabled", True)),
        "pointer_settle_seconds": max(
            float(config.get("carrier_pointer_settle_seconds", 0.20)), 0.0
        ),
        "click_hold_seconds": max(
            float(config.get("carrier_click_hold_seconds", 0.08)), 0.0
        ),
        "post_click_settle_seconds": max(
            float(config.get("carrier_post_click_settle_seconds", 0.25)), 0.0
        ),
    }
    for index, profile_path in enumerate(profile_paths):
        results.append(
            execute_fixed_click(
                profile_path,
                display=str(config.get("display", ":1")),
                xauthority=config.get("xauthority"),
                artifact_root=artifact_root,
                **click_timing,
            )
        )
        if index + 1 < len(profile_paths) and delay:
            time.sleep(delay)
    return {
        "schema": "iag.fixed_click_sequence_result.v1",
        "executed_at": now_iso(),
        "step_count": len(results),
        "steps": results,
    }


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != "iag.execution.v1":
        raise SupervisorError("Unsupported execution manifest schema.")
    action = manifest.get("action")
    if not isinstance(action, dict) or action.get("type") not in {
        "build_building",
        "build_district",
        "build_zone",
        "upgrade_building",
        "replace_building",
    }:
        raise SupervisorError("The selected run has no executable construction action.")
    safety = manifest.get("safety", {})
    required = (
        "one_shot",
        "preserve_udp_payload_length",
        "preserve_command_count",
        "preserve_carrier_serial",
        "require_authoritative_host_confirmation",
    )
    if not all(safety.get(key) is True for key in required):
        raise SupervisorError("The manifest does not enforce all execution invariants.")


def validate_source_save(manifest: dict[str, Any], config: dict[str, Any]) -> Path:
    source_value = manifest.get("source_save_path")
    expected_hash = manifest.get("source_save_sha256")
    if not source_value or not expected_hash:
        raise SupervisorError("The plan is missing its source save identity.")
    source = Path(str(source_value)).expanduser()
    if not source.is_file():
        raise StaleSourceSaveError(f"The source save is no longer present: {source}")
    if sha256_file(source) != expected_hash:
        raise StaleSourceSaveError(
            "The source save changed after planning. Re-plan before clicking."
        )
    maximum_age = int(config.get("require_fresh_save_seconds", 900))
    age = max(time.time() - source.stat().st_mtime, 0.0)
    if maximum_age > 0 and age > maximum_age:
        raise StaleSourceSaveError(
            f"The source save is {age:.0f}s old; the limit is {maximum_age}s."
        )
    latest = resolve_current_save(config)
    if latest.resolve() != source.resolve():
        current_manifest = read_manifest(config) or {}
        source_campaign = str(manifest.get("source_campaign_id") or "")
        current_campaign = str(current_manifest.get("campaign_id") or "")
        try:
            source_revision = int(manifest.get("source_save_revision") or 0)
        except (TypeError, ValueError):
            source_revision = 0
        current_revision = save_manifest_revision(config, current_manifest) or 0
        if source_campaign and current_campaign and source_campaign != current_campaign:
            raise StaleSourceSaveError(
                "The synchronized save belongs to a different campaign."
            )
        if source_revision > 0 and current_revision > 0:
            lag = current_revision - source_revision
            if lag < 0:
                raise StaleSourceSaveError(
                    "The source save revision is newer than the synchronized campaign state."
                )
            try:
                maximum_lag = maximum_source_save_lag_versions(config)
            except SaveIngestError as error:
                raise SupervisorError(str(error)) from error
            if lag > maximum_lag:
                raise StaleSourceSaveError(
                    "The plan source is "
                    f"{lag} synchronized save version(s) behind; "
                    f"the configured limit is {maximum_lag}."
                )
        elif latest.stat().st_mtime > source.stat().st_mtime:
            # Pre-revision plans retain the original strict-latest behavior.
            raise StaleSourceSaveError(
                "A newer synchronized save exists and the plan has no upload revision. "
                "Re-plan from the latest state."
            )
    return source


def host_ip_from_discovery(discovery: dict[str, Any]) -> str | None:
    """Return only a current, unambiguous endpoint from one discovery pass."""
    telemetry = discovery.get("telemetry") or {}
    verification = discovery.get("port_verification") or {}
    telemetry_fresh = verification.get("telemetry_fresh") is True
    source = telemetry.get("source")

    if telemetry_fresh and source == "passive_observer":
        if telemetry.get("candidate_active"):
            return telemetry.get("host_ip")
        return None
    if telemetry_fresh and source == "interceptor":
        if telemetry.get("carrier_seen") or telemetry.get(
            "authoritative_confirmation"
        ):
            return telemetry.get("host_ip")

    endpoint = discovery.get("selected_endpoint") or {}
    if not endpoint:
        return None
    endpoint_source = endpoint.get("source")
    if endpoint_source == "passive_observer":
        if not telemetry_fresh or not telemetry.get("candidate_active"):
            return None
    elif endpoint_source == "interceptor":
        if not telemetry_fresh or not telemetry.get("carrier_seen"):
            return None
    return endpoint.get("remote_ip")


def resolve_host_ip(config: dict[str, Any], telemetry_path: Path) -> str:
    configured = str(config.get("host_ip", "")).strip()
    if configured:
        address = ipaddress.ip_address(configured)
        if address.version != 4:
            raise SupervisorError("The host bridge execution path currently supports IPv4 only.")
        return str(address)

    # The save/host bridge is an authenticated, continuously refreshed source
    # of the host address. Prefer it over transient process socket discovery so
    # a Steam relay port rotation does not make execution lose its host.
    client = optional_json(save_client_status_path(config)) or {}
    heartbeat_timeout = max(
        float(config.get("save_client_heartbeat_timeout_seconds", 15)),
        5.0,
    )
    heartbeat_age = max(
        time.time() - float(client.get("last_seen_epoch", 0) or 0),
        0.0,
    )
    bridge_source_ip = str(client.get("source_ip", "")).strip()
    if (
        client.get("state") == "running"
        and heartbeat_age <= heartbeat_timeout
        and bridge_source_ip
    ):
        try:
            bridge_address = ipaddress.ip_address(bridge_source_ip)
        except ValueError:
            bridge_address = None
        if (
            bridge_address is not None
            and bridge_address.version == 4
            and not bridge_address.is_unspecified
        ):
            return str(bridge_address)
    passive_telemetry_path = runtime_path(
        config,
        "passive_telemetry_path",
        Path(config["runtime_root"]).expanduser() / "state" / "passive_flow_status.json",
    )
    try:
        timeout_seconds = float(config.get("host_discovery_timeout_seconds", 20))
        poll_seconds = float(config.get("host_discovery_poll_seconds", 0.5))
        telemetry_max_age = int(
            config.get("network_telemetry_max_age_seconds", 15)
        )
    except (TypeError, ValueError) as error:
        raise SupervisorError("Invalid host discovery timing configuration.") from error
    if timeout_seconds < 0 or poll_seconds <= 0 or telemetry_max_age <= 0:
        raise SupervisorError("Host discovery timing values must be positive.")

    deadline = time.monotonic() + timeout_seconds
    last_state = "not_observed"
    while True:
        discovery = discover_session(
            config.get("process_names", ["stellaris"]),
            transport_process_names=config.get(
                "transport_process_names", ["steam"]
            ),
            telemetry_path=telemetry_path,
            passive_telemetry_path=passive_telemetry_path,
            telemetry_max_age_seconds=telemetry_max_age,
        )
        verification = discovery.get("port_verification") or {}
        last_state = str(verification.get("state") or discovery.get("confidence"))
        remote_ip = host_ip_from_discovery(discovery)
        if remote_ip not in {None, "0.0.0.0", "::"}:
            try:
                address = ipaddress.ip_address(str(remote_ip))
            except ValueError as error:
                raise SupervisorError("The discovered host IP is invalid.") from error
            if address.version != 4:
                raise SupervisorError("The discovered host is not IPv4.")
            return str(address)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_seconds, remaining))

    raise SupervisorError(
        "No current unique Stellaris peer was discovered after "
        f"waiting {timeout_seconds:g}s (last state: {last_state})."
    )


class ExclusiveExecutionLock:
    def __init__(self, path: Path):
        self.path = path
        self.descriptor: int | None = None

    def __enter__(self) -> "ExclusiveExecutionLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as error:
            raise SupervisorError(
                "Another IAG execution is active or left a lock file. Inspect it before removal."
            ) from error
        os.write(self.descriptor, f"{os.getpid()}\n".encode("ascii"))
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
        self.path.unlink(missing_ok=True)


def interceptor_command(
    config: dict[str, Any],
    *,
    manifest_path: Path,
    host_ip: str,
    log_path: Path,
    ready_path: Path,
    telemetry_path: Path,
    stop_path: Path,
) -> list[str]:
    python = str(config.get("interceptor_python", sys.executable))
    command = [python, str(INTERCEPTOR)]
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        privilege_mode = str(
            config.get("interceptor_privilege_mode", "capability")
        ).strip()
        if privilege_mode == "sudo":
            command = [str(config.get("sudo_command", "sudo")), "-n"] + command
        elif privilege_mode != "capability":
            raise SupervisorError(
                "interceptor_privilege_mode must be capability or sudo."
            )
    command.extend(
        [
            "--manifest",
            str(manifest_path),
            "--host-ip",
            host_ip,
            "--host-port",
            "0",
            "--queue-num",
            str(int(config.get("nfqueue_number", 4242))),
            "--timeout-seconds",
            str(int(config.get("interceptor_timeout_seconds", 120))),
            "--observe-seconds",
            str(int(config.get("observe_seconds", 15))),
            "--log",
            str(log_path),
            "--ready-file",
            str(ready_path),
            "--status-file",
            str(telemetry_path),
            "--stop-file",
            str(stop_path),
        ]
    )
    return command


def wait_until_ready(
    process: subprocess.Popen[str],
    ready_path: Path,
    *,
    timeout_seconds: int,
    stderr_path: Path | None = None,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if ready_path.is_file():
            return
        return_code = process.poll()
        if return_code is not None:
            detail = ""
            if stderr_path is not None:
                try:
                    lines = stderr_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                except (FileNotFoundError, PermissionError):
                    lines = []
                if lines:
                    detail = f": {lines[-1][:500]}"
            raise SupervisorError(
                f"The interceptor exited with code {return_code} before READY{detail}."
            )
        time.sleep(0.1)
    raise SupervisorError("The interceptor did not become READY in time.")


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def execute_via_windows_host_bridge(
    *,
    run_dir: Path,
    config: dict[str, Any],
    manifest: dict[str, Any],
    manifest_path: Path,
    source_save: Path,
    host_ip: str,
    profile_paths: list[Path],
) -> dict[str, Any]:
    """Arm the host's inbound WinDivert bridge before issuing the carrier click."""
    runtime_root = Path(config["runtime_root"]).expanduser()
    stop_path = runtime_root / "state" / "emergency_stop"
    lock_path = runtime_root / "state" / "execution.lock"
    result_path = run_dir / "execution_result.json"
    started_at = now_iso()
    request: dict[str, Any] | None = None
    click_result: dict[str, Any] | None = None
    host_result: dict[str, Any] | None = None
    peer_ip = local_ipv4_for_remote(host_ip)

    with ExclusiveExecutionLock(lock_path):
        if stop_path.is_file():
            raise SupervisorError(
                "Emergency stop is active. Clear it before autonomous execution."
            )
        try:
            request = begin_host_execution(
                config,
                run_id=run_dir.name,
                manifest=manifest,
                host_ip=host_ip,
                peer_ip=peer_ip,
            )
            atomic_write_json(run_dir / "host_executor_request.json", request)
            ready = wait_for_host_ready(
                config,
                str(request["request_id"]),
                timeout_seconds=float(
                    config.get("host_executor_ready_timeout_seconds", 30)
                ),
                stop_path=stop_path,
            )
            atomic_write_json(run_dir / "host_executor_ready.json", ready)

            # The click is deliberately after the host proves that its elevated
            # WinDivert handle is already open.
            click_result = execute_carrier_click_sequence(
                profile_paths,
                config=config,
                artifact_root=run_dir,
            )
            host_result = wait_for_host_result(
                config,
                str(request["request_id"]),
                timeout_seconds=(
                    float(config.get("interceptor_timeout_seconds", 120))
                    + float(config.get("observe_seconds", 15))
                    + 10
                ),
                stop_path=stop_path,
            )
            atomic_write_json(run_dir / "host_executor_result.json", host_result)
        except Exception as error:
            if request is not None:
                cancel_host_execution(
                    config,
                    str(request["request_id"]),
                    f"{type(error).__name__}: {error}",
                )
            if isinstance(error, SupervisorError):
                raise
            if isinstance(error, HostExecutorProtocolError):
                raise SupervisorError(str(error)) from error
            raise

    assert request is not None
    assert host_result is not None
    complete_host_execution(config, str(request["request_id"]))
    telemetry = {
        "schema": "iag.interceptor_telemetry.v1",
        "updated_at": host_result.get("received_at") or now_iso(),
        "source": "windows_host_bridge",
        "phase": host_result.get("phase"),
        "host_ip": host_ip,
        "peer_ip": peer_ip,
        "carrier_seen": host_result.get("carrier_seen") is True,
        "rewritten": host_result.get("rewritten") is True,
        "authoritative_confirmation": (
            host_result.get("authoritative_confirmation") is True
        ),
        "errors": [host_result.get("error")] if host_result.get("error") else [],
        "details": host_result.get("telemetry", {}),
    }
    success = (
        host_result.get("success") is True
        and telemetry["carrier_seen"]
        and telemetry["rewritten"]
        and telemetry["authoritative_confirmation"]
    )
    awaiting_save_confirmation = host_result_awaits_save_confirmation(
        host_result,
        telemetry,
    )
    confirmation_state = (
        "confirmed_by_packet"
        if success
        else (
            "pending_save_confirmation"
            if awaiting_save_confirmation
            else "failed"
        )
    )
    result = {
        "schema": "iag.execution_result.v1",
        "started_at": started_at,
        "finished_at": now_iso(),
        "success": success,
        "confirmation_state": confirmation_state,
        "execution_transport": "windows_host_bridge",
        "host_ip": host_ip,
        "peer_ip": peer_ip,
        "source_save": str(source_save),
        "manifest": str(manifest_path),
        "click": click_result,
        "telemetry": telemetry,
        "artifacts": {
            "host_request": str(run_dir / "host_executor_request.json"),
            "host_ready": str(run_dir / "host_executor_ready.json"),
            "host_result": str(run_dir / "host_executor_result.json"),
        },
    }
    atomic_write_json(result_path, result)
    if not success and not awaiting_save_confirmation:
        detail = str(host_result.get("error") or host_result.get("phase") or "")
        suffix = f": {detail}" if detail else ""
        raise SupervisorError(
            "The Windows host bridge did not confirm the rewritten command"
            f"{suffix}."
        )
    return result

def execute_run(run_dir: Path, config_path: Path) -> dict[str, Any]:
    config = read_json(config_path)
    manifest_path = run_dir / "execution_manifest.json"
    manifest = read_json(manifest_path)
    validate_manifest(manifest)
    source_save = validate_source_save(manifest, config)

    runtime_root = Path(config["runtime_root"]).expanduser()
    telemetry_path = runtime_path(
        config,
        "interceptor_status_path",
        runtime_root / "state" / "interceptor_status.json",
    )
    host_ip = resolve_host_ip(config, telemetry_path)
    action_type = str(manifest["action"]["type"])
    profile_paths = carrier_click_sequence_paths(config, action_type)
    missing_profiles = [
        profile_path for profile_path in profile_paths if not profile_path.is_file()
    ]
    if missing_profiles:
        raise SupervisorError(
            f"The fixed click sequence for {action_type} is incomplete: "
            + ", ".join(str(path) for path in missing_profiles)
        )

    execution_transport = str(
        config.get("execution_transport", "windows_host_bridge")
    ).strip()
    if execution_transport == "windows_host_bridge":
        return execute_via_windows_host_bridge(
            run_dir=run_dir,
            config=config,
            manifest=manifest,
            manifest_path=manifest_path,
            source_save=source_save,
            host_ip=host_ip,
            profile_paths=profile_paths,
        )
    if execution_transport != "linux_client_nfqueue":
        raise SupervisorError(
            "execution_transport must be windows_host_bridge or "
            "linux_client_nfqueue."
        )
    if action_type == "replace_building":
        raise SupervisorError(
            "Building replacement requires the production Windows host bridge."
        )

    # Retained only as an explicit research fallback. The production route is
    # host-side inbound rewriting, which was validated in live co-op tests.
    log_path = run_dir / "interceptor.jsonl"
    ready_path = run_dir / "interceptor.ready.json"
    stdout_path = run_dir / "interceptor.stdout.log"
    stderr_path = run_dir / "interceptor.stderr.log"
    result_path = run_dir / "execution_result.json"
    stop_path = runtime_root / "state" / "emergency_stop"
    command = interceptor_command(
        config,
        manifest_path=manifest_path,
        host_ip=host_ip,
        log_path=log_path,
        ready_path=ready_path,
        telemetry_path=telemetry_path,
        stop_path=stop_path,
    )
    lock_path = runtime_root / "state" / "execution.lock"
    started_at = now_iso()

    with ExclusiveExecutionLock(lock_path):
        stop_path.unlink(missing_ok=True)
        ready_path.unlink(missing_ok=True)
        with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_handle:
            process = subprocess.Popen(
                command,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
            )
            click_result: dict[str, Any] | None = None
            try:
                wait_until_ready(
                    process,
                    ready_path,
                    timeout_seconds=int(config.get("interceptor_ready_timeout_seconds", 20)),
                    stderr_path=stderr_path,
                )
                click_result = execute_carrier_click_sequence(
                    profile_paths,
                    config=config,
                    artifact_root=run_dir,
                )
                maximum_wait = (
                    int(config.get("interceptor_timeout_seconds", 120))
                    + int(config.get("observe_seconds", 15))
                    + 10
                )
                return_code = process.wait(timeout=maximum_wait)
            except Exception:
                stop_process(process)
                raise

    telemetry = read_json(telemetry_path)
    success = (
        return_code == 0
        and telemetry.get("rewritten") is True
        and telemetry.get("authoritative_confirmation") is True
    )
    result = {
        "schema": "iag.execution_result.v1",
        "started_at": started_at,
        "finished_at": now_iso(),
        "success": success,
        "interceptor_return_code": return_code,
        "host_ip": host_ip,
        "source_save": str(source_save),
        "manifest": str(manifest_path),
        "click": click_result,
        "telemetry": telemetry,
        "artifacts": {
            "interceptor_log": str(log_path),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        },
    }
    atomic_write_json(result_path, result)
    if not success:
        raise SupervisorError(
            "The click was not confirmed by the authoritative host response. No retry was attempted."
        )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = execute_run(args.run_dir, args.config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
