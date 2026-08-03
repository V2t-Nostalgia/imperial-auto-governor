#!/usr/bin/env python3
"""Length-preserving rewrite for Stellaris 4.4.6 building replacements."""

from __future__ import annotations

from typing import Any

from iag_stream_command_injector import find_command_records


SERIALIZED_U32_SUFFIX = bytes.fromhex("01001400")
SERIALIZED_DYNAMIC_SUFFIX = bytes.fromhex("01000300")
CONTEXT_822C_TAG = bytes.fromhex("822c01001400")
REPLACEMENT_BUILDING_STRING_TAG = bytes.fromhex("b22b01000f00")
REPLACEMENT_COLONY_TAG = bytes.fromhex("132a01001400")
REPLACEMENT_ZONE_TAG = bytes.fromhex("b32b01001400")
REPLACEMENT_SOURCE_OBJECT_TAG = bytes.fromhex("5a3d01001400")
REPLACEMENT_DYNAMIC_TAG = bytes.fromhex("bd3d01000300")
REPLACEMENT_RECORD_TAIL = bytes.fromhex("040004000400")


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


def rewrite_building_replacement_carrier(
    payload: bytes,
    *,
    source_replacement_id: str,
    target_replacement_id: str,
    build_queue_id: int,
    colony_id: int,
    zone_id: int,
    source_building_object_id: int,
) -> tuple[bytes, dict[str, int | str]] | None:
    """Redirect one complete replacement command into an exact saved slot.

    The accepted shape is the layout captured from live 4.4.6 co-op traffic.
    In particular, replacement uses the dynamic ``bd3d`` field and source
    object ``5a3d`` field; upgrade records use different tags and are rejected.
    """

    source_id = _ascii_id(source_replacement_id, "Source replacement ID")
    target_id = _ascii_id(target_replacement_id, "Target replacement ID")
    if len(target_id) > len(source_id):
        raise ValueError(
            "Target replacement ID is longer than the equal-length carrier."
        )
    target_values = {
        "build_queue_id": _validate_u32(build_queue_id, "build_queue_id"),
        "colony_id": _validate_u32(colony_id, "colony_id"),
        "zone_id": _validate_u32(zone_id, "zone_id"),
        "source_building_object_id": _validate_u32(
            source_building_object_id,
            "source_building_object_id",
        ),
    }

    source_marker = (
        REPLACEMENT_BUILDING_STRING_TAG
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
        raise ValueError("Payload contains multiple matching replacement records.")

    command, marker_offset = matching[0]
    record = payload[command.offset : command.offset + command.length]
    dynamic_tag_offset = marker_offset - len(REPLACEMENT_DYNAMIC_TAG)
    queue_tag_offset = dynamic_tag_offset - 10
    context_tag_offset = queue_tag_offset - 10
    if context_tag_offset < 0:
        raise ValueError("Replacement record is missing its context fields.")
    if record[context_tag_offset : context_tag_offset + 6] != CONTEXT_822C_TAG:
        raise ValueError(
            "Replacement record does not contain the expected 822c context."
        )
    if record[queue_tag_offset + 2 : queue_tag_offset + 6] != SERIALIZED_U32_SUFFIX:
        raise ValueError("Replacement record queue field has an unknown shape.")
    if (
        record[dynamic_tag_offset : dynamic_tag_offset + 6]
        != REPLACEMENT_DYNAMIC_TAG
    ):
        raise ValueError("Replacement record does not use the verified bd3d marker.")

    source_end = marker_offset + len(source_marker)
    colony_tag_offset = source_end
    colony_value_offset = colony_tag_offset + len(REPLACEMENT_COLONY_TAG)
    zone_tag_offset = colony_value_offset + 4
    zone_value_offset = zone_tag_offset + len(REPLACEMENT_ZONE_TAG)
    source_object_tag_offset = zone_value_offset + 4
    source_object_value_offset = (
        source_object_tag_offset + len(REPLACEMENT_SOURCE_OBJECT_TAG)
    )
    tail_offset = source_object_value_offset + 4
    if record[colony_tag_offset:colony_value_offset] != REPLACEMENT_COLONY_TAG:
        raise ValueError("Replacement record is missing its colony field.")
    if record[zone_tag_offset:zone_value_offset] != REPLACEMENT_ZONE_TAG:
        raise ValueError("Replacement record is missing its zone field.")
    if (
        record[source_object_tag_offset:source_object_value_offset]
        != REPLACEMENT_SOURCE_OBJECT_TAG
    ):
        raise ValueError("Replacement record is missing its source object field.")
    if (
        record[tail_offset : tail_offset + len(REPLACEMENT_RECORD_TAIL)]
        != REPLACEMENT_RECORD_TAIL
    ):
        raise ValueError("Replacement record tail does not match the verified layout.")
    if tail_offset + len(REPLACEMENT_RECORD_TAIL) != len(record):
        raise ValueError("Replacement record contains unverified trailing data.")

    source_fields = {
        "source_context_822c": _u32(record, context_tag_offset + 6),
        "source_build_queue_id": _u32(record, queue_tag_offset + 6),
        "source_colony_id": _u32(record, colony_value_offset),
        "source_zone_id": _u32(record, zone_value_offset),
        "source_building_object_id": _u32(record, source_object_value_offset),
    }

    string_length_offset = marker_offset + len(REPLACEMENT_BUILDING_STRING_TAG)
    record_without_padding = (
        record[:string_length_offset]
        + len(target_id).to_bytes(2, "little")
        + target_id
        + record[source_end:]
    )
    if len(record_without_padding) > len(record):
        raise ValueError("Replacement rewrite exceeds the fixed command envelope.")
    padding_length = len(record) - len(record_without_padding)
    rewritten_record = bytearray(record_without_padding + bytes(padding_length))

    rewritten_queue_value_offset = queue_tag_offset + 6
    rewritten_colony_tag_offset = (
        marker_offset
        + len(REPLACEMENT_BUILDING_STRING_TAG)
        + 2
        + len(target_id)
    )
    rewritten_colony_value_offset = (
        rewritten_colony_tag_offset + len(REPLACEMENT_COLONY_TAG)
    )
    rewritten_zone_tag_offset = rewritten_colony_value_offset + 4
    rewritten_zone_value_offset = (
        rewritten_zone_tag_offset + len(REPLACEMENT_ZONE_TAG)
    )
    rewritten_source_object_tag_offset = rewritten_zone_value_offset + 4
    rewritten_source_object_value_offset = (
        rewritten_source_object_tag_offset + len(REPLACEMENT_SOURCE_OBJECT_TAG)
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
        rewritten_source_object_value_offset : rewritten_source_object_value_offset
        + 4
    ] = target_values["source_building_object_id"].to_bytes(4, "little")

    rewritten = (
        payload[: command.offset]
        + bytes(rewritten_record)
        + payload[command.offset + command.length :]
    )
    if len(rewritten) != len(payload):
        raise ValueError("Replacement rewrite changed the UDP payload length.")
    before_commands = find_command_records(payload)
    after_commands = find_command_records(rewritten)
    if len(before_commands) != len(after_commands):
        raise ValueError("Replacement rewrite changed the command count.")
    rewritten_command = after_commands[before_commands.index(command)]
    if rewritten_command.serial != command.serial:
        raise ValueError("Replacement rewrite changed the command serial.")

    return rewritten, {
        "layout": "building_replacement_v1",
        "carrier_offset": command.offset,
        "carrier_length": command.length,
        "carrier_serial": command.serial,
        "padding_length": padding_length,
        "queue_field_tag": record[queue_tag_offset : queue_tag_offset + 2].hex(),
        "dynamic_marker_tag": record[
            dynamic_tag_offset : dynamic_tag_offset + 2
        ].hex(),
        "source_object_field_tag": record[
            source_object_tag_offset : source_object_tag_offset + 2
        ].hex(),
        "match_reason": (
            "replacement_object_context_rewrite_with_fixed_length_padding"
        ),
        **source_fields,
    }
