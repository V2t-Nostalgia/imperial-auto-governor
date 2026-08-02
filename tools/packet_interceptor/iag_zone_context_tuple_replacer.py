#!/usr/bin/env python3
"""Rewrite a live zone command's target-context tuple.

This experimental tool is for testing cross-planet zone redirection after clean
paired samples proved the candidate tuple for Earth and Alpha Centauri I.

It changes only:

- optional zone string segment;
- the 4-byte values after selected zone-context tags.

It preserves the live carrier serial, command count, declared record length,
UDP payload length, and all unselected fields.
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

CONTEXT_TAGS = {
    "822c": bytes.fromhex("822c01001400"),
    "5b40": bytes.fromhex("5b4001001400"),
    "132a": bytes.fromhex("132a01001400"),
    "b42b": bytes.fromhex("b42b01001400"),
    "9c44": bytes.fromhex("9c4401000c00"),
}


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


def u32_to_bytes(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"u32 out of range: {value}")
    return value.to_bytes(4, "little")


def parse_context_args(args: argparse.Namespace) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for name in CONTEXT_TAGS:
        value = getattr(args, f"context_{name}")
        if value is not None:
            result[name] = u32_to_bytes(value)
    return result


def find_tag_value(record: bytes, tag: bytes, *, start: int, end: int) -> tuple[int, bytes] | None:
    offset = record.find(tag, start, end)
    if offset < 0:
        return None
    value_offset = offset + len(tag)
    if value_offset + 4 > len(record):
        return None
    return value_offset, record[value_offset:value_offset + 4]


def rewrite_zone_context_record(
    carrier_record: bytes,
    *,
    source_zone: str,
    target_zone: str,
    target_context: dict[str, bytes],
) -> tuple[bytes, dict[str, object]]:
    parsed = parse_zone_carrier(carrier_record)
    if parsed is None:
        raise ValueError("Carrier is not a parseable zone construction command.")
    if parsed.source_zone != source_zone:
        raise ValueError(f"Carrier source zone mismatch: {parsed.source_zone!r}.")

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

    reparsed = parse_zone_carrier(bytes(record))
    if reparsed is None:
        raise ValueError("Rewritten record became unparsable.")

    changes: dict[str, dict[str, object]] = {}
    for name, value in target_context.items():
        tag = CONTEXT_TAGS[name]
        # 822c and 5b40 live before the zone string; 132a/b42b/9c44 live after it.
        search_start = 0 if name in {"822c", "5b40"} else reparsed.zone_end
        found = find_tag_value(bytes(record), tag, start=search_start, end=len(record))
        if found is None:
            raise ValueError(f"Context tag {name} not found in carrier.")
        value_offset, before = found
        record[value_offset:value_offset + 4] = value
        changes[name] = {
            "value_offset": value_offset,
            "before_hex": before.hex(),
            "after_hex": value.hex(),
            "before_u32": int.from_bytes(before, "little"),
            "after_u32": int.from_bytes(value, "little"),
        }

    final_parsed = parse_zone_carrier(bytes(record))
    if final_parsed is None or final_parsed.source_zone != target_zone:
        raise ValueError("Final record is not parseable as target zone.")

    return bytes(record), {
        "source_zone": source_zone,
        "target_zone": target_zone,
        "carrier_length": len(carrier_record),
        "declared_record_length": len(carrier_record),
        "carrier_serial": int.from_bytes(
            carrier_record[COMMAND_SERIAL_OFFSET:COMMAND_SERIAL_OFFSET + 2],
            "little",
        ),
        "padding_length": len(carrier_record) - len(new_without_padding),
        "context_changes": changes,
        "before_record_hex": carrier_record.hex(),
        "after_record_hex": bytes(record).hex(),
    }


def replace_payload(
    payload: bytes,
    *,
    source_zone: str,
    target_zone: str,
    target_context: dict[str, bytes],
) -> tuple[bytes, dict[str, object]] | None:
    for command in find_command_records(payload):
        if command.serial == 0:
            continue
        carrier_record = payload[command.offset:command.offset + command.length]
        parsed = parse_zone_carrier(carrier_record)
        if parsed is None or parsed.source_zone != source_zone:
            continue
        try:
            replacement, metadata = rewrite_zone_context_record(
                carrier_record,
                source_zone=source_zone,
                target_zone=target_zone,
                target_context=target_context,
            )
        except ValueError:
            continue
        modified = payload[:command.offset] + replacement + payload[command.offset + command.length:]
        if len(modified) != len(payload):
            raise RuntimeError("Zone context tuple rewrite changed payload length.")
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
    parser.add_argument("--target-zone", default="zone_research_physics")
    parser.add_argument("--context-822c", type=int, default=None)
    parser.add_argument("--context-5b40", type=int, default=None)
    parser.add_argument("--context-132a", type=int, default=None)
    parser.add_argument("--context-b42b", type=int, default=None)
    parser.add_argument("--context-9c44", type=int, default=None)
    parser.add_argument(
        "--rewrite-direction",
        choices=("outbound", "inbound", "both"),
        default="outbound",
        help=(
            "Which matching direction may be rewritten. Use inbound when this "
            "machine is the host and the co-op player sends commands to it."
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--observe-seconds", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    target_context = parse_context_args(args)
    if not target_context:
        raise SystemExit("No context fields selected.")
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
        "target_context": {k: int.from_bytes(v, "little") for k, v in target_context.items()},
        "rewrite_direction": args.rewrite_direction,
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
                rewrite_this_direction = (
                    args.rewrite_direction == "both"
                    or (args.rewrite_direction == "outbound" and is_outbound)
                    or (args.rewrite_direction == "inbound" and not is_outbound)
                )
                packets_seen += 1
                if is_outbound:
                    outbound_packets_seen += 1
                else:
                    inbound_packets_seen += 1
                if rewrite_this_direction:
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
                                "event": "first_rewrite_direction_diagnostic",
                                "timestamp": now_iso(),
                                "direction": "outbound" if is_outbound else "inbound",
                                **diagnostic,
                                "src": f"{packet.src_addr}:{packet.src_port}",
                                "dst": f"{packet.dst_addr}:{packet.dst_port}",
                                "payload_length": len(original),
                            },
                        )

                try:
                    if not replaced and rewrite_this_direction:
                        result = replace_payload(
                            original,
                            source_zone=args.source_zone,
                            target_zone=args.target_zone,
                            target_context=target_context,
                        )
                        if result is not None:
                            candidate, metadata = result
                            append_jsonl(
                                args.log,
                                {
                                    "event": (
                                        "dry_run_zone_context_tuple_observed"
                                        if args.dry_run
                                        else "zone_context_tuple_replaced"
                                    ),
                                    "timestamp": now_iso(),
                                    "direction": "outbound" if is_outbound else "inbound",
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
