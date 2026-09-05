"""Verified Stellaris 4.4.6 fleet and civilian-ship command records.

The builders in this module serialize only fields established by paired client
requests and host-authoritative broadcasts.  Save-aware callers remain
responsible for proving ownership, availability, hostility and target legality.
"""

from __future__ import annotations

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
COMMAND_ENVELOPE = bytes.fromhex("04000000")
COMMON_COMMAND = bytes.fromhex("f30101000300")
COMMAND_PAYLOAD = bytes.fromhex("410001000300")

FLEET_ATTACK_FAMILY = bytes.fromhex("6b3301000300")
BUILD_STARBASE_FAMILY = bytes.fromhex("e02c01000300")
SHIP_AUTOMATION_FAMILY = bytes.fromhex("8f3201000300")

SOURCE_FLEET_TAG = bytes.fromhex("502c01001400")
ATTACK_TARGET_TAG = bytes.fromhex("d62e01001400")
STARBASE_TARGET_TAG = bytes.fromhex("e92d01001400")
CONTEXT_TAG = bytes.fromhex("822c01001400")
FLAG_6340_TAG = bytes.fromhex("634001000e00")
FLAG_DE35_TAG = bytes.fromhex("de3501000e00")
ATTACK_FLAG_TAG = bytes.fromhex("4c0101000e00")

BUILD_STARBASE_PREFIX = bytes.fromhex("e42c01000b3a")
AUTOMATION_COMMAND_OBJECT = bytes.fromhex("903201000300")
AUTOMATION_SETTINGS_OBJECT = bytes.fromhex("f74501000300")
AUTOMATION_ENABLED_TAG = bytes.fromhex("b03801000e00")
AUTOMATION_UNKNOWN_ID_TAG = bytes.fromhex("624501001400")
AUTOMATION_OPTIONS_OBJECT = bytes.fromhex("1e3f01000300")
BARE_STRING_TAG = bytes.fromhex("0f00")

AUTOMATION_COMPLETE_SPECIAL_PROJECTS = "AUTOMATION_COMPLETE_SPECIAL_PROJECTS"
AUTOMATION_EXPLORE = "AUTOMATION_EXPLORE"
AUTOMATION_SURVEY = "AUTOMATION_SURVEY"
AUTOMATION_ANOMALIES = "AUTOMATION_ANOMALIES"
AUTOMATION_DIGSITES = "AUTOMATION_DIGSITES"
AUTOMATION_MINING_STATIONS = "AUTOMATION_MINING_STATIONS"
AUTOMATION_RESEARCH_STATIONS = "AUTOMATION_RESEARCH_STATIONS"
AUTOMATION_OBSERVATION_POSTS = "AUTOMATION_OBSERVATION_POSTS"

AUTOMATION_OPTION_FLAG_TAGS = {
    AUTOMATION_COMPLETE_SPECIAL_PROJECTS: bytes.fromhex("414601000e00"),
    AUTOMATION_EXPLORE: bytes.fromhex("473001000e00"),
    AUTOMATION_SURVEY: bytes.fromhex("483001000e00"),
    AUTOMATION_ANOMALIES: bytes.fromhex("493001000e00"),
    AUTOMATION_DIGSITES: bytes.fromhex("4b3001000e00"),
    AUTOMATION_MINING_STATIONS: bytes.fromhex("3d4601000e00"),
    AUTOMATION_RESEARCH_STATIONS: bytes.fromhex("3e4601000e00"),
    AUTOMATION_OBSERVATION_POSTS: bytes.fromhex("3f4601000e00"),
}
AUTOMATION_FLAG_ORDER = (
    AUTOMATION_COMPLETE_SPECIAL_PROJECTS,
    AUTOMATION_EXPLORE,
    AUTOMATION_SURVEY,
    AUTOMATION_ANOMALIES,
    None,
    AUTOMATION_DIGSITES,
    None,
    AUTOMATION_MINING_STATIONS,
    AUTOMATION_RESEARCH_STATIONS,
    AUTOMATION_OBSERVATION_POSTS,
)
AUTOMATION_UNKNOWN_FLAG_TAGS = (
    bytes.fromhex("4a3001000e00"),
    bytes.fromhex("8d4301000e00"),
)


@dataclass(frozen=True)
class FleetAttackTarget:
    source_fleet_object: int
    target_fleet_object: int


@dataclass(frozen=True)
class ConstructionShipStarbaseTarget:
    source_fleet_object: int
    target_system_object: int


@dataclass(frozen=True)
class ShipAutomationTarget:
    context_822c: int
    source_fleet_object: int
    options: tuple[str, ...]


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


def _u8(tag: bytes, value: int, label: str) -> bytes:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{label} must be an unsigned byte.")
    return tag + bytes((value,))


def _tagged_u32(record: bytes, tag: bytes, label: str) -> int:
    offset = _unique_value_offset(record, tag, 4, label)
    return int.from_bytes(record[offset : offset + 4], "little")


def _tagged_u8(record: bytes, tag: bytes, label: str) -> int:
    return record[_unique_value_offset(record, tag, 1, label)]


def _record(body: bytes) -> bytes:
    record_length = 6 + len(body)
    declared = record_length - 1
    if declared > 0xFFFF:
        raise ValueError("The command exceeds the verified two-page envelope.")
    return bytes((declared & 0xFF, 0)) + COMMAND_ENVELOPE + body


def _identity_matches(record: bytes, *, actor: int, origin: int) -> bool:
    try:
        return _actor_origin(record) == (actor, origin)
    except ValueError:
        return False


def build_fleet_attack_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: FleetAttackTarget,
) -> bytes:
    """Build the paired 6b33 order used for hostile fleets and stations."""
    body = b"".join(
        (
            FLEET_ATTACK_FAMILY,
            _common_command(actor, origin, command_serial),
            COMMAND_PAYLOAD,
            _u32(
                SOURCE_FLEET_TAG,
                target.source_fleet_object,
                "source_fleet_object",
            ),
            _u32(
                ATTACK_TARGET_TAG,
                target.target_fleet_object,
                "target_fleet_object",
            ),
            _u8(ATTACK_FLAG_TAG, 1, "attack_flag"),
            END_OBJECT,
            END_OBJECT,
        )
    )
    result = _record(body)
    if parse_fleet_attack_record(result) != target:
        raise RuntimeError("The fleet-attack target changed during construction.")
    return result


def parse_fleet_attack_record(record: bytes) -> FleetAttackTarget | None:
    if record[6 : 6 + len(FLEET_ATTACK_FAMILY)] != FLEET_ATTACK_FAMILY:
        return None
    try:
        if _tagged_u8(record, ATTACK_FLAG_TAG, "attack_flag") != 1:
            return None
        return FleetAttackTarget(
            source_fleet_object=_tagged_u32(
                record, SOURCE_FLEET_TAG, "source_fleet_object"
            ),
            target_fleet_object=_tagged_u32(
                record, ATTACK_TARGET_TAG, "target_fleet_object"
            ),
        )
    except ValueError:
        return None


def build_construction_ship_starbase_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: ConstructionShipStarbaseTarget,
) -> bytes:
    """Build the paired e02c construction-ship outpost order."""
    body = b"".join(
        (
            BUILD_STARBASE_FAMILY,
            _common_command(actor, origin, command_serial),
            COMMAND_PAYLOAD,
            BUILD_STARBASE_PREFIX,
            _u32(
                SOURCE_FLEET_TAG,
                target.source_fleet_object,
                "source_fleet_object",
            ),
            _u32(
                STARBASE_TARGET_TAG,
                target.target_system_object,
                "target_system_object",
            ),
            _u8(FLAG_6340_TAG, 0, "flag_6340"),
            _u8(FLAG_DE35_TAG, 0, "flag_de35"),
            END_OBJECT,
            END_OBJECT,
        )
    )
    result = _record(body)
    if parse_construction_ship_starbase_record(result) != target:
        raise RuntimeError("The starbase target changed during construction.")
    return result


def parse_construction_ship_starbase_record(
    record: bytes,
) -> ConstructionShipStarbaseTarget | None:
    if record[6 : 6 + len(BUILD_STARBASE_FAMILY)] != BUILD_STARBASE_FAMILY:
        return None
    if BUILD_STARBASE_PREFIX not in record:
        return None
    try:
        if _tagged_u8(record, FLAG_6340_TAG, "flag_6340") != 0:
            return None
        if _tagged_u8(record, FLAG_DE35_TAG, "flag_de35") != 0:
            return None
        return ConstructionShipStarbaseTarget(
            source_fleet_object=_tagged_u32(
                record, SOURCE_FLEET_TAG, "source_fleet_object"
            ),
            target_system_object=_tagged_u32(
                record, STARBASE_TARGET_TAG, "target_system_object"
            ),
        )
    except ValueError:
        return None


def _bare_string(value: str, label: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must contain ASCII characters only.") from error
    if len(encoded) > 0xFFFF:
        raise ValueError(f"{label} is too long.")
    return BARE_STRING_TAG + len(encoded).to_bytes(2, "little") + encoded


def _validated_automation_options(options: tuple[str, ...]) -> tuple[str, ...]:
    if not options:
        raise ValueError("At least one verified automation option is required.")
    if len(set(options)) != len(options):
        raise ValueError("Automation options must not contain duplicates.")
    unsupported = [
        option for option in options if option not in AUTOMATION_OPTION_FLAG_TAGS
    ]
    if unsupported:
        raise ValueError(f"Unsupported automation option: {unsupported[0]}")
    return options


def build_ship_automation_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: ShipAutomationTarget,
) -> bytes:
    """Build an 8f32 science/construction ship automation configuration."""
    options = _validated_automation_options(target.options)
    enabled = set(options)
    flags: list[bytes] = []
    unknown_index = 0
    for option in AUTOMATION_FLAG_ORDER:
        if option is None:
            tag = AUTOMATION_UNKNOWN_FLAG_TAGS[unknown_index]
            unknown_index += 1
            flags.append(_u8(tag, 0, "unknown_automation_flag"))
        else:
            flags.append(
                _u8(
                    AUTOMATION_OPTION_FLAG_TAGS[option],
                    int(option in enabled),
                    option,
                )
            )
    body = b"".join(
        (
            SHIP_AUTOMATION_FAMILY,
            _common_command(actor, origin, command_serial),
            COMMAND_PAYLOAD,
            AUTOMATION_COMMAND_OBJECT,
            AUTOMATION_SETTINGS_OBJECT,
            _u8(AUTOMATION_ENABLED_TAG, 1, "automation_enabled"),
            _u32(
                AUTOMATION_UNKNOWN_ID_TAG,
                0xFFFFFFFF,
                "automation_unknown_id",
            ),
            AUTOMATION_OPTIONS_OBJECT,
            *(_bare_string(option, "automation option") for option in options),
            END_OBJECT,
            *flags,
            END_OBJECT,
            END_OBJECT,
            _u32(CONTEXT_TAG, target.context_822c, "context_822c"),
            _u32(
                SOURCE_FLEET_TAG,
                target.source_fleet_object,
                "source_fleet_object",
            ),
            END_OBJECT,
            END_OBJECT,
        )
    )
    result = _record(body)
    if parse_ship_automation_record(result) != target:
        raise RuntimeError("The ship-automation target changed during construction.")
    return result


def _parse_option_list(record: bytes) -> tuple[str, ...]:
    marker = record.find(AUTOMATION_OPTIONS_OBJECT)
    if marker < 0 or record.find(AUTOMATION_OPTIONS_OBJECT, marker + 1) >= 0:
        raise ValueError("Expected one automation option list.")
    cursor = marker + len(AUTOMATION_OPTIONS_OBJECT)
    options: list[str] = []
    while record[cursor : cursor + len(END_OBJECT)] != END_OBJECT:
        if record[cursor : cursor + 2] != BARE_STRING_TAG:
            raise ValueError("The automation option list is malformed.")
        if cursor + 4 > len(record):
            raise ValueError("The automation option length is truncated.")
        length = int.from_bytes(record[cursor + 2 : cursor + 4], "little")
        start = cursor + 4
        end = start + length
        if end > len(record):
            raise ValueError("The automation option value is truncated.")
        try:
            options.append(record[start:end].decode("ascii"))
        except UnicodeDecodeError as error:
            raise ValueError("Automation options must be ASCII.") from error
        cursor = end
    return _validated_automation_options(tuple(options))


def parse_ship_automation_record(record: bytes) -> ShipAutomationTarget | None:
    if record[6 : 6 + len(SHIP_AUTOMATION_FAMILY)] != SHIP_AUTOMATION_FAMILY:
        return None
    try:
        options = _parse_option_list(record)
        if _tagged_u8(record, AUTOMATION_ENABLED_TAG, "automation_enabled") != 1:
            return None
        if _tagged_u32(
            record,
            AUTOMATION_UNKNOWN_ID_TAG,
            "automation_unknown_id",
        ) != 0xFFFFFFFF:
            return None
        enabled = set(options)
        for option, tag in AUTOMATION_OPTION_FLAG_TAGS.items():
            if _tagged_u8(record, tag, option) != int(option in enabled):
                return None
        for index, tag in enumerate(AUTOMATION_UNKNOWN_FLAG_TAGS):
            if _tagged_u8(record, tag, f"unknown_flag_{index}") != 0:
                return None
        return ShipAutomationTarget(
            context_822c=_tagged_u32(record, CONTEXT_TAG, "context_822c"),
            source_fleet_object=_tagged_u32(
                record, SOURCE_FLEET_TAG, "source_fleet_object"
            ),
            options=options,
        )
    except ValueError:
        return None


def command_identity_matches(
    record: bytes,
    *,
    actor: int,
    origin: int,
) -> bool:
    """Return whether a parsed record carries the expected command identity."""
    return _identity_matches(record, actor=actor, origin=origin)
