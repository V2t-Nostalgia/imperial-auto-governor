"""Verified Stellaris 4.4.6 ship-design and direct-shipyard commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from iag.stellaris.execution.packet.autonomous_commands import (
    ACTOR_TAG,
    ORIGIN_TAG,
    SERIAL_WIDTH,
    _actor_origin,
    _serial_u32,
    _unique_value_offset,
    _validate_u32,
)
from iag.stellaris.execution.packet.iag_stream_command_injector import (
    COMMAND_SERIAL_OFFSET,
)


END_OBJECT = bytes.fromhex("0400")
ANONYMOUS_OBJECT = bytes.fromhex("0300")
COMMAND_ENVELOPE = bytes.fromhex("04000000")
SHIP_BUILD_FAMILY = bytes.fromhex("b43d01000300")
SHIP_BUILD_PAYLOAD_FAMILY = bytes.fromhex("c43d01000300")
SHIP_DESIGN_FAMILY = bytes.fromhex("fb2d01000300")
COMMON_COMMAND = bytes.fromhex("f30101000300")
COMMAND_PAYLOAD = bytes.fromhex("410001000300")

CONTEXT_TAG = bytes.fromhex("822c01001400")
BUILD_QUEUE_TAG = bytes.fromhex("634001001400")
DESIGN_ID_TAG = bytes.fromhex("652c01001400")
UPGRADE_ID_TAG = bytes.fromhex("143501001400")
GROWTH_STAGE_TAG = bytes.fromhex("c84401000c00")
SHIP_DESTINATION_TAG = bytes.fromhex("0c3a01001400")

DESIGN_OBJECT = bytes.fromhex("652c01000300")
DESIGN_NAME_OBJECT = bytes.fromhex("1b0001000300")
DESIGN_NAME_TAG = bytes.fromhex("dc0001000f00")
LITERAL_NAME_TAG = bytes.fromhex("3d4001000e00")
ENTITY_TAG = bytes.fromhex("232f01000f00")
GRAPHICAL_CULTURE_TAG = bytes.fromhex("cf3001000f00")
GROWTH_STAGES_OBJECT = bytes.fromhex("c94401000300")
SHIP_SIZE_TAG = bytes.fromhex("522c01000f00")
PARENT_TAG = bytes.fromhex("870001001400")
SECTION_OBJECT = bytes.fromhex("5f2c01000300")
TEMPLATE_TAG = bytes.fromhex("cd0201000f00")
SLOT_TAG = bytes.fromhex("632c01000f00")
COMPONENT_OBJECT = bytes.fromhex("642c01000300")
REQUIRED_COMPONENT_TAG = bytes.fromhex("3d3201000f00")


# Non-host request captured on 2026-08-24.  It queues one design at one direct
# shipyard.  All business fields and command identity fields are overwritten.
SHIP_BUILD_RECORD_TEMPLATE = bytes.fromhex(
    "9d0004000000b43d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400240000000400410001000300822c010014000000000063400100"
    "140003000000c43d01000300c74401000300652c01001400740c0000"
    "143501001400ffffffffc84401000c000000000004008b3d01000300"
    "0c3a01001400000000000400040004000400"
)


@dataclass(frozen=True)
class ShipBuildTarget:
    context_822c: int
    build_queue_id: int
    design_id: int
    upgrade_id: int
    growth_stage: int
    destination_object: int


@dataclass(frozen=True)
class ShipDesignTarget:
    blueprint: dict[str, Any]


def _set_tagged_u32(record: bytearray, tag: bytes, value: int, label: str) -> None:
    offset = _unique_value_offset(bytes(record), tag, 4, label)
    record[offset : offset + 4] = _validate_u32(value, label).to_bytes(4, "little")


def _tagged_u32(record: bytes, tag: bytes, label: str) -> int:
    offset = _unique_value_offset(record, tag, 4, label)
    return int.from_bytes(record[offset : offset + 4], "little")


def build_ship_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: ShipBuildTarget,
) -> bytes:
    """Build one b43d direct-shipyard request; one record means one ship."""
    if origin not in (0, 1):
        raise ValueError("origin must be 0 or 1.")
    record = bytearray(SHIP_BUILD_RECORD_TEMPLATE)
    actor_offset = _unique_value_offset(bytes(record), ACTOR_TAG, 4, "actor")
    origin_offset = _unique_value_offset(bytes(record), ORIGIN_TAG, 1, "origin")
    record[actor_offset : actor_offset + 4] = _validate_u32(actor, "actor").to_bytes(
        4, "little"
    )
    record[origin_offset] = origin
    record[
        COMMAND_SERIAL_OFFSET : COMMAND_SERIAL_OFFSET + SERIAL_WIDTH
    ] = _validate_u32(command_serial, "command_serial").to_bytes(4, "little")
    _set_tagged_u32(record, CONTEXT_TAG, target.context_822c, "context_822c")
    _set_tagged_u32(record, BUILD_QUEUE_TAG, target.build_queue_id, "build_queue_id")
    _set_tagged_u32(record, DESIGN_ID_TAG, target.design_id, "design_id")
    _set_tagged_u32(record, UPGRADE_ID_TAG, target.upgrade_id, "upgrade_id")
    _set_tagged_u32(record, GROWTH_STAGE_TAG, target.growth_stage, "growth_stage")
    _set_tagged_u32(
        record,
        SHIP_DESTINATION_TAG,
        target.destination_object,
        "destination_object",
    )
    result = bytes(record)
    if parse_ship_record(result) != target:
        raise RuntimeError("The ship-build target changed during construction.")
    return result


def parse_ship_record(record: bytes) -> ShipBuildTarget | None:
    if record[6 : 6 + len(SHIP_BUILD_FAMILY)] != SHIP_BUILD_FAMILY:
        return None
    if SHIP_BUILD_PAYLOAD_FAMILY not in record:
        return None
    try:
        return ShipBuildTarget(
            context_822c=_tagged_u32(record, CONTEXT_TAG, "context_822c"),
            build_queue_id=_tagged_u32(record, BUILD_QUEUE_TAG, "build_queue_id"),
            design_id=_tagged_u32(record, DESIGN_ID_TAG, "design_id"),
            upgrade_id=_tagged_u32(record, UPGRADE_ID_TAG, "upgrade_id"),
            growth_stage=_tagged_u32(record, GROWTH_STAGE_TAG, "growth_stage"),
            destination_object=_tagged_u32(
                record,
                SHIP_DESTINATION_TAG,
                "destination_object",
            ),
        )
    except ValueError:
        return None


def _string(tag: bytes, value: str, label: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must contain ASCII characters only.") from error
    if len(encoded) > 0xFFFF:
        raise ValueError(f"{label} is too long.")
    return tag + len(encoded).to_bytes(2, "little") + encoded


def _u32(tag: bytes, value: int, label: str) -> bytes:
    return tag + _validate_u32(value, label).to_bytes(4, "little")


def _u8(tag: bytes, value: int, label: str) -> bytes:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{label} must be an unsigned byte.")
    return tag + bytes((value,))


def _common_command(actor: int, origin: int, command_serial: int) -> bytes:
    if origin not in (0, 1):
        raise ValueError("origin must be 0 or 1.")
    return b"".join(
        (
            COMMON_COMMAND,
            bytes.fromhex("400201000c00")
            + _validate_u32(actor, "actor").to_bytes(4, "little"),
            bytes.fromhex("c70001000c00ff7f0000"),
            bytes.fromhex("cc0001000e0000"),
            ORIGIN_TAG + bytes((origin,)),
            bytes.fromhex("db0001001400")
            + _validate_u32(command_serial, "command_serial").to_bytes(4, "little"),
            END_OBJECT,
        )
    )


def _design_body(blueprint: dict[str, Any]) -> bytes:
    name = str(blueprint.get("name") or "")
    entity = str(blueprint.get("entity") or "screen")
    culture = str(blueprint.get("graphical_culture") or "")
    if not name or not culture:
        raise ValueError("A ship design requires name and graphical_culture.")
    stages = blueprint.get("growth_stages")
    if not isinstance(stages, list) or len(stages) != 1:
        raise ValueError("The verified fb2d path requires exactly one growth stage.")

    encoded_stages: list[bytes] = []
    for stage_index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            raise ValueError("Each growth stage must be an object.")
        ship_size = str(stage.get("ship_size") or "")
        if ship_size != "corvette":
            raise ValueError("The verified fb2d path currently supports corvettes only.")
        sections = stage.get("sections")
        if not isinstance(sections, list) or len(sections) != 1:
            raise ValueError("The verified fb2d path requires one section.")
        encoded_sections: list[bytes] = []
        for section_index, section in enumerate(sections):
            if not isinstance(section, dict):
                raise ValueError("Each section must be an object.")
            components = section.get("components")
            if not isinstance(components, list):
                raise ValueError("A section requires a component list.")
            encoded_components: list[bytes] = []
            for component_index, component in enumerate(components):
                if not isinstance(component, dict):
                    raise ValueError("Each component must be an object.")
                encoded_components.append(
                    b"".join(
                        (
                            COMPONENT_OBJECT,
                            _string(
                                SLOT_TAG,
                                str(component.get("slot") or ""),
                                f"component[{component_index}].slot",
                            ),
                            _string(
                                TEMPLATE_TAG,
                                str(component.get("component_id") or ""),
                                f"component[{component_index}].component_id",
                            ),
                            END_OBJECT,
                        )
                    )
                )
            encoded_sections.append(
                b"".join(
                    (
                        SECTION_OBJECT,
                        _string(
                            TEMPLATE_TAG,
                            str(section.get("template") or ""),
                            f"section[{section_index}].template",
                        ),
                        _string(
                            SLOT_TAG,
                            str(section.get("slot") or ""),
                            f"section[{section_index}].slot",
                        ),
                        *encoded_components,
                        END_OBJECT,
                    )
                )
            )
        required = stage.get("required_components")
        if not isinstance(required, list):
            raise ValueError("A growth stage requires required_components.")
        encoded_stages.append(
            b"".join(
                (
                    ANONYMOUS_OBJECT,
                    _string(SHIP_SIZE_TAG, ship_size, f"stage[{stage_index}].ship_size"),
                    _u32(
                        PARENT_TAG,
                        int(stage.get("parent", 0xFFFFFFFF)),
                        f"stage[{stage_index}].parent",
                    ),
                    *encoded_sections,
                    *(
                        _string(
                            REQUIRED_COMPONENT_TAG,
                            str(component_id),
                            "required_component",
                        )
                        for component_id in required
                    ),
                    END_OBJECT,
                )
            )
        )

    return b"".join(
        (
            DESIGN_OBJECT,
            DESIGN_NAME_OBJECT,
            _string(DESIGN_NAME_TAG, name, "name"),
            _u8(LITERAL_NAME_TAG, 1, "literal_name"),
            END_OBJECT,
            _string(ENTITY_TAG, entity, "entity"),
            _string(GRAPHICAL_CULTURE_TAG, culture, "graphical_culture"),
            GROWTH_STAGES_OBJECT,
            *encoded_stages,
            END_OBJECT,
            END_OBJECT,
        )
    )


def build_ship_design_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: ShipDesignTarget,
) -> bytes:
    """Serialize one verified single-section corvette fb2d request."""
    blueprint = target.blueprint
    context = _validate_u32(int(blueprint.get("context_822c", 0)), "context_822c")
    body = b"".join(
        (
            SHIP_DESIGN_FAMILY,
            _common_command(actor, origin, command_serial),
            COMMAND_PAYLOAD,
            _design_body(blueprint),
            _u32(CONTEXT_TAG, context, "context_822c"),
            END_OBJECT,
            END_OBJECT,
        )
    )
    record_length = 6 + len(body)
    declared = record_length - 1
    if declared > 0xFFFF:
        raise ValueError("The serialized ship design exceeds the verified envelope.")
    record = bytes((declared & 0xFF, 0)) + COMMAND_ENVELOPE + body
    if len(record) != record_length:
        raise RuntimeError("The ship-design record length changed unexpectedly.")
    return record


def application_prefix_for_record(record: bytes) -> bytes:
    """Encode the high page of Clausewitz's split command-length envelope."""
    if len(record) < 7 or record[1:6] != b"\x00" + COMMAND_ENVELOPE:
        raise ValueError("The command record has an invalid envelope.")
    declared = len(record) - 1
    if record[0] != declared & 0xFF:
        raise ValueError("The command record low length byte is invalid.")
    pages = declared >> 8
    if pages > 0xFF:
        raise ValueError("The command record needs more than 255 length pages.")
    return b"\x00\x00" + bytes((pages,))


def extended_record_from_application(
    application: bytes,
) -> tuple[int, bytes] | None:
    if len(application) < 10 or application[:2] != b"\x00\x00":
        return None
    offset = 3
    if application[offset + 1 : offset + 6] != b"\x00" + COMMAND_ENVELOPE:
        return None
    declared = (application[2] << 8) | application[offset]
    length = declared + 1
    if offset + length > len(application):
        return None
    return offset, application[offset : offset + length]


def ship_design_record_matches(
    record: bytes,
    *,
    target: ShipDesignTarget,
    actor: int,
    origin: int,
) -> bool:
    if record[6 : 6 + len(SHIP_DESIGN_FAMILY)] != SHIP_DESIGN_FAMILY:
        return False
    try:
        observed_actor, observed_origin = _actor_origin(record)
        if observed_actor != actor or observed_origin != origin:
            return False
        expected = build_ship_design_record(
            command_serial=0,
            actor=actor,
            origin=origin,
            target=target,
        )
        normalized = bytearray(record)
        normalized[
            COMMAND_SERIAL_OFFSET : COMMAND_SERIAL_OFFSET + SERIAL_WIDTH
        ] = bytes(SERIAL_WIDTH)
        return bytes(normalized) == expected
    except (TypeError, ValueError):
        return False


def ship_design_name(record: bytes) -> str:
    length_offset = _unique_value_offset(record, DESIGN_NAME_TAG, 2, "design name")
    length = int.from_bytes(record[length_offset : length_offset + 2], "little")
    start = length_offset + 2
    return record[start : start + length].decode("ascii")


def retag_actor(record: bytes, actor: int) -> bytes:
    output = bytearray(record)
    actor_offset = _unique_value_offset(record, ACTOR_TAG, 4, "actor")
    output[actor_offset : actor_offset + 4] = _validate_u32(actor, "actor").to_bytes(
        4, "little"
    )
    return bytes(output)


def command_identity(record: bytes) -> tuple[int, int, int]:
    actor, origin = _actor_origin(record)
    return actor, origin, _serial_u32(record)
