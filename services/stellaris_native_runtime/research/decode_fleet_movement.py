#!/usr/bin/env python3
"""Decode verified Stellaris 4.4.6 fleet-movement probe layouts.

This is a research utility, not an execution API. It translates the guarded
native-layout snapshots emitted by command_probe.cpp into semantic evidence
that can be compared across player actions and save snapshots.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from typing import Any


QUEUE_MODES = {0: "prepend", 1: "append", 2: "replace"}
ORBITABLE_KINDS = {1: "planet", 2: "starbase"}
DIRECT_MOVEMENT_COMMANDS = {
    "CFleetFlyToCoordinatesCommand",
    "CSendFleetToLocationCommand",
}


def _blob(event: dict[str, Any], key: str) -> bytes:
    value = event.get(key, "")
    if not isinstance(value, str) or len(value) % 2:
        return b""
    try:
        return bytes.fromhex(value)
    except ValueError:
        return b""


def _unpack(fmt: str, data: bytes, offset: int) -> int | None:
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):
        return None
    return int(struct.unpack_from(fmt, data, offset)[0])


def _u8(data: bytes, offset: int) -> int | None:
    return _unpack("<B", data, offset)


def _u32(data: bytes, offset: int) -> int | None:
    return _unpack("<I", data, offset)


def _i32(data: bytes, offset: int) -> int | None:
    return _unpack("<i", data, offset)


def _i64(data: bytes, offset: int) -> int | None:
    return _unpack("<q", data, offset)


def _fixed_point(raw: int | None) -> float | None:
    return None if raw is None else raw / 32768.0


def _queue_from_flags(queue: int | None, queue_to_front: int | None) -> str:
    if queue_to_front:
        return "prepend"
    if queue:
        return "append"
    return "replace"


def _coordinate(data: bytes, offset: int) -> dict[str, Any]:
    x_raw = _i64(data, offset + 0x08)
    y_raw = _i64(data, offset + 0x10)
    height_raw = _i64(data, offset + 0x18)
    return {
        "x_raw": x_raw,
        "y_raw": y_raw,
        "display_height_raw": height_raw,
        "x": _fixed_point(x_raw),
        "y": _fixed_point(y_raw),
        "display_height": _fixed_point(height_raw),
        "origin_system_id": _u32(data, offset + 0x20),
        "randomize_display_height": bool(_u8(data, offset + 0x25) or 0),
    }


def _decode_nested(event: dict[str, Any]) -> dict[str, Any] | None:
    nested_type = event.get("nested_type")
    data = _blob(event, "known_nested_hex")
    if not nested_type or not data:
        return None
    result: dict[str, Any] = {"order_type": nested_type}
    if nested_type == "CMoveToSystemPointFleetOrder":
        result.update(
            coordinate=_coordinate(data, 0x28),
            has_triggered_message=bool(_u8(data, 0x21) or 0),
        )
    elif nested_type == "COrbitPlanetFleetOrder":
        target_kind = _u8(data, 0x2C)
        result.update(
            target_id=_u32(data, 0x28),
            target_kind=ORBITABLE_KINDS.get(target_kind, target_kind),
            target_origin_system_id=_u32(data, 0x40),
            merge_option=_i32(data, 0x58),
        )
    elif nested_type == "CFollowFleetOrder":
        result.update(
            target_fleet_id=_u32(data, 0x24),
            target_coordinate=_coordinate(data, 0x28),
            flag_50=bool(_u8(data, 0x50) or 0),
            flag_51=bool(_u8(data, 0x51) or 0),
        )
    elif nested_type == "CReturnFleetOrder":
        result.update(
            base_flag_20=bool(_u8(data, 0x20) or 0),
            own_starbases_only=bool(_u8(data, 0x21) or 0),
            try_home_base=bool(_u8(data, 0x22) or 0),
        )
    elif nested_type == "CRepairFleetOrder":
        result.update(
            flag_20=bool(_u8(data, 0x20) or 0),
            flag_21=bool(_u8(data, 0x21) or 0),
            flag_22=bool(_u8(data, 0x22) or 0),
        )
    elif nested_type == "CMergeFleetOrder":
        result.update(
            target_fleet_id=_u32(data, 0x24),
            country_id=_u32(data, 0x28),
            flag_2c=bool(_u8(data, 0x2C) or 0),
        )
    elif nested_type == "CJumpDriveFleetOrder":
        result["destination_system_id"] = _u32(data, 0x24)
    return result


def decode_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return semantic movement evidence for one command-probe event."""

    command_type = event.get("command_type")
    data = _blob(event, "known_command_hex")
    if not isinstance(command_type, str) or not data:
        return None
    result: dict[str, Any] = {
        "sequence": event.get("sequence"),
        "phase": event.get("phase"),
        "command_type": command_type,
        "serial": event.get("serial"),
    }
    if command_type in DIRECT_MOVEMENT_COMMANDS:
        result.update(
            fleet_id=_u32(data, 0x1C),
            coordinate=_coordinate(data, 0x20),
            queue_mode=_queue_from_flags(
                _u8(data, 0x48), _u8(data, 0x49)
            ),
        )
    elif command_type == "CFleetCancelOrdersCommand":
        result.update(
            country_id=_u32(data, 0x1C),
            fleet_ids=list(event.get("related_ids") or []),
            operation="cancel_all_orders",
        )
    elif command_type == "CFleetCancelOrderCommand":
        result.update(
            country_id=_u32(data, 0x1C),
            fleet_id=_u32(data, 0x20),
            order_index=_i32(data, 0x24),
            operation="cancel_order",
        )
    elif command_type == "CFleetOrbitPlanetCommand":
        target_kind = _u8(data, 0x24)
        result.update(
            fleet_id=_u32(data, 0x1C),
            target_id=_u32(data, 0x20),
            target_kind=ORBITABLE_KINDS.get(target_kind, target_kind),
            queue_mode=_queue_from_flags(
                _u8(data, 0x38), _u8(data, 0x39)
            ),
        )
    elif command_type == "CFollowFleetCommand":
        result.update(
            fleet_id=_u32(data, 0x1C),
            target_fleet_id=_u32(data, 0x20),
            attack=bool(_u8(data, 0x24) or 0),
            cancelled=bool(_u8(data, 0x25) or 0),
            queue_mode=_queue_from_flags(
                _u8(data, 0x26), _u8(data, 0x27)
            ),
        )
    elif command_type == "CFleetUpgradeDesignCommand":
        result.update(
            country_id=_u32(data, 0x1C),
            fleet_id=_u32(data, 0x20),
            construction_queue_id=_u32(data, 0x24),
            queue_mode=_queue_from_flags(
                _u8(data, 0x28), _u8(data, 0x29)
            ),
            operation="upgrade_fleet",
        )
    elif command_type == "CFleetCombatEmergencyFTLCommand":
        result.update(
            fleet_id=_u32(data, 0x1C),
            operation="emergency_ftl",
        )
    elif command_type == "CMergeFleetsCommand":
        result.update(
            country_id=_u32(data, 0x1C),
            fleet_ids=list(event.get("related_ids") or []),
            flag_38=bool(_u8(data, 0x38) or 0),
            flag_39=bool(_u8(data, 0x39) or 0),
            operation="merge_fleets",
        )
    elif command_type in {
        "CQueueFleetOrderCommand",
        "CQueueFleetsOrderCommand",
    }:
        queue_value = _i32(data, 0x1C)
        fleet_ids = list(event.get("related_ids") or [])
        if command_type == "CQueueFleetOrderCommand":
            fleet_ids = [_u32(data, 0x24)]
            country_id = _u32(data, 0x20)
        else:
            country_id = _u32(data, 0x38)
        result.update(
            country_id=country_id,
            fleet_ids=fleet_ids,
            queue_mode=QUEUE_MODES.get(queue_value, queue_value),
            order=_decode_nested(event),
        )
    else:
        return None
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--phase", default="session_post", help="Phase to retain; use 'all' for all."
    )
    args = parser.parse_args()
    with args.capture.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if args.phase != "all" and event.get("phase") != args.phase:
                continue
            decoded = decode_event(event)
            if decoded is not None:
                print(json.dumps(decoded, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
