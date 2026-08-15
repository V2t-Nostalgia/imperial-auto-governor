#!/usr/bin/env python3
"""One-shot Linux NFQUEUE interceptor for the non-host Stellaris client."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # Windows imports this module only for platform-neutral tests.
    fcntl = None  # type: ignore[assignment]

from iag.stellaris.execution.linux_packet import (
    expected_authoritative_needle,
    parse_ipv4_udp,
    replace_udp_payload,
    rewrite_stellaris_payload,
    validate_host_ip,
)


CAP_NET_ADMIN = 12


@dataclass
class RuntimeState:
    host_ip: str
    host_port: int
    action: dict[str, Any]
    log_path: Path
    dry_run: bool
    status_path: Path | None = None
    replaced: bool = False
    carrier_seen: bool = False
    confirmed: bool = False
    replacement_time: float | None = None
    stop: bool = False
    inbound_tail: bytes = b""
    errors: list[str] = field(default_factory=list)
    phase: str = "initializing"
    outbound_packets: int = 0
    inbound_packets: int = 0
    local_udp_port: int | None = None
    host_destination_port: int | None = None
    host_source_port: int | None = None
    carrier_local_udp_port: int | None = None
    carrier_host_destination_port: int | None = None
    last_status_write: float = 0.0


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def has_effective_capability(
    capability_number: int,
    *,
    status_path: Path = Path("/proc/self/status"),
) -> bool:
    try:
        lines = status_path.read_text(encoding="ascii", errors="replace").splitlines()
    except (FileNotFoundError, PermissionError):
        return False
    for line in lines:
        if not line.startswith("CapEff:"):
            continue
        try:
            effective = int(line.split(":", 1)[1].strip(), 16)
        except ValueError:
            return False
        return bool(effective & (1 << capability_number))
    return False


def interceptor_lock_path(
    queue_number: int,
    *,
    status_path: Path | None,
    log_path: Path,
) -> Path:
    root = status_path.parent if status_path is not None else log_path.parent
    return root / f"iag_agent_{queue_number}.lock"


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        )
        handle.flush()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def telemetry_document(state: RuntimeState) -> dict[str, Any]:
    return {
        "schema": "iag.interceptor_telemetry.v1",
        "updated_at": now_iso(),
        "phase": state.phase,
        "host_ip": state.host_ip,
        "configured_host_port": state.host_port,
        "local_udp_port": state.local_udp_port,
        "host_destination_port": state.host_destination_port,
        "host_source_port": state.host_source_port,
        "port_locked": state.carrier_seen,
        "outbound_packets": state.outbound_packets,
        "inbound_packets": state.inbound_packets,
        "carrier_seen": state.carrier_seen,
        "rewritten": state.replaced,
        "authoritative_confirmation": state.confirmed,
        "dry_run": state.dry_run,
        "errors": state.errors,
    }


def publish_telemetry(state: RuntimeState, *, force: bool = False) -> None:
    if state.status_path is None:
        return
    current = time.monotonic()
    if not force and current - state.last_status_write < 0.5:
        return
    atomic_write_json(state.status_path, telemetry_document(state))
    state.last_status_write = current


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def run_nft(script: str, *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["nft", "-f", "-"],
        input=script.encode("ascii"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def nft_rules(table: str, host_ip: str, host_port: int, queue_num: int) -> str:
    outbound = f"ip daddr {host_ip} meta l4proto udp"
    inbound = f"ip saddr {host_ip} meta l4proto udp"
    if host_port:
        outbound += f" udp dport {host_port}"
        inbound += f" udp sport {host_port}"
    return f"""table inet {table} {{
 chain output {{
  type filter hook output priority -150; policy accept;
  {outbound} queue num {queue_num} bypass
 }}
 chain input {{
  type filter hook input priority -150; policy accept;
  {inbound} queue num {queue_num} bypass
 }}
}}
"""


def remove_nft_table(table: str) -> None:
    run_nft(f"delete table inet {table}\n", check=False)


def port_matches(state: RuntimeState, source_port: int, destination_port: int, outbound: bool) -> bool:
    if state.host_port == 0:
        return True
    return (
        destination_port == state.host_port
        if outbound
        else source_port == state.host_port
    )


def packet_handler(state: RuntimeState, packet: Any) -> None:
    raw = bytes(packet.get_payload())
    view = parse_ipv4_udp(raw)
    if view is None:
        packet.accept()
        return

    outbound = view.destination_ip == state.host_ip
    inbound = view.source_ip == state.host_ip
    if not outbound and not inbound:
        packet.accept()
        return
    if not port_matches(
        state,
        view.source_port,
        view.destination_port,
        outbound,
    ):
        packet.accept()
        return

    if outbound:
        state.outbound_packets += 1
        if (
            state.carrier_local_udp_port is None
            or view.source_port == state.carrier_local_udp_port
        ):
            state.local_udp_port = view.source_port
            state.host_destination_port = view.destination_port
    if inbound:
        state.inbound_packets += 1
        if (
            state.carrier_local_udp_port is None
            or view.destination_port == state.carrier_local_udp_port
        ):
            state.local_udp_port = view.destination_port
            state.host_source_port = view.source_port
    publish_telemetry(state)

    try:
        if outbound and not state.replaced and not state.carrier_seen:
            result = rewrite_stellaris_payload(view.udp_payload, state.action)
            if result is not None:
                rewritten_payload, metadata = result
                state.carrier_seen = True
                state.carrier_local_udp_port = view.source_port
                state.carrier_host_destination_port = view.destination_port
                state.local_udp_port = view.source_port
                state.host_destination_port = view.destination_port
                event = {
                    "event": "carrier_observed" if state.dry_run else "command_rewritten",
                    "timestamp": now_iso(),
                    "source_port": view.source_port,
                    "destination_port": view.destination_port,
                    "payload_length": len(view.udp_payload),
                    "before_sha256": sha256_hex(view.udp_payload),
                    "after_sha256": sha256_hex(rewritten_payload),
                    **metadata,
                }
                append_event(state.log_path, event)
                if not state.dry_run:
                    modified = replace_udp_payload(raw, view, rewritten_payload)
                    if len(modified) != len(raw):
                        raise RuntimeError("IPv4 packet length changed during rewrite.")
                    packet.set_payload(modified)
                    state.replaced = True
                    state.replacement_time = time.monotonic()
                    state.phase = "awaiting_authoritative_host_command"
                else:
                    state.stop = True
                publish_telemetry(state, force=True)

        if (
            inbound
            and state.replaced
            and view.destination_port == state.carrier_local_udp_port
        ):
            needle = expected_authoritative_needle(state.action)
            combined = state.inbound_tail + view.udp_payload
            if needle and needle in combined and not state.confirmed:
                state.confirmed = True
                state.phase = "authoritative_host_command_confirmed"
                append_event(
                    state.log_path,
                    {
                        "event": "authoritative_host_command_seen",
                        "timestamp": now_iso(),
                        "source_port": view.source_port,
                        "destination_port": view.destination_port,
                        "needle": needle.decode("ascii"),
                    },
                )
                publish_telemetry(state, force=True)
            state.inbound_tail = combined[-512:]
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        state.errors.append(message)
        state.stop = True
        state.phase = "handler_error"
        append_event(
            state.log_path,
            {
                "event": "handler_error",
                "timestamp": now_iso(),
                "error": message,
            },
        )
        publish_telemetry(state, force=True)
    finally:
        packet.accept()


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("schema") != "iag.execution.v1":
        raise ValueError("Unsupported execution manifest schema.")
    safety = manifest.get("safety", {})
    required = (
        "one_shot",
        "preserve_udp_payload_length",
        "preserve_command_count",
        "preserve_carrier_serial",
    )
    if not all(safety.get(key) is True for key in required):
        raise ValueError("Execution manifest is missing required safety invariants.")
    if manifest.get("action", {}).get("type") == "noop":
        return manifest
    expected_authoritative_needle(manifest["action"]).decode("ascii")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--host-ip", required=True)
    parser.add_argument("--host-port", type=int, default=0)
    parser.add_argument("--queue-num", type=int, default=4242)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--observe-seconds", type=int, default=15)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if fcntl is None or not hasattr(os, "geteuid"):
        raise RuntimeError("The NFQUEUE interceptor requires Linux.")
    if os.geteuid() != 0 and not has_effective_capability(CAP_NET_ADMIN):
        raise PermissionError(
            "The NFQUEUE interceptor requires root or CAP_NET_ADMIN."
        )
    if not 1 <= args.queue_num <= 65535:
        raise ValueError("queue-num must be between 1 and 65535.")
    if not 0 <= args.host_port <= 65535:
        raise ValueError("host-port must be between 0 and 65535.")

    try:
        from netfilterqueue import NetfilterQueue
    except ImportError as error:
        raise RuntimeError(
            "Missing NetfilterQueue. Install libnetfilter-queue-dev and the "
            "Python NetfilterQueue package before arming the interceptor."
        ) from error

    manifest = load_manifest(args.manifest)
    action = manifest["action"]
    if action.get("type") == "noop":
        append_event(args.log, {"event": "noop", "timestamp": now_iso()})
        return 0

    host_ip = validate_host_ip(args.host_ip)
    table = f"iag_agent_{args.queue_num}"
    lock_path = interceptor_lock_path(
        args.queue_num,
        status_path=args.status_file,
        log_path=args.log,
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("w", encoding="ascii")
    fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    state = RuntimeState(
        host_ip=host_ip,
        host_port=args.host_port,
        action=action,
        log_path=args.log,
        dry_run=args.dry_run,
        status_path=args.status_file,
    )
    queue = NetfilterQueue()
    callback = lambda packet: packet_handler(state, packet)
    installed = False

    def stop_handler(_signum: int, _frame: Any) -> None:
        state.stop = True

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    deadline = time.monotonic() + args.timeout_seconds

    try:
        remove_nft_table(table)
        queue.bind(args.queue_num, callback, max_len=4096)
        run_nft(nft_rules(table, host_ip, args.host_port, args.queue_num))
        installed = True
        state.phase = "armed_waiting_for_carrier_click"
        start_event = {
            "event": "ready",
            "timestamp": now_iso(),
            "pid": os.getpid(),
            "host_ip": host_ip,
            "host_port": args.host_port,
            "queue_num": args.queue_num,
            "table": table,
            "action": action,
            "dry_run": args.dry_run,
        }
        append_event(args.log, start_event)
        publish_telemetry(state, force=True)
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        args.ready_file.write_text(
            json.dumps(start_event, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        while not state.stop and time.monotonic() < deadline:
            if args.stop_file and args.stop_file.is_file():
                state.stop = True
                state.phase = "emergency_stop_requested"
                append_event(
                    args.log,
                    {
                        "event": "emergency_stop_requested",
                        "timestamp": now_iso(),
                    },
                )
                publish_telemetry(state, force=True)
                break
            queue.run(block=False)
            if (
                state.replaced
                and state.replacement_time is not None
                and time.monotonic() - state.replacement_time
                >= args.observe_seconds
            ):
                break
            time.sleep(0.005)
    finally:
        if installed:
            remove_nft_table(table)
        try:
            queue.unbind()
        except Exception:
            pass
        args.ready_file.unlink(missing_ok=True)
        fcntl.flock(lock_handle, fcntl.LOCK_UN)
        lock_handle.close()
        lock_path.unlink(missing_ok=True)

    if state.phase == "emergency_stop_requested":
        state.phase = "stopped_by_operator"
    elif state.errors:
        state.phase = "stopped_with_error"
    elif not state.replaced and not args.dry_run:
        state.phase = "stopped_without_carrier"
    else:
        state.phase = "stopped"
    publish_telemetry(state, force=True)
    result = {
        "event": "stopped",
        "timestamp": now_iso(),
        "carrier_seen": state.carrier_seen,
        "replaced": state.replaced,
        "authoritative_confirmation": state.confirmed,
        "errors": state.errors,
    }
    append_event(args.log, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if state.errors:
        return 5
    if args.dry_run:
        return 0 if state.carrier_seen else 3
    if not state.replaced:
        return 3
    return 0 if state.confirmed else 4


if __name__ == "__main__":
    raise SystemExit(main())
