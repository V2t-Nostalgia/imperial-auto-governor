#!/usr/bin/env python3
"""Length-preserving rewrite for Stellaris 4.4.6 building upgrades."""

from __future__ import annotations

from typing import Any

from iag.stellaris.execution.packet.iag_stream_command_injector import (
    find_command_records,
)


SERIALIZED_U32_SUFFIX = bytes.fromhex("01001400")
SERIALIZED_DYNAMIC_SUFFIX = bytes.fromhex("01000300")
CONTEXT_822C_TAG = bytes.fromhex("822c01001400")
UPGRADE_BUILDING_STRING_TAG = bytes.fromhex("b22b01000f00")
UPGRADE_COLONY_TAG = bytes.fromhex("132a01001400")
UPGRADE_ZONE_TAG = bytes.fromhex("b32b01001400")
UPGRADE_BUILDING_OBJECT_TAG = bytes.fromhex("bf3d01001400")
UPGRADE_RECORD_TAIL = bytes.fromhex("040004000400")


def _ascii_id(value: str, label: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be ASCII.") from error
    if not encoded:
        raise ValueError(f"{label} must not be empty.")
    return encoded


def _u32(record: bytes, value_offset: int) -> int:
    return int.from_bytes(record[value_offset : value_offset + 4], "little")


def _validate_u32(value: int, label: str) -> int:
    if not isinstance(value, int) or value < 0 or value > 0xFFFFFFFF:
        raise ValueError(f"{label} must be an unsigned 32-bit integer.")
    return value


def rewrite_building_upgrade_carrier(
    payload: bytes,
    *,
    source_upgrade_id: str,
    target_upgrade_id: str,
    build_queue_id: int,
    colony_id: int,
    zone_id: int,
    building_object_id: int,
) -> tuple[bytes, dict[str, int | str]] | None:
    """Redirect one complete upgrade command without changing its envelope.

    The source command is a legal upgrade click made by the non-host co-op
    player. All target context values are read from the synchronized save.
    Session-dependent field tags are preserved rather than guessed.
    """

    source_id = _ascii_id(source_upgrade_id, "Source upgrade ID")
    target_id = _ascii_id(target_upgrade_id, "Target upgrade ID")
    if len(target_id) > len(source_id):
        raise ValueError(
            "Target upgrade ID is longer than the equal-length upgrade carrier."
        )
    target_values = {
        "build_queue_id": _validate_u32(build_queue_id, "build_queue_id"),
        "colony_id": _validate_u32(colony_id, "colony_id"),
        "zone_id": _validate_u32(zone_id, "zone_id"),
        "building_object_id": _validate_u32(
            building_object_id,
            "building_object_id",
        ),
    }

    source_marker = (
        UPGRADE_BUILDING_STRING_TAG
        + len(source_id).to_bytes(2, "little")
        + source_id
    )
    matching: list[tuple[Any, int]] = []
    for command in find_command_records(payload):
        record = payload[command.offset : command.offset + command.length]
        marker_offset = record.find(source_marker)
        if marker_offset >= 0:
            matching.append((command, marker_offset))
    if not matching:
        return None
    if len(matching) != 1:
        raise ValueError("Payload contains multiple matching upgrade records.")

    command, marker_offset = matching[0]
    record = payload[command.offset : command.offset + command.length]
    outer_tag_offset = marker_offset - 6
    queue_tag_offset = outer_tag_offset - 10
    context_tag_offset = queue_tag_offset - 10
    if context_tag_offset < 0:
        raise ValueError("Upgrade record is missing its context fields.")
    if record[context_tag_offset : context_tag_offset + 6] != CONTEXT_822C_TAG:
        raise ValueError("Upgrade record does not contain the expected 822c context.")
    if (
        record[queue_tag_offset + 2 : queue_tag_offset + 6]
        != SERIALIZED_U32_SUFFIX
    ):
        raise ValueError("Upgrade record queue field has an unknown shape.")
    if (
        record[outer_tag_offset + 2 : outer_tag_offset + 6]
        != SERIALIZED_DYNAMIC_SUFFIX
    ):
        raise ValueError("Upgrade record dynamic string field has an unknown shape.")

    source_end = marker_offset + len(source_marker)
    colony_tag_offset = source_end
    colony_value_offset = colony_tag_offset + len(UPGRADE_COLONY_TAG)
    zone_tag_offset = colony_value_offset + 4
    zone_value_offset = zone_tag_offset + len(UPGRADE_ZONE_TAG)
    building_tag_offset = zone_value_offset + 4
    building_value_offset = (
        building_tag_offset + len(UPGRADE_BUILDING_OBJECT_TAG)
    )
    tail_offset = building_value_offset + 4
    if record[colony_tag_offset:colony_value_offset] != UPGRADE_COLONY_TAG:
        raise ValueError("Upgrade record is missing its colony field.")
    if record[zone_tag_offset:zone_value_offset] != UPGRADE_ZONE_TAG:
        raise ValueError("Upgrade record is missing its zone field.")
    if (
        record[building_tag_offset:building_value_offset]
        != UPGRADE_BUILDING_OBJECT_TAG
    ):
        raise ValueError("Upgrade record is missing its building object field.")
    if record[tail_offset : tail_offset + len(UPGRADE_RECORD_TAIL)] != UPGRADE_RECORD_TAIL:
        raise ValueError("Upgrade record tail does not match the verified layout.")
    if tail_offset + len(UPGRADE_RECORD_TAIL) != len(record):
        raise ValueError("Upgrade record contains unverified trailing data.")

    source_fields = {
        "source_context_822c": _u32(record, context_tag_offset + 6),
        "source_build_queue_id": _u32(record, queue_tag_offset + 6),
        "source_colony_id": _u32(record, colony_value_offset),
        "source_zone_id": _u32(record, zone_value_offset),
        "source_building_object_id": _u32(record, building_value_offset),
    }

    string_length_offset = marker_offset + len(UPGRADE_BUILDING_STRING_TAG)
    record_without_padding = (
        record[:string_length_offset]
        + len(target_id).to_bytes(2, "little")
        + target_id
        + record[source_end:]
    )
    if len(record_without_padding) > len(record):
        raise ValueError("Upgrade rewrite exceeds the fixed command envelope.")
    padding_length = len(record) - len(record_without_padding)
    rewritten_record = bytearray(record_without_padding + bytes(padding_length))

    rewritten_queue_value_offset = queue_tag_offset + 6
    rewritten_colony_tag_offset = (
        marker_offset
        + len(UPGRADE_BUILDING_STRING_TAG)
        + 2
        + len(target_id)
    )
    rewritten_colony_value_offset = (
        rewritten_colony_tag_offset + len(UPGRADE_COLONY_TAG)
    )
    rewritten_zone_tag_offset = rewritten_colony_value_offset + 4
    rewritten_zone_value_offset = (
        rewritten_zone_tag_offset + len(UPGRADE_ZONE_TAG)
    )
    rewritten_building_tag_offset = rewritten_zone_value_offset + 4
    rewritten_building_value_offset = (
        rewritten_building_tag_offset + len(UPGRADE_BUILDING_OBJECT_TAG)
    )
    rewritten_record[
        rewritten_queue_value_offset : rewritten_queue_value_offset + 4
    ] = target_values["build_queue_id"].to_bytes(4, "little")
    rewritten_record[
        rewritten_colony_value_offset : rewritten_colony_value_offset + 4
    ] = target_values["colony_id"].to_bytes(4, "little")
    rewritten_record[
        rewritten_zone_value_offset : rewritten_zone_value_offset + 4
    ] = target_values["zone_id"].to_bytes(4, "little")
    rewritten_record[
        rewritten_building_value_offset : rewritten_building_value_offset + 4
    ] = target_values["building_object_id"].to_bytes(4, "little")

    rewritten = (
        payload[: command.offset]
        + bytes(rewritten_record)
        + payload[command.offset + command.length :]
    )
    if len(rewritten) != len(payload):
        raise ValueError("Upgrade rewrite changed the UDP payload length.")
    before_commands = find_command_records(payload)
    after_commands = find_command_records(rewritten)
    if len(before_commands) != len(after_commands):
        raise ValueError("Upgrade rewrite changed the command count.")
    rewritten_command = after_commands[before_commands.index(command)]
    if rewritten_command.serial != command.serial:
        raise ValueError("Upgrade rewrite changed the command serial.")

    return rewritten, {
        "layout": "building_upgrade_v1",
        "carrier_offset": command.offset,
        "carrier_length": command.length,
        "carrier_serial": command.serial,
        "padding_length": padding_length,
        "queue_field_tag": record[queue_tag_offset : queue_tag_offset + 2].hex(),
        "dynamic_string_field_tag": record[
            outer_tag_offset : outer_tag_offset + 2
        ].hex(),
        "match_reason": "upgrade_object_context_rewrite_with_fixed_length_padding",
        **source_fields,
    }
