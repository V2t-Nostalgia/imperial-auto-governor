#!/usr/bin/env python3
"""Verified Clausewitz command builders used by the session proxy.

The session proxy inserts verified 4.4.6 application-command records into the
client-to-host reliable byte stream.  A record may have a different length for
each action or identifier.  After insertion the proxy presents two
stream-coordinate views:

* local client -> probe: the original stream without the inserted 153 bytes;
* probe -> host: the same stream with the inserted command at the boundary.

Later client sender offsets are therefore increased by every inserted length,
while host ACK offsets are decreased by those lengths before reaching the local
client.  Inserted commands reserve application serials, so later commands from
the same co-op actor are shifted by the number of synthetic commands.  If the
host broadcasts an injected action, the exactly correlated inbound record is
retagged from the non-host actor to the host actor.  Stream translation remains
active after that response because the two peers' byte coordinates differ for
the rest of the connection.

The live packet loop lives in :mod:`iag.stellaris.execution.session_proxy`.
This module keeps the byte-level record construction independently testable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from iag.stellaris.execution.packet.iag_stream_command_injector import (
    COMMAND_SERIAL_OFFSET,
    RELIABLE_HEADER_LENGTH,
    StreamTranslator,
    UINT24_HALF_RANGE,
    UINT24_MODULUS,
    find_command_records,
    forward_distance_uint24,
    is_reliable_packet,
    read_uint24_be,
)


ACTOR_TAG = bytes.fromhex("400201000c00")
ORIGIN_TAG = bytes.fromhex("130401000e00")
CONTEXT_TAG = bytes.fromhex("822c01001400")
BUILD_QUEUE_TAG = bytes.fromhex("634001001400")
BUILDING_TAG = bytes.fromhex("b22b01000f00")
COLONY_TAG = bytes.fromhex("132a01001400")
ZONE_TAG = bytes.fromhex("b32b01001400")
COMMAND_FAMILY = bytes.fromhex("b43d01000300")
SERIAL_WIDTH = 4

# Real 4.4.6 research-lab request captured during the 2026-07-30 UNE retest.
# All volatile business fields are overwritten by build_building_record().
RESEARCH_LAB_RECORD_TEMPLATE = bytes.fromhex(
    "980004000000b43d01000300f30101000300400201000c0002000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14005b0000000400410001000300822c010014000000000063400100"
    "14001d010000b23d01000300b22b01000f0017006275696c64696e67"
    "5f72657365617263685f6c61625f31132a0100140050000000b32b01"
    "001400d4000000040004000400"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def append_jsonl(path: Path, event: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def add_vendor_runtime() -> None:
    runtime = Path(__file__).resolve().parent / "vendor_runtime"
    if not runtime.is_dir():
        raise RuntimeError(f"Missing vendored PyDivert runtime: {runtime}")
    sys.path.insert(0, str(runtime))


def _validate_u32(value: int, label: str) -> int:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"{label} must be an unsigned 32-bit integer.")
    return value


def _unique_value_offset(record: bytes, tag: bytes, width: int, label: str) -> int:
    offsets: list[int] = []
    cursor = record.find(tag)
    while cursor >= 0:
        offsets.append(cursor + len(tag))
        cursor = record.find(tag, cursor + 1)
    if len(offsets) != 1:
        raise ValueError(f"Expected one {label} field, found {len(offsets)}.")
    value_offset = offsets[0]
    if value_offset + width > len(record):
        raise ValueError(f"The {label} value crosses the command boundary.")
    return value_offset


def _u32(record: bytes, tag: bytes, label: str) -> int:
    offset = _unique_value_offset(record, tag, 4, label)
    return int.from_bytes(record[offset : offset + 4], "little")


def _actor_origin(record: bytes) -> tuple[int, int]:
    actor_offset = _unique_value_offset(record, ACTOR_TAG, 4, "actor")
    origin_offset = _unique_value_offset(record, ORIGIN_TAG, 1, "origin")
    return (
        int.from_bytes(record[actor_offset : actor_offset + 4], "little"),
        record[origin_offset],
    )


def _serial_u32(record: bytes) -> int:
    end = COMMAND_SERIAL_OFFSET + SERIAL_WIDTH
    if end > len(record):
        raise ValueError("The four-byte command serial crosses the command boundary.")
    return int.from_bytes(record[COMMAND_SERIAL_OFFSET:end], "little")


def _set_tagged_u32(record: bytearray, tag: bytes, value: int, label: str) -> None:
    offset = _unique_value_offset(bytes(record), tag, 4, label)
    record[offset : offset + 4] = _validate_u32(value, label).to_bytes(4, "little")


def _building_id(record: bytes) -> str:
    length_offset = _unique_value_offset(record, BUILDING_TAG, 2, "building length")
    length = int.from_bytes(record[length_offset : length_offset + 2], "little")
    value_offset = length_offset + 2
    value = record[value_offset : value_offset + length]
    if len(value) != length:
        raise ValueError("The building ID crosses the command boundary.")
    return value.decode("ascii")


@dataclass(frozen=True)
class BuildingTarget:
    context_822c: int
    build_queue_id: int
    colony_id: int
    zone_id: int
    building_id: str = "building_research_lab_1"


def build_building_record(
    *,
    command_serial: int,
    actor: int,
    origin: int,
    target: BuildingTarget,
) -> bytes:
    """Build one 4.4.6 ``b43d`` building request from a real client fixture.

    The building identifier is a length-prefixed ASCII field.  Unlike the old
    click carrier, a synthesized stream record does not need to retain the
    fixture's byte length, so the envelope is resized and its declared length
    is updated.  Every surrounding field is retained byte-for-byte.
    """
    if origin not in (0, 1):
        raise ValueError("origin must be 0 or 1.")

    try:
        encoded_building_id = target.building_id.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("building_id must contain ASCII characters only.") from error
    if not target.building_id.startswith("building_"):
        raise ValueError("building_id must use a Stellaris building_ identifier.")
    if not 1 <= len(encoded_building_id) <= 255:
        raise ValueError("building_id must contain 1 to 255 ASCII bytes.")

    fixture = RESEARCH_LAB_RECORD_TEMPLATE
    length_offset = _unique_value_offset(
        fixture,
        BUILDING_TAG,
        2,
        "building length",
    )
    fixture_length = int.from_bytes(
        fixture[length_offset : length_offset + 2],
        "little",
    )
    fixture_value_offset = length_offset + 2
    fixture_value_end = fixture_value_offset + fixture_length
    resized = (
        fixture[:length_offset]
        + len(encoded_building_id).to_bytes(2, "little")
        + encoded_building_id
        + fixture[fixture_value_end:]
    )
    record = bytearray(resized)
    record[:2] = (len(record) - 1).to_bytes(2, "little")
    if record[6 : 6 + len(COMMAND_FAMILY)] != COMMAND_FAMILY:
        raise RuntimeError("The fixture is not a b43d construction record.")
    if int.from_bytes(record[:2], "little") + 1 != len(record):
        raise RuntimeError("The fixture declared length is invalid.")

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
    _set_tagged_u32(record, COLONY_TAG, target.colony_id, "colony_id")
    _set_tagged_u32(record, ZONE_TAG, target.zone_id, "zone_id")

    result = bytes(record)
    if _building_id(result) != target.building_id:
        raise RuntimeError("The fixture building ID changed unexpectedly.")
    if int.from_bytes(result[:2], "little") + 1 != len(result):
        raise RuntimeError("The resized building record has an invalid envelope.")
    return result


def _record_target(record: bytes) -> BuildingTarget | None:
    if record[6 : 6 + len(COMMAND_FAMILY)] != COMMAND_FAMILY:
        return None
    try:
        building_id = _building_id(record)
        return BuildingTarget(
            context_822c=_u32(record, CONTEXT_TAG, "context_822c"),
            build_queue_id=_u32(record, BUILD_QUEUE_TAG, "build_queue_id"),
            colony_id=_u32(record, COLONY_TAG, "colony_id"),
            zone_id=_u32(record, ZONE_TAG, "zone_id"),
            building_id=building_id,
        )
    except (UnicodeDecodeError, ValueError):
        return None


@dataclass(frozen=True)
class InjectionResult:
    payload: bytes
    injection_offset: int
    injected_serial: int
    untouched_carrier_serial: int
    carrier_offset: int
    carrier_length: int


@dataclass(frozen=True)
class BoundaryInjectionResult:
    payload: bytes
    injection_offset: int
    injected_serial: int
    carrier_offset: int
    carrier_length: int = 0
    untouched_carrier_serial: int | None = None


def inject_at_stream_boundary_without_translation(
    payload: bytes,
    *,
    command_serial: int,
    source_actor: int,
    target: BuildingTarget,
    max_payload_length: int = 1400,
) -> BoundaryInjectionResult | None:
    """Append one complete request at the current packet's stream boundary."""
    if not is_reliable_packet(payload):
        return None
    record = build_building_record(
        command_serial=command_serial,
        actor=source_actor,
        origin=0,
        target=target,
    )
    if len(payload) + len(record) > max_payload_length:
        return None
    application_length = len(payload) - RELIABLE_HEADER_LENGTH
    rewritten = payload + record
    if payload[:RELIABLE_HEADER_LENGTH] != rewritten[:RELIABLE_HEADER_LENGTH]:
        raise RuntimeError("Boundary injection changed the reliable header.")
    return BoundaryInjectionResult(
        payload=rewritten,
        injection_offset=(read_uint24_be(payload, 6) + application_length)
        % UINT24_MODULUS,
        injected_serial=_validate_u32(command_serial, "command_serial"),
        carrier_offset=application_length,
    )


def inject_before_live_command_without_translation(
    payload: bytes,
    *,
    source_actor: int,
    target: BuildingTarget,
    max_payload_length: int = 1400,
) -> InjectionResult | None:
    """Insert one request while intentionally preserving later stream state."""
    if not is_reliable_packet(payload) or len(payload) <= RELIABLE_HEADER_LENGTH:
        return None
    application = payload[RELIABLE_HEADER_LENGTH:]
    for command in find_command_records(application):
        record = application[command.offset : command.offset + command.length]
        try:
            actor, _origin = _actor_origin(record)
            serial = _serial_u32(record)
        except ValueError:
            continue
        if actor != source_actor or serial == 0:
            continue
        injected = build_building_record(
            command_serial=serial,
            actor=source_actor,
            origin=0,
            target=target,
        )
        rewritten_application = (
            application[: command.offset]
            + injected
            + application[command.offset:]
        )
        rewritten = payload[:RELIABLE_HEADER_LENGTH] + rewritten_application
        if len(rewritten) > max_payload_length:
            continue
        if payload[:RELIABLE_HEADER_LENGTH] != rewritten[:RELIABLE_HEADER_LENGTH]:
            raise RuntimeError("The no-translation probe changed the reliable header.")
        untouched = rewritten_application[
            command.offset + len(injected) : command.offset + len(injected) + command.length
        ]
        if untouched != record or _serial_u32(untouched) != serial:
            raise RuntimeError("The carrier command or its serial was translated.")
        return InjectionResult(
            payload=rewritten,
            injection_offset=(read_uint24_be(payload, 6) + command.offset)
            % UINT24_MODULUS,
            injected_serial=serial,
            untouched_carrier_serial=serial,
            carrier_offset=command.offset,
            carrier_length=command.length,
        )
    return None


def retag_matching_host_response(
    payload: bytes,
    *,
    source_actor: int,
    target_actor: int,
    target: BuildingTarget,
) -> tuple[bytes, dict[str, int | str]] | None:
    """Retag one correlated host broadcast without changing its envelope."""
    if not is_reliable_packet(payload):
        return None
    application = payload[RELIABLE_HEADER_LENGTH:]
    matches: list[tuple[object, bytes, int, int]] = []
    for command in find_command_records(application):
        record = application[command.offset : command.offset + command.length]
        parsed_target = _record_target(record)
        if parsed_target != target:
            continue
        try:
            actor, origin = _actor_origin(record)
        except ValueError:
            continue
        if actor == source_actor and origin == 0:
            actor_offset = _unique_value_offset(record, ACTOR_TAG, 4, "actor")
            matches.append((command, record, actor_offset, _serial_u32(record)))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(f"Expected one correlated host response, found {len(matches)}.")

    command, record, actor_offset, serial = matches[0]
    rewritten_record = bytearray(record)
    rewritten_record[actor_offset : actor_offset + 4] = _validate_u32(
        target_actor, "target_actor"
    ).to_bytes(4, "little")
    rewritten_application = (
        application[: command.offset]
        + bytes(rewritten_record)
        + application[command.offset + command.length :]
    )
    rewritten = payload[:RELIABLE_HEADER_LENGTH] + rewritten_application
    if len(rewritten) != len(payload):
        raise RuntimeError("Response retagging changed the payload length.")
    if _serial_u32(bytes(rewritten_record)) != serial:
        raise RuntimeError("Response retagging changed the host serial.")
    return rewritten, {
        "building_id": target.building_id,
        "context_822c": target.context_822c,
        "build_queue_id": target.build_queue_id,
        "colony_id": target.colony_id,
        "zone_id": target.zone_id,
        "host_command_serial_u32": serial,
        "source_actor": source_actor,
        "target_actor": target_actor,
        "source_origin": 0,
        "target_origin": 0,
        "carrier_offset": int(command.offset),
        "carrier_length": int(command.length),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-ip", required=True)
    parser.add_argument("--host-port", type=int, required=True)
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--local-port", type=int, required=True)
    parser.add_argument("--context-822c", type=int, required=True)
    parser.add_argument("--build-queue-id", type=int, required=True)
    parser.add_argument("--colony-id", type=int, required=True)
    parser.add_argument("--zone-id", type=int, required=True)
    parser.add_argument("--building-id", default="building_research_lab_1")
    parser.add_argument("--source-actor", type=int, default=2)
    parser.add_argument("--host-actor", type=int, default=1)
    parser.add_argument(
        "--command-serial",
        type=int,
        help=(
            "Explicit current-epoch serial for autonomous stream-boundary "
            "injection. Without it, the probe waits for a natural nonzero command."
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--response-timeout-seconds", type=int, default=30)
    parser.add_argument(
        "--session-seconds",
        type=int,
        default=7200,
        help=(
            "Keep stream-coordinate translation active for this many seconds "
            "after injection. Do not stop it while the game connection remains active."
        ),
    )
    parser.add_argument("--max-payload-length", type=int, default=1400)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--acknowledge-disposable-session", action="store_true")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    if args.execute and not args.acknowledge_disposable_session:
        raise RuntimeError(
            "Live execution requires --acknowledge-disposable-session."
        )
    if args.ready_file.exists():
        raise RuntimeError(f"A probe is already registered: {args.ready_file}")
    target = BuildingTarget(
        context_822c=_validate_u32(args.context_822c, "context_822c"),
        build_queue_id=_validate_u32(args.build_queue_id, "build_queue_id"),
        colony_id=_validate_u32(args.colony_id, "colony_id"),
        zone_id=_validate_u32(args.zone_id, "zone_id"),
        building_id=args.building_id,
    )
    add_vendor_runtime()
    import pydivert  # type: ignore[import-not-found]

    packet_filter = (
        "udp and ("
        f"(ip.SrcAddr == {args.local_ip} and ip.DstAddr == {args.host_ip} "
        f"and udp.SrcPort == {args.local_port} and udp.DstPort == {args.host_port}) or "
        f"(ip.SrcAddr == {args.host_ip} and ip.DstAddr == {args.local_ip} "
        f"and udp.SrcPort == {args.host_port} and udp.DstPort == {args.local_port}))"
    )
    started = {
        "event": "started",
        "timestamp": now_iso(),
        "pid": os.getpid(),
        "mode": "execute" if args.execute else "dry_run",
        "hypothesis": (
            "insert_one_record_then_translate_sender_plus_length_and_host_ack_"
            "minus_length_without_command_serial_translation"
        ),
        "filter": packet_filter,
        "source_actor": args.source_actor,
        "host_actor": args.host_actor,
        "explicit_command_serial": args.command_serial,
        "target": target.__dict__,
        "timeout_seconds": args.timeout_seconds,
        "response_timeout_seconds": args.response_timeout_seconds,
        "session_seconds": args.session_seconds,
    }
    append_jsonl(args.log, started)
    injected = False
    response_retagged = False
    injected_at: float | None = None
    original_carrier: bytes | None = None
    rewritten_carrier: bytes | None = None
    stream_translator: StreamTranslator | None = None
    deadline = time.monotonic() + args.timeout_seconds
    response_timeout_logged = False
    host_acknowledged_inserted_bytes = False
    translated_sender_packets = 0
    translated_ack_packets = 0
    carrier_retransmissions = 0

    try:
        with pydivert.WinDivert(packet_filter) as divert:
            args.ready_file.parent.mkdir(parents=True, exist_ok=True)
            args.ready_file.write_text(
                json.dumps(started, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print("IAG autonomous command probe is READY.", flush=True)
            print(f"Mode: {started['mode']}", flush=True)
            print(f"Filter: {packet_filter}", flush=True)
            for packet in divert:
                original = packet.payload or b""
                direction = "outbound" if packet.src_addr == args.local_ip else "inbound"
                try:
                    if not injected and direction == "outbound":
                        if args.command_serial is None:
                            result = inject_before_live_command_without_translation(
                                original,
                                source_actor=args.source_actor,
                                target=target,
                                max_payload_length=args.max_payload_length,
                            )
                        else:
                            result = inject_at_stream_boundary_without_translation(
                                original,
                                command_serial=args.command_serial,
                                source_actor=args.source_actor,
                                target=target,
                                max_payload_length=args.max_payload_length,
                            )
                        if result is not None:
                            append_jsonl(
                                args.log,
                                {
                                    "event": "dry_run_candidate" if not args.execute else "request_injected",
                                    "timestamp": now_iso(),
                                    "sender_offset": read_uint24_be(original, 6),
                                    "ack_offset": read_uint24_be(original, 10),
                                    "injection_offset": result.injection_offset,
                                    "injected_serial_u32": result.injected_serial,
                                    "untouched_carrier_serial_u32": result.untouched_carrier_serial,
                                    "carrier_offset": result.carrier_offset,
                                    "carrier_length": result.carrier_length,
                                    "before_length": len(original),
                                    "after_length": len(result.payload),
                                    "before_sha256": sha256_hex(original),
                                    "after_sha256": sha256_hex(result.payload),
                                    "before_payload_hex": original.hex(),
                                    "after_payload_hex": result.payload.hex(),
                                    "translate_later_sender_offsets_by": len(
                                        RESEARCH_LAB_RECORD_TEMPLATE
                                    ),
                                    "translate_later_host_acks_by": -len(
                                        RESEARCH_LAB_RECORD_TEMPLATE
                                    ),
                                    "translated_command_serial": False,
                                },
                            )
                            if not args.execute:
                                divert.send(packet, recalculate_checksum=True)
                                return 0
                            packet.payload = result.payload
                            divert.send(packet, recalculate_checksum=True)
                            original_carrier = original
                            rewritten_carrier = result.payload
                            stream_translator = StreamTranslator(
                                injection_offset=result.injection_offset,
                                inserted_length=len(RESEARCH_LAB_RECORD_TEMPLATE),
                            )
                            injected = True
                            injected_at = time.monotonic()
                            print(
                                "Inserted one 153-byte command; stream translation is active.",
                                flush=True,
                            )
                            print(
                                "Do not stop this probe until the game connection is closed.",
                                flush=True,
                            )
                            continue
                    elif injected and direction == "outbound":
                        if original_carrier is not None and original == original_carrier:
                            packet.payload = rewritten_carrier
                            carrier_retransmissions += 1
                        elif stream_translator is not None:
                            translated = stream_translator.translate_outbound(original)
                            packet.payload = translated
                            if translated != original:
                                translated_sender_packets += 1
                                if (
                                    translated_sender_packets <= 5
                                    or translated_sender_packets % 1000 == 0
                                ):
                                    append_jsonl(
                                        args.log,
                                        {
                                            "event": "client_sender_offset_translated",
                                            "timestamp": now_iso(),
                                            "packet_count": translated_sender_packets,
                                            "remote_sender_offset": read_uint24_be(
                                                translated, 6
                                            ),
                                            "local_sender_offset": read_uint24_be(
                                                original, 6
                                            ),
                                            "delta": len(RESEARCH_LAB_RECORD_TEMPLATE),
                                            "payload_length": len(original),
                                        },
                                    )
                        divert.send(packet, recalculate_checksum=True)
                        continue
                    elif injected and direction == "inbound":
                        translated_ack_payload = (
                            stream_translator.translate_inbound(original)
                            if stream_translator is not None
                            else original
                        )
                        if translated_ack_payload != original:
                            translated_ack_packets += 1
                            if (
                                translated_ack_packets <= 5
                                or translated_ack_packets % 1000 == 0
                            ):
                                append_jsonl(
                                    args.log,
                                    {
                                        "event": "host_ack_offset_translated",
                                        "timestamp": now_iso(),
                                        "packet_count": translated_ack_packets,
                                        "remote_ack_offset": read_uint24_be(original, 10),
                                        "local_ack_offset": read_uint24_be(
                                            translated_ack_payload, 10
                                        ),
                                        "delta": -len(RESEARCH_LAB_RECORD_TEMPLATE),
                                        "payload_length": len(original),
                                    },
                                )
                        if stream_translator is not None and is_reliable_packet(original):
                            remote_ack = read_uint24_be(original, 10)
                            acknowledged_distance = forward_distance_uint24(
                                remote_ack,
                                stream_translator.injection_offset,
                            )
                            if (
                                not host_acknowledged_inserted_bytes
                                and len(RESEARCH_LAB_RECORD_TEMPLATE)
                                <= acknowledged_distance
                                < UINT24_HALF_RANGE
                            ):
                                host_acknowledged_inserted_bytes = True
                                append_jsonl(
                                    args.log,
                                    {
                                        "event": "host_acknowledged_inserted_bytes",
                                        "timestamp": now_iso(),
                                        "injection_offset": (
                                            stream_translator.injection_offset
                                        ),
                                        "inserted_length": len(
                                            RESEARCH_LAB_RECORD_TEMPLATE
                                        ),
                                        "remote_ack_offset": remote_ack,
                                        "local_ack_offset": read_uint24_be(
                                            translated_ack_payload, 10
                                        ),
                                    },
                                )
                        retagged = retag_matching_host_response(
                            translated_ack_payload,
                            source_actor=args.source_actor,
                            target_actor=args.host_actor,
                            target=target,
                        )
                        if retagged is not None:
                            rewritten, metadata = retagged
                            packet.payload = rewritten
                            divert.send(packet, recalculate_checksum=True)
                            append_jsonl(
                                args.log,
                                {
                                    "event": "host_response_retagged",
                                    "timestamp": now_iso(),
                                    "before_length": len(original),
                                    "after_length": len(rewritten),
                                    "before_sha256": sha256_hex(original),
                                    "after_sha256": sha256_hex(rewritten),
                                    "before_payload_hex": original.hex(),
                                    "after_payload_hex": rewritten.hex(),
                                    **metadata,
                                },
                            )
                            response_retagged = True
                            continue
                        packet.payload = translated_ack_payload
                    divert.send(packet, recalculate_checksum=True)
                except Exception as error:
                    packet.payload = original
                    divert.send(packet, recalculate_checksum=True)
                    append_jsonl(
                        args.log,
                        {
                            "event": "packet_error_original_forwarded",
                            "timestamp": now_iso(),
                            "direction": direction,
                            "error": f"{type(error).__name__}: {error}",
                            "payload_length": len(original),
                            "payload_sha256": sha256_hex(original),
                        },
                    )
                now = time.monotonic()
                if (
                    injected_at is not None
                    and not response_retagged
                    and not response_timeout_logged
                    and now - injected_at >= args.response_timeout_seconds
                ):
                    append_jsonl(
                        args.log,
                        {
                            "event": "host_response_timeout",
                            "timestamp": now_iso(),
                            "seconds": args.response_timeout_seconds,
                            "translation_continues": True,
                        },
                    )
                    response_timeout_logged = True
                if not injected and now >= deadline:
                    return 2
                if (
                    injected_at is not None
                    and now - injected_at >= args.session_seconds
                ):
                    append_jsonl(
                        args.log,
                        {
                            "event": "session_translation_timeout",
                            "timestamp": now_iso(),
                            "seconds": args.session_seconds,
                        },
                    )
                    return 0 if response_retagged else 2
    finally:
        args.ready_file.unlink(missing_ok=True)
        append_jsonl(
            args.log,
            {
                "event": "stopped",
                "timestamp": now_iso(),
                "injected": injected,
                "response_retagged": response_retagged,
                "stream_translation_activated": stream_translator is not None,
                "host_acknowledged_inserted_bytes": (
                    host_acknowledged_inserted_bytes
                ),
                "translated_sender_packets": translated_sender_packets,
                "translated_ack_packets": translated_ack_packets,
                "carrier_retransmissions": carrier_retransmissions,
                "mode": started["mode"],
            },
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(run())
