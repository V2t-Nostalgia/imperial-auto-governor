#!/usr/bin/env python3
"""Session-lifetime Stellaris command proxy for an authorized co-op client.

Start this WinDivert proxy before the LLM client joins the multiplayer room.
It initially forwards every packet unchanged, discovers the new bidirectional
Stellaris reliable flow, and records current-epoch command observations. A
separate JSON arm file may request serialized actions without restarting the
proxy. Every accepted action reserves one application command serial and one
reliable-stream insertion mapping for the remainder of the connection.

After inserting a record, the proxy keeps client and host reliable-stream
coordinates virtualized for the remainder of the connection. This remains an
opt-in experimental executor: start it before room join and keep it active
until the co-op client has left the room.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iag.stellaris.execution.packet.autonomous_commands import (
    ACTOR_TAG,
    ORIGIN_TAG,
    RESEARCH_LAB_RECORD_TEMPLATE,
    SERIAL_WIDTH,
    BuildingTarget,
    _actor_origin,
    _serial_u32,
    _unique_value_offset,
    _validate_u32,
    build_building_record,
    retag_matching_host_response,
)
from iag.stellaris.execution.packet.iag_same_family_construction_rewriter import (
    rewrite_district_carrier,
    rewrite_zone_carrier,
)
from iag.stellaris.execution.packet.iag_stream_command_injector import (
    COMMAND_SERIAL_OFFSET,
    RELIABLE_HEADER_LENGTH,
    UINT24_HALF_RANGE,
    StreamTranslator,
    find_command_records,
    forward_distance_uint24,
    is_reliable_packet,
    read_uint24_be,
)
from iag.stellaris.execution.passive_network_observer import owned_udp_ports

APPLICATION_COMMAND_PREFIX = b"\x00\x00\x00"
COMMAND_RECORD_LENGTH = len(RESEARCH_LAB_RECORD_TEMPLATE)
INSERTED_LENGTH = len(APPLICATION_COMMAND_PREFIX) + COMMAND_RECORD_LENGTH
FLEET_MOVE_FAMILY = bytes.fromhex("d32c")
FLEET_SOURCE_TAG = bytes.fromhex("502c01001400")
FLEET_DESTINATION_TAGS = {
    "0c3a01001400",
    "132a01001400",
}
MAX_STELLARIS_UDP_PORTS = 96
DISCOVERY_FILTER = "ip and udp"
PROCESS_OWNED_ROUTES = frozenset(
    {
        "direct_peer_ip_owned_port",
        "steam_brokered_udp_port",
        "stellaris_process_udp_port",
    }
)

DISTRICT_GENERATOR_RECORD = bytes.fromhex(
    "890004000000b43d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400830400000400410001000300822c010014000000000063400100"
    "14001d010000b03d01000300b42b01000f0012006469737472696374"
    "5f67656e657261746f72132a0100140050000000040004000400"
)
ZONE_ENGINEERING_RECORD = bytes.fromhex(
    "a40004000000b43d01000300f30101000300400201000c0003000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14001a0000000400410001000300822c010014000000000063400100"
    "140000000000a24401000300b32b01000f0019007a6f6e655f726573"
    "65617263685f656e67696e656572696e67132a0100140000000000b4"
    "2b0100140001000000a44401000c0001000000040004000400"
)
FLEET_MOVE_RECORDS = {
    "0c3a01001400": bytes.fromhex(
        "730004000000d32c01000300f30101000300400201000c0001000000"
        "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
        "14006e1600000400410001000300502c010014000300000063400100"
        "0e0000de3501000e00008b3d010003000c3a01001400000000000400"
        "04000400"
    ),
    "132a01001400": bytes.fromhex(
        "730004000000d32c01000300f30101000300400201000c0001000000"
        "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
        "1400dd0200000400410001000300502c010014000300000063400100"
        "0e0000de3501000e00008b3d01000300132a01001400040100000400"
        "04000400"
    ),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def append_jsonl(path: Path, event: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def write_json(
    path: Path,
    value: dict[str, object],
    *,
    required: bool = False,
) -> bool:
    """Publish telemetry without allowing a Windows reader lock to kill the proxy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        for attempt in range(3):
            try:
                temporary.replace(path)
                return True
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.002)
    except OSError:
        temporary.unlink(missing_ok=True)
        if required:
            raise
        return False
    return False


@dataclass(frozen=True)
class FlowKey:
    local_ip: str
    local_port: int
    host_ip: str
    host_port: int
    route: str


@dataclass
class FlowEvidence:
    outbound_packets: int = 0
    inbound_packets: int = 0
    reliable_outbound_packets: int = 0
    reliable_inbound_packets: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    last_outbound_seen: float = 0.0
    last_inbound_seen: float = 0.0


class FlowDiscovery:
    """Lock one fresh reliable flow over a direct or Steam-relay route."""

    def __init__(
        self,
        minimum_each_direction: int = 3,
        *,
        relay_settle_seconds: float = 0.5,
        candidate_window_seconds: float = 5.0,
    ) -> None:
        self.minimum_each_direction = minimum_each_direction
        self.relay_settle_seconds = relay_settle_seconds
        self.candidate_window_seconds = candidate_window_seconds
        self.candidates: dict[FlowKey, FlowEvidence] = defaultdict(FlowEvidence)
        self.locked: FlowKey | None = None
        self.provisional: FlowKey | None = None
        self.provisional_since = 0.0

    def _eligible_candidates(self, observed_at: float) -> list[FlowKey]:
        eligible: list[FlowKey] = []
        for candidate, evidence in self.candidates.items():
            minimum_packets = (
                1
                if candidate.route in PROCESS_OWNED_ROUTES
                else self.minimum_each_direction
            )
            if (
                observed_at - evidence.last_seen
                <= self.candidate_window_seconds
                and observed_at - evidence.last_outbound_seen
                <= self.candidate_window_seconds
                and observed_at - evidence.last_inbound_seen
                <= self.candidate_window_seconds
                and evidence.outbound_packets >= minimum_packets
                and evidence.inbound_packets >= minimum_packets
                and (
                    evidence.reliable_outbound_packets
                    or evidence.reliable_inbound_packets
                )
            ):
                eligible.append(candidate)
        return eligible

    def _consider_lock(self, observed_at: float) -> FlowKey | None:
        eligible = self._eligible_candidates(observed_at)
        direct = [
            candidate
            for candidate in eligible
            if candidate.route.startswith("direct_peer_ip")
        ]
        if len(direct) == 1:
            self.locked = direct[0]
        elif len(eligible) != 1:
            self.provisional = None
            self.provisional_since = 0.0
        elif self.provisional != eligible[0]:
            self.provisional = eligible[0]
            self.provisional_since = observed_at
        elif observed_at - self.provisional_since >= self.relay_settle_seconds:
            self.locked = eligible[0]
        return self.locked

    def observe(
        self,
        *,
        src_ip: str,
        src_port: int,
        dst_ip: str,
        dst_port: int,
        local_ip: str,
        host_ip: str,
        port_owners: dict[int, set[str]],
        game_process_present: bool,
        is_outbound: bool,
        is_inbound: bool,
        payload: bytes,
        observed_at: float,
    ) -> FlowKey | None:
        if self.locked is not None or not game_process_present:
            return self.locked
        if self._consider_lock(observed_at) is not None:
            return self.locked

        direct_outbound = is_outbound and src_ip == local_ip and dst_ip == host_ip
        direct_inbound = is_inbound and src_ip == host_ip and dst_ip == local_ip
        outbound_owners = port_owners.get(src_port, set())
        inbound_owners = port_owners.get(dst_port, set())
        relay_outbound = is_outbound and bool(outbound_owners)
        relay_inbound = is_inbound and bool(inbound_owners)
        if direct_outbound or relay_outbound:
            owner_names = outbound_owners
            key = FlowKey(
                local_ip=src_ip,
                local_port=src_port,
                host_ip=dst_ip,
                host_port=dst_port,
                route=(
                    (
                        "direct_peer_ip_owned_port"
                        if owner_names
                        else "direct_peer_ip"
                    )
                    if direct_outbound
                    else _brokered_route(owner_names)
                ),
            )
            direction = "outbound"
        elif direct_inbound or relay_inbound:
            owner_names = inbound_owners
            key = FlowKey(
                local_ip=dst_ip,
                local_port=dst_port,
                host_ip=src_ip,
                host_port=src_port,
                route=(
                    (
                        "direct_peer_ip_owned_port"
                        if owner_names
                        else "direct_peer_ip"
                    )
                    if direct_inbound
                    else _brokered_route(owner_names)
                ),
            )
            direction = "inbound"
        else:
            return self._consider_lock(observed_at)

        try:
            peer = ipaddress.ip_address(key.host_ip)
        except ValueError:
            return None
        if peer.version != 4 or peer.is_unspecified or peer.is_multicast:
            return None

        for stale_key, stale_evidence in list(self.candidates.items()):
            if (
                stale_key != key
                and observed_at - stale_evidence.last_seen
                > self.candidate_window_seconds
            ):
                del self.candidates[stale_key]

        evidence = self.candidates[key]
        if evidence.first_seen == 0.0:
            evidence.first_seen = observed_at
        evidence.last_seen = observed_at
        if direction == "outbound":
            if (
                evidence.last_outbound_seen
                and observed_at - evidence.last_outbound_seen
                > self.candidate_window_seconds
            ):
                evidence.outbound_packets = 0
                evidence.reliable_outbound_packets = 0
            evidence.outbound_packets += 1
            evidence.last_outbound_seen = observed_at
            if is_reliable_packet(payload):
                evidence.reliable_outbound_packets += 1
        else:
            if (
                evidence.last_inbound_seen
                and observed_at - evidence.last_inbound_seen
                > self.candidate_window_seconds
            ):
                evidence.inbound_packets = 0
                evidence.reliable_inbound_packets = 0
            evidence.inbound_packets += 1
            evidence.last_inbound_seen = observed_at
            if is_reliable_packet(payload):
                evidence.reliable_inbound_packets += 1
        return self._consider_lock(observed_at)

    def candidate_payload(self) -> list[dict[str, object]]:
        """Expose bounded route evidence without logging packet contents."""
        return [
            {
                **asdict(key),
                "outbound_packets": evidence.outbound_packets,
                "inbound_packets": evidence.inbound_packets,
                "reliable_outbound_packets": (
                    evidence.reliable_outbound_packets
                ),
                "reliable_inbound_packets": evidence.reliable_inbound_packets,
            }
            for key, evidence in sorted(
                self.candidates.items(),
                key=lambda item: (
                    item[0].host_ip,
                    item[0].host_port,
                    item[0].local_port,
                ),
            )
        ][:16]


def _brokered_route(owner_names: set[str]) -> str:
    normalized = {Path(value).stem.casefold() for value in owner_names}
    if "steam" in normalized:
        return "steam_brokered_udp_port"
    return "stellaris_process_udp_port"


def session_udp_port_owners(
    process_names: list[str],
    transport_process_names: list[str],
) -> tuple[dict[int, set[str]], bool]:
    """Return current game/transport UDP ports and game-process presence."""
    owners, game_process_present = owned_udp_ports(
        process_names,
        transport_process_names,
    )
    owners = {
        int(port): set(names)
        for port, names in owners.items()
        if 1 <= int(port) <= 65535
    }
    if len(owners) > MAX_STELLARIS_UDP_PORTS:
        raise RuntimeError(
            "Stellaris/transport UDP port count is unexpectedly broad: "
            f"{len(owners)} > {MAX_STELLARIS_UDP_PORTS}."
        )
    return owners, game_process_present


def packet_filter_for_flow(flow: FlowKey) -> str:
    """Build the modifying filter only after the actual peer tuple is known."""
    return (
        "udp and ("
        f"(outbound and ip.SrcAddr == {flow.local_ip} and "
        f"ip.DstAddr == {flow.host_ip} and udp.SrcPort == {flow.local_port} "
        f"and udp.DstPort == {flow.host_port}) or "
        f"(inbound and ip.SrcAddr == {flow.host_ip} and "
        f"ip.DstAddr == {flow.local_ip} and udp.SrcPort == {flow.host_port} "
        f"and udp.DstPort == {flow.local_port})"
        ")"
    )


@dataclass(frozen=True)
class ArmRequest:
    request_id: str
    session_id: str
    action: str
    source_actor: int
    host_actor: int
    request_origin: int
    target: (
        BuildingTarget
        | DistrictConstructionTarget
        | ZoneSpecializationTarget
        | FleetMoveTarget
    )
    template_record: bytes | None = None
    previous_serial_floor: int = 0


@dataclass(frozen=True)
class ZoneSpecializationTarget:
    context_822c: int
    build_queue_id: int
    colony_id: int
    district_id: int
    slot_selector: int
    zone_type: str


@dataclass(frozen=True)
class DistrictConstructionTarget:
    context_822c: int
    build_queue_id: int
    colony_id: int
    district_type: str


@dataclass(frozen=True)
class FleetMoveTarget:
    source_fleet_object: int
    destination_tag_hex: str
    destination_object: int


def parse_arm_request(path: Path, expected_session_id: str) -> ArmRequest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("The arm file root must be an object.")
    if raw.get("session_id") != expected_session_id:
        raise ValueError("The arm file session_id does not match this proxy.")
    request_id = str(raw.get("request_id", "")).strip()
    if not request_id or len(request_id) > 96:
        raise ValueError(
            "The arm file requires a request_id of at most 96 characters."
        )
    action = str(raw.get("action", ""))
    if action == "specialize_zone":
        action = "build_zone"
    if action not in (
        "build_building",
        "build_district",
        "build_zone",
        "move_fleet",
    ):
        raise ValueError("Unsupported session-proxy action.")
    target_raw = raw.get("target")
    if not isinstance(target_raw, dict):
        raise ValueError("The arm file requires a target object.")
    template_record: bytes | None = None
    if action == "build_building":
        target: (
            BuildingTarget
            | DistrictConstructionTarget
            | ZoneSpecializationTarget
            | FleetMoveTarget
        ) = BuildingTarget(
            context_822c=int(target_raw.get("context_822c", 0)),
            build_queue_id=int(target_raw["build_queue_id"]),
            colony_id=int(target_raw["colony_id"]),
            zone_id=int(target_raw["zone_id"]),
            building_id=str(
                target_raw.get("building_id", "building_research_lab_1")
            ),
        )
    elif action == "build_district":
        target = DistrictConstructionTarget(
            context_822c=int(target_raw.get("context_822c", 0)),
            build_queue_id=int(target_raw["build_queue_id"]),
            colony_id=int(target_raw["colony_id"]),
            district_type=str(target_raw["district_type"]),
        )
    elif action == "build_zone":
        target = ZoneSpecializationTarget(
            context_822c=int(target_raw.get("context_822c", 0)),
            build_queue_id=int(target_raw["build_queue_id"]),
            colony_id=int(target_raw["colony_id"]),
            district_id=int(target_raw["district_id"]),
            slot_selector=int(target_raw["slot_selector"]),
            zone_type=str(target_raw["zone_type"]),
        )
    else:
        destination_tag_hex = str(target_raw["destination_tag_hex"]).lower()
        if destination_tag_hex not in FLEET_DESTINATION_TAGS:
            raise ValueError("move_fleet has an unsupported destination tag.")
        target = FleetMoveTarget(
            source_fleet_object=_validate_u32(
                int(target_raw["source_fleet_object"]),
                "source_fleet_object",
            ),
            destination_tag_hex=destination_tag_hex,
            destination_object=_validate_u32(
                int(target_raw["destination_object"]),
                "destination_object",
            ),
        )
    if raw.get("template_record_hex"):
        template_hex = str(raw.get("template_record_hex", ""))
        try:
            template_record = bytes.fromhex(template_hex)
        except ValueError as error:
            raise ValueError("template_record_hex must be valid hexadecimal.") from error
    source_actor = int(raw.get("source_actor") or 0)
    host_actor = int(raw.get("host_actor", 1))
    request_origin = int(raw.get("request_origin", 0))
    previous_serial_floor = int(raw.get("previous_serial_floor", 0))
    if request_origin not in (0, 1):
        raise ValueError("request_origin must be 0 or 1.")
    if previous_serial_floor < 0 or previous_serial_floor > 0xFFFFFFFF:
        raise ValueError("previous_serial_floor must be an unsigned 32-bit integer.")
    return ArmRequest(
        request_id=request_id,
        session_id=expected_session_id,
        action=action,
        source_actor=source_actor,
        host_actor=host_actor,
        request_origin=request_origin,
        target=target,
        template_record=template_record,
        previous_serial_floor=previous_serial_floor,
    )


def next_actor_serial(
    last_serials: dict[tuple[str, int, int], int],
    actor: int,
    previous_serial_floor: int = 0,
    reserved_serials: int = 0,
    previous_synthetic_serial: int = 0,
) -> int:
    """Return the next serial shared by every outbound origin for one actor."""
    previous = max(
        (
            serial
            for (direction, observed_actor, _origin), serial in last_serials.items()
            if direction == "outbound" and observed_actor == actor
        ),
        default=previous_serial_floor,
    )
    previous = max(
        previous + reserved_serials,
        previous_serial_floor,
        previous_synthetic_serial,
    )
    if previous >= 0xFFFFFFFF:
        raise ValueError("The actor command serial reached its 32-bit limit.")
    return previous + 1


def candidate_source_actors(
    last_serials: dict[tuple[str, int, int], int],
    host_actor: int,
) -> list[int]:
    """Return observed non-host actors with outbound local commands."""
    return sorted(
        {
            actor
            for direction, actor, _origin in last_serials
            if direction == "outbound" and actor not in {0, host_actor}
        }
    )


def observe_command_serials(
    payload: bytes,
    last_serials: dict[tuple[str, int, int], int],
    direction: str,
) -> list[dict[str, int]]:
    if direction not in ("outbound", "inbound"):
        raise ValueError("direction must be outbound or inbound.")
    if not is_reliable_packet(payload):
        return []
    application = payload[RELIABLE_HEADER_LENGTH:]
    observations: list[dict[str, int]] = []
    for command in find_command_records(application):
        record = application[command.offset : command.offset + command.length]
        try:
            actor, origin = _actor_origin(record)
            serial = _serial_u32(record)
        except ValueError:
            continue
        if serial == 0:
            continue
        last_serials[(direction, actor, origin)] = serial
        observations.append(
            {
                "actor": actor,
                "origin": origin,
                "serial_u32": serial,
                "record_length": command.length,
            }
        )
    return observations


def translate_outbound_command_serials(
    payload: bytes,
    *,
    actor: int,
    delta: int = 1,
) -> tuple[bytes, list[dict[str, int]]]:
    """Reserve inserted serials by shifting complete later actor commands."""
    if not is_reliable_packet(payload) or delta == 0:
        return payload, []

    application = payload[RELIABLE_HEADER_LENGTH:]
    output = bytearray(payload)
    changes: list[dict[str, int]] = []
    for command in find_command_records(application):
        record = application[command.offset : command.offset + command.length]
        try:
            observed_actor, origin = _actor_origin(record)
            serial = _serial_u32(record)
        except ValueError:
            continue
        if observed_actor != actor or serial == 0:
            continue
        translated = serial + delta
        if translated > 0xFFFFFFFF:
            raise ValueError("The translated command serial exceeds 32 bits.")
        serial_offset = (
            RELIABLE_HEADER_LENGTH + command.offset + COMMAND_SERIAL_OFFSET
        )
        output[serial_offset : serial_offset + SERIAL_WIDTH] = translated.to_bytes(
            SERIAL_WIDTH,
            "little",
        )
        changes.append(
            {
                "actor": observed_actor,
                "origin": origin,
                "serial_u32": serial,
                "translated_serial_u32": translated,
                "record_length": command.length,
            }
        )
    return bytes(output), changes


@dataclass(frozen=True)
class Injection:
    payload: bytes
    injection_offset: int
    serial: int
    command_record_length: int
    inserted_length: int


def _parse_district_target(
    record: bytes,
    district_type: str,
) -> DistrictConstructionTarget | None:
    parsed = rewrite_district_carrier(
        record,
        source_district_type=district_type,
        target_district_type=district_type,
        build_queue_id=0,
        colony_id=0,
        preserve_length=False,
    )
    if parsed is None:
        return None
    _, metadata = parsed
    context_tag = bytes.fromhex("822c01001400")
    context_offset = _unique_value_offset(record, context_tag, 4, "context_822c")
    return DistrictConstructionTarget(
        context_822c=int.from_bytes(
            record[context_offset : context_offset + 4],
            "little",
        ),
        build_queue_id=int(metadata["source_build_queue_id"]),
        colony_id=int(metadata["source_colony_id"]),
        district_type=district_type,
    )


def build_district_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: DistrictConstructionTarget,
) -> bytes:
    rewritten = rewrite_district_carrier(
        DISTRICT_GENERATOR_RECORD,
        source_district_type="district_generator",
        target_district_type=target.district_type,
        build_queue_id=target.build_queue_id,
        colony_id=target.colony_id,
        preserve_length=False,
    )
    if rewritten is None:
        raise ValueError("The district fixture does not match its verified schema.")
    record = bytearray(rewritten[0])
    actor_offset = _unique_value_offset(bytes(record), ACTOR_TAG, 4, "actor")
    origin_offset = _unique_value_offset(bytes(record), ORIGIN_TAG, 1, "origin")
    context_tag = bytes.fromhex("822c01001400")
    context_offset = _unique_value_offset(
        bytes(record),
        context_tag,
        4,
        "context_822c",
    )
    record[actor_offset : actor_offset + 4] = _validate_u32(
        actor,
        "actor",
    ).to_bytes(4, "little")
    record[origin_offset] = origin
    record[context_offset : context_offset + 4] = _validate_u32(
        target.context_822c,
        "context_822c",
    ).to_bytes(4, "little")
    record[
        COMMAND_SERIAL_OFFSET : COMMAND_SERIAL_OFFSET + SERIAL_WIDTH
    ] = _validate_u32(command_serial, "command_serial").to_bytes(4, "little")
    result = bytes(record)
    if _parse_district_target(result, target.district_type) != target:
        raise RuntimeError("The district request target changed during construction.")
    return result


def _parse_zone_target(
    record: bytes,
    zone_type: str,
) -> ZoneSpecializationTarget | None:
    parsed = rewrite_zone_carrier(
        record,
        source_zone_type=zone_type,
        target_zone_type=zone_type,
        build_queue_id=0,
        colony_id=0,
        district_id=0,
        slot_selector=0,
        preserve_length=False,
    )
    if parsed is None:
        return None
    _, metadata = parsed
    context_tag = bytes.fromhex("822c01001400")
    context_offset = _unique_value_offset(record, context_tag, 4, "context_822c")
    return ZoneSpecializationTarget(
        context_822c=int.from_bytes(record[context_offset : context_offset + 4], "little"),
        build_queue_id=int(metadata["source_build_queue_id"]),
        colony_id=int(metadata["source_colony_id"]),
        district_id=int(metadata["source_district_id"]),
        slot_selector=int(metadata["source_slot_selector"]),
        zone_type=zone_type,
    )


def build_zone_specialization_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: ZoneSpecializationTarget,
    template_record: bytes = ZONE_ENGINEERING_RECORD,
) -> bytes:
    """Build one request from the verified 4.4.6 zone record layout."""
    source_zone_type = (
        "zone_research_engineering"
        if template_record == ZONE_ENGINEERING_RECORD
        else target.zone_type
    )
    rewritten = rewrite_zone_carrier(
        template_record,
        source_zone_type=source_zone_type,
        target_zone_type=target.zone_type,
        build_queue_id=target.build_queue_id,
        colony_id=target.colony_id,
        district_id=target.district_id,
        slot_selector=target.slot_selector,
        preserve_length=False,
    )
    if rewritten is None:
        raise ValueError("The zone template does not match the requested specialization.")
    record = bytearray(rewritten[0])
    actor_offset = _unique_value_offset(bytes(record), ACTOR_TAG, 4, "actor")
    origin_offset = _unique_value_offset(bytes(record), ORIGIN_TAG, 1, "origin")
    context_tag = bytes.fromhex("822c01001400")
    context_offset = _unique_value_offset(
        bytes(record), context_tag, 4, "context_822c"
    )
    record[actor_offset : actor_offset + 4] = _validate_u32(actor, "actor").to_bytes(
        4, "little"
    )
    record[origin_offset] = origin
    record[context_offset : context_offset + 4] = _validate_u32(
        target.context_822c,
        "context_822c",
    ).to_bytes(4, "little")
    record[
        COMMAND_SERIAL_OFFSET : COMMAND_SERIAL_OFFSET + SERIAL_WIDTH
    ] = _validate_u32(command_serial, "command_serial").to_bytes(4, "little")
    result = bytes(record)
    if int.from_bytes(result[:2], "little") + 1 != len(result):
        raise RuntimeError("The zone template declared length is invalid.")
    if _parse_zone_target(result, target.zone_type) != target:
        raise RuntimeError("The zone request target changed during construction.")
    return result


def _parse_fleet_move_target(record: bytes) -> FleetMoveTarget | None:
    """Read the verified source and destination fields from a d32c move record."""
    if len(record) < 60 or record[6:8] != FLEET_MOVE_FAMILY:
        return None
    source_offset = _unique_value_offset(
        record,
        FLEET_SOURCE_TAG,
        4,
        "source_fleet_object",
    )
    destinations: list[tuple[str, int]] = []
    for tag_hex in FLEET_DESTINATION_TAGS:
        tag = bytes.fromhex(tag_hex)
        count = record.count(tag)
        if count > 1:
            raise ValueError(f"Fleet destination tag {tag_hex} is ambiguous.")
        if count == 1:
            value_offset = record.index(tag) + len(tag)
            if value_offset + 4 > len(record):
                raise ValueError("Fleet destination value is truncated.")
            destinations.append(
                (
                    tag_hex,
                    int.from_bytes(record[value_offset : value_offset + 4], "little"),
                )
            )
    if len(destinations) != 1:
        raise ValueError(
            f"Expected one verified fleet destination field, found {len(destinations)}."
        )
    destination_tag_hex, destination_object = destinations[0]
    return FleetMoveTarget(
        source_fleet_object=int.from_bytes(
            record[source_offset : source_offset + 4],
            "little",
        ),
        destination_tag_hex=destination_tag_hex,
        destination_object=destination_object,
    )


def build_fleet_move_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: FleetMoveTarget,
    template_record: bytes | None = None,
) -> bytes:
    """Build one move request from a verified destination-form fixture."""
    template_record = template_record or FLEET_MOVE_RECORDS[
        target.destination_tag_hex
    ]
    template_target = _parse_fleet_move_target(template_record)
    if template_target is None:
        raise ValueError("The fleet template is not a d32c move record.")
    if template_target.destination_tag_hex != target.destination_tag_hex:
        raise ValueError(
            "The requested destination form does not match the fleet template."
        )

    record = bytearray(template_record)
    actor_offset = _unique_value_offset(bytes(record), ACTOR_TAG, 4, "actor")
    origin_offset = _unique_value_offset(bytes(record), ORIGIN_TAG, 1, "origin")
    source_offset = _unique_value_offset(
        bytes(record),
        FLEET_SOURCE_TAG,
        4,
        "source_fleet_object",
    )
    destination_tag = bytes.fromhex(target.destination_tag_hex)
    destination_offset = _unique_value_offset(
        bytes(record),
        destination_tag,
        4,
        "destination_object",
    )
    record[actor_offset : actor_offset + 4] = _validate_u32(actor, "actor").to_bytes(
        4, "little"
    )
    record[origin_offset] = origin
    record[
        COMMAND_SERIAL_OFFSET : COMMAND_SERIAL_OFFSET + SERIAL_WIDTH
    ] = _validate_u32(command_serial, "command_serial").to_bytes(4, "little")
    record[source_offset : source_offset + 4] = _validate_u32(
        target.source_fleet_object,
        "source_fleet_object",
    ).to_bytes(4, "little")
    record[destination_offset : destination_offset + 4] = _validate_u32(
        target.destination_object,
        "destination_object",
    ).to_bytes(4, "little")

    result = bytes(record)
    if int.from_bytes(result[:2], "little") + 1 != len(result):
        raise RuntimeError("The fleet template declared length is invalid.")
    if _parse_fleet_move_target(result) != target:
        raise RuntimeError("The fleet move target changed during construction.")
    return result


def _build_request_record(request: ArmRequest, command_serial: int) -> bytes:
    if request.action == "build_building":
        if not isinstance(request.target, BuildingTarget):
            raise RuntimeError("The building action has the wrong target type.")
        return build_building_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "build_district":
        if not isinstance(request.target, DistrictConstructionTarget):
            raise RuntimeError("The district action has the wrong target type.")
        return build_district_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "build_zone":
        if not isinstance(request.target, ZoneSpecializationTarget):
            raise RuntimeError("The zone action has the wrong target type.")
        return build_zone_specialization_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
            template_record=request.template_record or ZONE_ENGINEERING_RECORD,
        )
    if request.action == "move_fleet":
        if not isinstance(request.target, FleetMoveTarget):
            raise RuntimeError("The fleet action has the wrong target type.")
        return build_fleet_move_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
            template_record=request.template_record,
        )
    raise RuntimeError(f"Unsupported action: {request.action}")


def retag_matching_response(
    payload: bytes,
    *,
    request: ArmRequest,
) -> tuple[bytes, dict[str, int | str]] | None:
    if isinstance(request.target, BuildingTarget):
        return retag_matching_host_response(
            payload,
            source_actor=request.source_actor,
            target_actor=request.host_actor,
            target=request.target,
        )
    if not is_reliable_packet(payload):
        return None
    application = payload[RELIABLE_HEADER_LENGTH:]
    matches: list[tuple[Any, bytes, int, int]] = []
    for command in find_command_records(application):
        record = application[command.offset : command.offset + command.length]
        try:
            if isinstance(request.target, DistrictConstructionTarget):
                parsed_target = _parse_district_target(
                    record,
                    request.target.district_type,
                )
            elif isinstance(request.target, ZoneSpecializationTarget):
                parsed_target = _parse_zone_target(record, request.target.zone_type)
            elif isinstance(request.target, FleetMoveTarget):
                parsed_target = _parse_fleet_move_target(record)
            else:
                raise TypeError("Unsupported correlated response target.")
            actor, origin = _actor_origin(record)
        except ValueError:
            continue
        if parsed_target != request.target or actor != request.source_actor or origin != 0:
            continue
        actor_offset = _unique_value_offset(record, ACTOR_TAG, 4, "actor")
        matches.append((command, record, actor_offset, _serial_u32(record)))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(
            f"Expected one correlated authoritative response, found {len(matches)}."
        )
    command, record, actor_offset, serial = matches[0]
    rewritten_record = bytearray(record)
    rewritten_record[actor_offset : actor_offset + 4] = _validate_u32(
        request.host_actor, "host_actor"
    ).to_bytes(4, "little")
    rewritten_application = (
        application[: command.offset]
        + bytes(rewritten_record)
        + application[command.offset + command.length :]
    )
    rewritten = payload[:RELIABLE_HEADER_LENGTH] + rewritten_application
    if len(rewritten) != len(payload):
        raise RuntimeError("Authoritative response retagging changed the payload length.")
    metadata: dict[str, int | str] = {
        "action": request.action,
        "host_command_serial_u32": serial,
        "source_actor": request.source_actor,
        "target_actor": request.host_actor,
        "source_origin": 0,
        "target_origin": 0,
        "carrier_offset": int(command.offset),
        "carrier_length": int(command.length),
    }
    if isinstance(request.target, DistrictConstructionTarget):
        metadata["district_type"] = request.target.district_type
    elif isinstance(request.target, ZoneSpecializationTarget):
        metadata["zone_type"] = request.target.zone_type
    else:
        metadata["source_fleet_object"] = request.target.source_fleet_object
        metadata["destination_tag_hex"] = request.target.destination_tag_hex
        metadata["destination_object"] = request.target.destination_object
    return rewritten, metadata


def inject_at_packet_boundary(
    payload: bytes,
    *,
    request: ArmRequest,
    command_serial: int,
    max_payload_length: int,
) -> Injection | None:
    # Verified client-origin requests captured in 4.4.6 use one pure reliable
    # ACK as the carrier, followed by a three-byte application prefix and one
    # length-prefixed command record. Refuse other packet shapes so the proxy
    # cannot silently create a different application envelope.
    if not is_reliable_packet(payload) or len(payload) != RELIABLE_HEADER_LENGTH:
        return None
    command = _build_request_record(request, command_serial)
    inserted_length = len(APPLICATION_COMMAND_PREFIX) + len(command)
    if (
        len(payload) + inserted_length > max_payload_length
    ):
        return None
    application = APPLICATION_COMMAND_PREFIX + command
    return Injection(
        payload=payload + application,
        injection_offset=read_uint24_be(payload, 6),
        serial=command_serial,
        command_record_length=len(command),
        inserted_length=inserted_length,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--host-ip", required=True)
    parser.add_argument("--process-name", action="append")
    parser.add_argument("--transport-process-name", action="append")
    parser.add_argument("--minimum-flow-packets", type=int, default=3)
    parser.add_argument("--max-payload-length", type=int, default=1400)
    parser.add_argument("--response-timeout-seconds", type=int, default=30)
    parser.add_argument("--session-seconds", type=int, default=14400)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--arm-file", type=Path, required=True)
    parser.add_argument("--acknowledge-disposable-session", action="store_true")
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    if not args.acknowledge_disposable_session:
        raise RuntimeError("The session proxy requires disposable-session acknowledgement.")
    if args.ready_file.exists():
        raise RuntimeError(f"A session proxy is already registered: {args.ready_file}")
    if args.minimum_flow_packets < 2:
        raise ValueError("minimum-flow-packets must be at least 2.")

    process_names = [
        str(value).strip()
        for value in (args.process_name or ["stellaris"])
        if str(value).strip()
    ]
    transport_process_names = [
        str(value).strip()
        for value in (args.transport_process_name or ["steam"])
        if str(value).strip()
    ]
    session_id = uuid.uuid4().hex
    discovery = FlowDiscovery(args.minimum_flow_packets)
    port_owners, game_process_present = session_udp_port_owners(
        process_names,
        transport_process_names,
    )
    last_port_refresh = time.monotonic()
    active_filter = DISCOVERY_FILTER
    last_serials: dict[tuple[str, int, int], int] = {}
    arm_request: ArmRequest | None = None
    arm_error_signature: tuple[int, int] | None = None
    stream_translators: list[StreamTranslator] = []
    carrier_rewrites: dict[bytes, bytes] = {}
    proxy_actor: int | None = None
    synthetic_serial_count = 0
    last_synthetic_serial = 0
    completed_requests: list[dict[str, object]] = []
    last_request: dict[str, object] | None = None
    injected_at: float | None = None
    injected_serial: int | None = None
    active_inserted_length: int | None = None
    active_command_record_length: int | None = None
    response_retagged = False
    response_timeout_logged = False
    host_acknowledged_inserted_bytes = False
    translated_sender_packets = 0
    translated_ack_packets = 0
    translated_command_serials = 0
    carrier_retransmissions = 0
    started_at = time.monotonic()

    def state_name() -> str:
        if arm_request is not None:
            return "ARMED"
        if stream_translators:
            return "TRANSLATING"
        if discovery.locked is not None:
            return "FLOW_LOCKED"
        return "READY_WAITING_FOR_FLOW"

    def status() -> dict[str, object]:
        return {
            "session_id": session_id,
            "state": state_name(),
            "pid": os.getpid(),
            "local_ip": args.local_ip,
            "host_ip": args.host_ip,
            "configured_host_ip": args.host_ip,
            "route_scope": "direct_peer_ip_or_owned_game_transport_udp_port",
            "active_filter": active_filter,
            "game_process_present": game_process_present,
            "stellaris_udp_ports": sorted(port_owners),
            "udp_port_owners": {
                str(port): sorted(names)
                for port, names in sorted(port_owners.items())
            },
            "flow": asdict(discovery.locked) if discovery.locked else None,
            "flow_candidates": discovery.candidate_payload(),
            "last_serials": {
                f"{direction}_actor_{actor}_origin_{origin}": serial
                for (direction, actor, origin), serial in sorted(last_serials.items())
            },
            "candidate_source_actors": candidate_source_actors(
                last_serials,
                1,
            ),
            "source_actor": proxy_actor,
            "arm_file": str(args.arm_file),
            "armed": arm_request is not None,
            "armed_action": arm_request.action if arm_request else None,
            "injected_serial_u32": injected_serial,
            "active_inserted_length": active_inserted_length,
            "active_command_record_length": active_command_record_length,
            "host_acknowledged_inserted_bytes": host_acknowledged_inserted_bytes,
            "response_retagged": response_retagged,
            "insertion_count": len(stream_translators),
            "synthetic_serial_count": synthetic_serial_count,
            "last_request": last_request,
            "completed_request_count": len(completed_requests),
            "translated_sender_packets": translated_sender_packets,
            "translated_ack_packets": translated_ack_packets,
            "translated_command_serials": translated_command_serials,
            "updated_at": now_iso(),
        }

    def finish_request(
        outcome: str,
        *,
        error: str | None = None,
        response: dict[str, object] | None = None,
    ) -> None:
        nonlocal arm_request
        nonlocal arm_error_signature
        nonlocal injected_at
        nonlocal response_retagged
        nonlocal response_timeout_logged
        nonlocal host_acknowledged_inserted_bytes
        nonlocal last_request
        if arm_request is None:
            return
        record: dict[str, object] = {
            "request_id": arm_request.request_id,
            "action": arm_request.action,
            "outcome": outcome,
            "source_actor": arm_request.source_actor,
            "serial_u32": injected_serial,
            "host_acknowledged_inserted_bytes": (
                host_acknowledged_inserted_bytes
            ),
            "response_retagged": response_retagged,
            "target": asdict(arm_request.target),
            "finished_at": now_iso(),
        }
        if error:
            record["error"] = error
        if response:
            record["response"] = response
        completed_requests.append(record)
        del completed_requests[:-50]
        last_request = record
        arm_request = None
        arm_error_signature = None
        injected_at = None
        response_retagged = False
        response_timeout_logged = False
        host_acknowledged_inserted_bytes = False
        args.arm_file.unlink(missing_ok=True)
        write_json(args.status_file, status())

    import pydivert  # type: ignore[import-not-found]

    started = {
        "event": "session_proxy_started",
        "timestamp": now_iso(),
        "session_id": session_id,
        "pid": os.getpid(),
        "filter": DISCOVERY_FILTER,
        "route_scope": "direct_peer_ip_or_owned_game_transport_udp_port",
        "configured_host_ip": args.host_ip,
        "process_names": process_names,
        "transport_process_names": transport_process_names,
        "game_process_present": game_process_present,
        "stellaris_udp_ports": sorted(port_owners),
        "inserted_length": INSERTED_LENGTH,
        "application_prefix_hex": APPLICATION_COMMAND_PREFIX.hex(),
        "command_record_length": COMMAND_RECORD_LENGTH,
        "supported_actions": [
            "build_building",
            "build_district",
            "build_zone",
            "move_fleet",
        ],
        "command_serial_translation": True,
        "multi_injection": True,
        "request_origin_default": 0,
    }
    append_jsonl(args.log, started)

    try:
        with pydivert.WinDivert(
            DISCOVERY_FILTER,
            flags=pydivert.Flag.SNIFF,
        ) as discovery_divert:
            args.ready_file.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.ready_file, started, required=True)
            write_json(args.status_file, status())
            print("IAG session proxy is READY before room join.", flush=True)
            print(f"Session ID: {session_id}", flush=True)
            print("State: READY_WAITING_FOR_FLOW", flush=True)

            for packet in discovery_divert:
                original = packet.payload or b""
                now = time.monotonic()
                if now - last_port_refresh >= 1.0:
                    refreshed_owners, refreshed_game_present = (
                        session_udp_port_owners(
                            process_names,
                            transport_process_names,
                        )
                    )
                    last_port_refresh = now
                    if (
                        refreshed_owners != port_owners
                        or refreshed_game_present != game_process_present
                    ):
                        port_owners = refreshed_owners
                        game_process_present = refreshed_game_present
                        append_jsonl(
                            args.log,
                            {
                                "event": "game_transport_udp_ports_refreshed",
                                "timestamp": now_iso(),
                                "game_process_present": game_process_present,
                                "udp_port_owners": {
                                    str(port): sorted(names)
                                    for port, names in sorted(port_owners.items())
                                },
                            },
                        )
                        write_json(args.status_file, status())

                candidate_packets_before = sum(
                    evidence.outbound_packets + evidence.inbound_packets
                    for evidence in discovery.candidates.values()
                )
                locked = discovery.observe(
                    src_ip=str(packet.src_addr),
                    src_port=int(packet.src_port),
                    dst_ip=str(packet.dst_addr),
                    dst_port=int(packet.dst_port),
                    local_ip=args.local_ip,
                    host_ip=args.host_ip,
                    port_owners=port_owners,
                    game_process_present=game_process_present,
                    is_outbound=bool(packet.is_outbound),
                    is_inbound=bool(packet.is_inbound),
                    payload=original,
                    observed_at=now,
                )
                candidate_packets_after = sum(
                    evidence.outbound_packets + evidence.inbound_packets
                    for evidence in discovery.candidates.values()
                )
                if candidate_packets_after != candidate_packets_before:
                    write_json(args.status_file, status())
                if locked is not None:
                    append_jsonl(
                        args.log,
                        {
                            "event": "flow_locked",
                            "timestamp": now_iso(),
                            **asdict(locked),
                            "configured_host_ip": args.host_ip,
                        },
                    )
                    write_json(args.status_file, status())
                    break
                if now - started_at >= args.session_seconds:
                    return 0

        locked = discovery.locked
        if locked is None:
            return 0
        active_filter = packet_filter_for_flow(locked)
        append_jsonl(
            args.log,
            {
                "event": "modifying_filter_activated",
                "timestamp": now_iso(),
                "filter": active_filter,
                "flow": asdict(locked),
            },
        )
        write_json(args.status_file, status())

        with pydivert.WinDivert(active_filter) as divert:
            print(
                "State: FLOW_LOCKED "
                f"({locked.route} {locked.local_ip}:{locked.local_port} -> "
                f"{locked.host_ip}:{locked.host_port})",
                flush=True,
            )
            for packet in divert:
                original = packet.payload or b""
                outgoing = original
                now = time.monotonic()
                src_ip = str(packet.src_addr)
                dst_ip = str(packet.dst_addr)
                is_outbound_flow = (
                    bool(packet.is_outbound)
                    and src_ip == locked.local_ip
                    and dst_ip == locked.host_ip
                    and int(packet.src_port) == locked.local_port
                    and int(packet.dst_port) == locked.host_port
                )
                is_inbound_flow = (
                    bool(packet.is_inbound)
                    and src_ip == locked.host_ip
                    and dst_ip == locked.local_ip
                    and int(packet.src_port) == locked.host_port
                    and int(packet.dst_port) == locked.local_port
                )
                if not is_outbound_flow and not is_inbound_flow:
                    divert.send(packet, recalculate_checksum=True)
                    continue

                direction = "outbound" if is_outbound_flow else "inbound"
                observations = observe_command_serials(
                    original,
                    last_serials,
                    direction,
                )
                if observations:
                    append_jsonl(
                        args.log,
                        {
                            "event": "command_serials_observed",
                            "timestamp": now_iso(),
                            "direction": direction,
                            "observations": observations,
                        },
                    )
                    write_json(args.status_file, status())

                if arm_request is None and args.arm_file.exists():
                    signature = (
                        args.arm_file.stat().st_mtime_ns,
                        args.arm_file.stat().st_size,
                    )
                    if signature != arm_error_signature:
                        try:
                            parsed_request = parse_arm_request(
                                args.arm_file,
                                session_id,
                            )
                            requested_actor = parsed_request.source_actor
                            if requested_actor == 0:
                                candidates = candidate_source_actors(
                                    last_serials,
                                    parsed_request.host_actor,
                                )
                                if len(candidates) != 1:
                                    raise ValueError(
                                        "source_actor is automatic, but the proxy has not "
                                        "observed exactly one non-host outbound actor."
                                    )
                                requested_actor = candidates[0]
                            if proxy_actor is not None and requested_actor != proxy_actor:
                                raise ValueError(
                                    "The source actor changed inside one proxied session."
                                )
                            arm_request = replace(
                                parsed_request,
                                source_actor=requested_actor,
                            )
                            proxy_actor = requested_actor
                            append_jsonl(
                                args.log,
                                {
                                    "event": "proxy_armed",
                                    "timestamp": now_iso(),
                                    "request": {
                                        "request_id": arm_request.request_id,
                                        "session_id": arm_request.session_id,
                                        "action": arm_request.action,
                                        "source_actor": arm_request.source_actor,
                                        "host_actor": arm_request.host_actor,
                                        "request_origin": arm_request.request_origin,
                                        "previous_serial_floor": arm_request.previous_serial_floor,
                                        "target": asdict(arm_request.target),
                                    },
                                },
                            )
                            write_json(args.status_file, status())
                        except Exception as error:
                            arm_request = None
                            arm_error_signature = signature
                            append_jsonl(
                                args.log,
                                {
                                    "event": "arm_file_rejected",
                                    "timestamp": now_iso(),
                                    "error": f"{type(error).__name__}: {error}",
                                },
                            )

                try:
                    # A retransmitted carrier belongs to the byte range that was
                    # already inserted at that stream position.  Replaying its
                    # cached rewrite must win over a newly armed request; putting
                    # another command into the old range would overlap reliable
                    # stream data and desynchronize the room.
                    if (
                        stream_translators
                        and is_outbound_flow
                        and original in carrier_rewrites
                    ):
                        packet.payload = carrier_rewrites[original]
                        divert.send(packet, recalculate_checksum=True)
                        carrier_retransmissions += 1
                        continue

                    if arm_request is not None and injected_at is None and is_outbound_flow:
                        serial = next_actor_serial(
                            last_serials,
                            arm_request.source_actor,
                            arm_request.previous_serial_floor,
                            reserved_serials=synthetic_serial_count,
                            previous_synthetic_serial=last_synthetic_serial,
                        )
                        translated_carrier = original
                        for translator in stream_translators:
                            translated_carrier = translator.translate_outbound(
                                translated_carrier
                            )
                        injection = inject_at_packet_boundary(
                            translated_carrier,
                            request=arm_request,
                            command_serial=serial,
                            max_payload_length=args.max_payload_length,
                        )
                        if injection is not None:
                            packet.payload = injection.payload
                            divert.send(packet, recalculate_checksum=True)
                            carrier_rewrites[original] = injection.payload
                            stream_translators.append(StreamTranslator(
                                injection_offset=injection.injection_offset,
                                inserted_length=injection.inserted_length,
                            ))
                            synthetic_serial_count += 1
                            last_synthetic_serial = injection.serial
                            injected_at = now
                            injected_serial = injection.serial
                            active_inserted_length = injection.inserted_length
                            active_command_record_length = injection.command_record_length
                            append_jsonl(
                                args.log,
                                {
                                    "event": "request_injected",
                                    "timestamp": now_iso(),
                                    "injection_offset": injection.injection_offset,
                                    "inserted_length": injection.inserted_length,
                                    "application_prefix_hex": APPLICATION_COMMAND_PREFIX.hex(),
                                    "command_record_length": injection.command_record_length,
                                    "serial_u32": injection.serial,
                                    "request_id": arm_request.request_id,
                                    "action": arm_request.action,
                                    "request_origin": arm_request.request_origin,
                                    "subsequent_command_serial_delta": synthetic_serial_count,
                                    "before_length": len(original),
                                    "after_length": len(injection.payload),
                                    "sender_offset": read_uint24_be(original, 6),
                                    "ack_offset": read_uint24_be(original, 10),
                                    "target": asdict(arm_request.target),
                                },
                            )
                            write_json(args.status_file, status())
                            continue

                    elif stream_translators and is_outbound_flow:
                        outgoing = original
                        for translator in stream_translators:
                            outgoing = translator.translate_outbound(outgoing)
                        if outgoing != original:
                            translated_sender_packets += 1
                        outgoing, serial_changes = translate_outbound_command_serials(
                            outgoing,
                            actor=proxy_actor or 0,
                            delta=synthetic_serial_count,
                        )
                        if serial_changes:
                            translated_command_serials += len(serial_changes)
                            append_jsonl(
                                args.log,
                                {
                                    "event": "outbound_command_serials_translated",
                                    "timestamp": now_iso(),
                                    "changes": serial_changes,
                                },
                            )
                        packet.payload = outgoing

                    elif stream_translators and is_inbound_flow:
                        outgoing = original
                        for translator in reversed(stream_translators):
                            outgoing = translator.translate_inbound(outgoing)
                        if outgoing != original:
                            translated_ack_packets += 1
                        remote_ack = read_uint24_be(original, 10) if is_reliable_packet(original) else None
                        if (
                            remote_ack is not None
                            and injected_at is not None
                            and not host_acknowledged_inserted_bytes
                        ):
                            distance = forward_distance_uint24(
                                remote_ack,
                                stream_translators[-1].injection_offset,
                            )
                            if (
                                active_inserted_length is not None
                                and active_inserted_length <= distance < UINT24_HALF_RANGE
                            ):
                                host_acknowledged_inserted_bytes = True
                                append_jsonl(
                                    args.log,
                                    {
                                        "event": "host_acknowledged_inserted_bytes",
                                        "timestamp": now_iso(),
                                        "remote_ack_offset": remote_ack,
                                        "local_ack_offset": read_uint24_be(outgoing, 10),
                                        "request_id": (
                                            arm_request.request_id
                                            if arm_request
                                            else None
                                        ),
                                    },
                                )
                                write_json(args.status_file, status())

                        if arm_request is not None and not response_retagged:
                            retagged = retag_matching_response(
                                outgoing,
                                request=arm_request,
                            )
                            if retagged is not None:
                                outgoing, metadata = retagged
                                response_retagged = True
                                append_jsonl(
                                    args.log,
                                    {
                                        "event": "host_response_retagged",
                                        "timestamp": now_iso(),
                                        **metadata,
                                    },
                                )
                                finish_request(
                                    "confirmed",
                                    response={
                                        str(key): value
                                        for key, value in metadata.items()
                                    },
                                )
                        packet.payload = outgoing

                    divert.send(packet, recalculate_checksum=True)
                except Exception as error:
                    packet.payload = original
                    divert.send(packet, recalculate_checksum=True)
                    append_jsonl(
                        args.log,
                        {
                            "event": "packet_error_original_forwarded",
                            "timestamp": now_iso(),
                            "error": f"{type(error).__name__}: {error}",
                            "direction": "outbound" if is_outbound_flow else "inbound",
                        },
                    )

                if (
                    injected_at is not None
                    and not response_retagged
                    and not response_timeout_logged
                    and now - injected_at >= args.response_timeout_seconds
                ):
                    response_timeout_logged = True
                    append_jsonl(
                        args.log,
                        {
                            "event": "host_response_timeout",
                            "timestamp": now_iso(),
                            "seconds": args.response_timeout_seconds,
                            "translation_continues": True,
                        },
                    )
                    finish_request(
                        "response_timeout",
                        error=(
                            "The host did not emit a correlated authoritative "
                            "response before the timeout."
                        ),
                    )
                if now - started_at >= args.session_seconds:
                    return 0
                if (
                    (translated_sender_packets + translated_ack_packets) % 500 == 0
                    and stream_translators
                ):
                    write_json(args.status_file, status())
    except BaseException as error:
        try:
            append_jsonl(
                args.log,
                {
                    "event": "session_proxy_fatal_error",
                    "timestamp": now_iso(),
                    "error": f"{type(error).__name__}: {error}",
                },
            )
        except OSError:
            pass
        raise
    finally:
        append_jsonl(
            args.log,
            {
                "event": "session_proxy_stopped",
                "timestamp": now_iso(),
                "session_id": session_id,
                "state": state_name(),
                "flow": asdict(discovery.locked) if discovery.locked else None,
                "injected_serial_u32": injected_serial,
                "host_acknowledged_inserted_bytes": host_acknowledged_inserted_bytes,
                "response_retagged": response_retagged,
                "translated_sender_packets": translated_sender_packets,
                "translated_ack_packets": translated_ack_packets,
                "translated_command_serials": translated_command_serials,
                "carrier_retransmissions": carrier_retransmissions,
                "insertion_count": len(stream_translators),
                "synthetic_serial_count": synthetic_serial_count,
                "completed_request_count": len(completed_requests),
            },
        )
        args.ready_file.unlink(missing_ok=True)
        write_json(args.status_file, status())
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
