#!/usr/bin/env python3
"""Pure IPv4/UDP and Stellaris payload helpers for the Linux interceptor."""

from __future__ import annotations

import ipaddress
import socket
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PACKET_TOOLS = Path(__file__).resolve().parents[1] / "packet_interceptor"
if str(PACKET_TOOLS) not in sys.path:
    sys.path.insert(0, str(PACKET_TOOLS))

from iag_building_to_zone_replacer import (  # noqa: E402
    replace_building_command_with_zone,
)
from iag_command_replacement_injector import replace_one_command  # noqa: E402


@dataclass(frozen=True)
class UdpView:
    source_ip: str
    destination_ip: str
    source_port: int
    destination_port: int
    ip_header_length: int
    udp_offset: int
    udp_payload_offset: int
    udp_payload: bytes


def internet_checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    words = struct.unpack(f"!{len(data) // 2}H", data)
    total = sum(words)
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def parse_ipv4_udp(packet: bytes) -> UdpView | None:
    if len(packet) < 28 or packet[0] >> 4 != 4:
        return None
    ip_header_length = (packet[0] & 0x0F) * 4
    if ip_header_length < 20 or len(packet) < ip_header_length + 8:
        return None
    if packet[9] != socket.IPPROTO_UDP:
        return None
    total_length = int.from_bytes(packet[2:4], "big")
    if total_length < ip_header_length + 8 or total_length > len(packet):
        return None
    udp_offset = ip_header_length
    udp_length = int.from_bytes(packet[udp_offset + 4 : udp_offset + 6], "big")
    if udp_length < 8 or udp_offset + udp_length > total_length:
        return None
    payload_offset = udp_offset + 8
    return UdpView(
        source_ip=socket.inet_ntoa(packet[12:16]),
        destination_ip=socket.inet_ntoa(packet[16:20]),
        source_port=int.from_bytes(packet[udp_offset : udp_offset + 2], "big"),
        destination_port=int.from_bytes(
            packet[udp_offset + 2 : udp_offset + 4], "big"
        ),
        ip_header_length=ip_header_length,
        udp_offset=udp_offset,
        udp_payload_offset=payload_offset,
        udp_payload=packet[payload_offset : udp_offset + udp_length],
    )


def replace_udp_payload(packet: bytes, view: UdpView, payload: bytes) -> bytes:
    if len(payload) != len(view.udp_payload):
        raise ValueError("The rewritten UDP payload must remain equal length.")
    output = bytearray(packet)
    output[
        view.udp_payload_offset : view.udp_payload_offset + len(payload)
    ] = payload

    output[10:12] = b"\x00\x00"
    ip_checksum = internet_checksum(bytes(output[: view.ip_header_length]))
    output[10:12] = ip_checksum.to_bytes(2, "big")

    udp_length = int.from_bytes(
        output[view.udp_offset + 4 : view.udp_offset + 6], "big"
    )
    output[view.udp_offset + 6 : view.udp_offset + 8] = b"\x00\x00"
    pseudo_header = (
        bytes(output[12:20])
        + b"\x00"
        + bytes([socket.IPPROTO_UDP])
        + udp_length.to_bytes(2, "big")
    )
    udp_segment = bytes(output[view.udp_offset : view.udp_offset + udp_length])
    udp_checksum = internet_checksum(pseudo_header + udp_segment)
    if udp_checksum == 0:
        udp_checksum = 0xFFFF
    output[view.udp_offset + 6 : view.udp_offset + 8] = udp_checksum.to_bytes(
        2, "big"
    )
    return bytes(output)


def validate_host_ip(host_ip: str) -> str:
    address = ipaddress.ip_address(host_ip)
    if address.version != 4:
        raise ValueError("The first Linux interceptor supports IPv4 only.")
    return str(address)


def rewrite_stellaris_payload(
    payload: bytes,
    action: dict[str, Any],
) -> tuple[bytes, dict[str, int]] | None:
    action_type = action.get("type")
    if action_type == "build_building":
        building_id = str(action["building_id"])
        if len(building_id.encode("ascii")) > 23:
            raise ValueError("Target building ID exceeds the equal-length carrier slot.")
        return replace_one_command(
            payload,
            build_queue_id=int(action["build_queue_id"]),
            building_id=building_id,
            planet_id=int(action["planet_id"]),
            placement=int(action["zone_id"]),
        )
    if action_type == "build_zone":
        return replace_building_command_with_zone(
            payload,
            zone_type=str(action["zone_type"]),
            build_queue_id=int(action["build_queue_id"]),
            colony_id=int(action["colony_id"]),
            district_id=int(action["district_id"]),
            slot_selector=int(action["slot_selector"]),
            context_822c=0,
        )
    if action_type == "noop":
        return None
    raise ValueError(f"Unsupported execution action: {action_type}")


def expected_authoritative_needle(action: dict[str, Any]) -> bytes:
    if action.get("type") == "build_building":
        return str(action["building_id"]).encode("ascii")
    if action.get("type") == "build_zone":
        return str(action["zone_type"]).encode("ascii")
    return b""
