"""Verified Stellaris 4.4.6 bombardment and ground-warfare records.

The serializers only encode fields established by paired captures.  Save-aware
callers must still prove fleet ownership, availability, war state, reachability,
queue ownership, species eligibility and sufficient resources.
"""

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

BOMBARDMENT_STANCE_FAMILY = bytes.fromhex("d62d01000300")
ARMY_LANDING_FAMILY = bytes.fromhex("6f3301000300")
ARMY_RECRUITMENT_FAMILY = bytes.fromhex("b43d01000300")

SOURCE_FLEET_TAG = bytes.fromhex("502c01001400")
BOMBARDMENT_STANCE_TAG = bytes.fromhex("d22d01000f00")
TARGET_COLONY_TAG = bytes.fromhex("1e3901001400")
FLAG_6340_TAG = bytes.fromhex("634001000e00")
FLAG_DE35_TAG = bytes.fromhex("de3501000e00")

CONTEXT_TAG = bytes.fromhex("822c01001400")
ARMY_BUILD_QUEUE_TAG = bytes.fromhex("634001001400")
ARMY_DESIGN_OBJECT = bytes.fromhex("c83d01000300")
ARMY_TYPE_TAG = bytes.fromhex("ec4601000f00")
SPECIES_TAG = bytes.fromhex("4c2b01001400")
SOURCE_COLONY_TAG = bytes.fromhex("132a01001400")
RECRUITMENT_STARBASE_TAG = bytes.fromhex("0c3a01001400")

VERIFIED_BOMBARDMENT_STANCES = frozenset(
    {"raiding", "selective", "indiscriminate"}
)
SCRIPT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class BombardmentStanceTarget:
    source_fleet_object: int
    stance: str


@dataclass(frozen=True)
class ArmyLandingTarget:
    source_fleet_object: int
    target_colony_object: int


@dataclass(frozen=True)
class ArmyRecruitmentTarget:
    context_822c: int
    army_build_queue_id: int
    army_type: str
    species_id: int
    source_colony_object: int
    recruitment_starbase_object: int


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
    return bytes((declared & 0xFF, declared >> 8)) + COMMAND_ENVELOPE + body


def _u32(tag: bytes, value: int, label: str) -> bytes:
    return tag + _validate_u32(value, label).to_bytes(4, "little")


def _u8(tag: bytes, value: int, label: str) -> bytes:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{label} must be an unsigned byte.")
    return tag + bytes((value,))


def _script_id(value: str, label: str) -> str:
    if not value or len(value) > 160 or not SCRIPT_ID_RE.fullmatch(value):
        raise ValueError(f"{label} contains unsupported characters.")
    return value


def _string(tag: bytes, value: str, label: str) -> bytes:
    encoded = _script_id(value, label).encode("ascii")
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


def build_bombardment_stance_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: BombardmentStanceTarget,
) -> bytes:
    """Build the paired d62d ground-support stance command."""
    if target.stance not in VERIFIED_BOMBARDMENT_STANCES:
        raise ValueError("Bombardment stance is not in the verified allowlist.")
    body = b"".join(
        (
            BOMBARDMENT_STANCE_FAMILY,
            _common_command(actor, origin, command_serial),
            COMMAND_PAYLOAD,
            _u32(
                SOURCE_FLEET_TAG,
                target.source_fleet_object,
                "source_fleet_object",
            ),
            _string(BOMBARDMENT_STANCE_TAG, target.stance, "stance"),
            END_OBJECT,
            END_OBJECT,
        )
    )
    result = _record(body)
    if parse_bombardment_stance_record(result) != target:
        raise RuntimeError("The bombardment-stance target changed during construction.")
    return result


def parse_bombardment_stance_record(
    record: bytes,
) -> BombardmentStanceTarget | None:
    if record[6 : 6 + len(BOMBARDMENT_STANCE_FAMILY)] != (
        BOMBARDMENT_STANCE_FAMILY
    ):
        return None
    try:
        target = BombardmentStanceTarget(
            source_fleet_object=_tagged_u32(
                record, SOURCE_FLEET_TAG, "source_fleet_object"
            ),
            stance=_tagged_string(record, BOMBARDMENT_STANCE_TAG, "stance"),
        )
    except ValueError:
        return None
    return target if target.stance in VERIFIED_BOMBARDMENT_STANCES else None


def build_army_landing_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: ArmyLandingTarget,
) -> bytes:
    """Build the paired 6f33 transport-fleet landing command."""
    body = b"".join(
        (
            ARMY_LANDING_FAMILY,
            _common_command(actor, origin, command_serial),
            COMMAND_PAYLOAD,
            _u32(
                SOURCE_FLEET_TAG,
                target.source_fleet_object,
                "source_fleet_object",
            ),
            _u32(
                TARGET_COLONY_TAG,
                target.target_colony_object,
                "target_colony_object",
            ),
            _u8(FLAG_6340_TAG, 0, "flag_6340"),
            _u8(FLAG_DE35_TAG, 0, "flag_de35"),
            END_OBJECT,
            END_OBJECT,
        )
    )
    result = _record(body)
    if parse_army_landing_record(result) != target:
        raise RuntimeError("The army-landing target changed during construction.")
    return result


def parse_army_landing_record(record: bytes) -> ArmyLandingTarget | None:
    if record[6 : 6 + len(ARMY_LANDING_FAMILY)] != ARMY_LANDING_FAMILY:
        return None
    try:
        if _tagged_u8(record, FLAG_6340_TAG, "flag_6340") != 0:
            return None
        if _tagged_u8(record, FLAG_DE35_TAG, "flag_de35") != 0:
            return None
        return ArmyLandingTarget(
            source_fleet_object=_tagged_u32(
                record, SOURCE_FLEET_TAG, "source_fleet_object"
            ),
            target_colony_object=_tagged_u32(
                record, TARGET_COLONY_TAG, "target_colony_object"
            ),
        )
    except ValueError:
        return None


def build_army_recruitment_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: ArmyRecruitmentTarget,
) -> bytes:
    """Build one ordinary b43d army recruitment command."""
    body = b"".join(
        (
            ARMY_RECRUITMENT_FAMILY,
            _common_command(actor, origin, command_serial),
            COMMAND_PAYLOAD,
            _u32(CONTEXT_TAG, target.context_822c, "context_822c"),
            _u32(
                ARMY_BUILD_QUEUE_TAG,
                target.army_build_queue_id,
                "army_build_queue_id",
            ),
            ARMY_DESIGN_OBJECT,
            _string(ARMY_TYPE_TAG, target.army_type, "army_type"),
            _u32(SPECIES_TAG, target.species_id, "species_id"),
            _u32(
                SOURCE_COLONY_TAG,
                target.source_colony_object,
                "source_colony_object",
            ),
            _u32(
                RECRUITMENT_STARBASE_TAG,
                target.recruitment_starbase_object,
                "recruitment_starbase_object",
            ),
            END_OBJECT,
            END_OBJECT,
            END_OBJECT,
        )
    )
    result = _record(body)
    if parse_army_recruitment_record(result) != target:
        raise RuntimeError("The army-recruitment target changed during construction.")
    return result


def parse_army_recruitment_record(
    record: bytes,
) -> ArmyRecruitmentTarget | None:
    if record[6 : 6 + len(ARMY_RECRUITMENT_FAMILY)] != ARMY_RECRUITMENT_FAMILY:
        return None
    if ARMY_DESIGN_OBJECT not in record:
        return None
    try:
        return ArmyRecruitmentTarget(
            context_822c=_tagged_u32(record, CONTEXT_TAG, "context_822c"),
            army_build_queue_id=_tagged_u32(
                record, ARMY_BUILD_QUEUE_TAG, "army_build_queue_id"
            ),
            army_type=_tagged_string(record, ARMY_TYPE_TAG, "army_type"),
            species_id=_tagged_u32(record, SPECIES_TAG, "species_id"),
            source_colony_object=_tagged_u32(
                record, SOURCE_COLONY_TAG, "source_colony_object"
            ),
            recruitment_starbase_object=_tagged_u32(
                record,
                RECRUITMENT_STARBASE_TAG,
                "recruitment_starbase_object",
            ),
        )
    except ValueError:
        return None
