"""Verified Stellaris 4.4.6 colonization and starbase command records.

These serializers cover transport structure only.  The calling application must
still validate current ownership, resources, slots, habitability and rule IDs
against the latest save and the active game/content-pack version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from iag.stellaris.execution.packet.autonomous_commands import (
    ACTOR_TAG,
    ORIGIN_TAG,
    SERIAL_WIDTH,
    _actor_origin,
    _unique_value_offset,
    _validate_u32,
)

END_OBJECT = bytes.fromhex("0400")
ANONYMOUS_OBJECT = bytes.fromhex("0300")
COMMAND_ENVELOPE = bytes.fromhex("04000000")
COMMON_COMMAND = bytes.fromhex("f30101000300")
COMMAND_PAYLOAD = bytes.fromhex("410001000300")

ORDER_COLONY_SHIP_FAMILY = bytes.fromhex("3d3701000300")
EXISTING_COLONY_SHIP_FAMILY = bytes.fromhex("e62c01000300")
STARBASE_ACTION_FAMILY = bytes.fromhex("b43d01000300")
STARBASE_UPGRADE_SUBTYPE = bytes.fromhex("cb3d01000300")
STARBASE_MODULE_SUBTYPE = bytes.fromhex("ca3d01000300")
STARBASE_BUILDING_SUBTYPE = bytes.fromhex("c93d01000300")

CONTEXT_TAG = bytes.fromhex("822c01001400")
BUILD_QUEUE_TAG = bytes.fromhex("634001001400")
SPECIES_TAG = bytes.fromhex("4c2b01001400")
COLONY_DESIGNATION_TAG = bytes.fromhex("cd2a01000f00")
DESIGN_ID_TAG = bytes.fromhex("652c01001400")
UPGRADE_ID_TAG = bytes.fromhex("143501001400")
GROWTH_STAGE_TAG = bytes.fromhex("c84401000c00")
COLONY_TARGET_PLANET_TAG = bytes.fromhex("962d01001400")
COLONY_SOURCE_SHIPYARD_QUEUE_TAG = bytes.fromhex("c33d01001400")
COLONY_UNKNOWN_D83D_TAG = bytes.fromhex("d83d01001400")
SOURCE_FLEET_TAG = bytes.fromhex("502c01001400")
EXISTING_COLONY_TARGET_TAG = bytes.fromhex("132a01001400")
FLAG_6340_TAG = bytes.fromhex("634001000e00")
FLAG_DE35_TAG = bytes.fromhex("de3501000e00")

COLONY_REQUEST_OBJECT = bytes.fromhex("3c3701000300")
COLONY_SPECIES_OBJECT = bytes.fromhex("e52f01000300")
COLONY_DESIGN_OBJECT = bytes.fromhex("c74401000300")
COLONY_NAME_OBJECT = bytes.fromhex("1b0001000300")
LOCALIZED_NAME_TAG = bytes.fromhex("dc0001000f00")
LOCALIZATION_OBJECT = bytes.fromhex("3c4001000300")
LOCALIZATION_VALUE_OBJECT = bytes.fromhex("d20201000300")

STARBASE_LEVEL_TAG = bytes.fromhex("1c3a01000f00")
STARBASE_OBJECT_TAG = bytes.fromhex("0c3a01001400")
STARBASE_MODULE_ID_TAG = bytes.fromhex("193a01000f00")
STARBASE_BUILDING_ID_TAG = bytes.fromhex("1a3a01000f00")
STARBASE_SLOT_TAG = bytes.fromhex("632c01000c00")

SCRIPT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
SYSTEM_NAME_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class OrderColonyShipTarget:
    context_822c: int
    species_id: int
    colony_designation: str
    design_id: int
    upgrade_id: int
    growth_stage: int
    target_planet_id: int
    source_shipyard_build_queue_id: int
    system_name_key: str


@dataclass(frozen=True)
class ExistingColonyShipTarget:
    source_fleet_object: int
    target_planet_id: int
    system_name_key: str


@dataclass(frozen=True)
class StarbaseUpgradeTarget:
    context_822c: int
    build_queue_id: int
    target_level: str
    starbase_object: int


@dataclass(frozen=True)
class StarbaseComponentTarget:
    context_822c: int
    build_queue_id: int
    component_id: str
    slot_index: int
    starbase_object: int


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


def _record(body: bytes) -> bytes:
    record_length = 6 + len(body)
    declared = record_length - 1
    if declared > 0xFFFF:
        raise ValueError("The command exceeds the verified two-page envelope.")
    return bytes((declared & 0xFF, 0)) + COMMAND_ENVELOPE + body


def _u32(tag: bytes, value: int, label: str) -> bytes:
    return tag + _validate_u32(value, label).to_bytes(4, "little")


def _u8(tag: bytes, value: int, label: str) -> bytes:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{label} must be an unsigned byte.")
    return tag + bytes((value,))


def _script_id(value: str, label: str, *, maximum: int = 160) -> str:
    if not value or len(value) > maximum or not SCRIPT_ID_RE.fullmatch(value):
        raise ValueError(f"{label} contains unsupported characters.")
    return value


def _string(tag: bytes, value: str, label: str) -> bytes:
    _script_id(value, label, maximum=0xFFFF)
    encoded = value.encode("ascii")
    return tag + len(encoded).to_bytes(2, "little") + encoded


def _tagged_u32(record: bytes, tag: bytes, label: str) -> int:
    offset = _unique_value_offset(record, tag, 4, label)
    return int.from_bytes(record[offset : offset + 4], "little")


def _tagged_u8(record: bytes, tag: bytes, label: str) -> int:
    return record[_unique_value_offset(record, tag, 1, label)]


def _tagged_string(record: bytes, tag: bytes, label: str) -> str:
    length_offset = _unique_value_offset(record, tag, 2, label)
    length = int.from_bytes(record[length_offset : length_offset + 2], "little")
    start = length_offset + 2
    end = start + length
    if end > len(record):
        raise ValueError(f"{label} is truncated.")
    try:
        return record[start:end].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} must be ASCII.") from error


def _default_colony_name(system_name_key: str) -> bytes:
    if not SYSTEM_NAME_KEY_RE.fullmatch(system_name_key):
        raise ValueError("system_name_key contains unsupported characters.")
    return b"".join(
        (
            COLONY_NAME_OBJECT,
            _string(LOCALIZED_NAME_TAG, "NEW_COLONY_NAME_1", "name template"),
            LOCALIZATION_OBJECT,
            ANONYMOUS_OBJECT,
            _string(LOCALIZED_NAME_TAG, "NAME", "name variable"),
            LOCALIZATION_VALUE_OBJECT,
            _string(LOCALIZED_NAME_TAG, system_name_key, "system_name_key"),
            END_OBJECT,
            END_OBJECT,
            END_OBJECT,
            END_OBJECT,
        )
    )


def _parse_default_colony_name(record: bytes) -> str:
    marker = record.find(LOCALIZATION_VALUE_OBJECT)
    if marker < 0 or record.find(LOCALIZATION_VALUE_OBJECT, marker + 1) >= 0:
        raise ValueError("Expected one colony localization value object.")
    cursor = marker + len(LOCALIZATION_VALUE_OBJECT)
    if record[cursor : cursor + len(LOCALIZED_NAME_TAG)] != LOCALIZED_NAME_TAG:
        raise ValueError("The colony system-name field is missing.")
    length_offset = cursor + len(LOCALIZED_NAME_TAG)
    if length_offset + 2 > len(record):
        raise ValueError("The colony system-name length is truncated.")
    length = int.from_bytes(record[length_offset : length_offset + 2], "little")
    start = length_offset + 2
    end = start + length
    if end > len(record):
        raise ValueError("The colony system-name value is truncated.")
    try:
        value = record[start:end].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("The colony system-name key must be ASCII.") from error
    if _default_colony_name(value) not in record:
        raise ValueError("The default colony-name object is not canonical.")
    return value


def build_order_colony_ship_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: OrderColonyShipTarget,
) -> bytes:
    """Build the paired 3d37 order-a-colony-ship-and-colonize request."""
    designation = _script_id(target.colony_designation, "colony_designation")
    body = b"".join(
        (
            ORDER_COLONY_SHIP_FAMILY,
            _common_command(actor, origin, command_serial),
            COMMAND_PAYLOAD,
            _u32(CONTEXT_TAG, target.context_822c, "context_822c"),
            COLONY_REQUEST_OBJECT,
            COLONY_SPECIES_OBJECT,
            _u32(SPECIES_TAG, target.species_id, "species_id"),
            _string(COLONY_DESIGNATION_TAG, designation, "colony_designation"),
            END_OBJECT,
            COLONY_DESIGN_OBJECT,
            _u32(DESIGN_ID_TAG, target.design_id, "design_id"),
            _u32(UPGRADE_ID_TAG, target.upgrade_id, "upgrade_id"),
            _u32(GROWTH_STAGE_TAG, target.growth_stage, "growth_stage"),
            END_OBJECT,
            _u32(
                COLONY_TARGET_PLANET_TAG,
                target.target_planet_id,
                "target_planet_id",
            ),
            _u32(
                COLONY_SOURCE_SHIPYARD_QUEUE_TAG,
                target.source_shipyard_build_queue_id,
                "source_shipyard_build_queue_id",
            ),
            _u32(COLONY_UNKNOWN_D83D_TAG, 0xFFFFFFFF, "unknown_d83d"),
            _default_colony_name(target.system_name_key),
            END_OBJECT,
            END_OBJECT,
            END_OBJECT,
        )
    )
    result = _record(body)
    if parse_order_colony_ship_record(result) != target:
        raise RuntimeError("The colony-order target changed during construction.")
    return result


def parse_order_colony_ship_record(record: bytes) -> OrderColonyShipTarget | None:
    if record[6 : 6 + len(ORDER_COLONY_SHIP_FAMILY)] != ORDER_COLONY_SHIP_FAMILY:
        return None
    try:
        if _tagged_u32(
            record, COLONY_UNKNOWN_D83D_TAG, "unknown_d83d"
        ) != 0xFFFFFFFF:
            return None
        return OrderColonyShipTarget(
            context_822c=_tagged_u32(record, CONTEXT_TAG, "context_822c"),
            species_id=_tagged_u32(record, SPECIES_TAG, "species_id"),
            colony_designation=_tagged_string(
                record, COLONY_DESIGNATION_TAG, "colony_designation"
            ),
            design_id=_tagged_u32(record, DESIGN_ID_TAG, "design_id"),
            upgrade_id=_tagged_u32(record, UPGRADE_ID_TAG, "upgrade_id"),
            growth_stage=_tagged_u32(record, GROWTH_STAGE_TAG, "growth_stage"),
            target_planet_id=_tagged_u32(
                record, COLONY_TARGET_PLANET_TAG, "target_planet_id"
            ),
            source_shipyard_build_queue_id=_tagged_u32(
                record,
                COLONY_SOURCE_SHIPYARD_QUEUE_TAG,
                "source_shipyard_build_queue_id",
            ),
            system_name_key=_parse_default_colony_name(record),
        )
    except ValueError:
        return None


def build_existing_colony_ship_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: ExistingColonyShipTarget,
) -> bytes:
    """Build the paired e62c order for an already existing colony ship."""
    body = b"".join(
        (
            EXISTING_COLONY_SHIP_FAMILY,
            _common_command(actor, origin, command_serial),
            COMMAND_PAYLOAD,
            _u32(
                SOURCE_FLEET_TAG,
                target.source_fleet_object,
                "source_fleet_object",
            ),
            _u32(
                EXISTING_COLONY_TARGET_TAG,
                target.target_planet_id,
                "target_planet_id",
            ),
            _u8(FLAG_6340_TAG, 0, "flag_6340"),
            _u8(FLAG_DE35_TAG, 0, "flag_de35"),
            _default_colony_name(target.system_name_key),
            END_OBJECT,
            END_OBJECT,
        )
    )
    result = _record(body)
    if parse_existing_colony_ship_record(result) != target:
        raise RuntimeError("The existing-colony-ship target changed during construction.")
    return result


def parse_existing_colony_ship_record(
    record: bytes,
) -> ExistingColonyShipTarget | None:
    if record[6 : 6 + len(EXISTING_COLONY_SHIP_FAMILY)] != (
        EXISTING_COLONY_SHIP_FAMILY
    ):
        return None
    try:
        if _tagged_u8(record, FLAG_6340_TAG, "flag_6340") != 0:
            return None
        if _tagged_u8(record, FLAG_DE35_TAG, "flag_de35") != 0:
            return None
        return ExistingColonyShipTarget(
            source_fleet_object=_tagged_u32(
                record, SOURCE_FLEET_TAG, "source_fleet_object"
            ),
            target_planet_id=_tagged_u32(
                record, EXISTING_COLONY_TARGET_TAG, "target_planet_id"
            ),
            system_name_key=_parse_default_colony_name(record),
        )
    except ValueError:
        return None


def _build_starbase_action(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    context_822c: int,
    build_queue_id: int,
    subtype: bytes,
    payload: bytes,
) -> bytes:
    return _record(
        b"".join(
            (
                STARBASE_ACTION_FAMILY,
                _common_command(actor, origin, command_serial),
                COMMAND_PAYLOAD,
                _u32(CONTEXT_TAG, context_822c, "context_822c"),
                _u32(BUILD_QUEUE_TAG, build_queue_id, "build_queue_id"),
                subtype,
                payload,
                END_OBJECT,
                END_OBJECT,
                END_OBJECT,
            )
        )
    )


def build_starbase_upgrade_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: StarbaseUpgradeTarget,
) -> bytes:
    level = _script_id(target.target_level, "target_level")
    result = _build_starbase_action(
        command_serial=command_serial,
        actor=actor,
        origin=origin,
        context_822c=target.context_822c,
        build_queue_id=target.build_queue_id,
        subtype=STARBASE_UPGRADE_SUBTYPE,
        payload=b"".join(
            (
                _string(STARBASE_LEVEL_TAG, level, "target_level"),
                _u32(
                    STARBASE_OBJECT_TAG,
                    target.starbase_object,
                    "starbase_object",
                ),
            )
        ),
    )
    if parse_starbase_upgrade_record(result) != target:
        raise RuntimeError("The starbase-upgrade target changed during construction.")
    return result


def parse_starbase_upgrade_record(record: bytes) -> StarbaseUpgradeTarget | None:
    if record[6 : 6 + len(STARBASE_ACTION_FAMILY)] != STARBASE_ACTION_FAMILY:
        return None
    if STARBASE_UPGRADE_SUBTYPE not in record:
        return None
    try:
        return StarbaseUpgradeTarget(
            context_822c=_tagged_u32(record, CONTEXT_TAG, "context_822c"),
            build_queue_id=_tagged_u32(record, BUILD_QUEUE_TAG, "build_queue_id"),
            target_level=_tagged_string(record, STARBASE_LEVEL_TAG, "target_level"),
            starbase_object=_tagged_u32(
                record, STARBASE_OBJECT_TAG, "starbase_object"
            ),
        )
    except ValueError:
        return None


def build_starbase_component_record(
    *,
    kind: str,
    command_serial: int,
    actor: int,
    origin: int,
    target: StarbaseComponentTarget,
) -> bytes:
    if kind == "module":
        subtype = STARBASE_MODULE_SUBTYPE
        component_tag = STARBASE_MODULE_ID_TAG
    elif kind == "building":
        subtype = STARBASE_BUILDING_SUBTYPE
        component_tag = STARBASE_BUILDING_ID_TAG
    else:
        raise ValueError("Starbase component kind must be module or building.")
    component_id = _script_id(target.component_id, "component_id")
    result = _build_starbase_action(
        command_serial=command_serial,
        actor=actor,
        origin=origin,
        context_822c=target.context_822c,
        build_queue_id=target.build_queue_id,
        subtype=subtype,
        payload=b"".join(
            (
                _string(component_tag, component_id, "component_id"),
                _u32(STARBASE_SLOT_TAG, target.slot_index, "slot_index"),
                _u32(
                    STARBASE_OBJECT_TAG,
                    target.starbase_object,
                    "starbase_object",
                ),
            )
        ),
    )
    if parse_starbase_component_record(result, kind=kind) != target:
        raise RuntimeError("The starbase-component target changed during construction.")
    return result


def parse_starbase_component_record(
    record: bytes,
    *,
    kind: str,
) -> StarbaseComponentTarget | None:
    if record[6 : 6 + len(STARBASE_ACTION_FAMILY)] != STARBASE_ACTION_FAMILY:
        return None
    if kind == "module":
        subtype = STARBASE_MODULE_SUBTYPE
        component_tag = STARBASE_MODULE_ID_TAG
    elif kind == "building":
        subtype = STARBASE_BUILDING_SUBTYPE
        component_tag = STARBASE_BUILDING_ID_TAG
    else:
        raise ValueError("Starbase component kind must be module or building.")
    if subtype not in record:
        return None
    try:
        return StarbaseComponentTarget(
            context_822c=_tagged_u32(record, CONTEXT_TAG, "context_822c"),
            build_queue_id=_tagged_u32(record, BUILD_QUEUE_TAG, "build_queue_id"),
            component_id=_tagged_string(record, component_tag, "component_id"),
            slot_index=_tagged_u32(record, STARBASE_SLOT_TAG, "slot_index"),
            starbase_object=_tagged_u32(
                record, STARBASE_OBJECT_TAG, "starbase_object"
            ),
        )
    except ValueError:
        return None


def command_identity_matches(
    record: bytes,
    *,
    actor: int,
    origin: int,
) -> bool:
    try:
        return _actor_origin(record) == (actor, origin)
    except ValueError:
        return False
