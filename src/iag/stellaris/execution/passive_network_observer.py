#!/usr/bin/env python3
"""Passively identify the active Stellaris UDP transport without changing packets."""

from __future__ import annotations

import argparse
import ctypes
import ipaddress
import json
import os
import signal
import socket
import struct
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from iag.stellaris.execution.port_discovery import discover_process_sockets


ETH_P_IP = 0x0800
ETH_P_ALL = 0x0003
VLAN_ETHERTYPES = {0x8100, 0x88A8}
RELIABLE_PREFIX = bytes.fromhex("0100000000")
RELIABLE_VARIANT_BYTES = frozenset((0x00, 0x01))
RELIABLE_MARKER = RELIABLE_PREFIX + b"\x00"
COMMAND_MARKER = bytes.fromhex("f301010003004002")
CARRIER_NEEDLES = (b"building_", b"zone_")
SIOCGIFADDR = 0x8915
SO_ATTACH_FILTER = 26
BPF_LD_H_ABS = 0x28
BPF_LD_B_ABS = 0x30
BPF_LDX_B_MSH = 0xB1
BPF_LD_H_IND = 0x48
BPF_JMP_JEQ_K = 0x15
BPF_RET_K = 0x06


class SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class SockFprog(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ushort),
        ("filters", ctypes.POINTER(SockFilter)),
    ]


@dataclass(frozen=True)
class DatagramView:
    source_ip: str
    destination_ip: str
    source_port: int
    destination_port: int
    payload: bytes


def is_reliable_transport_payload(payload: bytes) -> bool:
    """Recognize the reliable transport marker variants observed on both legs."""
    marker_length = len(RELIABLE_PREFIX) + 1
    return (
        len(payload) >= marker_length
        and payload.startswith(RELIABLE_PREFIX)
        and payload[len(RELIABLE_PREFIX)] in RELIABLE_VARIANT_BYTES
    )


@dataclass(frozen=True)
class FlowEvent:
    observed_mono: float
    observed_at: str
    direction: str
    local_ip: str
    local_port: int
    remote_port: int
    payload_length: int
    reliable_marker: bool
    command_marker: bool
    carrier_text: bool
    owner_names: tuple[str, ...]


@dataclass
class PeerFlow:
    remote_ip: str
    events: deque[FlowEvent] = field(default_factory=deque)

    def observe(self, event: FlowEvent) -> None:
        self.events.append(event)

    def prune(self, cutoff: float) -> None:
        while self.events and self.events[0].observed_mono < cutoff:
            self.events.popleft()

    @staticmethod
    def dominant_value(events: list[FlowEvent], attribute: str) -> int | None:
        if not events:
            return None
        counts = Counter(getattr(event, attribute) for event in events)
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    def summary(self, configured_host_ip: str | None) -> dict[str, Any] | None:
        if not self.events:
            return None
        outbound = [event for event in self.events if event.direction == "outbound"]
        inbound = [event for event in self.events if event.direction == "inbound"]
        reliable_out = [event for event in outbound if event.reliable_marker]
        reliable_in = [event for event in inbound if event.reliable_marker]
        selected_out = reliable_out or outbound
        selected_in = reliable_in or inbound
        local_out_port = self.dominant_value(selected_out, "local_port")
        local_in_port = self.dominant_value(selected_in, "local_port")
        host_out_port = self.dominant_value(selected_out, "remote_port")
        host_in_port = self.dominant_value(selected_in, "remote_port")

        stream_out = [
            event
            for event in outbound
            if local_out_port is None
            or (
                event.local_port == local_out_port
                and event.remote_port == host_out_port
            )
        ]
        stream_in = [
            event
            for event in inbound
            if local_in_port is None
            or (
                event.local_port == local_in_port
                and event.remote_port == host_in_port
            )
        ]
        stream_events = stream_out + stream_in
        owner_names = sorted(
            {
                owner
                for event in stream_events
                for owner in event.owner_names
            }
        )
        command_count = sum(event.command_marker for event in stream_events)
        carrier_count = sum(event.carrier_text for event in stream_events)
        reliable_out_count = sum(event.reliable_marker for event in stream_out)
        reliable_in_count = sum(event.reliable_marker for event in stream_in)
        bidirectional = bool(stream_out and stream_in)
        reliable_bidirectional = bool(reliable_out_count and reliable_in_count)

        score = min(len(stream_events), 250)
        if bidirectional:
            score += 500
        if reliable_bidirectional:
            score += 700
        elif reliable_out_count or reliable_in_count:
            score += 250
        score += min(command_count, 5) * 300
        score += min(carrier_count, 2) * 600
        if configured_host_ip and self.remote_ip == configured_host_ip:
            score += 2000
        try:
            if ipaddress.ip_address(self.remote_ip).is_private:
                score += 50
        except ValueError:
            pass
        if "stellaris" in owner_names:
            score += 80
        if "steam" in owner_names:
            score += 40

        return {
            "remote_ip": self.remote_ip,
            "local_udp_port": local_out_port or local_in_port,
            "local_inbound_port": local_in_port,
            "host_destination_port": host_out_port,
            "host_source_port": host_in_port,
            "outbound_packets": len(stream_out),
            "inbound_packets": len(stream_in),
            "reliable_outbound_packets": reliable_out_count,
            "reliable_inbound_packets": reliable_in_count,
            "command_marker_packets": command_count,
            "carrier_text_packets": carrier_count,
            "owner_names": owner_names,
            "bidirectional": bidirectional,
            "reliable_bidirectional": reliable_bidirectional,
            "last_packet_at": max(event.observed_at for event in stream_events),
            "last_packet_mono": max(event.observed_mono for event in stream_events),
            "local_ip": (
                Counter(event.local_ip for event in stream_events).most_common(1)[0][0]
                if stream_events
                else None
            ),
            "score": score,
        }


class FlowTracker:
    def __init__(self, window_seconds: float):
        self.window_seconds = window_seconds
        self.peers: dict[str, PeerFlow] = {}

    def observe(
        self,
        view: DatagramView,
        *,
        direction: str,
        owner_names: Iterable[str],
        now_mono: float,
        observed_at: str,
    ) -> None:
        if direction == "outbound":
            remote_ip = view.destination_ip
            local_ip = view.source_ip
            local_port = view.source_port
            remote_port = view.destination_port
        else:
            remote_ip = view.source_ip
            local_ip = view.destination_ip
            local_port = view.destination_port
            remote_port = view.source_port
        event = FlowEvent(
            observed_mono=now_mono,
            observed_at=observed_at,
            direction=direction,
            local_ip=local_ip,
            local_port=local_port,
            remote_port=remote_port,
            payload_length=len(view.payload),
            reliable_marker=is_reliable_transport_payload(view.payload),
            command_marker=COMMAND_MARKER in view.payload,
            carrier_text=any(needle in view.payload for needle in CARRIER_NEEDLES),
            owner_names=tuple(sorted(set(owner_names))),
        )
        self.peers.setdefault(remote_ip, PeerFlow(remote_ip)).observe(event)

    def summaries(
        self,
        *,
        now_mono: float,
        configured_host_ip: str | None,
    ) -> list[dict[str, Any]]:
        cutoff = now_mono - self.window_seconds
        summaries: list[dict[str, Any]] = []
        for remote_ip, flow in list(self.peers.items()):
            flow.prune(cutoff)
            if not flow.events:
                del self.peers[remote_ip]
                continue
            summary = flow.summary(configured_host_ip)
            if summary:
                summaries.append(summary)
        return sorted(summaries, key=lambda item: item["score"], reverse=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_udp_port_filter(ports: Iterable[int]) -> list[tuple[int, int, int, int]]:
    """Build a classic BPF program that admits IPv4 UDP on owned ports only."""
    normalized = sorted({int(port) for port in ports if 1 <= int(port) <= 65535})
    if not normalized:
        return [(BPF_RET_K, 0, 0, 0)]
    instructions: list[tuple[int, int, int, int]] = [
        (BPF_LD_H_ABS, 0, 0, 12),
        (BPF_JMP_JEQ_K, 1, 0, ETH_P_IP),
        (BPF_RET_K, 0, 0, 0),
        (BPF_LD_B_ABS, 0, 0, 23),
        (BPF_JMP_JEQ_K, 1, 0, socket.IPPROTO_UDP),
        (BPF_RET_K, 0, 0, 0),
        (BPF_LDX_B_MSH, 0, 0, 14),
    ]
    for port_offset in (14, 16):
        instructions.append((BPF_LD_H_IND, 0, 0, port_offset))
        for port in normalized:
            instructions.extend(
                [
                    (BPF_JMP_JEQ_K, 0, 1, port),
                    (BPF_RET_K, 0, 0, 65535),
                ]
            )
    instructions.append((BPF_RET_K, 0, 0, 0))
    return instructions


def attach_udp_port_filter(capture: socket.socket, ports: Iterable[int]) -> int:
    instructions = build_udp_port_filter(ports)
    filters = (SockFilter * len(instructions))(
        *(SockFilter(*instruction) for instruction in instructions)
    )
    program = SockFprog(len(instructions), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    set_socket_option = libc.setsockopt
    set_socket_option.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    set_socket_option.restype = ctypes.c_int
    result = set_socket_option(
        capture.fileno(),
        socket.SOL_SOCKET,
        SO_ATTACH_FILTER,
        ctypes.byref(program),
        ctypes.sizeof(program),
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return len(instructions)


def parse_ethernet_ipv4_udp(frame: bytes) -> DatagramView | None:
    if len(frame) < 14:
        return None
    offset = 14
    ethertype = struct.unpack_from("!H", frame, 12)[0]
    while ethertype in VLAN_ETHERTYPES:
        if len(frame) < offset + 4:
            return None
        ethertype = struct.unpack_from("!H", frame, offset + 2)[0]
        offset += 4
    if ethertype != ETH_P_IP or len(frame) < offset + 20:
        return None

    version_ihl = frame[offset]
    if version_ihl >> 4 != 4:
        return None
    ihl = (version_ihl & 0x0F) * 4
    if ihl < 20 or len(frame) < offset + ihl + 8:
        return None
    if frame[offset + 9] != socket.IPPROTO_UDP:
        return None
    fragment = struct.unpack_from("!H", frame, offset + 6)[0]
    if fragment & 0x1FFF:
        return None

    total_length = struct.unpack_from("!H", frame, offset + 2)[0]
    ip_end = min(offset + total_length, len(frame))
    udp_offset = offset + ihl
    source_port, destination_port, udp_length = struct.unpack_from(
        "!HHH", frame, udp_offset
    )
    if udp_length < 8:
        return None
    udp_end = min(udp_offset + udp_length, ip_end)
    if udp_end < udp_offset + 8:
        return None
    return DatagramView(
        source_ip=socket.inet_ntoa(frame[offset + 12 : offset + 16]),
        destination_ip=socket.inet_ntoa(frame[offset + 16 : offset + 20]),
        source_port=source_port,
        destination_port=destination_port,
        payload=frame[udp_offset + 8 : udp_end],
    )


def local_ipv4_addresses(interface: str | None) -> set[str]:
    try:
        import fcntl
    except ImportError:
        return {"127.0.0.1"}
    names = [interface] if interface else [name for _, name in socket.if_nameindex()]
    addresses = {"127.0.0.1"}
    control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for name in names:
            if not name:
                continue
            request = struct.pack("256s", name.encode("ascii", errors="ignore")[:15])
            try:
                result = fcntl.ioctl(control.fileno(), SIOCGIFADDR, request)
            except OSError:
                continue
            addresses.add(socket.inet_ntoa(result[20:24]))
    finally:
        control.close()
    return addresses


def owned_udp_ports(
    process_names: Iterable[str],
    transport_process_names: Iterable[str],
) -> tuple[dict[int, set[str]], bool]:
    game_processes, game_sockets = discover_process_sockets(process_names)
    transport_names = tuple(transport_process_names)
    if transport_names:
        transport_processes, transport_sockets = discover_process_sockets(
            transport_names
        )
    else:
        transport_processes, transport_sockets = [], []
    pid_names = {
        process.pid: process.comm.lower()
        for process in game_processes + transport_processes
    }
    owners: dict[int, set[str]] = {}
    for udp_socket in game_sockets + transport_sockets:
        names = {
            pid_names[pid]
            for pid in udp_socket.pids
            if pid in pid_names
        }
        if names:
            owners.setdefault(udp_socket.local_port, set()).update(names)
    return owners, bool(game_processes)


def packet_direction(
    view: DatagramView,
    *,
    addresses: set[str],
    port_owners: dict[int, set[str]],
) -> tuple[str, set[str]] | None:
    source_owners = port_owners.get(view.source_port, set())
    destination_owners = port_owners.get(view.destination_port, set())
    if view.source_ip in addresses and source_owners:
        return "outbound", source_owners
    if view.destination_ip in addresses and destination_owners:
        return "inbound", destination_owners
    if source_owners and not destination_owners:
        return "outbound", source_owners
    if destination_owners and not source_owners:
        return "inbound", destination_owners
    return None


def select_flow_candidate(
    summaries: list[dict[str, Any]],
    *,
    configured_host_ip: str | None,
    game_process_present: bool,
) -> dict[str, Any] | None:
    """Select one current game flow without guessing between peers.

    Steam's brokered transport does not always expose the reliable marker in
    both directions. Bidirectional traffic plus a marker in either direction
    is sufficient for endpoint discovery; the carrier command still provides
    the protocol-level confirmation before a rewrite can succeed.
    """
    if not game_process_present:
        return None
    eligible = [
        summary
        for summary in summaries
        if summary["bidirectional"]
        and (
            summary["reliable_outbound_packets"]
            or summary["reliable_inbound_packets"]
        )
    ]
    if configured_host_ip:
        eligible = [
            item for item in eligible if item["remote_ip"] == configured_host_ip
        ]
    return eligible[0] if len(eligible) == 1 else None


def telemetry_document(
    tracker: FlowTracker,
    *,
    now_mono: float,
    configured_host_ip: str | None,
    interface: str | None,
    game_process_present: bool,
    phase: str = "passive_read_only",
) -> dict[str, Any]:
    summaries = tracker.summaries(
        now_mono=now_mono,
        configured_host_ip=configured_host_ip,
    )
    selected = select_flow_candidate(
        summaries,
        configured_host_ip=configured_host_ip,
        game_process_present=game_process_present,
    )
    owner_names = selected.get("owner_names", []) if selected else []
    if "steam" in owner_names:
        transport_owner = "steam"
        candidate_kind = "steam_brokered"
    elif "stellaris" in owner_names:
        transport_owner = "stellaris"
        candidate_kind = "stellaris_direct"
    else:
        transport_owner = None
        candidate_kind = None
    return {
        "schema": "iag.passive_flow_telemetry.v1",
        "updated_at": now_iso(),
        "phase": phase,
        "source": "passive_observer",
        "observer_alive": phase == "passive_read_only",
        "network_interface": interface or "all",
        "configured_host_ip": configured_host_ip,
        "game_process_present": game_process_present,
        "candidate_active": selected is not None,
        "candidate_count": len(summaries),
        "candidate_kind": candidate_kind,
        "transport_owner": transport_owner,
        "local_ip": selected.get("local_ip") if selected else None,
        "host_ip": selected.get("remote_ip") if selected else configured_host_ip,
        "local_udp_port": selected.get("local_udp_port") if selected else None,
        "local_inbound_port": selected.get("local_inbound_port") if selected else None,
        "host_destination_port": (
            selected.get("host_destination_port") if selected else None
        ),
        "host_source_port": selected.get("host_source_port") if selected else None,
        "outbound_packets": selected.get("outbound_packets", 0) if selected else 0,
        "inbound_packets": selected.get("inbound_packets", 0) if selected else 0,
        "carrier_seen": False,
        "authoritative_confirmation": False,
        "protocol_markers": {
            "reliable_outbound_packets": (
                selected.get("reliable_outbound_packets", 0) if selected else 0
            ),
            "reliable_inbound_packets": (
                selected.get("reliable_inbound_packets", 0) if selected else 0
            ),
            "command_marker_packets": (
                selected.get("command_marker_packets", 0) if selected else 0
            ),
            "carrier_text_packets": (
                selected.get("carrier_text_packets", 0) if selected else 0
            ),
        },
        "last_packet_at": selected.get("last_packet_at") if selected else None,
        "candidates": [
            {key: value for key, value in item.items() if key != "last_packet_mono"}
            for item in summaries[:5]
        ],
    }


def runtime_path(config: dict[str, Any], key: str, default: Path) -> Path:
    value = Path(str(config.get(key, default))).expanduser()
    if value.is_absolute():
        return value
    return Path(config["runtime_root"]).expanduser() / value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--interface")
    parser.add_argument("--window-seconds", type=float)
    return parser.parse_args()


def main() -> int:
    if not hasattr(socket, "AF_PACKET"):
        raise RuntimeError("The passive observer requires Linux AF_PACKET support.")
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Agent config must contain a JSON object.")
    runtime_root = Path(config["runtime_root"]).expanduser()
    status_path = args.status_file or runtime_path(
        config,
        "passive_telemetry_path",
        runtime_root / "state" / "passive_flow_status.json",
    )
    interface = (args.interface or str(config.get("network_interface", ""))).strip() or None
    configured_host_ip = str(config.get("host_ip", "")).strip() or None
    if configured_host_ip:
        configured_host_ip = str(ipaddress.ip_address(configured_host_ip))
    window_seconds = args.window_seconds or float(
        config.get("passive_observer_window_seconds", 12)
    )
    owner_refresh_seconds = float(config.get("network_owner_refresh_seconds", 10))
    if owner_refresh_seconds < 1:
        raise ValueError(
            "The network owner refresh interval must be at least 1 second."
        )
    if window_seconds < 3:
        raise ValueError("The passive observer window must be at least 3 seconds.")

    process_names = config.get("process_names", ["stellaris"])
    transport_process_names = config.get("transport_process_names", ["steam"])
    tracker = FlowTracker(window_seconds)
    addresses = local_ipv4_addresses(interface)
    port_owners: dict[int, set[str]] = {}
    game_process_present = False
    stop_requested = False

    def stop_handler(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    capture = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    if interface:
        capture.bind((interface, 0))
    capture.settimeout(0.5)
    next_owner_refresh = 0.0
    filtered_ports: tuple[int, ...] | None = None
    next_publish = 0.0
    try:
        while not stop_requested:
            current = time.monotonic()
            if current >= next_owner_refresh:
                port_owners, game_process_present = owned_udp_ports(
                    process_names,
                    transport_process_names,
                )
                current_ports = tuple(sorted(port_owners))
                if current_ports != filtered_ports:
                    attach_udp_port_filter(capture, current_ports)
                    filtered_ports = current_ports
                addresses = local_ipv4_addresses(interface)
                next_owner_refresh = current + owner_refresh_seconds
            try:
                frame = capture.recv(65535)
            except socket.timeout:
                frame = b""
            if frame and game_process_present:
                view = parse_ethernet_ipv4_udp(frame)
                if view is not None:
                    classified = packet_direction(
                        view,
                        addresses=addresses,
                        port_owners=port_owners,
                    )
                    if classified is not None:
                        direction, owners = classified
                        peer_ip = (
                            view.destination_ip
                            if direction == "outbound"
                            else view.source_ip
                        )
                        if not configured_host_ip or peer_ip == configured_host_ip:
                            tracker.observe(
                                view,
                                direction=direction,
                                owner_names=owners,
                                now_mono=current,
                                observed_at=now_iso(),
                            )
            current = time.monotonic()
            if current >= next_publish:
                atomic_write_json(
                    status_path,
                    telemetry_document(
                        tracker,
                        now_mono=current,
                        configured_host_ip=configured_host_ip,
                        interface=interface,
                        game_process_present=game_process_present,
                    ),
                )
                next_publish = current + 1.0
    finally:
        capture.close()
        atomic_write_json(
            status_path,
            telemetry_document(
                tracker,
                now_mono=time.monotonic(),
                configured_host_ip=configured_host_ip,
                interface=interface,
                game_process_present=game_process_present,
                phase="stopped",
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
