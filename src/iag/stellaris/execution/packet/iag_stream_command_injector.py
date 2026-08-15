#!/usr/bin/env python3
"""Inject one Stellaris construction command into the live reliable stream.

The injector inserts a verified 153-byte application record at the sender's
current stream boundary. It then translates later client sender offsets and
host ACK offsets so the unmodified local game process keeps its original view
of the byte stream.

This is an experimental, one-shot tool for the authorized co-op test session.
It fails closed: unknown packets and malformed reliable headers pass unchanged.
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


RELIABLE_HEADER_LENGTH = 25
UINT24_MODULUS = 1 << 24
UINT24_HALF_RANGE = 1 << 23
# The first five bytes identify this reliable transport family. Byte 5 is the
# sender epoch and legitimately changes after a 24-bit stream-offset wrap.
RELIABLE_MARKER = b"\x01\x00\x00\x00\x00"
COMMAND_COMMON_MARKER = bytes.fromhex(
    "f30101000300400201000c00"
)
COMMAND_ENVELOPE_TYPE = bytes.fromhex("04000000")

# Captured from the current session on 2026-06-14. Only the command serial,
# build queue, building ID, planet ID, and placement are changed by the builder.
EARTH_RESEARCH_COMMAND_TEMPLATE = bytes.fromhex(
    "980004000000433d01000300f30101000300400201000c0003000000"
    "c70001000c00ff7f0000cc0001000e0000130401000e0000db000100"
    "14009d0100000400410001000300612c0100140000000000ea3f0100"
    "140006000000413d01000300922b01000f0017006275696c64696e67"
    "5f72657365617263685f6c61625f31fc290100140003000000932b01"
    "00140000000000040004000400"
)

COMMAND_SERIAL_OFFSET = 58
BUILD_QUEUE_OFFSET = 86
BUILDING_LENGTH_OFFSET = 102
BUILDING_ID_OFFSET = 104
PLANET_ID_OFFSET_FROM_BUILDING_END = 6


def read_uint24_be(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 3], "big")


def write_uint24_be(data: bytes, offset: int, value: int) -> bytes:
    output = bytearray(data)
    output[offset : offset + 3] = (value % UINT24_MODULUS).to_bytes(3, "big")
    return bytes(output)


def forward_distance_uint24(value: int, origin: int) -> int:
    return (value - origin) % UINT24_MODULUS


def is_at_or_after_uint24(value: int, origin: int) -> bool:
    return forward_distance_uint24(value, origin) < UINT24_HALF_RANGE


def is_reliable_packet(payload: bytes) -> bool:
    return (
        len(payload) >= RELIABLE_HEADER_LENGTH
        and payload.startswith(RELIABLE_MARKER)
    )


def build_construction_command(
    *,
    command_serial: int,
    build_queue_id: int,
    building_id: str,
    planet_id: int,
    placement: int,
) -> bytes:
    """Build the verified construction record without resizing its envelope."""
    encoded_id = building_id.encode("ascii")
    template_id = b"building_research_lab_1"
    if len(encoded_id) > len(template_id):
        raise ValueError("Building ID is longer than the verified command slot.")

    command = bytearray(EARTH_RESEARCH_COMMAND_TEMPLATE)
    command[COMMAND_SERIAL_OFFSET : COMMAND_SERIAL_OFFSET + 2] = (
        command_serial.to_bytes(2, "little")
    )
    command[BUILD_QUEUE_OFFSET : BUILD_QUEUE_OFFSET + 4] = (
        build_queue_id.to_bytes(4, "little")
    )
    command[BUILDING_LENGTH_OFFSET : BUILDING_LENGTH_OFFSET + 2] = (
        len(encoded_id).to_bytes(2, "little")
    )

    # Keep the outer record fixed at 153 bytes, but keep padding at the record
    # boundary. Padding inside the string would make the following field start
    # at the wrong parser position.
    suffix = command[BUILDING_ID_OFFSET + len(template_id) :]
    padding = bytes(len(template_id) - len(encoded_id))
    command = (
        command[:BUILDING_ID_OFFSET]
        + encoded_id
        + suffix
        + padding
    )

    planet_offset = BUILDING_ID_OFFSET + len(encoded_id)
    if command[planet_offset : planet_offset + 6] != bytes.fromhex(
        "fc2901001400"
    ):
        raise RuntimeError("Template planet tag is not at the expected offset.")
    planet_value_offset = planet_offset + PLANET_ID_OFFSET_FROM_BUILDING_END
    command[planet_value_offset : planet_value_offset + 4] = (
        planet_id.to_bytes(4, "little")
    )

    placement_tag_offset = planet_value_offset + 4
    if command[placement_tag_offset : placement_tag_offset + 6] != bytes.fromhex(
        "932b01001400"
    ):
        raise RuntimeError("Template placement tag is not at the expected offset.")
    placement_value_offset = placement_tag_offset + 6
    command[placement_value_offset : placement_value_offset + 4] = (
        placement.to_bytes(4, "little")
    )

    # The declared record length is one less than the total record length.
    if int.from_bytes(command[:2], "little") + 1 != len(command):
        raise RuntimeError("Construction command envelope length is invalid.")
    return bytes(command)


@dataclass
class StreamTranslator:
    injection_offset: int
    inserted_length: int

    def translate_outbound(self, payload: bytes) -> bytes:
        """Move local sender offsets after the inserted command."""
        if not is_reliable_packet(payload):
            return payload
        sender_offset = read_uint24_be(payload, 6)
        # The injector is armed at the current stream end. Packets at or after
        # that boundary belong after the inserted record.
        if is_at_or_after_uint24(sender_offset, self.injection_offset):
            return write_uint24_be(
                payload,
                6,
                sender_offset + self.inserted_length,
            )
        return payload

    def translate_inbound(self, payload: bytes) -> bytes:
        """Hide acknowledgements for inserted bytes from the local game."""
        if not is_reliable_packet(payload):
            return payload
        ack_offset = read_uint24_be(payload, 10)
        distance = forward_distance_uint24(ack_offset, self.injection_offset)
        if distance == 0 or distance >= UINT24_HALF_RANGE:
            return payload
        if distance < self.inserted_length:
            local_ack = self.injection_offset
        else:
            local_ack = ack_offset - self.inserted_length
        return write_uint24_be(payload, 10, local_ack)


@dataclass(frozen=True)
class CommandRecord:
    offset: int
    length: int
    serial: int


def find_command_records(data: bytes) -> list[CommandRecord]:
    """Locate complete Clausewitz command records in one byte range."""
    records: list[CommandRecord] = []
    marker_offset = data.find(COMMAND_COMMON_MARKER)
    while marker_offset >= 0:
        record_offset = marker_offset - 12
        if record_offset >= 0 and record_offset + 60 <= len(data):
            declared_length = int.from_bytes(
                data[record_offset : record_offset + 2],
                "little",
            )
            record_length = declared_length + 1
            record_end = record_offset + record_length
            if (
                60 <= record_length <= 512
                and record_end <= len(data)
                and data[record_offset + 2 : record_offset + 6]
                == COMMAND_ENVELOPE_TYPE
            ):
                records.append(
                    CommandRecord(
                        offset=record_offset,
                        length=record_length,
                        serial=int.from_bytes(
                            data[
                                record_offset
                                + COMMAND_SERIAL_OFFSET : record_offset
                                + COMMAND_SERIAL_OFFSET
                                + 2
                            ],
                            "little",
                        ),
                    )
                )
        marker_offset = data.find(COMMAND_COMMON_MARKER, marker_offset + 1)
    return records


class CommandSerialTranslator:
    """Shift every later nonzero local command serial by a fixed delta."""

    def __init__(self, delta: int = 1) -> None:
        self.delta = delta
        self.previous_tail = b""
        self.previous_end: int | None = None

    def rewrite(
        self,
        application_data: bytes,
        sender_offset: int,
    ) -> tuple[bytes, list[tuple[int, int]]]:
        contiguous = self.previous_end == sender_offset
        prefix = self.previous_tail if contiguous else b""
        combined = prefix + application_data
        current_start = len(prefix)
        output = bytearray(application_data)
        changes: list[tuple[int, int]] = []

        marker_offset = combined.find(COMMAND_COMMON_MARKER)
        while marker_offset >= 0:
            record_offset = marker_offset - 12
            serial_offset = record_offset + COMMAND_SERIAL_OFFSET
            relative_serial_offset = serial_offset - current_start
            if (
                record_offset >= 0
                and serial_offset >= current_start
                and relative_serial_offset + 2 <= len(output)
                and combined[record_offset + 2 : record_offset + 6]
                == COMMAND_ENVELOPE_TYPE
            ):
                serial = int.from_bytes(
                    combined[serial_offset : serial_offset + 2],
                    "little",
                )
                if serial != 0:
                    translated = (serial + self.delta) & 0xFFFF
                    output[
                        relative_serial_offset : relative_serial_offset + 2
                    ] = translated.to_bytes(2, "little")
                    changes.append((serial, translated))
            marker_offset = combined.find(
                COMMAND_COMMON_MARKER,
                marker_offset + 1,
            )

        # Sixty-four bytes cover the marker-to-serial distance and enough
        # preceding envelope bytes for a record split across two packets.
        self.previous_tail = combined[-64:]
        self.previous_end = (
            sender_offset + len(application_data)
        ) % UINT24_MODULUS
        return bytes(output), changes


@dataclass(frozen=True)
class LiveInjection:
    payload: bytes
    injection_offset: int
    command_serial: int
    translated_serials: list[tuple[int, int]]


def inject_before_live_command(
    payload: bytes,
    command_builder,
) -> LiveInjection | None:
    """Insert before a real command and shift that packet's later serials."""
    if not is_reliable_packet(payload) or len(payload) <= RELIABLE_HEADER_LENGTH:
        return None
    application_data = payload[RELIABLE_HEADER_LENGTH:]
    records = [record for record in find_command_records(application_data) if record.serial]
    if not records:
        return None
    first = records[0]
    command = command_builder(first.serial)
    serial_translator = CommandSerialTranslator(delta=1)
    prefix = application_data[: first.offset]
    suffix = application_data[first.offset :]
    translated_suffix, changes = serial_translator.rewrite(
        suffix,
        sender_offset=0,
    )
    if not changes or changes[0] != (first.serial, (first.serial + 1) & 0xFFFF):
        raise RuntimeError("The carrier command serial was not translated.")
    sender_offset = read_uint24_be(payload, 6)
    return LiveInjection(
        payload=(
            payload[:RELIABLE_HEADER_LENGTH]
            + prefix
            + command
            + translated_suffix
        ),
        injection_offset=(sender_offset + first.offset) % UINT24_MODULUS,
        command_serial=first.serial,
        translated_serials=changes,
    )


def inject_into_ack(payload: bytes, command: bytes) -> tuple[bytes, int]:
    """Turn one pure ACK into a data packet at the same stream boundary."""
    if not is_reliable_packet(payload):
        raise ValueError("Carrier is not a supported reliable packet.")
    if len(payload) != RELIABLE_HEADER_LENGTH:
        raise ValueError("Carrier is not a pure 25-byte ACK.")
    injection_offset = read_uint24_be(payload, 6)
    return payload + command, injection_offset


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def append_jsonl(path: Path, event: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def add_vendor_runtime() -> None:
    try:
        import pydivert  # noqa: F401

        return
    except ImportError:
        pass
    runtime = Path(__file__).resolve().parent / "vendor_runtime"
    if not runtime.is_dir():
        raise RuntimeError(f"Missing vendored PyDivert runtime: {runtime}")
    sys.path.insert(0, str(runtime))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote-ip", required=True)
    parser.add_argument("--remote-port", type=int, required=True)
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--local-port", type=int, required=True)
    parser.add_argument("--planet-id", type=int, default=3)
    parser.add_argument("--build-queue-id", type=int, default=6)
    parser.add_argument("--placement", type=int, default=0)
    parser.add_argument("--building-id", default="building_research_lab_1")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--session-seconds", type=int, default=7200)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    def command_builder(command_serial: int) -> bytes:
        return build_construction_command(
            command_serial=command_serial,
            build_queue_id=args.build_queue_id,
            building_id=args.building_id,
            planet_id=args.planet_id,
            placement=args.placement,
        )

    command = command_builder(0)
    add_vendor_runtime()
    import pydivert  # type: ignore[import-not-found]

    packet_filter = (
        "udp and ("
        f"(ip.SrcAddr == {args.local_ip} and ip.DstAddr == {args.remote_ip} "
        f"and udp.SrcPort == {args.local_port} "
        f"and udp.DstPort == {args.remote_port}) or "
        f"(ip.SrcAddr == {args.remote_ip} and ip.DstAddr == {args.local_ip} "
        f"and udp.SrcPort == {args.remote_port} "
        f"and udp.DstPort == {args.local_port}))"
    )
    start_event = {
        "event": "started",
        "timestamp": now_iso(),
        "pid": os.getpid(),
        "filter": packet_filter,
        "command_length": len(command),
        "command_sha256": sha256_hex(command),
        "command_serial": "learn_from_live_carrier",
        "planet_id": args.planet_id,
        "build_queue_id": args.build_queue_id,
        "placement": args.placement,
        "building_id": args.building_id,
        "dry_run": args.dry_run,
    }
    append_jsonl(args.log, start_event)
    args.ready_file.write_text(
        json.dumps(start_event, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    translator: StreamTranslator | None = None
    serial_translator: CommandSerialTranslator | None = None
    carrier_original: bytes | None = None
    carrier_output: bytes | None = None
    injected_at: float | None = None
    host_acknowledged = False
    deadline = time.monotonic() + args.timeout_seconds

    try:
        with pydivert.WinDivert(packet_filter) as divert:
            for packet in divert:
                original = packet.payload or b""
                outgoing = original
                direction = (
                    "outbound"
                    if packet.src_addr == args.local_ip
                    else "inbound"
                )

                try:
                    if (
                        translator is None
                        and direction == "outbound"
                        and len(original) <= 1300
                    ):
                        injection = inject_before_live_command(
                            original,
                            command_builder,
                        )
                        if injection is None:
                            divert.send(packet, recalculate_checksum=True)
                            continue
                        if not args.dry_run:
                            outgoing = injection.payload
                            packet.payload = outgoing
                            carrier_original = original
                            carrier_output = outgoing
                            translator = StreamTranslator(
                                injection_offset=injection.injection_offset,
                                inserted_length=len(command_builder(
                                    injection.command_serial
                                )),
                            )
                            serial_translator = CommandSerialTranslator(delta=1)
                            original_app = original[RELIABLE_HEADER_LENGTH:]
                            serial_translator.previous_tail = original_app[-64:]
                            serial_translator.previous_end = (
                                read_uint24_be(original, 6) + len(original_app)
                            ) % UINT24_MODULUS
                            injected_at = time.monotonic()
                        append_jsonl(
                            args.log,
                            {
                                "event": (
                                    "dry_run_carrier_observed"
                                    if args.dry_run
                                    else "command_injected"
                                ),
                                "timestamp": now_iso(),
                                "injection_offset": injection.injection_offset,
                                "inserted_length": len(command_builder(
                                    injection.command_serial
                                )),
                                "command_serial": injection.command_serial,
                                "translated_serials": injection.translated_serials,
                                "before_length": len(original),
                                "after_length": len(outgoing),
                                "before_sha256": sha256_hex(original),
                                "after_sha256": sha256_hex(outgoing),
                                "header_hex": original.hex(),
                            },
                        )
                        if args.dry_run:
                            divert.send(packet, recalculate_checksum=True)
                            return 0
                    elif translator is not None:
                        if direction == "outbound":
                            if (
                                carrier_original is not None
                                and carrier_output is not None
                                and original == carrier_original
                            ):
                                outgoing = carrier_output
                            elif is_reliable_packet(original):
                                sender_offset = read_uint24_be(original, 6)
                                app_data = original[RELIABLE_HEADER_LENGTH:]
                                translated_app, serial_changes = (
                                    serial_translator.rewrite(
                                        app_data,
                                        sender_offset,
                                    )
                                    if serial_translator is not None
                                    else (app_data, [])
                                )
                                outgoing = (
                                    original[:RELIABLE_HEADER_LENGTH]
                                    + translated_app
                                )
                                outgoing = translator.translate_outbound(outgoing)
                                if serial_changes:
                                    append_jsonl(
                                        args.log,
                                        {
                                            "event": "client_serials_translated",
                                            "timestamp": now_iso(),
                                            "sender_offset": sender_offset,
                                            "changes": serial_changes,
                                        },
                                    )
                            else:
                                outgoing = original
                        else:
                            remote_ack = (
                                read_uint24_be(original, 10)
                                if is_reliable_packet(original)
                                else None
                            )
                            outgoing = translator.translate_inbound(original)
                            if (
                                remote_ack is not None
                                and forward_distance_uint24(
                                    remote_ack,
                                    translator.injection_offset,
                                )
                                >= translator.inserted_length
                                and forward_distance_uint24(
                                    remote_ack,
                                    translator.injection_offset,
                                )
                                < UINT24_HALF_RANGE
                            ):
                                if not host_acknowledged:
                                    host_acknowledged = True
                                    append_jsonl(
                                        args.log,
                                        {
                                            "event": "host_acknowledged_injection",
                                            "timestamp": now_iso(),
                                            "remote_ack": remote_ack,
                                            "local_ack": read_uint24_be(
                                                outgoing,
                                                10,
                                            ),
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
                            "direction": direction,
                            "error": repr(error),
                            "payload_length": len(original),
                        },
                    )

                if translator is None and time.monotonic() >= deadline:
                    break
                if (
                    injected_at is not None
                    and time.monotonic() - injected_at >= args.session_seconds
                ):
                    break
    finally:
        append_jsonl(
            args.log,
            {
                "event": "stopped",
                "timestamp": now_iso(),
                "injected": translator is not None,
                "host_acknowledged": host_acknowledged,
            },
        )
        args.ready_file.unlink(missing_ok=True)

    return 0 if translator is not None and host_acknowledged else 2


if __name__ == "__main__":
    raise SystemExit(run())
