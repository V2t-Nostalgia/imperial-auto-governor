#!/usr/bin/env python3
"""Discover the Stellaris process, transport sockets, and live network telemetry."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SOCKET_LINK_RE = re.compile(r"^socket:\[(\d+)]$")
ZERO_ADDRESSES = {"0.0.0.0", "::"}


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    comm: str
    command: str


@dataclass(frozen=True)
class UdpSocket:
    family: str
    local_ip: str
    local_port: int
    remote_ip: str
    remote_port: int
    state_hex: str
    tx_queue: int
    rx_queue: int
    uid: int
    inode: int
    pids: tuple[int, ...]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return ""


def find_processes(
    process_names: Iterable[str],
    *,
    proc_root: Path = Path("/proc"),
) -> list[ProcessInfo]:
    needles = tuple(
        normalize_process_name(item) for item in process_names if item
    )
    if not needles:
        raise ValueError("At least one process name is required.")
    processes: list[ProcessInfo] = []
    try:
        entries = list(proc_root.iterdir())
    except (FileNotFoundError, PermissionError):
        return processes
    for entry in entries:
        if not entry.name.isdigit():
            continue
        comm = read_text(entry / "comm").strip()
        if normalize_process_name(comm) not in needles:
            continue
        command = read_text(entry / "cmdline").replace("\x00", " ").strip()
        processes.append(ProcessInfo(int(entry.name), comm, command))
    return sorted(processes, key=lambda item: item.pid)


def normalize_process_name(value: str) -> str:
    """Normalize a process identity without matching wrapper command lines."""
    name = Path(value.strip()).name.lower()
    return name[:-4] if name.endswith(".exe") else name

def process_identity_matches(
    comm: str,
    command: str,
    process_names: Iterable[str],
) -> bool:
    """Match the executable identity, never arbitrary wrapper arguments."""
    executable = command.split(maxsplit=1)[0] if command else ""
    candidates = {
        normalize_process_name(comm),
        normalize_process_name(executable),
    }
    needles = {normalize_process_name(item) for item in process_names if item}
    return bool(needles.intersection(candidates))



def process_socket_inodes(
    processes: Iterable[ProcessInfo],
    *,
    proc_root: Path = Path("/proc"),
) -> dict[int, set[int]]:
    owners: dict[int, set[int]] = {}
    for process in processes:
        fd_root = proc_root / str(process.pid) / "fd"
        try:
            descriptors = list(fd_root.iterdir())
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                continue
            match = SOCKET_LINK_RE.match(target)
            if match:
                owners.setdefault(int(match.group(1)), set()).add(process.pid)
    return owners


def decode_proc_ip(value: str, family: str) -> str:
    raw = bytes.fromhex(value)
    if family == "ipv4":
        if len(raw) != 4:
            raise ValueError("Invalid IPv4 value in /proc/net/udp.")
        return str(ipaddress.IPv4Address(raw[::-1]))
    if len(raw) != 16:
        raise ValueError("Invalid IPv6 value in /proc/net/udp6.")
    reordered = b"".join(raw[index : index + 4][::-1] for index in range(0, 16, 4))
    return str(ipaddress.IPv6Address(reordered))


def decode_endpoint(value: str, family: str) -> tuple[str, int]:
    address_hex, port_hex = value.split(":", 1)
    return decode_proc_ip(address_hex, family), int(port_hex, 16)


def parse_udp_table(
    path: Path,
    family: str,
    inode_owners: dict[int, set[int]],
) -> list[UdpSocket]:
    text = read_text(path)
    sockets: list[UdpSocket] = []
    for line in text.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 10:
            continue
        try:
            inode = int(columns[9])
            pids = inode_owners.get(inode)
            if not pids:
                continue
            local_ip, local_port = decode_endpoint(columns[1], family)
            remote_ip, remote_port = decode_endpoint(columns[2], family)
            tx_hex, rx_hex = columns[4].split(":", 1)
            sockets.append(
                UdpSocket(
                    family=family,
                    local_ip=local_ip,
                    local_port=local_port,
                    remote_ip=remote_ip,
                    remote_port=remote_port,
                    state_hex=columns[3],
                    tx_queue=int(tx_hex, 16),
                    rx_queue=int(rx_hex, 16),
                    uid=int(columns[7]),
                    inode=inode,
                    pids=tuple(sorted(pids)),
                )
            )
        except (ValueError, IndexError):
            continue
    return sockets


def discover_process_sockets(
    process_names: Iterable[str],
    *,
    proc_root: Path = Path("/proc"),
) -> tuple[list[ProcessInfo], list[UdpSocket]]:
    if os.name == "nt" and proc_root == Path("/proc"):
        return discover_windows_process_sockets(process_names)
    processes = find_processes(process_names, proc_root=proc_root)
    owners = process_socket_inodes(processes, proc_root=proc_root)
    sockets = parse_udp_table(proc_root / "net" / "udp", "ipv4", owners)
    sockets.extend(parse_udp_table(proc_root / "net" / "udp6", "ipv6", owners))
    sockets.sort(key=lambda item: (item.family, item.local_port, item.remote_port))
    return processes, sockets


def discover_windows_process_sockets(
    process_names: Iterable[str],
) -> tuple[list[ProcessInfo], list[UdpSocket]]:
    """Discover process-owned UDP sockets through psutil on Windows."""
    try:
        import psutil
    except ImportError:
        return [], []

    needles = {normalize_process_name(item) for item in process_names if item}
    processes: list[ProcessInfo] = []
    matching_pids: set[int] = set()
    for process in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            info = process.info
            name = str(info.get("name") or "")
            executable = str(info.get("exe") or "")
            if not ({normalize_process_name(name), normalize_process_name(executable)} & needles):
                continue
            command_parts = info.get("cmdline") or []
            command = " ".join(str(item) for item in command_parts)
            pid = int(info["pid"])
            matching_pids.add(pid)
            processes.append(ProcessInfo(pid, name or Path(executable).name, command))
        except (psutil.AccessDenied, psutil.NoSuchProcess, KeyError, TypeError, ValueError):
            continue

    try:
        connections = psutil.net_connections(kind="udp")
    except (psutil.AccessDenied, OSError):
        connections = []
    sockets: list[UdpSocket] = []
    for index, connection in enumerate(connections, start=1):
        if connection.pid not in matching_pids or not connection.laddr:
            continue
        try:
            local_ip = str(connection.laddr.ip)
            local_port = int(connection.laddr.port)
        except AttributeError:
            local_ip = str(connection.laddr[0])
            local_port = int(connection.laddr[1])
        if connection.raddr:
            try:
                remote_ip = str(connection.raddr.ip)
                remote_port = int(connection.raddr.port)
            except AttributeError:
                remote_ip = str(connection.raddr[0])
                remote_port = int(connection.raddr[1])
        else:
            remote_ip, remote_port = ("::", 0) if connection.family == socket.AF_INET6 else ("0.0.0.0", 0)
        family = "ipv6" if connection.family == socket.AF_INET6 else "ipv4"
        sockets.append(
            UdpSocket(
                family=family,
                local_ip=local_ip,
                local_port=local_port,
                remote_ip=remote_ip,
                remote_port=remote_port,
                state_hex=str(connection.status or ""),
                tx_queue=0,
                rx_queue=0,
                uid=0,
                inode=index,
                pids=(int(connection.pid),),
            )
        )
    return (
        sorted(processes, key=lambda item: item.pid),
        sorted(sockets, key=lambda item: (item.family, item.local_port, item.remote_port)),
    )


def read_json_if_present(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return None
    return value if isinstance(value, dict) else None


def timestamp_age_seconds(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds(), 0.0)


def socket_score(socket: UdpSocket, host_ip: str | None) -> int:
    score = 0
    if socket.remote_ip not in ZERO_ADDRESSES and socket.remote_port:
        score += 40
    if host_ip and socket.remote_ip == host_ip:
        score += 100
    if socket.remote_ip not in ZERO_ADDRESSES:
        try:
            if ipaddress.ip_address(socket.remote_ip).is_private:
                score += 10
        except ValueError:
            pass
    if socket.tx_queue or socket.rx_queue:
        score += 5
    return score


def select_socket(sockets: list[UdpSocket], host_ip: str | None) -> UdpSocket | None:
    if not sockets:
        return None
    ranked = sorted(sockets, key=lambda item: socket_score(item, host_ip), reverse=True)
    best_score = socket_score(ranked[0], host_ip)
    if best_score <= 0:
        return None
    tied = [item for item in ranked if socket_score(item, host_ip) == best_score]
    if len(tied) > 1 and not host_ip:
        return None
    return ranked[0]


def confidence_from_telemetry(
    processes: list[ProcessInfo],
    sockets: list[UdpSocket],
    selected: UdpSocket | None,
    telemetry: dict[str, Any] | None,
    *,
    telemetry_max_age_seconds: int,
) -> str:
    if not processes:
        return "offline"
    confidence = "process_detected"
    if sockets:
        confidence = "socket_detected"
    if selected and selected.remote_ip not in ZERO_ADDRESSES and selected.remote_port:
        confidence = "peer_detected"
    if not telemetry:
        return confidence
    age = timestamp_age_seconds(telemetry.get("updated_at"))
    if age is None or age > telemetry_max_age_seconds:
        return confidence
    if (
        telemetry.get("source") == "passive_observer"
        and telemetry.get("candidate_active")
        and telemetry.get("outbound_packets", 0)
        and telemetry.get("inbound_packets", 0)
    ):
        return "brokered_flow_candidate"
    if telemetry.get("outbound_packets", 0) and telemetry.get("inbound_packets", 0):
        confidence = "bidirectional_flow_observed"
    if telemetry.get("carrier_seen"):
        confidence = "carrier_command_verified"
    if telemetry.get("authoritative_confirmation"):
        confidence = "authoritative_confirmed"
    return confidence


def port_verification(
    processes: list[ProcessInfo],
    sockets: list[UdpSocket],
    selected: UdpSocket | None,
    telemetry: dict[str, Any] | None,
    *,
    telemetry_max_age_seconds: int,
) -> dict[str, Any]:
    """Explain whether displayed ports belong to this Stellaris session.

    Process-owned sockets establish provenance. A carrier signature or the
    authoritative host echo establishes the stronger protocol-level lock.
    """
    if not processes:
        return {
            "state": "offline",
            "verified": False,
            "severity": "offline",
            "summary_zh": "未发现 Stellaris 进程",
            "telemetry_fresh": False,
            "process_socket_match": None,
        }

    age = timestamp_age_seconds(telemetry.get("updated_at")) if telemetry else None
    telemetry_fresh = age is not None and age <= telemetry_max_age_seconds
    telemetry_local = telemetry.get("local_udp_port") if telemetry_fresh else None
    passive_observer = (
        telemetry_fresh
        and telemetry is not None
        and telemetry.get("source") == "passive_observer"
    )
    matching_socket = None
    if telemetry_local is not None and sockets:
        try:
            matching_socket = any(
                item.local_port == int(telemetry_local) for item in sockets
            )
        except (TypeError, ValueError):
            matching_socket = False

    if telemetry_fresh and telemetry.get("authoritative_confirmation"):
        return {
            "state": "authoritative_confirmed",
            "verified": True,
            "severity": "good",
            "summary_zh": "端口已由载体命令与房主权威回包确认",
            "telemetry_fresh": True,
            "process_socket_match": matching_socket,
        }
    if telemetry_fresh and telemetry.get("carrier_seen"):
        return {
            "state": "carrier_locked",
            "verified": True,
            "severity": "good",
            "summary_zh": "端口已由本局载体建造命令锁定",
            "telemetry_fresh": True,
            "process_socket_match": matching_socket,
        }
    if (
        passive_observer
        and telemetry.get("candidate_active")
        and matching_socket is True
        and telemetry.get("outbound_packets", 0)
        and telemetry.get("inbound_packets", 0)
    ):
        owner = str(telemetry.get("transport_owner", "")).lower()
        if owner == "steam":
            summary = (
                "已发现 Steam 代理的 Stellaris 双向流量候选，"
                "等待载体命令最终确认"
            )
            state = "brokered_flow_candidate"
        else:
            summary = "已发现 Stellaris 双向流量候选，等待载体命令最终确认"
            state = "flow_candidate"
        return {
            "state": state,
            "verified": False,
            "severity": "warn",
            "summary_zh": summary,
            "telemetry_fresh": True,
            "process_socket_match": True,
        }
    if matching_socket is False:
        return {
            "state": "conflict",
            "verified": False,
            "severity": "bad",
            "summary_zh": "遥测端口与 Stellaris 进程套接字不一致",
            "telemetry_fresh": True,
            "process_socket_match": False,
        }
    if (
        telemetry_fresh
        and matching_socket is True
        and telemetry.get("outbound_packets", 0)
        and telemetry.get("inbound_packets", 0)
    ):
        return {
            "state": "flow_matched",
            "verified": False,
            "severity": "warn",
            "summary_zh": "双向流量已匹配 Stellaris 套接字，等待载体命令确认",
            "telemetry_fresh": True,
            "process_socket_match": True,
        }
    if passive_observer:
        return {
            "state": "passive_waiting",
            "verified": False,
            "severity": "warn",
            "summary_zh": "只读监听已启动，等待本局 Stellaris 双向 UDP 流量",
            "telemetry_fresh": True,
            "process_socket_match": matching_socket,
        }
    if selected is not None:
        return {
            "state": "socket_candidate",
            "verified": False,
            "severity": "warn",
            "summary_zh": "已找到 Stellaris 端口候选，尚未由载体命令确认",
            "telemetry_fresh": telemetry_fresh,
            "process_socket_match": matching_socket,
        }
    if sockets:
        summary = "发现多个或未连接的 Stellaris UDP 套接字，暂不猜测端口"
        state = "ambiguous"
    else:
        summary = "Stellaris 已运行，等待联机 UDP 套接字"
        state = "waiting_for_socket"
    return {
        "state": state,
        "verified": False,
        "severity": "warn",
        "summary_zh": summary,
        "telemetry_fresh": telemetry_fresh,
        "process_socket_match": matching_socket,
    }


def discover_session(
    process_names: Iterable[str],
    *,
    transport_process_names: Iterable[str] = (),
    host_ip: str | None = None,
    telemetry_path: Path | None = None,
    passive_telemetry_path: Path | None = None,
    proc_root: Path = Path("/proc"),
    telemetry_max_age_seconds: int = 15,
) -> dict[str, Any]:
    normalized_host = str(ipaddress.ip_address(host_ip)) if host_ip else None
    processes, sockets = discover_process_sockets(process_names, proc_root=proc_root)
    transport_names = tuple(transport_process_names)
    if transport_names:
        transport_processes, transport_sockets = discover_process_sockets(
            transport_names,
            proc_root=proc_root,
        )
    else:
        transport_processes, transport_sockets = [], []
    all_sockets_by_inode = {
        (item.family, item.inode): item for item in sockets + transport_sockets
    }
    all_sockets = sorted(
        all_sockets_by_inode.values(),
        key=lambda item: (item.family, item.local_port, item.remote_port),
    )

    interceptor_telemetry = read_json_if_present(telemetry_path)
    passive_telemetry = read_json_if_present(passive_telemetry_path)
    interceptor_age = (
        timestamp_age_seconds(interceptor_telemetry.get("updated_at"))
        if interceptor_telemetry
        else None
    )
    passive_age = (
        timestamp_age_seconds(passive_telemetry.get("updated_at"))
        if passive_telemetry
        else None
    )
    if (
        interceptor_telemetry
        and interceptor_age is not None
        and interceptor_age <= telemetry_max_age_seconds
    ):
        telemetry = dict(interceptor_telemetry)
        telemetry["source"] = "interceptor"
    elif (
        passive_telemetry
        and passive_age is not None
        and passive_age <= telemetry_max_age_seconds
    ):
        telemetry = dict(passive_telemetry)
        telemetry["source"] = "passive_observer"
    else:
        telemetry = interceptor_telemetry or passive_telemetry
        if telemetry:
            telemetry = dict(telemetry)
            telemetry.setdefault(
                "source",
                "interceptor" if interceptor_telemetry else "passive_observer",
            )

    selected = select_socket(all_sockets, normalized_host)
    confidence = confidence_from_telemetry(
        processes,
        all_sockets,
        selected,
        None,
        telemetry_max_age_seconds=telemetry_max_age_seconds,
    )
    verification = port_verification(
        processes,
        all_sockets,
        selected,
        telemetry,
        telemetry_max_age_seconds=telemetry_max_age_seconds,
    )
    protocol_confidence = {
        "flow_matched": "bidirectional_flow_observed",
        "carrier_locked": "carrier_command_verified",
        "authoritative_confirmed": "authoritative_confirmed",
        "conflict": "port_conflict",
        "brokered_flow_candidate": "brokered_flow_candidate",
        "flow_candidate": "brokered_flow_candidate",
    }
    confidence = protocol_confidence.get(verification["state"], confidence)
    endpoint = asdict(selected) if selected else None
    if endpoint is None and telemetry and telemetry.get("host_ip"):
        endpoint = {
            "family": "ipv4",
            "local_ip": telemetry.get("local_ip") or "0.0.0.0",
            "local_port": telemetry.get("local_udp_port"),
            "remote_ip": telemetry.get("host_ip"),
            "remote_port": telemetry.get("host_destination_port"),
            "source": telemetry.get("source"),
        }
    live_ports = None
    if telemetry:
        live_ports = {
            "local_udp_port": telemetry.get("local_udp_port"),
            "host_destination_port": telemetry.get("host_destination_port"),
            "host_source_port": telemetry.get("host_source_port"),
        }
    return {
        "schema": "iag.session_discovery.v1",
        "observed_at": now_iso(),
        "confidence": confidence,
        "port_verification": verification,
        "configured_host_ip": normalized_host,
        "processes": [asdict(item) for item in processes],
        "transport_processes": [asdict(item) for item in transport_processes],
        "udp_sockets": [asdict(item) for item in sockets],
        "transport_udp_sockets": [asdict(item) for item in transport_sockets],
        "selected_endpoint": endpoint,
        "live_ports": live_ports,
        "telemetry": telemetry,
        "interceptor_telemetry": interceptor_telemetry,
        "passive_telemetry": passive_telemetry,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--process-name", action="append", default=[])
    parser.add_argument("--transport-process-name", action="append", default=[])
    parser.add_argument("--host-ip")
    parser.add_argument("--telemetry", type=Path)
    parser.add_argument("--passive-telemetry", type=Path)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    names = args.process_name or ["stellaris"]
    result = discover_session(
        names,
        transport_process_names=args.transport_process_name,
        host_ip=args.host_ip,
        telemetry_path=args.telemetry,
        passive_telemetry_path=args.passive_telemetry,
        proc_root=args.proc_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
