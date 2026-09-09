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
import hashlib
import ipaddress
import json
import os
import re
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
from iag.stellaris.execution.packet.building_mutation_commands import (
    BuildingReplacementTarget,
    BuildingUpgradeTarget,
    build_building_replacement_record,
    build_building_upgrade_record,
    parse_building_replacement_record,
    parse_building_upgrade_record,
)
from iag.stellaris.execution.packet.expansion_commands import (
    ExistingColonyShipTarget,
    OrderColonyShipTarget,
    StarbaseComponentTarget,
    StarbaseUpgradeTarget,
    build_existing_colony_ship_record,
    build_order_colony_ship_record,
    build_starbase_component_record,
    build_starbase_upgrade_record,
    parse_existing_colony_ship_record,
    parse_order_colony_ship_record,
    parse_starbase_component_record,
    parse_starbase_upgrade_record,
)
from iag.stellaris.execution.packet.fleet_operation_commands import (
    ConstructionShipStarbaseTarget,
    FleetAttackTarget,
    FleetRepairTarget,
    FleetUpgradeTarget,
    ShipAutomationTarget,
    build_construction_ship_starbase_record,
    build_fleet_attack_record,
    build_fleet_repair_record,
    build_fleet_upgrade_record,
    build_ship_automation_record,
    parse_construction_ship_starbase_record,
    parse_fleet_attack_record,
    parse_fleet_repair_record,
    parse_fleet_upgrade_record,
    parse_ship_automation_record,
)
from iag.stellaris.execution.packet.fleet_reinforcement_commands import (
    FleetReinforcementTarget,
    FleetTemplateAddTarget,
    FleetTemplateCreationTarget,
    FleetTemplateRemoveTarget,
    build_selected_fleet_reinforcement_record,
    build_template_creation_record,
    build_template_edit_record,
    parse_selected_fleet_reinforcement,
    parse_template_creation,
    parse_template_edit,
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
from iag.stellaris.execution.packet.ship_commands import (
    ShipBuildTarget,
    ShipDesignTarget,
    application_prefix_for_record,
    build_ship_design_record,
    build_ship_record,
    command_identity,
    extended_record_from_application,
    parse_ship_record,
    retag_actor,
    ship_design_name,
    ship_design_record_matches,
)
from iag.stellaris.execution.passive_network_observer import owned_udp_ports
from iag.stellaris.execution.protocol_compatibility import (
    SUPPORTED_SESSION_PROXY_ACTIONS,
)

APPLICATION_COMMAND_PREFIX = b"\x00\x00\x00"
COMMAND_RECORD_LENGTH = len(RESEARCH_LAB_RECORD_TEMPLATE)
INSERTED_LENGTH = len(APPLICATION_COMMAND_PREFIX) + COMMAND_RECORD_LENGTH
FLEET_MOVE_FAMILY = bytes.fromhex("d32c")
FLEET_SOURCE_TAG = bytes.fromhex("502c01001400")
FLEET_DESTINATION_TAGS = {
    "0c3a01001400",
    "132a01001400",
}
FLEET_COORDINATE_FAMILY = bytes.fromhex("4f2c01000300")
FLEET_COORDINATE_CONTAINER = bytes.fromhex("6b0001000300")
FLEET_X_TAG = bytes.fromhex("200001001404")
FLEET_Y_TAG = bytes.fromhex("210001001404")
FLEET_SYSTEM_TAG = bytes.fromhex("a72c01001400")
FLEET_FLAG_6340_TAG = bytes.fromhex("634001000e00")
FLEET_FLAG_DE35_TAG = bytes.fromhex("de3501000e00")
RESEARCH_START_FAMILY = bytes.fromhex("062d01000300")
RESEARCH_STOP_FAMILY = bytes.fromhex("453301000300")
RESEARCH_CONTEXT_TAG = bytes.fromhex("822c01001400")
RESEARCH_TECHNOLOGY_TAG = bytes.fromhex("072d01000f00")
COMMAND_TRAILER = bytes.fromhex("04000400")
TECHNOLOGY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
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
FLEET_COORDINATE_RECORD = bytes.fromhex(
    "8f00040000004f2c01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14001a0000000400410001000300502c01001400030000006b000100"
    "03002000010014041cc40400000000002100010014043a2746feffffff"
    "ffa72c01001400810100000400634001000e0000de3501000e000004"
    "000400"
)
RESEARCH_START_RECORD = bytes.fromhex(
    "690004000000062d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "1400110000000400410001000300822c0100140000000000072d0100"
    "0f000e00746563685f736869656c64735f3204000400"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _validate_i64(value: int, field: str) -> int:
    if not -(1 << 63) <= value < (1 << 63):
        raise ValueError(f"{field} must be a signed 64-bit integer.")
    return value


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

        for candidate in self.candidates:
            direction = flow_packet_direction(
                candidate,
                src_ip=src_ip,
                src_port=src_port,
                dst_ip=dst_ip,
                dst_port=dst_port,
            )
            if direction is not None:
                key = candidate
                break
        else:
            key = None
            direction = None

        direct_outbound = src_ip == local_ip and dst_ip == host_ip
        direct_inbound = src_ip == host_ip and dst_ip == local_ip
        outbound_owners = port_owners.get(src_port, set())
        inbound_owners = port_owners.get(dst_port, set())
        relay_outbound = bool(outbound_owners) and not inbound_owners
        relay_inbound = bool(inbound_owners) and not outbound_owners
        if key is None:
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
            elif outbound_owners and inbound_owners and is_outbound != is_inbound:
                # Use driver direction only to break the rare tie where both
                # ports belong to local game transport processes.
                owner_names = outbound_owners if is_outbound else inbound_owners
                key = FlowKey(
                    local_ip=src_ip if is_outbound else dst_ip,
                    local_port=src_port if is_outbound else dst_port,
                    host_ip=dst_ip if is_outbound else src_ip,
                    host_port=dst_port if is_outbound else src_port,
                    route=_brokered_route(owner_names),
                )
                direction = "outbound" if is_outbound else "inbound"
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
                "bidirectional": bool(
                    evidence.outbound_packets and evidence.inbound_packets
                ),
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
    """Match both tuple directions even when a TUN reinjects them as outbound."""
    return (
        "udp and ("
        f"(ip.SrcAddr == {flow.local_ip} and "
        f"ip.DstAddr == {flow.host_ip} and udp.SrcPort == {flow.local_port} "
        f"and udp.DstPort == {flow.host_port}) or "
        f"(ip.SrcAddr == {flow.host_ip} and "
        f"ip.DstAddr == {flow.local_ip} and udp.SrcPort == {flow.host_port} "
        f"and udp.DstPort == {flow.local_port})"
        ")"
    )


def flow_packet_direction(
    flow: FlowKey,
    *,
    src_ip: str,
    src_port: int,
    dst_ip: str,
    dst_port: int,
) -> str | None:
    """Classify direction from the verified tuple, not driver metadata."""
    if (
        src_ip == flow.local_ip
        and src_port == flow.local_port
        and dst_ip == flow.host_ip
        and dst_port == flow.host_port
    ):
        return "outbound"
    if (
        src_ip == flow.host_ip
        and src_port == flow.host_port
        and dst_ip == flow.local_ip
        and dst_port == flow.local_port
    ):
        return "inbound"
    return None


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
        | FleetCoordinateMoveTarget
        | ResearchTarget
        | ShipBuildTarget
        | ShipDesignTarget
        | FleetTemplateAddTarget
        | FleetTemplateCreationTarget
        | FleetTemplateRemoveTarget
        | FleetReinforcementTarget
        | FleetAttackTarget
        | ConstructionShipStarbaseTarget
        | ShipAutomationTarget
        | OrderColonyShipTarget
        | ExistingColonyShipTarget
        | StarbaseUpgradeTarget
        | StarbaseComponentTarget
        | BuildingUpgradeTarget
        | BuildingReplacementTarget
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


@dataclass(frozen=True)
class FleetCoordinateMoveTarget:
    source_fleet_object: int
    x_fixed: int
    y_fixed: int
    system_origin: int


@dataclass(frozen=True)
class ResearchTarget:
    context_822c: int
    technology_id: str


def parse_arm_document(raw: Any, expected_session_id: str) -> ArmRequest:
    """Validate one in-memory arm document using the worker's exact contract."""
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
    if action not in SUPPORTED_SESSION_PROXY_ACTIONS:
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
            | FleetCoordinateMoveTarget
            | ResearchTarget
            | ShipBuildTarget
            | ShipDesignTarget
            | FleetTemplateAddTarget
            | FleetTemplateCreationTarget
            | FleetTemplateRemoveTarget
            | FleetReinforcementTarget
            | FleetAttackTarget
            | FleetRepairTarget
            | FleetUpgradeTarget
            | ConstructionShipStarbaseTarget
            | ShipAutomationTarget
            | OrderColonyShipTarget
            | ExistingColonyShipTarget
            | StarbaseUpgradeTarget
            | StarbaseComponentTarget
            | BuildingUpgradeTarget
            | BuildingReplacementTarget
        ) = BuildingTarget(
            context_822c=int(target_raw.get("context_822c", 0)),
            build_queue_id=int(target_raw["build_queue_id"]),
            colony_id=int(target_raw["colony_id"]),
            zone_id=int(target_raw["zone_id"]),
            building_id=str(
                target_raw.get("building_id", "building_research_lab_1")
            ),
        )
    elif action == "upgrade_building":
        target = BuildingUpgradeTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
            build_queue_id=_validate_u32(
                int(target_raw["build_queue_id"]),
                "build_queue_id",
            ),
            colony_id=_validate_u32(int(target_raw["colony_id"]), "colony_id"),
            zone_id=_validate_u32(int(target_raw["zone_id"]), "zone_id"),
            building_object_id=_validate_u32(
                int(target_raw["building_object_id"]),
                "building_object_id",
            ),
            building_id=str(target_raw["building_id"]),
        )
    elif action == "replace_building":
        target = BuildingReplacementTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
            build_queue_id=_validate_u32(
                int(target_raw["build_queue_id"]),
                "build_queue_id",
            ),
            colony_id=_validate_u32(int(target_raw["colony_id"]), "colony_id"),
            zone_id=_validate_u32(int(target_raw["zone_id"]), "zone_id"),
            source_building_object_id=_validate_u32(
                int(target_raw["source_building_object_id"]),
                "source_building_object_id",
            ),
            building_id=str(target_raw["building_id"]),
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
    elif action == "move_fleet":
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
    elif action == "move_fleet_to_coordinate":
        target = FleetCoordinateMoveTarget(
            source_fleet_object=_validate_u32(
                int(target_raw["source_fleet_object"]),
                "source_fleet_object",
            ),
            x_fixed=_validate_i64(int(target_raw["x_fixed"]), "x_fixed"),
            y_fixed=_validate_i64(int(target_raw["y_fixed"]), "y_fixed"),
            system_origin=_validate_u32(
                int(target_raw["system_origin"]),
                "system_origin",
            ),
        )
    elif action == "attack_fleet":
        target = FleetAttackTarget(
            source_fleet_object=_validate_u32(
                int(target_raw["source_fleet_object"]),
                "source_fleet_object",
            ),
            target_fleet_object=_validate_u32(
                int(target_raw["target_fleet_object"]),
                "target_fleet_object",
            ),
        )
    elif action == "repair_fleet":
        target = FleetRepairTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
            source_fleet_object=_validate_u32(
                int(target_raw["source_fleet_object"]),
                "source_fleet_object",
            ),
        )
    elif action == "upgrade_fleet":
        target = FleetUpgradeTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
            source_fleet_object=_validate_u32(
                int(target_raw["source_fleet_object"]),
                "source_fleet_object",
            ),
            shipyard_build_queue_id=_validate_u32(
                int(target_raw["shipyard_build_queue_id"]),
                "shipyard_build_queue_id",
            ),
        )
    elif action == "build_starbase":
        target = ConstructionShipStarbaseTarget(
            source_fleet_object=_validate_u32(
                int(target_raw["source_fleet_object"]),
                "source_fleet_object",
            ),
            target_system_object=_validate_u32(
                int(target_raw["target_system_object"]),
                "target_system_object",
            ),
        )
    elif action == "configure_ship_automation":
        options_raw = target_raw.get("options")
        if not isinstance(options_raw, list):
            raise ValueError("configure_ship_automation requires an options list.")
        target = ShipAutomationTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
            source_fleet_object=_validate_u32(
                int(target_raw["source_fleet_object"]),
                "source_fleet_object",
            ),
            options=tuple(str(option) for option in options_raw),
        )
    elif action == "order_colony_ship_and_colonize":
        target = OrderColonyShipTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
            species_id=_validate_u32(int(target_raw["species_id"]), "species_id"),
            colony_designation=str(target_raw["colony_designation"]),
            design_id=_validate_u32(int(target_raw["design_id"]), "design_id"),
            upgrade_id=_validate_u32(
                int(target_raw.get("upgrade_id", 0xFFFFFFFF)),
                "upgrade_id",
            ),
            growth_stage=_validate_u32(
                int(target_raw.get("growth_stage", 0)),
                "growth_stage",
            ),
            target_planet_id=_validate_u32(
                int(target_raw["target_planet_id"]),
                "target_planet_id",
            ),
            source_shipyard_build_queue_id=_validate_u32(
                int(target_raw["source_shipyard_build_queue_id"]),
                "source_shipyard_build_queue_id",
            ),
            system_name_key=str(target_raw["system_name_key"]),
        )
    elif action == "colonize_with_existing_ship":
        target = ExistingColonyShipTarget(
            source_fleet_object=_validate_u32(
                int(target_raw["source_fleet_object"]),
                "source_fleet_object",
            ),
            target_planet_id=_validate_u32(
                int(target_raw["target_planet_id"]),
                "target_planet_id",
            ),
            system_name_key=str(target_raw["system_name_key"]),
        )
    elif action == "upgrade_starbase":
        target = StarbaseUpgradeTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
            build_queue_id=_validate_u32(
                int(target_raw["build_queue_id"]),
                "build_queue_id",
            ),
            target_level=str(target_raw["target_level"]),
            starbase_object=_validate_u32(
                int(target_raw["starbase_object"]),
                "starbase_object",
            ),
        )
    elif action in {"set_starbase_module", "set_starbase_building"}:
        target = StarbaseComponentTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
            build_queue_id=_validate_u32(
                int(target_raw["build_queue_id"]),
                "build_queue_id",
            ),
            component_id=str(target_raw["component_id"]),
            slot_index=_validate_u32(
                int(target_raw["slot_index"]),
                "slot_index",
            ),
            starbase_object=_validate_u32(
                int(target_raw["starbase_object"]),
                "starbase_object",
            ),
        )
    elif action in {"start_research", "stop_research"}:
        technology_id = str(target_raw["technology_id"])
        if len(technology_id) > 160 or not TECHNOLOGY_ID_RE.fullmatch(
            technology_id
        ):
            raise ValueError("technology_id contains unsupported characters.")
        target = ResearchTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
            technology_id=technology_id,
        )
    elif action == "create_fleet_template":
        target = FleetTemplateCreationTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
        )
    elif action == "add_fleet_template_ship":
        target = FleetTemplateAddTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
            fleet_template_id=_validate_u32(
                int(target_raw["fleet_template_id"]),
                "fleet_template_id",
            ),
            design_id=_validate_u32(int(target_raw["design_id"]), "design_id"),
            upgrade_id=_validate_u32(
                int(target_raw.get("upgrade_id", 0xFFFFFFFF)),
                "upgrade_id",
            ),
            growth_stage=_validate_u32(
                int(target_raw.get("growth_stage", 0)),
                "growth_stage",
            ),
        )
    elif action == "remove_fleet_template_ship":
        target = FleetTemplateRemoveTarget(
            fleet_template_id=_validate_u32(
                int(target_raw["fleet_template_id"]),
                "fleet_template_id",
            ),
            design_id=_validate_u32(int(target_raw["design_id"]), "design_id"),
            upgrade_id=_validate_u32(
                int(target_raw.get("upgrade_id", 0xFFFFFFFF)),
                "upgrade_id",
            ),
            growth_stage=_validate_u32(
                int(target_raw.get("growth_stage", 0)),
                "growth_stage",
            ),
        )
    elif action == "reinforce_selected_fleet":
        target = FleetReinforcementTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
            fleet_template_id=_validate_u32(
                int(target_raw["fleet_template_id"]),
                "fleet_template_id",
            ),
        )
    elif action == "build_ship":
        if str(target_raw.get("destination_tag_hex", "")).lower() != (
            "0c3a01001400"
        ):
            raise ValueError("build_ship requires the verified starbase destination tag.")
        target = ShipBuildTarget(
            context_822c=_validate_u32(
                int(target_raw.get("context_822c", 0)),
                "context_822c",
            ),
            build_queue_id=_validate_u32(
                int(target_raw["build_queue_id"]),
                "build_queue_id",
            ),
            design_id=_validate_u32(int(target_raw["design_id"]), "design_id"),
            upgrade_id=_validate_u32(
                int(target_raw.get("upgrade_id", 0xFFFFFFFF)),
                "upgrade_id",
            ),
            growth_stage=_validate_u32(
                int(target_raw.get("growth_stage", 0)),
                "growth_stage",
            ),
            destination_object=_validate_u32(
                int(target_raw["destination_object"]),
                "destination_object",
            ),
        )
    else:
        blueprint = target_raw.get("blueprint")
        if not isinstance(blueprint, dict):
            raise ValueError("create_ship_design requires a blueprint object.")
        target = ShipDesignTarget(blueprint=dict(blueprint))
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


def parse_arm_request(path: Path, expected_session_id: str) -> ArmRequest:
    """Read and validate one arm-file request."""
    return parse_arm_document(
        json.loads(path.read_text(encoding="utf-8")),
        expected_session_id,
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


def _parse_fleet_coordinate_target(
    record: bytes,
) -> FleetCoordinateMoveTarget | None:
    """Read the verified fixed-width 4f2c coordinate-move body."""
    if len(record) < 12 or record[6:12] != FLEET_COORDINATE_FAMILY:
        return None
    if len(record) != 144 or int.from_bytes(record[:2], "little") + 1 != 144:
        raise ValueError("The 4f2c coordinate record has an invalid length.")
    if record[-4:] != COMMAND_TRAILER:
        raise ValueError("The 4f2c coordinate record has an invalid trailer.")
    source_offset = _unique_value_offset(
        record,
        FLEET_SOURCE_TAG,
        4,
        "source_fleet_object",
    )
    x_offset = _unique_value_offset(record, FLEET_X_TAG, 8, "x_fixed")
    y_offset = _unique_value_offset(record, FLEET_Y_TAG, 8, "y_fixed")
    system_offset = _unique_value_offset(
        record,
        FLEET_SYSTEM_TAG,
        4,
        "system_origin",
    )
    flag_6340 = _unique_value_offset(record, FLEET_FLAG_6340_TAG, 1, "flag_6340")
    flag_de35 = _unique_value_offset(record, FLEET_FLAG_DE35_TAG, 1, "flag_de35")
    if record[flag_6340] != 0 or record[flag_de35] != 0:
        raise ValueError("The unverified 4f2c boolean fields are not zero.")
    return FleetCoordinateMoveTarget(
        source_fleet_object=int.from_bytes(
            record[source_offset : source_offset + 4],
            "little",
        ),
        x_fixed=int.from_bytes(
            record[x_offset : x_offset + 8],
            "little",
            signed=True,
        ),
        y_fixed=int.from_bytes(
            record[y_offset : y_offset + 8],
            "little",
            signed=True,
        ),
        system_origin=int.from_bytes(
            record[system_offset : system_offset + 4],
            "little",
        ),
    )


def build_fleet_coordinate_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: FleetCoordinateMoveTarget,
    template_record: bytes = FLEET_COORDINATE_RECORD,
) -> bytes:
    """Build one same-system move while preserving both unknown zero flags."""
    if _parse_fleet_coordinate_target(template_record) is None:
        raise ValueError("The fleet coordinate fixture is not a 4f2c record.")
    record = bytearray(template_record)
    actor_offset = _unique_value_offset(bytes(record), ACTOR_TAG, 4, "actor")
    origin_offset = _unique_value_offset(bytes(record), ORIGIN_TAG, 1, "origin")
    source_offset = _unique_value_offset(
        bytes(record),
        FLEET_SOURCE_TAG,
        4,
        "source_fleet_object",
    )
    x_offset = _unique_value_offset(bytes(record), FLEET_X_TAG, 8, "x_fixed")
    y_offset = _unique_value_offset(bytes(record), FLEET_Y_TAG, 8, "y_fixed")
    system_offset = _unique_value_offset(
        bytes(record),
        FLEET_SYSTEM_TAG,
        4,
        "system_origin",
    )
    record[actor_offset : actor_offset + 4] = _validate_u32(
        actor,
        "actor",
    ).to_bytes(4, "little")
    record[origin_offset] = origin
    record[
        COMMAND_SERIAL_OFFSET : COMMAND_SERIAL_OFFSET + SERIAL_WIDTH
    ] = _validate_u32(command_serial, "command_serial").to_bytes(4, "little")
    record[source_offset : source_offset + 4] = _validate_u32(
        target.source_fleet_object,
        "source_fleet_object",
    ).to_bytes(4, "little")
    record[x_offset : x_offset + 8] = _validate_i64(
        target.x_fixed,
        "x_fixed",
    ).to_bytes(8, "little", signed=True)
    record[y_offset : y_offset + 8] = _validate_i64(
        target.y_fixed,
        "y_fixed",
    ).to_bytes(8, "little", signed=True)
    record[system_offset : system_offset + 4] = _validate_u32(
        target.system_origin,
        "system_origin",
    ).to_bytes(4, "little")
    result = bytes(record)
    if _parse_fleet_coordinate_target(result) != target:
        raise RuntimeError("The fleet coordinate target changed during construction.")
    return result


def _research_action(record: bytes) -> str | None:
    family = record[6:12]
    if family == RESEARCH_START_FAMILY:
        return "start_research"
    if family == RESEARCH_STOP_FAMILY:
        return "stop_research"
    return None


def _parse_research_target(record: bytes) -> ResearchTarget | None:
    """Parse the shared variable-width 062d/4533 research layout."""
    if len(record) < 92 or _research_action(record) is None:
        return None
    if int.from_bytes(record[:2], "little") + 1 != len(record):
        raise ValueError("The research record declared length is invalid.")
    if record[-4:] != COMMAND_TRAILER:
        raise ValueError("The research record trailer is invalid.")
    context_offset = _unique_value_offset(
        record,
        RESEARCH_CONTEXT_TAG,
        4,
        "context_822c",
    )
    technology_offset = _unique_value_offset(
        record,
        RESEARCH_TECHNOLOGY_TAG,
        2,
        "technology_length",
    )
    technology_length = int.from_bytes(
        record[technology_offset : technology_offset + 2],
        "little",
    )
    start = technology_offset + 2
    end = start + technology_length
    if end + len(COMMAND_TRAILER) != len(record):
        raise ValueError("The research technology string length is invalid.")
    try:
        technology_id = record[start:end].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("The research technology ID is not ASCII.") from error
    if not TECHNOLOGY_ID_RE.fullmatch(technology_id):
        raise ValueError("The research technology ID has an invalid format.")
    return ResearchTarget(
        context_822c=int.from_bytes(
            record[context_offset : context_offset + 4],
            "little",
        ),
        technology_id=technology_id,
    )


def build_research_record(
    *,
    action: str,
    command_serial: int,
    actor: int,
    origin: int,
    target: ResearchTarget,
) -> bytes:
    """Build one variable-width start/stop research command."""
    if action not in {"start_research", "stop_research"}:
        raise ValueError("Research action must start or stop research.")
    if len(target.technology_id) > 160 or not TECHNOLOGY_ID_RE.fullmatch(
        target.technology_id
    ):
        raise ValueError("technology_id contains unsupported characters.")
    technology = target.technology_id.encode("ascii")
    if len(technology) > 0xFFFF:
        raise ValueError("technology_id is too long for the protocol field.")

    record = bytearray(RESEARCH_START_RECORD[:86])
    record.extend(len(technology).to_bytes(2, "little"))
    record.extend(technology)
    record.extend(COMMAND_TRAILER)
    record[6:12] = (
        RESEARCH_START_FAMILY
        if action == "start_research"
        else RESEARCH_STOP_FAMILY
    )
    record[:2] = (len(record) - 1).to_bytes(2, "little")
    actor_offset = _unique_value_offset(bytes(record), ACTOR_TAG, 4, "actor")
    origin_offset = _unique_value_offset(bytes(record), ORIGIN_TAG, 1, "origin")
    context_offset = _unique_value_offset(
        bytes(record),
        RESEARCH_CONTEXT_TAG,
        4,
        "context_822c",
    )
    record[actor_offset : actor_offset + 4] = _validate_u32(
        actor,
        "actor",
    ).to_bytes(4, "little")
    record[origin_offset] = origin
    record[
        COMMAND_SERIAL_OFFSET : COMMAND_SERIAL_OFFSET + SERIAL_WIDTH
    ] = _validate_u32(command_serial, "command_serial").to_bytes(4, "little")
    record[context_offset : context_offset + 4] = _validate_u32(
        target.context_822c,
        "context_822c",
    ).to_bytes(4, "little")
    result = bytes(record)
    if _research_action(result) != action or _parse_research_target(result) != target:
        raise RuntimeError("The research target changed during construction.")
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
    if request.action == "upgrade_building":
        if not isinstance(request.target, BuildingUpgradeTarget):
            raise RuntimeError("The building-upgrade action has the wrong target type.")
        return build_building_upgrade_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "replace_building":
        if not isinstance(request.target, BuildingReplacementTarget):
            raise RuntimeError(
                "The building-replacement action has the wrong target type."
            )
        return build_building_replacement_record(
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
    if request.action == "move_fleet_to_coordinate":
        if not isinstance(request.target, FleetCoordinateMoveTarget):
            raise RuntimeError("The fleet coordinate action has the wrong target type.")
        return build_fleet_coordinate_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
            template_record=request.template_record or FLEET_COORDINATE_RECORD,
        )
    if request.action == "attack_fleet":
        if not isinstance(request.target, FleetAttackTarget):
            raise RuntimeError("The fleet-attack action has the wrong target type.")
        return build_fleet_attack_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "repair_fleet":
        if not isinstance(request.target, FleetRepairTarget):
            raise RuntimeError("The fleet-repair action has the wrong target type.")
        return build_fleet_repair_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "upgrade_fleet":
        if not isinstance(request.target, FleetUpgradeTarget):
            raise RuntimeError("The fleet-upgrade action has the wrong target type.")
        return build_fleet_upgrade_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "build_starbase":
        if not isinstance(request.target, ConstructionShipStarbaseTarget):
            raise RuntimeError("The starbase-build action has the wrong target type.")
        return build_construction_ship_starbase_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "configure_ship_automation":
        if not isinstance(request.target, ShipAutomationTarget):
            raise RuntimeError("The ship-automation action has the wrong target type.")
        return build_ship_automation_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "order_colony_ship_and_colonize":
        if not isinstance(request.target, OrderColonyShipTarget):
            raise RuntimeError("The colony-order action has the wrong target type.")
        return build_order_colony_ship_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "colonize_with_existing_ship":
        if not isinstance(request.target, ExistingColonyShipTarget):
            raise RuntimeError("The colony-ship action has the wrong target type.")
        return build_existing_colony_ship_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "upgrade_starbase":
        if not isinstance(request.target, StarbaseUpgradeTarget):
            raise RuntimeError("The starbase-upgrade action has the wrong target type.")
        return build_starbase_upgrade_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action in {"set_starbase_module", "set_starbase_building"}:
        if not isinstance(request.target, StarbaseComponentTarget):
            raise RuntimeError("The starbase-component action has the wrong target type.")
        return build_starbase_component_record(
            kind="module" if request.action.endswith("module") else "building",
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action in {"start_research", "stop_research"}:
        if not isinstance(request.target, ResearchTarget):
            raise RuntimeError("The research action has the wrong target type.")
        return build_research_record(
            action=request.action,
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "build_ship":
        if not isinstance(request.target, ShipBuildTarget):
            raise RuntimeError("The ship-build action has the wrong target type.")
        return build_ship_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "create_ship_design":
        if not isinstance(request.target, ShipDesignTarget):
            raise RuntimeError("The ship-design action has the wrong target type.")
        return build_ship_design_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "create_fleet_template":
        if not isinstance(request.target, FleetTemplateCreationTarget):
            raise RuntimeError("The fleet-template creation has the wrong target type.")
        return build_template_creation_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action in {
        "add_fleet_template_ship",
        "remove_fleet_template_ship",
    }:
        action = (
            "add"
            if request.action == "add_fleet_template_ship"
            else "remove"
        )
        if not isinstance(
            request.target,
            (FleetTemplateAddTarget, FleetTemplateRemoveTarget),
        ):
            raise RuntimeError("The fleet-template edit has the wrong target type.")
        return build_template_edit_record(
            action=action,
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    if request.action == "reinforce_selected_fleet":
        if not isinstance(request.target, FleetReinforcementTarget):
            raise RuntimeError("The reinforcement action has the wrong target type.")
        return build_selected_fleet_reinforcement_record(
            command_serial=command_serial,
            actor=request.source_actor,
            origin=request.request_origin,
            target=request.target,
        )
    raise RuntimeError(f"Unsupported action: {request.action}")


def build_action_probe(
    *,
    action: str,
    target: dict[str, Any],
    template_record_hex: str | None = None,
    source_actor: int = 2,
    host_actor: int = 1,
    request_origin: int = 0,
) -> dict[str, Any]:
    """Build one command offline and return a non-secret structural fingerprint."""
    document: dict[str, Any] = {
        "request_id": "offline-protocol-probe",
        "session_id": "offline-protocol-probe",
        "action": action,
        "source_actor": source_actor,
        "host_actor": host_actor,
        "request_origin": request_origin,
        "target": target,
    }
    if template_record_hex:
        document["template_record_hex"] = template_record_hex
    request = parse_arm_document(document, "offline-protocol-probe")
    record = _build_request_record(request, command_serial=1)
    application_prefix = application_prefix_for_record(record)
    if len(record) < 12:
        raise RuntimeError("The generated command record is too short.")
    return {
        "action": request.action,
        "wire_family_hex": record[6:12].hex(),
        "record_length": len(record),
        "application_prefix_hex": application_prefix.hex(),
        "inserted_length": len(application_prefix) + len(record),
        "record_sha256": hashlib.sha256(record).hexdigest(),
    }


def retag_matching_response(
    payload: bytes,
    *,
    request: ArmRequest,
) -> tuple[bytes, dict[str, int | str]] | None:
    if isinstance(request.target, ShipDesignTarget):
        if not is_reliable_packet(payload):
            return None
        application = payload[RELIABLE_HEADER_LENGTH:]
        extended = extended_record_from_application(application)
        if extended is None:
            return None
        record_offset, record = extended
        if not ship_design_record_matches(
            record,
            target=request.target,
            actor=request.source_actor,
            origin=0,
        ):
            return None
        _actor, _origin, serial = command_identity(record)
        rewritten_record = retag_actor(record, request.host_actor)
        rewritten_application = (
            application[:record_offset]
            + rewritten_record
            + application[record_offset + len(record) :]
        )
        rewritten = payload[:RELIABLE_HEADER_LENGTH] + rewritten_application
        if len(rewritten) != len(payload):
            raise RuntimeError("Ship-design response retagging changed payload length.")
        return rewritten, {
            "action": request.action,
            "host_command_serial_u32": serial,
            "source_actor": request.source_actor,
            "target_actor": request.host_actor,
            "source_origin": 0,
            "target_origin": 0,
            "carrier_offset": record_offset,
            "carrier_length": len(record),
            "design_name": ship_design_name(record),
        }
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
            if isinstance(request.target, BuildingUpgradeTarget):
                parsed_target = parse_building_upgrade_record(record)
            elif isinstance(request.target, BuildingReplacementTarget):
                parsed_target = parse_building_replacement_record(record)
            elif isinstance(request.target, DistrictConstructionTarget):
                parsed_target = _parse_district_target(
                    record,
                    request.target.district_type,
                )
            elif isinstance(request.target, ZoneSpecializationTarget):
                parsed_target = _parse_zone_target(record, request.target.zone_type)
            elif isinstance(request.target, FleetMoveTarget):
                parsed_target = _parse_fleet_move_target(record)
            elif isinstance(request.target, FleetCoordinateMoveTarget):
                parsed_target = _parse_fleet_coordinate_target(record)
            elif isinstance(request.target, FleetAttackTarget):
                parsed_target = parse_fleet_attack_record(record)
            elif isinstance(request.target, FleetRepairTarget):
                parsed_target = parse_fleet_repair_record(record)
            elif isinstance(request.target, FleetUpgradeTarget):
                parsed_target = parse_fleet_upgrade_record(record)
            elif isinstance(request.target, ConstructionShipStarbaseTarget):
                parsed_target = parse_construction_ship_starbase_record(record)
            elif isinstance(request.target, ShipAutomationTarget):
                parsed_target = parse_ship_automation_record(record)
            elif isinstance(request.target, OrderColonyShipTarget):
                parsed_target = parse_order_colony_ship_record(record)
            elif isinstance(request.target, ExistingColonyShipTarget):
                parsed_target = parse_existing_colony_ship_record(record)
            elif isinstance(request.target, StarbaseUpgradeTarget):
                parsed_target = parse_starbase_upgrade_record(record)
            elif isinstance(request.target, StarbaseComponentTarget):
                parsed_target = parse_starbase_component_record(
                    record,
                    kind=(
                        "module"
                        if request.action == "set_starbase_module"
                        else "building"
                    ),
                )
            elif isinstance(request.target, ResearchTarget):
                if _research_action(record) != request.action:
                    continue
                parsed_target = _parse_research_target(record)
            elif isinstance(request.target, ShipBuildTarget):
                parsed_target = parse_ship_record(record)
            elif isinstance(request.target, FleetTemplateAddTarget):
                parsed = parse_template_edit(record)
                if parsed is None or parsed[0] != "add":
                    continue
                parsed_target = parsed[1]
            elif isinstance(request.target, FleetTemplateCreationTarget):
                parsed_target = parse_template_creation(record)
                if parsed_target is None:
                    continue
            elif isinstance(request.target, FleetTemplateRemoveTarget):
                parsed = parse_template_edit(record)
                if parsed is None or parsed[0] != "remove":
                    continue
                parsed_target = parsed[1]
            elif isinstance(request.target, FleetReinforcementTarget):
                parsed_target = parse_selected_fleet_reinforcement(record)
                if parsed_target is None:
                    continue
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
    if isinstance(request.target, BuildingUpgradeTarget):
        metadata["build_queue_id"] = request.target.build_queue_id
        metadata["building_object_id"] = request.target.building_object_id
        metadata["building_id"] = request.target.building_id
    elif isinstance(request.target, BuildingReplacementTarget):
        metadata["build_queue_id"] = request.target.build_queue_id
        metadata["source_building_object_id"] = (
            request.target.source_building_object_id
        )
        metadata["building_id"] = request.target.building_id
    elif isinstance(request.target, DistrictConstructionTarget):
        metadata["district_type"] = request.target.district_type
    elif isinstance(request.target, ZoneSpecializationTarget):
        metadata["zone_type"] = request.target.zone_type
    elif isinstance(request.target, FleetMoveTarget):
        metadata["source_fleet_object"] = request.target.source_fleet_object
        metadata["destination_tag_hex"] = request.target.destination_tag_hex
        metadata["destination_object"] = request.target.destination_object
    elif isinstance(request.target, FleetCoordinateMoveTarget):
        metadata["source_fleet_object"] = request.target.source_fleet_object
        metadata["x_fixed"] = request.target.x_fixed
        metadata["y_fixed"] = request.target.y_fixed
        metadata["system_origin"] = request.target.system_origin
    elif isinstance(request.target, FleetAttackTarget):
        metadata["source_fleet_object"] = request.target.source_fleet_object
        metadata["target_fleet_object"] = request.target.target_fleet_object
    elif isinstance(request.target, FleetRepairTarget):
        metadata["source_fleet_object"] = request.target.source_fleet_object
        metadata["context_822c"] = request.target.context_822c
    elif isinstance(request.target, FleetUpgradeTarget):
        metadata["source_fleet_object"] = request.target.source_fleet_object
        metadata["context_822c"] = request.target.context_822c
        metadata["shipyard_build_queue_id"] = (
            request.target.shipyard_build_queue_id
        )
    elif isinstance(request.target, ConstructionShipStarbaseTarget):
        metadata["source_fleet_object"] = request.target.source_fleet_object
        metadata["target_system_object"] = request.target.target_system_object
    elif isinstance(request.target, ShipAutomationTarget):
        metadata["source_fleet_object"] = request.target.source_fleet_object
        metadata["context_822c"] = request.target.context_822c
        metadata["automation_options"] = ",".join(request.target.options)
    elif isinstance(request.target, OrderColonyShipTarget):
        metadata["target_planet_id"] = request.target.target_planet_id
        metadata["source_shipyard_build_queue_id"] = (
            request.target.source_shipyard_build_queue_id
        )
        metadata["colony_designation"] = request.target.colony_designation
        metadata["system_name_key"] = request.target.system_name_key
    elif isinstance(request.target, ExistingColonyShipTarget):
        metadata["source_fleet_object"] = request.target.source_fleet_object
        metadata["target_planet_id"] = request.target.target_planet_id
        metadata["system_name_key"] = request.target.system_name_key
    elif isinstance(request.target, StarbaseUpgradeTarget):
        metadata["build_queue_id"] = request.target.build_queue_id
        metadata["target_level"] = request.target.target_level
        metadata["starbase_object"] = request.target.starbase_object
    elif isinstance(request.target, StarbaseComponentTarget):
        metadata["build_queue_id"] = request.target.build_queue_id
        metadata["component_id"] = request.target.component_id
        metadata["slot_index"] = request.target.slot_index
        metadata["starbase_object"] = request.target.starbase_object
    elif isinstance(request.target, ShipBuildTarget):
        metadata["build_queue_id"] = request.target.build_queue_id
        metadata["design_id"] = request.target.design_id
        metadata["destination_object"] = request.target.destination_object
    elif isinstance(
        request.target,
        (FleetTemplateAddTarget, FleetTemplateRemoveTarget),
    ):
        metadata["fleet_template_id"] = request.target.fleet_template_id
        metadata["design_id"] = request.target.design_id
    elif isinstance(request.target, FleetTemplateCreationTarget):
        metadata["context_822c"] = request.target.context_822c
        metadata["template_id_transport"] = "deterministic_allocator_not_on_wire"
    elif isinstance(request.target, FleetReinforcementTarget):
        metadata["fleet_template_id"] = request.target.fleet_template_id
    else:
        metadata["technology_id"] = request.target.technology_id
        metadata["context_822c"] = request.target.context_822c
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
    application_prefix = application_prefix_for_record(command)
    inserted_length = len(application_prefix) + len(command)
    if (
        len(payload) + inserted_length > max_payload_length
    ):
        return None
    application = application_prefix + command
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
        "supported_actions": list(SUPPORTED_SESSION_PROXY_ACTIONS),
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
                direction = flow_packet_direction(
                    locked,
                    src_ip=src_ip,
                    src_port=int(packet.src_port),
                    dst_ip=dst_ip,
                    dst_port=int(packet.dst_port),
                )
                if direction is None:
                    divert.send(packet, recalculate_checksum=True)
                    continue

                is_outbound_flow = direction == "outbound"
                is_inbound_flow = direction == "inbound"
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
