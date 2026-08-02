#!/usr/bin/env python3
"""Replace one legal Stellaris zone construction command with another zone.

This is the same-family follow-up to the failed building -> zone experiment.
The tool waits for a real outbound zone/district construction command, extracts
its planet/build-queue/target-zone ids, and rewrites only the zone type while
preserving the carrier command record length and declared length.
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

from iag_building_to_zone_replacer import build_packet_filter
from iag_stream_command_injector import COMMAND_SERIAL_OFFSET, find_command_records

BUILD_HEAD = bytes.fromhex("612c0100140000000000")
EA3F_TAG = bytes.fromhex("ea3f01001400")
ZONE_STRING_HEAD = bytes.fromhex("174401000300932b01000f00")
ZONE_STRING_HEAD_CURRENT = bytes.fromhex("9a4401000300b32b01000f00")
FC29_TAG = bytes.fromhex("fc2901001400")
ZONE_TARGET_TAG = bytes.fromhex("942b01001400")
ZONE_RULE_TAG = bytes.fromhex("194401000c0001000000")
BUILD_TAIL = bytes.fromhex("0400040004000000")

COMMAND_ENVELOPE_LENGTH = 70
COMMAND_ENVELOPE_TYPE = bytes.fromhex("04000000")


@dataclass(frozen=True)
class ZoneCarrier:
    source_zone: str
    build_queue_id: int
    planet_id: int
    target_zone_id: int
    head_offset: int
    length_offset: int
    zone_offset: int
    zone_end: int


def build_zone_core(
    *,
    zone_type: str,
    build_queue_id: int,
    planet_id: int,
    target_zone_id: int,
) -> bytes:
    encoded_zone = zone_type.encode("ascii")
    return (
        BUILD_HEAD
        + EA3F_TAG
        + build_queue_id.to_bytes(4, "little")
        + ZONE_STRING_HEAD
        + len(encoded_zone).to_bytes(2, "little")
        + encoded_zone
        + FC29_TAG
        + planet_id.to_bytes(4, "little")
        + ZONE_TARGET_TAG
        + target_zone_id.to_bytes(4, "little")
        + ZONE_RULE_TAG
        + BUILD_TAIL
    )


def parse_zone_carrier(record_data: bytes) -> ZoneCarrier | None:
    if (
        len(record_data) < COMMAND_ENVELOPE_LENGTH
        or record_data[2:6] != COMMAND_ENVELOPE_TYPE
    ):
        return None
    if b"zone_" not in record_data or b"building_" in record_data:
        return None

    zone_head = ZONE_STRING_HEAD
    zone_head_offset = record_data.find(zone_head)
    if zone_head_offset < 0:
        zone_head = ZONE_STRING_HEAD_CURRENT
        zone_head_offset = record_data.find(zone_head)
    if zone_head_offset < 0:
        return None
    queue_tag_offset = record_data.rfind(EA3F_TAG, 0, zone_head_offset)
    queue_value_offset = queue_tag_offset + len(EA3F_TAG) if queue_tag_offset >= 0 else -1

    zone_length_offset = zone_head_offset + len(zone_head)
    if zone_length_offset + 2 > len(record_data):
        return None
    zone_length = int.from_bytes(record_data[zone_length_offset:zone_length_offset + 2], "little")
    zone_offset = zone_length_offset + 2
    zone_end = zone_offset + zone_length
    if zone_end > len(record_data):
        return None
    try:
        source_zone = record_data[zone_offset:zone_end].decode("ascii")
    except UnicodeDecodeError:
        return None
    if not source_zone.startswith("zone_"):
        return None

    planet_tag_offset = record_data.find(FC29_TAG, zone_end)
    target_tag_offset = record_data.find(ZONE_TARGET_TAG, zone_end)
    planet_value_offset = planet_tag_offset + len(FC29_TAG) if planet_tag_offset >= 0 else -1
    target_value_offset = target_tag_offset + len(ZONE_TARGET_TAG) if target_tag_offset >= 0 else -1

    return ZoneCarrier(
        source_zone=source_zone,
        build_queue_id=(
            int.from_bytes(record_data[queue_value_offset:queue_value_offset + 4], "little")
            if queue_value_offset >= 0 and queue_value_offset + 4 <= len(record_data)
            else -1
        ),
        planet_id=(
            int.from_bytes(record_data[planet_value_offset:planet_value_offset + 4], "little")
            if planet_value_offset >= 0 and planet_value_offset + 4 <= len(record_data)
            else -1
        ),
        target_zone_id=(
            int.from_bytes(record_data[target_value_offset:target_value_offset + 4], "little")
            if target_value_offset >= 0 and target_value_offset + 4 <= len(record_data)
            else -1
        ),
        head_offset=zone_head_offset,
        length_offset=zone_length_offset,
        zone_offset=zone_offset,
        zone_end=zone_end,
    )


def build_zone_record_from_zone_carrier(
    carrier_record: bytes,
    *,
    target_zone: str,
) -> tuple[bytes, dict[str, int | str]]:
    parsed = parse_zone_carrier(carrier_record)
    if parsed is None:
        raise ValueError("Carrier is not a parseable zone command.")

    target_encoded = target_zone.encode("ascii")
    replacement_segment = len(target_encoded).to_bytes(2, "little") + target_encoded
    source_segment_length = parsed.zone_end - parsed.length_offset
    new_record_without_padding = (
        carrier_record[:parsed.length_offset]
        + replacement_segment
        + carrier_record[parsed.zone_end:]
    )
    zone_record_length = len(new_record_without_padding)
    if zone_record_length > len(carrier_record):
        raise ValueError(
            "Target zone command does not fit in source zone carrier: "
            f"target_length={zone_record_length};carrier_length={len(carrier_record)}"
        )

    declared_record_length = len(carrier_record)
    record = bytearray(
        new_record_without_padding
        + bytes(len(carrier_record) - len(new_record_without_padding))
    )
    record[:2] = (declared_record_length - 1).to_bytes(2, "little")
    return bytes(record), {
        "source_zone": parsed.source_zone,
        "target_zone": target_zone,
        "build_queue_id": parsed.build_queue_id,
        "planet_id": parsed.planet_id,
        "target_zone_id": parsed.target_zone_id,
        "carrier_length": len(carrier_record),
        "zone_record_length": zone_record_length,
        "declared_record_length": declared_record_length,
        "padding_length": len(carrier_record) - len(new_record_without_padding),
        "source_segment_length": source_segment_length,
        "target_segment_length": len(replacement_segment),
        "carrier_serial": int.from_bytes(
            carrier_record[COMMAND_SERIAL_OFFSET:COMMAND_SERIAL_OFFSET + 2],
            "little",
        ),
    }


def replace_zone_command(
    payload: bytes,
    *,
    target_zone: str,
    source_zone: str = "",
) -> tuple[bytes, dict[str, int | str]] | None:
    for record in find_command_records(payload):
        if record.serial == 0:
            continue
        carrier_record = payload[record.offset:record.offset + record.length]
        parsed = parse_zone_carrier(carrier_record)
        if parsed is None:
            continue
        if source_zone and parsed.source_zone != source_zone:
            continue
        try:
            zone_record, metadata = build_zone_record_from_zone_carrier(
                carrier_record,
                target_zone=target_zone,
            )
        except ValueError:
            continue

        record_end = record.offset + record.length
        modified = payload[:record.offset] + zone_record + payload[record_end:]
        if len(modified) != len(payload):
            raise RuntimeError("Zone-to-zone rewrite changed payload length.")
        return modified, {
            **metadata,
            "carrier_offset": record.offset,
        }
    return None


def inspect_payload(payload: bytes, *, source_zone: str = "") -> dict[str, int]:
    command_records = find_command_records(payload)
    zone_commands = 0
    matching_zone_commands = 0
    for record in command_records:
        parsed = parse_zone_carrier(payload[record.offset:record.offset + record.length])
        if parsed is None:
            continue
        zone_commands += 1
        if not source_zone or parsed.source_zone == source_zone:
            matching_zone_commands += 1
    return {
        "command_records": len(command_records),
        "zone_commands": zone_commands,
        "matching_zone_commands": matching_zone_commands,
    }


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
    runtime = Path(__file__).resolve().parent / "vendor_runtime"
    if not runtime.is_dir():
        raise RuntimeError(f"Missing vendored PyDivert runtime: {runtime}")
    sys.path.insert(0, str(runtime))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote-ip", required=True)
    parser.add_argument("--remote-outbound-port", type=int, required=True)
    parser.add_argument("--remote-inbound-port", type=int, required=True)
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--local-port", type=int, required=True)
    parser.add_argument("--source-zone", default="")
    parser.add_argument("--target-zone", default="zone_trade")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--observe-seconds", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    add_vendor_runtime()
    import pydivert  # type: ignore[import-not-found]

    packet_filter = build_packet_filter(args)
    start_event = {
        "event": "started",
        "timestamp": now_iso(),
        "pid": os.getpid(),
        "filter": packet_filter,
        "source_zone": args.source_zone,
        "target_zone": args.target_zone,
        "dry_run": args.dry_run,
    }
    append_jsonl(args.log, start_event)
    args.ready_file.write_text(
        json.dumps(start_event, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    replaced = False
    replacement_time: float | None = None
    host_broadcast_seen = False
    deadline = time.monotonic() + args.timeout_seconds
    target_needle = args.target_zone.encode("ascii")
    packets_seen = 0
    outbound_packets_seen = 0
    inbound_packets_seen = 0
    command_records_seen = 0
    zone_commands_seen = 0
    matching_zone_commands_seen = 0
    first_diagnostic_logged = False

    try:
        with pydivert.WinDivert(packet_filter) as divert:
            for packet in divert:
                original = packet.payload or b""
                is_outbound = packet.src_addr == args.local_ip
                packets_seen += 1
                if is_outbound:
                    outbound_packets_seen += 1
                    diagnostic = inspect_payload(
                        original,
                        source_zone=args.source_zone,
                    )
                    command_records_seen += diagnostic["command_records"]
                    zone_commands_seen += diagnostic["zone_commands"]
                    matching_zone_commands_seen += diagnostic["matching_zone_commands"]
                    if not first_diagnostic_logged and (
                        diagnostic["command_records"] or diagnostic["zone_commands"]
                    ):
                        first_diagnostic_logged = True
                        append_jsonl(
                            args.log,
                            {
                                "event": "first_outbound_diagnostic",
                                "timestamp": now_iso(),
                                **diagnostic,
                                "src": f"{packet.src_addr}:{packet.src_port}",
                                "dst": f"{packet.dst_addr}:{packet.dst_port}",
                                "payload_length": len(original),
                            },
                        )
                else:
                    inbound_packets_seen += 1

                try:
                    if not replaced and is_outbound:
                        result = replace_zone_command(
                            original,
                            target_zone=args.target_zone,
                            source_zone=args.source_zone,
                        )
                        if result is not None:
                            candidate, metadata = result
                            append_jsonl(
                                args.log,
                                {
                                    "event": (
                                        "dry_run_zone_carrier_observed"
                                        if args.dry_run
                                        else "zone_to_zone_replaced"
                                    ),
                                    "timestamp": now_iso(),
                                    **metadata,
                                    "payload_length": len(original),
                                    "before_sha256": sha256_hex(original),
                                    "after_sha256": sha256_hex(
                                        original if args.dry_run else candidate
                                    ),
                                },
                            )
                            if args.dry_run:
                                divert.send(packet, recalculate_checksum=True)
                                return 0
                            packet.payload = candidate
                            replaced = True
                            replacement_time = time.monotonic()
                    elif replaced and not is_outbound and target_needle in original:
                        host_broadcast_seen = True
                        append_jsonl(
                            args.log,
                            {
                                "event": "host_target_zone_broadcast_seen",
                                "timestamp": now_iso(),
                                "payload_length": len(original),
                                "payload_sha256": sha256_hex(original),
                            },
                        )

                    divert.send(packet, recalculate_checksum=True)
                except Exception as error:
                    packet.payload = original
                    divert.send(packet, recalculate_checksum=True)
                    append_jsonl(
                        args.log,
                        {
                            "event": "packet_error_original_forwarded",
                            "timestamp": now_iso(),
                            "error": repr(error),
                        },
                    )

                if (
                    replacement_time is not None
                    and time.monotonic() - replacement_time >= args.observe_seconds
                ):
                    break
                if not replaced and time.monotonic() >= deadline:
                    break
    finally:
        append_jsonl(
            args.log,
            {
                "event": "stopped",
                "timestamp": now_iso(),
                "replaced": replaced,
                "host_broadcast_seen": host_broadcast_seen,
                "packets_seen": packets_seen,
                "outbound_packets_seen": outbound_packets_seen,
                "inbound_packets_seen": inbound_packets_seen,
                "command_records_seen": command_records_seen,
                "zone_commands_seen": zone_commands_seen,
                "matching_zone_commands_seen": matching_zone_commands_seen,
            },
        )
        args.ready_file.unlink(missing_ok=True)

    return 0 if replaced and host_broadcast_seen else 2


if __name__ == "__main__":
    raise SystemExit(run())
