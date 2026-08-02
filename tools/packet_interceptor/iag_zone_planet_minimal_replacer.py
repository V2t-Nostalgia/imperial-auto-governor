#!/usr/bin/env python3
"""Minimal live test for zone type plus one suspected planet/container field.

This is deliberately not a profile copier. It changes only:

1. the zone string segment, using the proven same-family zone rewrite method;
2. the four bytes after tag 5b4001001400, currently the strongest zone-side
   planet/container candidate.

All other build queue and slot-context fields are preserved from the live
carrier, because copying them caused BUILD_QUEUE OOS in testing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from iag_building_to_zone_replacer import build_packet_filter
from iag_stream_command_injector import COMMAND_SERIAL_OFFSET, find_command_records
from iag_zone_to_zone_replacer import parse_zone_carrier

ZONE_PLANET_CANDIDATE_TAG = bytes.fromhex("5b4001001400")


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


def parse_u32_hex(value: str) -> bytes:
    cleaned = value.replace(" ", "").replace("_", "")
    raw = bytes.fromhex(cleaned)
    if len(raw) != 4:
        raise ValueError("--target-planet-field-hex must be exactly four bytes.")
    return raw


def replace_zone_and_planet_candidate(
    carrier_record: bytes,
    *,
    source_zone: str,
    target_zone: str,
    target_planet_field: bytes,
) -> tuple[bytes, dict[str, object]]:
    parsed = parse_zone_carrier(carrier_record)
    if parsed is None:
        raise ValueError("Carrier is not a parseable zone construction command.")
    if parsed.source_zone != source_zone:
        raise ValueError(f"Carrier source zone mismatch: {parsed.source_zone!r}.")

    tag_offset = carrier_record.find(ZONE_PLANET_CANDIDATE_TAG, 0, parsed.head_offset)
    if tag_offset < 0:
        raise ValueError("Carrier does not contain the 5b40 planet candidate tag.")
    value_offset = tag_offset + len(ZONE_PLANET_CANDIDATE_TAG)
    if value_offset + 4 > len(carrier_record):
        raise ValueError("Carrier has an incomplete 5b40 value.")
    original_planet_field = carrier_record[value_offset:value_offset + 4]

    target_encoded = target_zone.encode("ascii")
    replacement_segment = len(target_encoded).to_bytes(2, "little") + target_encoded
    new_without_padding = (
        carrier_record[:parsed.length_offset]
        + replacement_segment
        + carrier_record[parsed.zone_end:]
    )
    if len(new_without_padding) > len(carrier_record):
        raise ValueError(
            "Target zone does not fit in source carrier: "
            f"target_length={len(new_without_padding)};carrier_length={len(carrier_record)}"
        )

    record = bytearray(new_without_padding + bytes(len(carrier_record) - len(new_without_padding)))
    record[:2] = (len(carrier_record) - 1).to_bytes(2, "little")
    record[COMMAND_SERIAL_OFFSET:COMMAND_SERIAL_OFFSET + 2] = carrier_record[
        COMMAND_SERIAL_OFFSET:COMMAND_SERIAL_OFFSET + 2
    ]
    # The tag is before the variable-length zone string, so its offset is stable.
    record[value_offset:value_offset + 4] = target_planet_field

    reparsed = parse_zone_carrier(bytes(record))
    if reparsed is None or reparsed.source_zone != target_zone:
        raise ValueError("Rewritten zone record is not parseable as the target zone.")

    return bytes(record), {
        "source_zone": source_zone,
        "target_zone": target_zone,
        "carrier_length": len(carrier_record),
        "declared_record_length": len(carrier_record),
        "carrier_serial": int.from_bytes(
            carrier_record[COMMAND_SERIAL_OFFSET:COMMAND_SERIAL_OFFSET + 2],
            "little",
        ),
        "planet_candidate_tag_offset": tag_offset,
        "planet_candidate_value_offset": value_offset,
        "source_planet_candidate_hex": original_planet_field.hex(),
        "target_planet_candidate_hex": target_planet_field.hex(),
        "padding_length": len(carrier_record) - len(new_without_padding),
        "before_record_hex": carrier_record.hex(),
        "after_record_hex": bytes(record).hex(),
    }


def replace_payload(
    payload: bytes,
    *,
    source_zone: str,
    target_zone: str,
    target_planet_field: bytes,
) -> tuple[bytes, dict[str, object]] | None:
    for command in find_command_records(payload):
        if command.serial == 0:
            continue
        carrier_record = payload[command.offset:command.offset + command.length]
        parsed = parse_zone_carrier(carrier_record)
        if parsed is None or parsed.source_zone != source_zone:
            continue
        try:
            replacement, metadata = replace_zone_and_planet_candidate(
                carrier_record,
                source_zone=source_zone,
                target_zone=target_zone,
                target_planet_field=target_planet_field,
            )
        except ValueError:
            continue
        modified = payload[:command.offset] + replacement + payload[command.offset + command.length:]
        if len(modified) != len(payload):
            raise RuntimeError("Minimal zone planet rewrite changed payload length.")
        return modified, {**metadata, "carrier_offset": command.offset}
    return None


def inspect_payload(payload: bytes, *, source_zone: str) -> dict[str, int]:
    command_records = find_command_records(payload)
    zone_commands = 0
    matching_zone_commands = 0
    for command in command_records:
        parsed = parse_zone_carrier(payload[command.offset:command.offset + command.length])
        if parsed is None:
            continue
        zone_commands += 1
        if parsed.source_zone == source_zone:
            matching_zone_commands += 1
    return {
        "command_records": len(command_records),
        "zone_commands": zone_commands,
        "matching_zone_commands": matching_zone_commands,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote-ip", required=True)
    parser.add_argument("--remote-outbound-port", type=int, required=True)
    parser.add_argument("--remote-inbound-port", type=int, required=True)
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--local-port", type=int, required=True)
    parser.add_argument("--source-zone", default="zone_research_physics")
    parser.add_argument("--target-zone", default="zone_trade")
    parser.add_argument("--target-planet-field-hex", default="00000000")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--observe-seconds", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    target_planet_field = parse_u32_hex(args.target_planet_field_hex)
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
        "target_planet_field_hex": target_planet_field.hex(),
        "dry_run": args.dry_run,
    }
    append_jsonl(args.log, start_event)
    args.ready_file.write_text(json.dumps(start_event, ensure_ascii=False, indent=2), encoding="utf-8")

    replaced = False
    replacement_time: float | None = None
    host_broadcast_seen = False
    packets_seen = 0
    outbound_packets_seen = 0
    inbound_packets_seen = 0
    command_records_seen = 0
    zone_commands_seen = 0
    matching_zone_commands_seen = 0
    first_diagnostic_logged = False
    deadline = time.monotonic() + args.timeout_seconds
    target_needle = args.target_zone.encode("ascii")

    try:
        with pydivert.WinDivert(packet_filter) as divert:
            for packet in divert:
                original = packet.payload or b""
                is_outbound = packet.src_addr == args.local_ip
                packets_seen += 1
                if is_outbound:
                    outbound_packets_seen += 1
                    diagnostic = inspect_payload(original, source_zone=args.source_zone)
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
                        result = replace_payload(
                            original,
                            source_zone=args.source_zone,
                            target_zone=args.target_zone,
                            target_planet_field=target_planet_field,
                        )
                        if result is not None:
                            candidate, metadata = result
                            append_jsonl(
                                args.log,
                                {
                                    "event": (
                                        "dry_run_zone_planet_candidate_observed"
                                        if args.dry_run
                                        else "zone_planet_candidate_replaced"
                                    ),
                                    "timestamp": now_iso(),
                                    **metadata,
                                    "payload_length": len(original),
                                    "before_sha256": sha256_hex(original),
                                    "after_sha256": sha256_hex(original if args.dry_run else candidate),
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

                if replacement_time is not None and time.monotonic() - replacement_time >= args.observe_seconds:
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
