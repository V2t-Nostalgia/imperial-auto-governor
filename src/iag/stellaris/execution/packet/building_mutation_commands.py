"""Verified Stellaris 4.4.6 building upgrade and replacement records."""

from __future__ import annotations

import re
from dataclasses import dataclass

from iag.stellaris.execution.packet.autonomous_commands import (
    ACTOR_TAG,
    ORIGIN_TAG,
    SERIAL_WIDTH,
    _unique_value_offset,
    _validate_u32,
)

END_OBJECT = bytes.fromhex("0400")
COMMAND_ENVELOPE = bytes.fromhex("04000000")
COMMON_COMMAND = bytes.fromhex("f30101000300")
COMMAND_PAYLOAD = bytes.fromhex("410001000300")
BUILDING_ACTION_FAMILY = bytes.fromhex("b43d01000300")
UPGRADE_SUBTYPE = bytes.fromhex("c03d01000300")
REPLACEMENT_SUBTYPE = bytes.fromhex("bd3d01000300")
CONTEXT_TAG = bytes.fromhex("822c01001400")
BUILD_QUEUE_TAG = bytes.fromhex("634001001400")
BUILDING_ID_TAG = bytes.fromhex("b22b01000f00")
COLONY_TAG = bytes.fromhex("132a01001400")
ZONE_TAG = bytes.fromhex("b32b01001400")
UPGRADE_BUILDING_OBJECT_TAG = bytes.fromhex("bf3d01001400")
REPLACEMENT_SOURCE_OBJECT_TAG = bytes.fromhex("5a3d01001400")
SCRIPT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class BuildingUpgradeTarget:
    context_822c: int
    build_queue_id: int
    colony_id: int
    zone_id: int
    building_object_id: int
    building_id: str


@dataclass(frozen=True)
class BuildingReplacementTarget:
    context_822c: int
    build_queue_id: int
    colony_id: int
    zone_id: int
    source_building_object_id: int
    building_id: str


def _common_command(actor: int, origin: int, command_serial: int) -> bytes:
    if origin not in (0, 1):
        raise ValueError("origin must be 0 or 1.")
    return b"".join(
        (
            COMMON_COMMAND,
            ACTOR_TAG + _validate_u32(actor, "actor").to_bytes(4, "little"),
            bytes.fromhex("c70001000c00ff7f0000"),
            bytes.fromhex("cc0001000e0000"),
            ORIGIN_TAG + bytes((origin,)),
            bytes.fromhex("db0001001400")
            + _validate_u32(command_serial, "command_serial").to_bytes(
                SERIAL_WIDTH, "little"
            ),
            END_OBJECT,
        )
    )


def _u32(tag: bytes, value: int, label: str) -> bytes:
    return tag + _validate_u32(value, label).to_bytes(4, "little")


def _string(tag: bytes, value: str, label: str) -> bytes:
    if not value or len(value) > 160 or not SCRIPT_ID_RE.fullmatch(value):
        raise ValueError(f"{label} contains unsupported characters.")
    encoded = value.encode("ascii")
    return tag + len(encoded).to_bytes(2, "little") + encoded


def _record(body: bytes) -> bytes:
    declared = 6 + len(body) - 1
    if declared > 0xFFFF:
        raise ValueError("The building command is too large.")
    return bytes((declared & 0xFF, 0)) + COMMAND_ENVELOPE + body


def _tagged_u32(record: bytes, tag: bytes, label: str) -> int:
    offset = _unique_value_offset(record, tag, 4, label)
    return int.from_bytes(record[offset : offset + 4], "little")


def _tagged_string(record: bytes, tag: bytes, label: str) -> str:
    offset = _unique_value_offset(record, tag, 2, label)
    length = int.from_bytes(record[offset : offset + 2], "little")
    start = offset + 2
    end = start + length
    if end > len(record):
        raise ValueError(f"{label} is truncated.")
    try:
        return record[start:end].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} must be ASCII.") from error


def _build(
    *,
    subtype: bytes,
    object_tag: bytes,
    command_serial: int,
    actor: int,
    origin: int,
    context_822c: int,
    build_queue_id: int,
    colony_id: int,
    zone_id: int,
    building_object_id: int,
    building_id: str,
) -> bytes:
    return _record(
        b"".join(
            (
                BUILDING_ACTION_FAMILY,
                _common_command(actor, origin, command_serial),
                COMMAND_PAYLOAD,
                _u32(CONTEXT_TAG, context_822c, "context_822c"),
                _u32(BUILD_QUEUE_TAG, build_queue_id, "build_queue_id"),
                subtype,
                _string(BUILDING_ID_TAG, building_id, "building_id"),
                _u32(COLONY_TAG, colony_id, "colony_id"),
                _u32(ZONE_TAG, zone_id, "zone_id"),
                _u32(object_tag, building_object_id, "building_object_id"),
                END_OBJECT,
                END_OBJECT,
                END_OBJECT,
            )
        )
    )


def build_building_upgrade_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: BuildingUpgradeTarget,
) -> bytes:
    result = _build(
        subtype=UPGRADE_SUBTYPE,
        object_tag=UPGRADE_BUILDING_OBJECT_TAG,
        command_serial=command_serial,
        actor=actor,
        origin=origin,
        context_822c=target.context_822c,
        build_queue_id=target.build_queue_id,
        colony_id=target.colony_id,
        zone_id=target.zone_id,
        building_object_id=target.building_object_id,
        building_id=target.building_id,
    )
    if parse_building_upgrade_record(result) != target:
        raise RuntimeError("The building-upgrade target changed during construction.")
    return result


def parse_building_upgrade_record(record: bytes) -> BuildingUpgradeTarget | None:
    if record[6 : 6 + len(BUILDING_ACTION_FAMILY)] != BUILDING_ACTION_FAMILY:
        return None
    if UPGRADE_SUBTYPE not in record or REPLACEMENT_SUBTYPE in record:
        return None
    try:
        return BuildingUpgradeTarget(
            context_822c=_tagged_u32(record, CONTEXT_TAG, "context_822c"),
            build_queue_id=_tagged_u32(record, BUILD_QUEUE_TAG, "build_queue_id"),
            colony_id=_tagged_u32(record, COLONY_TAG, "colony_id"),
            zone_id=_tagged_u32(record, ZONE_TAG, "zone_id"),
            building_object_id=_tagged_u32(
                record,
                UPGRADE_BUILDING_OBJECT_TAG,
                "building_object_id",
            ),
            building_id=_tagged_string(record, BUILDING_ID_TAG, "building_id"),
        )
    except ValueError:
        return None


def build_building_replacement_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: BuildingReplacementTarget,
) -> bytes:
    result = _build(
        subtype=REPLACEMENT_SUBTYPE,
        object_tag=REPLACEMENT_SOURCE_OBJECT_TAG,
        command_serial=command_serial,
        actor=actor,
        origin=origin,
        context_822c=target.context_822c,
        build_queue_id=target.build_queue_id,
        colony_id=target.colony_id,
        zone_id=target.zone_id,
        building_object_id=target.source_building_object_id,
        building_id=target.building_id,
    )
    if parse_building_replacement_record(result) != target:
        raise RuntimeError("The building-replacement target changed during construction.")
    return result


def parse_building_replacement_record(
    record: bytes,
) -> BuildingReplacementTarget | None:
    if record[6 : 6 + len(BUILDING_ACTION_FAMILY)] != BUILDING_ACTION_FAMILY:
        return None
    if REPLACEMENT_SUBTYPE not in record or UPGRADE_SUBTYPE in record:
        return None
    try:
        return BuildingReplacementTarget(
            context_822c=_tagged_u32(record, CONTEXT_TAG, "context_822c"),
            build_queue_id=_tagged_u32(record, BUILD_QUEUE_TAG, "build_queue_id"),
            colony_id=_tagged_u32(record, COLONY_TAG, "colony_id"),
            zone_id=_tagged_u32(record, ZONE_TAG, "zone_id"),
            source_building_object_id=_tagged_u32(
                record,
                REPLACEMENT_SOURCE_OBJECT_TAG,
                "source_building_object_id",
            ),
            building_id=_tagged_string(record, BUILDING_ID_TAG, "building_id"),
        )
    except ValueError:
        return None
