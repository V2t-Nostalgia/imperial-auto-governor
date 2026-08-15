#!/usr/bin/env python3
"""District and zone construction-record rewrites.

Live carrier replacement keeps the original UDP payload length by default.
The session proxy can explicitly request a resized, length-prefixed command
record because it inserts new reliable-stream bytes instead of replacing an
existing carrier in place.
"""

from __future__ import annotations

from typing import Any

from iag.stellaris.execution.packet.iag_stream_command_injector import (
    find_command_records,
)


COMMAND_BODY_OFFSET = 70
COMMAND_ENVELOPE_TYPE = bytes.fromhex("04000000")
CONTEXT_TAG = bytes.fromhex("822c01001400")
SERIALIZED_U32_SUFFIX = bytes.fromhex("01001400")
SERIALIZED_I32_SUFFIX = bytes.fromhex("01000c00")
SERIALIZED_MARKER_SUFFIX = bytes.fromhex("01000300")
DISTRICT_STRING_TAG = bytes.fromhex("b42b01000f00")
ZONE_STRING_TAG = bytes.fromhex("b32b01000f00")
COLONY_TAG = bytes.fromhex("132a01001400")
ZONE_DISTRICT_TAG = bytes.fromhex("b42b01001400")
RECORD_TAIL = bytes.fromhex("040004000400")


def _matching_record(
    payload: bytes,
    *,
    context_offset: int,
    tail_end: int,
) -> Any | None:
    matches = [
        record
        for record in find_command_records(payload)
        if record.offset + COMMAND_BODY_OFFSET == context_offset
        and record.offset + record.length == tail_end
        and payload[record.offset + 2 : record.offset + 6]
        == COMMAND_ENVELOPE_TYPE
    ]
    return matches[0] if len(matches) == 1 else None


def _rewrite_record(
    payload: bytes,
    *,
    record: Any,
    string_length_offset: int,
    source_id_end: int,
    target_id: bytes,
    prefix_u32_updates: dict[int, int],
    suffix_u32_updates: dict[int, int],
    preserve_length: bool,
) -> tuple[bytes, int]:
    record_end = record.offset + record.length
    prefix = bytearray(payload[record.offset:string_length_offset])
    suffix = bytearray(payload[source_id_end:record_end])
    for absolute_offset, value in prefix_u32_updates.items():
        relative = absolute_offset - record.offset
        prefix[relative : relative + 4] = value.to_bytes(4, "little")
    for absolute_offset, value in suffix_u32_updates.items():
        relative = absolute_offset - source_id_end
        suffix[relative : relative + 4] = value.to_bytes(4, "little")

    rewritten_without_padding = (
        bytes(prefix)
        + len(target_id).to_bytes(2, "little")
        + target_id
        + bytes(suffix)
    )
    if preserve_length:
        if len(rewritten_without_padding) > record.length:
            raise ValueError(
                "The target construction record is longer than its live carrier."
            )
        padding_length = record.length - len(rewritten_without_padding)
        rewritten_record = rewritten_without_padding + bytes(padding_length)
    else:
        padding_length = 0
        dynamic_record = bytearray(rewritten_without_padding)
        dynamic_record[:2] = (len(dynamic_record) - 1).to_bytes(2, "little")
        rewritten_record = bytes(dynamic_record)
    rewritten = (
        payload[: record.offset]
        + rewritten_record
        + payload[record_end:]
    )
    if preserve_length and len(rewritten) != len(payload):
        raise RuntimeError("The construction rewrite changed UDP payload length.")
    return rewritten, padding_length


def rewrite_district_carrier(
    payload: bytes,
    *,
    source_district_type: str,
    target_district_type: str,
    build_queue_id: int,
    colony_id: int,
    preserve_length: bool = True,
) -> tuple[bytes, dict[str, int | str]] | None:
    source_id = source_district_type.encode("ascii")
    target_id = target_district_type.encode("ascii")
    marker = (
        DISTRICT_STRING_TAG
        + len(source_id).to_bytes(2, "little")
        + source_id
    )
    if payload.count(marker) != 1:
        return None

    marker_offset = payload.index(marker)
    dynamic_marker_offset = marker_offset - 6
    queue_tag_offset = dynamic_marker_offset - 10
    context_offset = queue_tag_offset - 10
    if context_offset < 0:
        return None
    if payload[context_offset : context_offset + 6] != CONTEXT_TAG:
        return None
    if (
        payload[queue_tag_offset + 2 : queue_tag_offset + 6]
        != SERIALIZED_U32_SUFFIX
    ):
        return None
    if (
        payload[dynamic_marker_offset + 2 : dynamic_marker_offset + 6]
        != SERIALIZED_MARKER_SUFFIX
    ):
        return None

    source_id_end = marker_offset + len(marker)
    colony_value_offset = source_id_end + len(COLONY_TAG)
    tail_offset = colony_value_offset + 4
    if payload[source_id_end:colony_value_offset] != COLONY_TAG:
        return None
    if payload[tail_offset : tail_offset + len(RECORD_TAIL)] != RECORD_TAIL:
        return None
    record = _matching_record(
        payload,
        context_offset=context_offset,
        tail_end=tail_offset + len(RECORD_TAIL),
    )
    if record is None:
        return None

    queue_value_offset = queue_tag_offset + 6
    string_length_offset = marker_offset + len(DISTRICT_STRING_TAG)
    rewritten, padding_length = _rewrite_record(
        payload,
        record=record,
        string_length_offset=string_length_offset,
        source_id_end=source_id_end,
        target_id=target_id,
        prefix_u32_updates={queue_value_offset: build_queue_id},
        suffix_u32_updates={colony_value_offset: colony_id},
        preserve_length=preserve_length,
    )
    return rewritten, {
        "carrier_offset": record.offset,
        "carrier_length": record.length,
        "carrier_serial": record.serial,
        "padding_length": padding_length,
        "source_district_type": source_district_type,
        "target_district_type": target_district_type,
        "source_build_queue_id": int.from_bytes(
            payload[queue_value_offset : queue_value_offset + 4],
            "little",
        ),
        "source_colony_id": int.from_bytes(
            payload[colony_value_offset : colony_value_offset + 4],
            "little",
        ),
        "queue_field_tag": payload[
            queue_tag_offset : queue_tag_offset + 2
        ].hex(),
        "dynamic_marker_tag": payload[
            dynamic_marker_offset : dynamic_marker_offset + 2
        ].hex(),
        "layout": "district_construction_v1",
    }


def rewrite_zone_carrier(
    payload: bytes,
    *,
    source_zone_type: str,
    target_zone_type: str,
    build_queue_id: int,
    colony_id: int,
    district_id: int,
    slot_selector: int,
    preserve_length: bool = True,
) -> tuple[bytes, dict[str, int | str]] | None:
    source_id = source_zone_type.encode("ascii")
    target_id = target_zone_type.encode("ascii")
    marker = ZONE_STRING_TAG + len(source_id).to_bytes(2, "little") + source_id
    if payload.count(marker) != 1:
        return None

    marker_offset = payload.index(marker)
    dynamic_marker_offset = marker_offset - 6
    queue_tag_offset = dynamic_marker_offset - 10
    context_offset = queue_tag_offset - 10
    if context_offset < 0:
        return None
    if payload[context_offset : context_offset + 6] != CONTEXT_TAG:
        return None
    if (
        payload[queue_tag_offset + 2 : queue_tag_offset + 6]
        != SERIALIZED_U32_SUFFIX
    ):
        return None
    if (
        payload[dynamic_marker_offset + 2 : dynamic_marker_offset + 6]
        != SERIALIZED_MARKER_SUFFIX
    ):
        return None

    source_id_end = marker_offset + len(marker)
    colony_value_offset = source_id_end + len(COLONY_TAG)
    district_tag_offset = colony_value_offset + 4
    district_value_offset = district_tag_offset + len(ZONE_DISTRICT_TAG)
    slot_tag_offset = district_value_offset + 4
    slot_value_offset = slot_tag_offset + 6
    tail_offset = slot_value_offset + 4
    if payload[source_id_end:colony_value_offset] != COLONY_TAG:
        return None
    if (
        payload[district_tag_offset:district_value_offset]
        != ZONE_DISTRICT_TAG
    ):
        return None
    if (
        payload[slot_tag_offset + 2 : slot_tag_offset + 6]
        != SERIALIZED_I32_SUFFIX
    ):
        return None
    if payload[tail_offset : tail_offset + len(RECORD_TAIL)] != RECORD_TAIL:
        return None
    record = _matching_record(
        payload,
        context_offset=context_offset,
        tail_end=tail_offset + len(RECORD_TAIL),
    )
    if record is None:
        return None

    queue_value_offset = queue_tag_offset + 6
    string_length_offset = marker_offset + len(ZONE_STRING_TAG)
    rewritten, padding_length = _rewrite_record(
        payload,
        record=record,
        string_length_offset=string_length_offset,
        source_id_end=source_id_end,
        target_id=target_id,
        prefix_u32_updates={queue_value_offset: build_queue_id},
        suffix_u32_updates={
            colony_value_offset: colony_id,
            district_value_offset: district_id,
            slot_value_offset: slot_selector,
        },
        preserve_length=preserve_length,
    )
    return rewritten, {
        "carrier_offset": record.offset,
        "carrier_length": record.length,
        "carrier_serial": record.serial,
        "padding_length": padding_length,
        "source_zone_type": source_zone_type,
        "target_zone_type": target_zone_type,
        "source_build_queue_id": int.from_bytes(
            payload[queue_value_offset : queue_value_offset + 4],
            "little",
        ),
        "source_colony_id": int.from_bytes(
            payload[colony_value_offset : colony_value_offset + 4],
            "little",
        ),
        "source_district_id": int.from_bytes(
            payload[district_value_offset : district_value_offset + 4],
            "little",
        ),
        "source_slot_selector": int.from_bytes(
            payload[slot_value_offset : slot_value_offset + 4],
            "little",
        ),
        "queue_field_tag": payload[
            queue_tag_offset : queue_tag_offset + 2
        ].hex(),
        "dynamic_marker_tag": payload[
            dynamic_marker_offset : dynamic_marker_offset + 2
        ].hex(),
        "slot_field_tag": payload[
            slot_tag_offset : slot_tag_offset + 2
        ].hex(),
        "layout": "zone_construction_v1",
    }
