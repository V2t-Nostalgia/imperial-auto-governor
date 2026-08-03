#!/usr/bin/env python3
"""Windows host-side one-shot interceptor for IAG carrier construction."""

from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SOURCE_CARRIER_IDS = {
    "build_building": "building_upc_construction_command_relay",
    "build_district": "district_generator",
    "build_zone": "zone_research_engineering",
    "upgrade_building": "building_upc_upgrade_command_relay_target",
    "replace_building": "building_upc_replacement_command_relay_target",
}
SUPPORTED_SOURCE_CARRIER_IDS = {
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
    "replace_building": {
        "building_upc_replacement_command_relay_target",
        "building_holo_theatres",
    },
}
# Backward-compatible aliases for the building-layout helpers below.
SOURCE_CARRIER_ID = SOURCE_CARRIER_IDS["build_building"]
SOURCE_CARRIER_NEEDLE = SOURCE_CARRIER_ID.encode("ascii")

ZONE_SLOT_CONTEXT_TAG = bytes.fromhex("822c01001400")
SERIALIZED_U32_FIELD_SUFFIX = bytes.fromhex("01001400")
SERIALIZED_STRING_FIELD_SUFFIX = bytes.fromhex("01000300")
ZONE_SLOT_BUILD_STRING_TAG = bytes.fromhex("b22b01000f00")
ZONE_SLOT_COLONY_TAG = bytes.fromhex("132a01001400")
ZONE_SLOT_ZONE_TAG = bytes.fromhex("b32b01001400")
ZONE_SLOT_RECORD_TAIL = bytes.fromhex("040004000400")


def _prepare_import_paths() -> None:
    roots = [
        Path(__file__).resolve().parents[1] / "packet_interceptor",
    ]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.extend(
            [
                Path(bundle_root) / "packet_interceptor",
                Path(bundle_root),
            ]
        )
    for root in roots:
        vendor = root / "vendor_runtime"
        for candidate in (root, vendor):
            if candidate.is_dir() and str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))


_prepare_import_paths()

from iag_same_family_construction_rewriter import (  # noqa: E402
    rewrite_district_carrier,
    rewrite_zone_carrier,
)
from iag_building_upgrade_rewriter import (  # noqa: E402
    rewrite_building_upgrade_carrier,
)
from iag_building_replacement_rewriter import (  # noqa: E402
    rewrite_building_replacement_carrier,
)
from iag_packet_interceptor import (  # noqa: E402
    BUILD_HEAD,
    BUILD_STRING_TAG,
    COMMAND_ENVELOPE_DISTANCE,
    COMMAND_ENVELOPE_TYPE,
    EA3F_TAG,
    FC29_TAG,
    PLACEMENT_TAG,
)
from iag_stream_command_injector import (  # noqa: E402
    COMMAND_SERIAL_OFFSET,
    find_command_records,
)


class HostInterceptorError(RuntimeError):
    """An operator-facing host interceptor failure."""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def is_windows_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_carrier_id(action: dict[str, Any]) -> str:
    action_type = str(action.get("type", ""))
    try:
        default_carrier = SOURCE_CARRIER_IDS[action_type]
        supported = SUPPORTED_SOURCE_CARRIER_IDS[action_type]
    except KeyError as error:
        raise HostInterceptorError(
            f"不支持的建设动作：{action_type or '<missing>'}"
        ) from error
    selected = str(action.get("_source_carrier_id") or default_carrier)
    if selected not in supported:
        raise HostInterceptorError(
            f"动作 {action_type} 使用了未验证的载体 {selected}。"
        )
    return selected


def source_carrier_needle(action: dict[str, Any]) -> bytes:
    return source_carrier_id(action).encode("ascii")


def validate_request(request: dict[str, Any], *, client_id: str) -> dict[str, Any]:
    if request.get("schema") != "iag.host_executor_request.v1":
        raise HostInterceptorError("不支持的房主执行请求格式。")
    if request.get("expected_client_id") != client_id:
        raise HostInterceptorError("执行请求不属于当前 Windows 客户端。")
    action = request.get("action")
    if not isinstance(action, dict):
        raise HostInterceptorError("执行请求缺少建设动作。")
    action_type = str(action.get("type", ""))
    carrier = request.get("carrier")
    selected_carrier = (
        str(carrier.get("command", "")) if isinstance(carrier, dict) else ""
    )
    supported_carriers = SUPPORTED_SOURCE_CARRIER_IDS.get(action_type, set())
    if not isinstance(carrier, dict) or (
        selected_carrier not in supported_carriers
        or carrier.get("required_direction") != "inbound_to_host"
    ):
        raise HostInterceptorError(
            f"请求没有使用动作 {action_type} 的已验证同族入站载体 "
            f"{', '.join(sorted(supported_carriers))}。"
        )
    validated_action = dict(action)
    validated_action["_source_carrier_id"] = selected_carrier
    safety = request.get("safety")
    required_safety = (
        "one_shot",
        "preserve_udp_payload_length",
        "preserve_command_count",
        "preserve_carrier_serial",
        "require_authoritative_host_confirmation",
        "drop_unrewritable_carrier",
    )
    if not isinstance(safety, dict) or not all(
        safety.get(name) is True for name in required_safety
    ):
        raise HostInterceptorError("执行请求缺少必要的等长改写安全约束。")
    if action_type == "build_building":
        building_id = str(action.get("building_id", ""))
        try:
            encoded = building_id.encode("ascii")
        except UnicodeEncodeError as error:
            raise HostInterceptorError("建筑 ID 必须是 ASCII。") from error
        if not encoded or len(encoded) > len(selected_carrier.encode("ascii")):
            raise HostInterceptorError(
                f"目标建筑无法放入等长载体 {selected_carrier}。"
            )
        for field in ("planet_id", "build_queue_id", "colony_id", "zone_id"):
            if not isinstance(action.get(field), int) or int(action[field]) < 0:
                raise HostInterceptorError(f"建筑动作字段 {field} 无效。")
    elif action_type == "build_district":
        district_type = str(action.get("district_type", ""))
        try:
            encoded = district_type.encode("ascii")
        except UnicodeEncodeError as error:
            raise HostInterceptorError("主区划 ID 必须是 ASCII。") from error
        maximum_length = len(selected_carrier.encode("ascii"))
        if not encoded or len(encoded) > maximum_length:
            raise HostInterceptorError(
                f"目标主区划无法放入等长载体 {selected_carrier}。"
            )
        for field in ("build_queue_id", "colony_id"):
            if not isinstance(action.get(field), int) or int(action[field]) < 0:
                raise HostInterceptorError(f"主区划动作字段 {field} 无效。")
    elif action_type == "build_zone":
        zone_type = str(action.get("zone_type", ""))
        try:
            encoded = zone_type.encode("ascii")
        except UnicodeEncodeError as error:
            raise HostInterceptorError("区划特化 ID 必须是 ASCII。") from error
        maximum_length = len(selected_carrier.encode("ascii"))
        if not encoded or len(encoded) > maximum_length:
            raise HostInterceptorError(
                f"目标区划特化无法放入等长载体 {selected_carrier}。"
            )
        for field in (
            "build_queue_id",
            "colony_id",
            "district_id",
            "slot_selector",
        ):
            if not isinstance(action.get(field), int) or int(action[field]) < 0:
                raise HostInterceptorError(f"区划特化动作字段 {field} 无效。")
    elif action_type in {"upgrade_building", "replace_building"}:
        target_id = str(action.get("to_building_id", ""))
        operation_label = "升级" if action_type == "upgrade_building" else "替换"
        try:
            encoded = target_id.encode("ascii")
        except UnicodeEncodeError as error:
            raise HostInterceptorError(
                f"{operation_label}目标建筑 ID 必须是 ASCII。"
            ) from error
        maximum_length = len(selected_carrier.encode("ascii"))
        if not encoded or len(encoded) > maximum_length:
            raise HostInterceptorError(
                f"{operation_label}目标无法放入等长载体 {selected_carrier}。"
            )
        for field in (
            "build_queue_id",
            "colony_id",
            "zone_id",
            "building_object_id",
        ):
            if not isinstance(action.get(field), int) or int(action[field]) < 0:
                raise HostInterceptorError(
                    f"建筑{operation_label}动作字段 {field} 无效。"
                )
    return validated_action


def target_needle(action: dict[str, Any]) -> bytes:
    if action.get("type") == "build_building":
        return str(action["building_id"]).encode("ascii")
    if action.get("type") == "build_district":
        return str(action["district_type"]).encode("ascii")
    if action.get("type") == "build_zone":
        return str(action["zone_type"]).encode("ascii")
    return str(action["to_building_id"]).encode("ascii")


def rewrite_zone_slot_building_carrier_minimal(
    payload: bytes,
    action: dict[str, Any],
    source_id: bytes,
) -> tuple[bytes, dict[str, int | str]] | None:
    """Rewrite the 4.x zone-slot building layout without fixing session tags."""
    target_id = str(action["building_id"]).encode("ascii")
    building_marker = (
        ZONE_SLOT_BUILD_STRING_TAG
        + len(source_id).to_bytes(2, "little")
        + source_id
    )
    if payload.count(building_marker) != 1:
        return None

    marker_offset = payload.index(building_marker)
    string_length_offset = marker_offset + len(ZONE_SLOT_BUILD_STRING_TAG)
    source_id_start = string_length_offset + 2
    source_id_end = source_id_start + len(source_id)

    # Paired captures from two sessions showed that the first two bytes of the
    # queue and outer string field tags can change. Their serialized types,
    # order, and the surrounding semantic fields remained stable.
    outer_string_tag_offset = marker_offset - 6
    queue_tag_offset = outer_string_tag_offset - 10
    context_tag_offset = queue_tag_offset - 10
    if context_tag_offset < 0:
        return None
    if (
        payload[context_tag_offset : context_tag_offset + 6]
        != ZONE_SLOT_CONTEXT_TAG
    ):
        return None
    if (
        payload[queue_tag_offset + 2 : queue_tag_offset + 6]
        != SERIALIZED_U32_FIELD_SUFFIX
    ):
        return None
    if (
        payload[
            outer_string_tag_offset + 2 : outer_string_tag_offset + 6
        ]
        != SERIALIZED_STRING_FIELD_SUFFIX
    ):
        return None

    colony_tag_offset = source_id_end
    colony_value_offset = colony_tag_offset + len(ZONE_SLOT_COLONY_TAG)
    zone_tag_offset = colony_value_offset + 4
    zone_value_offset = zone_tag_offset + len(ZONE_SLOT_ZONE_TAG)
    tail_offset = zone_value_offset + 4
    if (
        payload[colony_tag_offset : colony_value_offset]
        != ZONE_SLOT_COLONY_TAG
        or payload[zone_tag_offset : zone_value_offset] != ZONE_SLOT_ZONE_TAG
        or payload[
            tail_offset : tail_offset + len(ZONE_SLOT_RECORD_TAIL)
        ]
        != ZONE_SLOT_RECORD_TAIL
    ):
        return None

    matching_records = [
        record
        for record in find_command_records(payload)
        if record.offset <= context_tag_offset
        and tail_offset + len(ZONE_SLOT_RECORD_TAIL)
        <= record.offset + record.length
    ]
    if len(matching_records) != 1:
        return None
    record = matching_records[0]
    record_end = record.offset + record.length
    if (
        context_tag_offset != record.offset + COMMAND_ENVELOPE_DISTANCE
        or tail_offset + len(ZONE_SLOT_RECORD_TAIL) != record_end
    ):
        return None

    queue_value_offset = queue_tag_offset + 6
    source_context = int.from_bytes(
        payload[context_tag_offset + 6 : context_tag_offset + 10],
        "little",
    )
    source_build_queue_id = int.from_bytes(
        payload[queue_value_offset : queue_value_offset + 4],
        "little",
    )
    source_colony_id = int.from_bytes(
        payload[colony_value_offset : colony_value_offset + 4],
        "little",
    )
    source_zone_id = int.from_bytes(
        payload[zone_value_offset : zone_value_offset + 4],
        "little",
    )

    record_prefix = bytearray(payload[record.offset:string_length_offset])
    record_suffix = bytearray(payload[source_id_end:record_end])
    queue_relative = queue_value_offset - record.offset
    record_prefix[queue_relative : queue_relative + 4] = int(
        action["build_queue_id"]
    ).to_bytes(4, "little")
    colony_relative = colony_value_offset - source_id_end
    record_suffix[colony_relative : colony_relative + 4] = int(
        action["colony_id"]
    ).to_bytes(4, "little")
    zone_relative = zone_value_offset - source_id_end
    record_suffix[zone_relative : zone_relative + 4] = int(
        action["zone_id"]
    ).to_bytes(4, "little")

    rewritten_without_padding = (
        bytes(record_prefix)
        + len(target_id).to_bytes(2, "little")
        + target_id
        + bytes(record_suffix)
    )
    if len(rewritten_without_padding) > record.length:
        raise HostInterceptorError(
            "目标建筑比载体命令记录更长，无法保持固定长度。"
        )
    padding_length = record.length - len(rewritten_without_padding)
    rewritten_record = rewritten_without_padding + bytes(padding_length)
    rewritten = (
        payload[: record.offset]
        + rewritten_record
        + payload[record_end:]
    )
    return rewritten, {
        "carrier_offset": record.offset,
        "carrier_length": record.length,
        "carrier_serial": record.serial,
        "padding_length": padding_length,
        "match_reason": (
            "zone_slot_structural_field_rewrite_with_fixed_length_padding"
        ),
        "layout": "zone_slot_building_v1",
        "source_context_822c": source_context,
        "source_build_queue_id": source_build_queue_id,
        "source_colony_id": source_colony_id,
        "source_zone_id": source_zone_id,
        "queue_field_tag": payload[
            queue_tag_offset : queue_tag_offset + 2
        ].hex(),
        "outer_string_field_tag": payload[
            outer_string_tag_offset : outer_string_tag_offset + 2
        ].hex(),
    }


def rewrite_building_carrier_minimal(
    payload: bytes,
    action: dict[str, Any],
    source_id: bytes,
) -> tuple[bytes, dict[str, int | str]] | None:
    """Apply the field-level building rewrite proven by the live experiments."""
    zone_slot_result = rewrite_zone_slot_building_carrier_minimal(
        payload,
        action,
        source_id,
    )
    if zone_slot_result is not None:
        return zone_slot_result

    target_id = str(action["building_id"]).encode("ascii")
    building_marker = (
        BUILD_STRING_TAG
        + len(source_id).to_bytes(2, "little")
        + source_id
    )
    if payload.count(building_marker) != 1:
        return None

    marker_offset = payload.index(building_marker)
    core_prefix_length = len(BUILD_HEAD) + len(EA3F_TAG) + 4
    core_offset = marker_offset - core_prefix_length
    if core_offset < 0:
        return None
    if payload[core_offset : core_offset + len(BUILD_HEAD)] != BUILD_HEAD:
        return None

    queue_tag_offset = core_offset + len(BUILD_HEAD)
    if payload[
        queue_tag_offset : queue_tag_offset + len(EA3F_TAG)
    ] != EA3F_TAG:
        return None
    queue_value_offset = queue_tag_offset + len(EA3F_TAG)
    source_build_queue_id = int.from_bytes(
        payload[queue_value_offset : queue_value_offset + 4],
        "little",
    )

    source_end = marker_offset + len(building_marker)
    if payload[source_end : source_end + len(FC29_TAG)] != FC29_TAG:
        return None
    planet_value_offset = source_end + len(FC29_TAG)
    source_planet_id = int.from_bytes(
        payload[planet_value_offset : planet_value_offset + 4],
        "little",
    )

    placement_tag_offset = planet_value_offset + 4
    if payload[
        placement_tag_offset : placement_tag_offset + len(PLACEMENT_TAG)
    ] != PLACEMENT_TAG:
        return None
    placement_value_offset = placement_tag_offset + len(PLACEMENT_TAG)
    source_placement = int.from_bytes(
        payload[placement_value_offset : placement_value_offset + 4],
        "little",
    )

    envelope_offset = core_offset - COMMAND_ENVELOPE_DISTANCE
    if envelope_offset < 0:
        raise HostInterceptorError("载体业务片段前缺少完整命令信封。")
    if payload[
        envelope_offset + 2 : envelope_offset + 6
    ] != COMMAND_ENVELOPE_TYPE:
        raise HostInterceptorError("载体业务片段前的命令信封类型不匹配。")

    declared_length = int.from_bytes(
        payload[envelope_offset : envelope_offset + 2],
        "little",
    )
    carrier_length = declared_length + 1
    record_end = envelope_offset + carrier_length
    if record_end > len(payload):
        raise HostInterceptorError(
            "载体命令跨越当前 UDP payload，不能进行单包等长改写："
            f"declared_end={record_end};payload_length={len(payload)}"
        )

    string_length_offset = marker_offset + len(BUILD_STRING_TAG)
    declared_string_length = int.from_bytes(
        payload[string_length_offset : string_length_offset + 2],
        "little",
    )
    if declared_string_length != len(source_id):
        raise HostInterceptorError(
            "载体建筑字符串长度字段不匹配："
            f"declared={declared_string_length};actual={len(source_id)}"
        )
    source_id_start = string_length_offset + 2
    source_id_end = source_id_start + len(source_id)
    if source_id_end >= record_end:
        raise HostInterceptorError("载体建筑字符串越过命令记录边界。")

    record_prefix = bytearray(payload[envelope_offset:string_length_offset])
    record_suffix = bytearray(payload[source_id_end:record_end])
    queue_relative = queue_value_offset - envelope_offset
    record_prefix[queue_relative : queue_relative + 4] = int(
        action["build_queue_id"]
    ).to_bytes(4, "little")

    planet_relative = len(FC29_TAG)
    record_suffix[planet_relative : planet_relative + 4] = int(
        action["planet_id"]
    ).to_bytes(4, "little")
    placement_relative = len(FC29_TAG) + 4 + len(PLACEMENT_TAG)
    record_suffix[placement_relative : placement_relative + 4] = int(
        action["zone_id"]
    ).to_bytes(4, "little")

    rewritten_without_padding = (
        bytes(record_prefix)
        + len(target_id).to_bytes(2, "little")
        + target_id
        + bytes(record_suffix)
    )
    if len(rewritten_without_padding) > carrier_length:
        raise HostInterceptorError(
            "目标建筑比载体命令记录更长，无法保持固定长度。"
        )
    padding_length = carrier_length - len(rewritten_without_padding)
    rewritten_record = rewritten_without_padding + bytes(padding_length)
    rewritten = (
        payload[:envelope_offset]
        + rewritten_record
        + payload[record_end:]
    )

    serial_offset = envelope_offset + COMMAND_SERIAL_OFFSET
    carrier_serial = int.from_bytes(
        payload[serial_offset : serial_offset + 2],
        "little",
    )
    return rewritten, {
        "carrier_offset": envelope_offset,
        "carrier_length": carrier_length,
        "carrier_serial": carrier_serial,
        "padding_length": padding_length,
        "match_reason": "minimal_declared_record_field_rewrite_with_fixed_length_padding",
        "source_build_queue_id": source_build_queue_id,
        "source_planet_id": source_planet_id,
        "source_placement": source_placement,
    }

def rewrite_carrier_payload(
    payload: bytes,
    action: dict[str, Any],
) -> tuple[bytes, dict[str, int | str]] | None:
    """Rewrite one complete carrier with a same-family construction action."""
    carrier_id = source_carrier_id(action)
    carrier_needle = carrier_id.encode("ascii")
    if carrier_needle not in payload:
        return None
    if payload.count(carrier_needle) != 1:
        raise HostInterceptorError(
            f"载体包包含多个 {carrier_id} 标记，已拒绝改写。"
        )
    try:
        action_type = action.get("type")
        if action_type == "build_building":
            result = rewrite_building_carrier_minimal(
                payload,
                action,
                carrier_needle,
            )
        elif action_type == "build_district":
            result = rewrite_district_carrier(
                payload,
                source_district_type=carrier_id,
                target_district_type=str(action["district_type"]),
                build_queue_id=int(action["build_queue_id"]),
                colony_id=int(action["colony_id"]),
            )
        elif action_type == "build_zone":
            result = rewrite_zone_carrier(
                payload,
                source_zone_type=carrier_id,
                target_zone_type=str(action["zone_type"]),
                build_queue_id=int(action["build_queue_id"]),
                colony_id=int(action["colony_id"]),
                district_id=int(action["district_id"]),
                slot_selector=int(action["slot_selector"]),
            )
        elif action_type == "upgrade_building":
            result = rewrite_building_upgrade_carrier(
                payload,
                source_upgrade_id=carrier_id,
                target_upgrade_id=str(action["to_building_id"]),
                build_queue_id=int(action["build_queue_id"]),
                colony_id=int(action["colony_id"]),
                zone_id=int(action["zone_id"]),
                building_object_id=int(action["building_object_id"]),
            )
        elif action_type == "replace_building":
            result = rewrite_building_replacement_carrier(
                payload,
                source_replacement_id=carrier_id,
                target_replacement_id=str(action["to_building_id"]),
                build_queue_id=int(action["build_queue_id"]),
                colony_id=int(action["colony_id"]),
                zone_id=int(action["zone_id"]),
                source_building_object_id=int(action["building_object_id"]),
            )
        else:
            raise HostInterceptorError(f"不支持的建设动作：{action_type}")
    except ValueError as error:
        raise HostInterceptorError(str(error)) from error
    if result is None:
        raise HostInterceptorError(
            "发现载体字符串，但没有找到可安全等长改写的完整建设记录。"
        )
    rewritten, metadata = result
    if len(rewritten) != len(payload):
        raise HostInterceptorError("改写改变了 UDP payload 长度。")
    if carrier_needle in rewritten and target_needle(action) != carrier_needle:
        raise HostInterceptorError("改写后仍残留原始载体 ID。")
    return rewritten, metadata


def packet_filter_for_peer(peer_ip: str) -> str:
    return (
        "udp and ("
        f"ip.SrcAddr == {peer_ip} or ip.DstAddr == {peer_ip}"
        ")"
    )


def run_host_interceptor(
    request: dict[str, Any],
    *,
    client_id: str,
    ready_callback: Callable[[dict[str, Any]], None],
    stop_event: threading.Event | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Open WinDivert, announce READY, rewrite one inbound carrier, and confirm."""
    action = validate_request(request, client_id=client_id)
    carrier_id = source_carrier_id(action)
    carrier_needle = carrier_id.encode("ascii")
    if not is_windows_admin():
        raise HostInterceptorError(
            "房主执行桥未以管理员身份运行，WinDivert 无法安全启用。"
        )
    try:
        import pydivert  # type: ignore[import-not-found]
    except ImportError as error:
        raise HostInterceptorError("发布包缺少 PyDivert/WinDivert 运行库。") from error

    peer_ip = str(request.get("peer_ip", "")).strip()
    if not peer_ip:
        raise HostInterceptorError("执行请求缺少合作端 IP。")
    packet_filter = packet_filter_for_peer(peer_ip)
    deadline = time.monotonic() + max(int(request.get("timeout_seconds", 120)), 15)
    observe_seconds = max(int(request.get("observe_seconds", 15)), 3)
    stop = stop_event or threading.Event()
    telemetry: dict[str, Any] = {
        "request_id": request["request_id"],
        "carrier_command": carrier_id,
        "filter": packet_filter,
        "inbound_packets": 0,
        "outbound_packets": 0,
        "carrier_seen": False,
        "rewritten": False,
        "authoritative_confirmation": False,
        "carrier_payload_length": None,
        "carrier_serial": None,
        "padding_length": None,
        "before_sha256": None,
        "after_sha256": None,
    }
    replacement_time: float | None = None

    def publish(event: str, **fields: Any) -> None:
        if event_callback is None:
            return
        try:
            event_callback(
                {
                    "event": event,
                    "timestamp": now_iso(),
                    "request_id": request["request_id"],
                    **fields,
                }
            )
        except Exception:
            pass

    with pydivert.WinDivert(packet_filter) as divert:
        ready_callback(
            {
                "request_id": request["request_id"],
                "client_id": client_id,
                "elevated": True,
                "interceptor_open": True,
                "filter": packet_filter,
                "process_id": os.getpid(),
                "carrier_command": carrier_id,
            }
        )
        publish("host_interceptor_ready", filter=packet_filter)

        for packet in divert:
            original = packet.payload or b""
            is_peer_inbound = packet.is_inbound and packet.src_addr == peer_ip
            is_peer_outbound = packet.is_outbound and packet.dst_addr == peer_ip
            if is_peer_inbound:
                telemetry["inbound_packets"] += 1
            elif is_peer_outbound:
                telemetry["outbound_packets"] += 1

            if stop.is_set():
                divert.send(packet, recalculate_checksum=True)
                return {
                    "success": False,
                    "phase": "stopped_by_operator",
                    "error": "Windows 上传器已请求停止。",
                    **telemetry,
                }

            try:
                if (
                    is_peer_inbound
                    and not telemetry["rewritten"]
                    and carrier_needle in original
                ):
                    telemetry["carrier_seen"] = True
                    result = rewrite_carrier_payload(original, action)
                    assert result is not None
                    rewritten, metadata = result
                    packet.payload = rewritten
                    telemetry.update(
                        {
                            "rewritten": True,
                            "carrier_payload_length": len(original),
                            "carrier_serial": metadata.get("carrier_serial"),
                            "padding_length": metadata.get("padding_length"),
                            "before_sha256": sha256_hex(original),
                            "after_sha256": sha256_hex(rewritten),
                        }
                    )
                    replacement_time = time.monotonic()
                    publish(
                        "host_inbound_carrier_rewritten",
                        source=f"{packet.src_addr}:{packet.src_port}",
                        destination=f"{packet.dst_addr}:{packet.dst_port}",
                        payload_length=len(original),
                        **metadata,
                    )
                elif (
                    is_peer_outbound
                    and telemetry["rewritten"]
                    and target_needle(action) in original
                ):
                    telemetry["authoritative_confirmation"] = True
                    publish(
                        "host_authoritative_command_seen",
                        source=f"{packet.src_addr}:{packet.src_port}",
                        destination=f"{packet.dst_addr}:{packet.dst_port}",
                    )

                divert.send(packet, recalculate_checksum=True)
            except Exception:
                # A packet containing the exact carrier is never forwarded
                # unchanged after a failed rewrite attempt.
                if not (
                    is_peer_inbound and carrier_needle in original
                ):
                    packet.payload = original
                    divert.send(packet, recalculate_checksum=True)
                raise

            if telemetry["authoritative_confirmation"]:
                break
            if (
                replacement_time is not None
                and time.monotonic() - replacement_time >= observe_seconds
            ):
                break
            if replacement_time is None and time.monotonic() >= deadline:
                break

    success = bool(
        telemetry["carrier_seen"]
        and telemetry["rewritten"]
        and telemetry["authoritative_confirmation"]
    )
    if success:
        phase = "authoritative_host_command_confirmed"
        confirmation_state = "confirmed_by_packet"
        error = ""
    elif not telemetry["carrier_seen"]:
        phase = "stopped_without_carrier"
        confirmation_state = "failed"
        error = f"房主入站拦截器未看到载体 {carrier_id}。"
    elif not telemetry["rewritten"]:
        phase = "carrier_not_rewritten"
        confirmation_state = "failed"
        error = "载体已出现，但未完成等长改写。"
    else:
        phase = "rewritten_without_authoritative_confirmation"
        confirmation_state = "pending_save_confirmation"
        error = "已改写载体；回包未提供可识别的明文目标，等待新存档确认。"
    return {
        "success": success,
        "phase": phase,
        "confirmation_state": confirmation_state,
        "error": error,
        **telemetry,
    }
