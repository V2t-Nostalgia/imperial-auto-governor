#!/usr/bin/env python3
"""Verified Stellaris 4.4.6 fleet-template and reinforcement records.

The paired non-host captures show that the Fleet Manager does not send an
absolute quantity. Each plus/minus click is one command, followed by one
``123b`` request for the selected understrength fleet template. ``f23b`` was
observed in the separate empire-wide Reinforce All route and is deliberately
not exposed as part of selected-fleet reinforcement.
"""

from __future__ import annotations

from dataclasses import dataclass

from iag.stellaris.execution.packet.autonomous_commands import (
    ACTOR_TAG,
    ORIGIN_TAG,
    _actor_origin,
    _serial_u32,
    _unique_value_offset,
    _validate_u32,
)
from iag.stellaris.execution.packet.iag_stream_command_injector import (
    COMMAND_SERIAL_OFFSET,
)

SERIAL_WIDTH = 4
COMMAND_TRAILER = bytes.fromhex("04000400")
ADD_TEMPLATE_SHIP_FAMILY = bytes.fromhex("5f3b01000300")
REMOVE_TEMPLATE_SHIP_FAMILY = bytes.fromhex("603b01000300")
SELECTED_FLEET_REINFORCEMENT_FAMILY = bytes.fromhex("123b01000300")
EMPIRE_REINFORCE_ALL_FAMILY = bytes.fromhex("f23b01000300")
CREATE_FLEET_TEMPLATE_FAMILY = bytes.fromhex("7e3b01000300")

FLEET_TEMPLATE_TAG = bytes.fromhex("0e3b01001400")
DESIGN_ID_TAG = bytes.fromhex("652c01001400")
UPGRADE_ID_TAG = bytes.fromhex("143501001400")
GROWTH_STAGE_TAG = bytes.fromhex("c84401000c00")
CONTEXT_TAG = bytes.fromhex("822c01001400")


# Non-host requests captured in one authorized 4.4.6 co-op session.  Every
# volatile field is overwritten by the builders below.
ADD_TEMPLATE_SHIP_RECORD = bytes.fromhex(
    "9800040000005f3b01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14002e00000004004100010003000e3b0100140000000000692c0100"
    "0300652c01001400740c0000143501001400ffffffffc84401000c00"
    "000000000400822c0100140000000000132b01000e00010c00000000"
    "0001000c000100000004000400"
)
REMOVE_TEMPLATE_SHIP_RECORD = bytes.fromhex(
    "8e0004000000603b01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14002f00000004004100010003000e3b0100140000000000692c0100"
    "0300652c01001400740c0000143501001400ffffffffc84401000c00"
    "000000000400132b01000e00010c000000000001000c000100000004"
    "000400"
)
SELECTED_FLEET_REINFORCEMENT_RECORD = bytes.fromhex(
    "5d0004000000123b01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400390000000400410001000300822c01001400000000000e3b0100"
    "1400fd00000a04000400"
)
# Retained only as protocol evidence. Its exact empire-wide semantics are not
# sufficiently mapped for autonomous execution.
EMPIRE_REINFORCE_ALL_RECORD = bytes.fromhex(
    "5d0004000000f23b01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400340000000400410001000300822c01001400000000000e3b0100"
    "14000000000004000400"
)
CREATE_FLEET_TEMPLATE_RECORD = bytes.fromhex(
    "5a00040000007e3b01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400350000000400410001000300822c0100140000000000132b0100"
    "0e000104000400"
)


@dataclass(frozen=True)
class FleetTemplateAddTarget:
    context_822c: int
    fleet_template_id: int
    design_id: int
    upgrade_id: int = 0xFFFFFFFF
    growth_stage: int = 0


@dataclass(frozen=True)
class FleetTemplateRemoveTarget:
    fleet_template_id: int
    design_id: int
    upgrade_id: int = 0xFFFFFFFF
    growth_stage: int = 0


@dataclass(frozen=True)
class FleetReinforcementTarget:
    context_822c: int
    fleet_template_id: int


@dataclass(frozen=True)
class FleetTemplateCreationTarget:
    context_822c: int


def _family(record: bytes) -> bytes:
    return record[6:12]


def _validate_record(record: bytes) -> None:
    if len(record) < 12 or int.from_bytes(record[:2], "little") + 1 != len(record):
        raise ValueError("The fleet-template record has an invalid declared length.")
    if record[-len(COMMAND_TRAILER) :] != COMMAND_TRAILER:
        raise ValueError("The fleet-template record has an invalid trailer.")


def _u32(record: bytes, tag: bytes, label: str) -> int:
    offset = _unique_value_offset(record, tag, 4, label)
    return int.from_bytes(record[offset : offset + 4], "little")


def _set_u32(record: bytearray, tag: bytes, value: int, label: str) -> None:
    offset = _unique_value_offset(bytes(record), tag, 4, label)
    record[offset : offset + 4] = _validate_u32(value, label).to_bytes(4, "little")


def _set_identity(
    record: bytearray,
    *,
    command_serial: int,
    actor: int,
    origin: int,
) -> None:
    if origin not in (0, 1):
        raise ValueError("origin must be 0 or 1.")
    actor_offset = _unique_value_offset(bytes(record), ACTOR_TAG, 4, "actor")
    origin_offset = _unique_value_offset(bytes(record), ORIGIN_TAG, 1, "origin")
    record[actor_offset : actor_offset + 4] = _validate_u32(actor, "actor").to_bytes(
        4, "little"
    )
    record[origin_offset] = origin
    record[
        COMMAND_SERIAL_OFFSET : COMMAND_SERIAL_OFFSET + SERIAL_WIDTH
    ] = _validate_u32(command_serial, "command_serial").to_bytes(4, "little")


def parse_template_edit(
    record: bytes,
) -> tuple[str, FleetTemplateAddTarget | FleetTemplateRemoveTarget] | None:
    family = _family(record)
    if family not in {ADD_TEMPLATE_SHIP_FAMILY, REMOVE_TEMPLATE_SHIP_FAMILY}:
        return None
    _validate_record(record)
    common = {
        "fleet_template_id": _u32(
            record, FLEET_TEMPLATE_TAG, "fleet_template_id"
        ),
        "design_id": _u32(record, DESIGN_ID_TAG, "design_id"),
        "upgrade_id": _u32(record, UPGRADE_ID_TAG, "upgrade_id"),
        "growth_stage": _u32(record, GROWTH_STAGE_TAG, "growth_stage"),
    }
    if family == ADD_TEMPLATE_SHIP_FAMILY:
        return (
            "add",
            FleetTemplateAddTarget(
                context_822c=_u32(record, CONTEXT_TAG, "context_822c"),
                **common,
            ),
        )
    return "remove", FleetTemplateRemoveTarget(**common)


def build_template_edit_record(
    *,
    action: str,
    command_serial: int,
    actor: int,
    origin: int,
    target: FleetTemplateAddTarget | FleetTemplateRemoveTarget,
) -> bytes:
    if action == "add" and isinstance(target, FleetTemplateAddTarget):
        record = bytearray(ADD_TEMPLATE_SHIP_RECORD)
        _set_u32(record, CONTEXT_TAG, target.context_822c, "context_822c")
    elif action == "remove" and isinstance(target, FleetTemplateRemoveTarget):
        record = bytearray(REMOVE_TEMPLATE_SHIP_RECORD)
    else:
        raise TypeError("The fleet-template edit action and target do not match.")
    _set_identity(
        record,
        command_serial=command_serial,
        actor=actor,
        origin=origin,
    )
    _set_u32(record, FLEET_TEMPLATE_TAG, target.fleet_template_id, "fleet_template_id")
    _set_u32(record, DESIGN_ID_TAG, target.design_id, "design_id")
    _set_u32(record, UPGRADE_ID_TAG, target.upgrade_id, "upgrade_id")
    _set_u32(record, GROWTH_STAGE_TAG, target.growth_stage, "growth_stage")
    result = bytes(record)
    parsed = parse_template_edit(result)
    if parsed != (action, target):
        raise RuntimeError("The fleet-template edit target changed during construction.")
    return result


def parse_selected_fleet_reinforcement(
    record: bytes,
) -> FleetReinforcementTarget | None:
    if _family(record) != SELECTED_FLEET_REINFORCEMENT_FAMILY:
        return None
    _validate_record(record)
    return FleetReinforcementTarget(
        context_822c=_u32(record, CONTEXT_TAG, "context_822c"),
        fleet_template_id=_u32(
            record, FLEET_TEMPLATE_TAG, "fleet_template_id"
        ),
    )


def build_selected_fleet_reinforcement_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: FleetReinforcementTarget,
) -> bytes:
    record = bytearray(SELECTED_FLEET_REINFORCEMENT_RECORD)
    _set_identity(
        record,
        command_serial=command_serial,
        actor=actor,
        origin=origin,
    )
    _set_u32(record, CONTEXT_TAG, target.context_822c, "context_822c")
    _set_u32(record, FLEET_TEMPLATE_TAG, target.fleet_template_id, "fleet_template_id")
    result = bytes(record)
    if parse_selected_fleet_reinforcement(result) != target:
        raise RuntimeError("The reinforcement target changed during construction.")
    return result


def parse_template_creation(record: bytes) -> FleetTemplateCreationTarget | None:
    if _family(record) != CREATE_FLEET_TEMPLATE_FAMILY:
        return None
    _validate_record(record)
    return FleetTemplateCreationTarget(
        context_822c=_u32(record, CONTEXT_TAG, "context_822c")
    )


def build_template_creation_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: FleetTemplateCreationTarget,
) -> bytes:
    record = bytearray(CREATE_FLEET_TEMPLATE_RECORD)
    _set_identity(
        record,
        command_serial=command_serial,
        actor=actor,
        origin=origin,
    )
    _set_u32(record, CONTEXT_TAG, target.context_822c, "context_822c")
    result = bytes(record)
    if parse_template_creation(result) != target:
        raise RuntimeError("The fleet-template creation target changed during construction.")
    return result


def command_identity(record: bytes) -> tuple[int, int, int]:
    actor, origin = _actor_origin(record)
    return actor, origin, _serial_u32(record)
