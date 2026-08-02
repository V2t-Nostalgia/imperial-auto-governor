#!/usr/bin/env python3
"""Rewrite a live zone construction command using a captured target profile.

This tool is intentionally narrower than a generic packet editor. It waits for
a real outbound zone construction command, keeps that command's transport and
simulation envelope, then copies only the zone business fields observed in a
real target zone sample.
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
from iag_zone_to_zone_replacer import parse_zone_carrier

PRE_ZONE_CONTEXT_TAGS = [
    bytes.fromhex("822c01001400"),
    bytes.fromhex("5b4001001400"),
]

POST_ZONE_CONTEXT_TAGS = [
    bytes.fromhex("132a01001400"),
    bytes.fromhex("b42b01001400"),
    bytes.fromhex("9c4401000c00"),
]


@dataclass(frozen=True)
class ZoneProfile:
    target_zone: str
    pre_values: dict[str, bytes]
    post_values: dict[str, bytes]
    source_length: int
    source_serial: int


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


def _tag_name(tag: bytes) -> str:
    return tag.hex()


def _read_tag_value(record: bytes, tag: bytes, *, start: int, end: int) -> bytes | None:
    offset = record.find(tag, start, end)
    if offset < 0:
        return None
    value_offset = offset + len(tag)
    if value_offset + 4 > len(record):
        return None
    return record[value_offset:value_offset + 4]


def _write_tag_value(record: bytearray, tag: bytes, value: bytes, *, start: int, end: int) -> bool:
    offset = bytes(record).find(tag, start, end)
    if offset < 0:
        return False
    value_offset = offset + len(tag)
    if value_offset + 4 > len(record):
        return False
    record[value_offset:value_offset + 4] = value
    return True


def load_target_profile_from_record(record: bytes) -> ZoneProfile:
    parsed = parse_zone_carrier(record)
    if parsed is None:
        raise ValueError("Target profile record is not a parseable zone construction command.")

    pre_values: dict[str, bytes] = {}
    for tag in PRE_ZONE_CONTEXT_TAGS:
        value = _read_tag_value(record, tag, start=0, end=parsed.head_offset)
        if value is not None:
            pre_values[_tag_name(tag)] = value

    post_values: dict[str, bytes] = {}
    for tag in POST_ZONE_CONTEXT_TAGS:
        value = _read_tag_value(record, tag, start=parsed.zone_end, end=len(record))
        if value is not None:
            post_values[_tag_name(tag)] = value

    return ZoneProfile(
        target_zone=parsed.source_zone,
        pre_values=pre_values,
        post_values=post_values,
        source_length=len(record),
        source_serial=int.from_bytes(
            record[COMMAND_SERIAL_OFFSET:COMMAND_SERIAL_OFFSET + 2],
            "little",
        ),
    )


def load_record_from_records_json(path: Path, index: int) -> bytes:
    records = json.loads(path.read_text(encoding="utf-8"))
    try:
        item = records[index]
    except IndexError as exc:
        raise ValueError(f"Record index {index} is outside {path}.") from exc
    return bytes.fromhex(item["hex"])


def build_profiled_zone_record(
    carrier_record: bytes,
    *,
    profile: ZoneProfile,
    source_zone: str = "",
) -> tuple[bytes, dict[str, object]]:
    parsed = parse_zone_carrier(carrier_record)
    if parsed is None:
        raise ValueError("Carrier is not a parseable zone construction command.")
    if source_zone and parsed.source_zone != source_zone:
        raise ValueError(f"Carrier source zone mismatch: {parsed.source_zone!r}.")

    target_encoded = profile.target_zone.encode("ascii")
    replacement_segment = len(target_encoded).to_bytes(2, "little") + target_encoded
    new_without_padding = (
        carrier_record[:parsed.length_offset]
        + replacement_segment
        + carrier_record[parsed.zone_end:]
    )
    if len(new_without_padding) > len(carrier_record):
        raise ValueError(
            "Target zone profile does not fit in source carrier: "
            f"target_length={len(new_without_padding)};carrier_length={len(carrier_record)}"
        )

    record = bytearray(new_without_padding + bytes(len(carrier_record) - len(new_without_padding)))
    record[:2] = (len(carrier_record) - 1).to_bytes(2, "little")
    # Preserve the live carrier serial. The host must see this as the command
    # the local client actually predicted, not as the old sample's command.
    record[COMMAND_SERIAL_OFFSET:COMMAND_SERIAL_OFFSET + 2] = carrier_record[
        COMMAND_SERIAL_OFFSET:COMMAND_SERIAL_OFFSET + 2
    ]

    new_parsed = parse_zone_carrier(bytes(record))
    if new_parsed is None:
        raise ValueError("Profiled zone record became unparsable after rewrite.")

    pre_written = 0
    for tag in PRE_ZONE_CONTEXT_TAGS:
        value = profile.pre_values.get(_tag_name(tag))
        if value is not None and _write_tag_value(record, tag, value, start=0, end=new_parsed.head_offset):
            pre_written += 1

    post_written = 0
    for tag in POST_ZONE_CONTEXT_TAGS:
        value = profile.post_values.get(_tag_name(tag))
        if value is not None and _write_tag_value(
            record,
            tag,
            value,
            start=new_parsed.zone_end,
            end=len(record),
        ):
            post_written += 1

    return bytes(record), {
        "source_zone": parsed.source_zone,
        "target_zone": profile.target_zone,
        "carrier_length": len(carrier_record),
        "target_profile_length": profile.source_length,
        "declared_record_length": len(carrier_record),
        "profile_source_serial": profile.source_serial,
        "carrier_serial": int.from_bytes(
            carrier_record[COMMAND_SERIAL_OFFSET:COMMAND_SERIAL_OFFSET + 2],
            "little",
        ),
        "padding_length": len(carrier_record) - len(new_without_padding),
        "pre_context_fields_written": pre_written,
        "post_context_fields_written": post_written,
        "profile_pre_values": {k: v.hex() for k, v in profile.pre_values.items()},
        "profile_post_values": {k: v.hex() for k, v in profile.post_values.items()},
    }


def replace_zone_command_with_profile(
    payload: bytes,
    *,
    profile: ZoneProfile,
    source_zone: str = "",
) -> tuple[bytes, dict[str, object]] | None:
    for command in find_command_records(payload):
        if command.serial == 0:
            continue
        carrier_record = payload[command.offset:command.offset + command.length]
        parsed = parse_zone_carrier(carrier_record)
        if parsed is None:
            continue
        if source_zone and parsed.source_zone != source_zone:
            continue
        try:
            profiled_record, metadata = build_profiled_zone_record(
                carrier_record,
                profile=profile,
                source_zone=source_zone,
            )
        except ValueError:
            continue
        modified = payload[:command.offset] + profiled_record + payload[command.offset + command.length:]
        if len(modified) != len(payload):
            raise RuntimeError("Zone profile rewrite changed payload length.")
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
    parser.add_argument("--source-zone", required=True)
    parser.add_argument("--target-record-json", type=Path, required=True)
    parser.add_argument("--target-record-index", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--observe-seconds", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    target_record = load_record_from_records_json(args.target_record_json, args.target_record_index)
    profile = load_target_profile_from_record(target_record)
    add_vendor_runtime()
    import pydivert  # type: ignore[import-not-found]

    packet_filter = build_packet_filter(args)
    start_event = {
        "event": "started",
        "timestamp": now_iso(),
        "pid": os.getpid(),
        "filter": packet_filter,
        "source_zone": args.source_zone,
        "target_zone": profile.target_zone,
        "target_record_json": str(args.target_record_json),
        "target_record_index": args.target_record_index,
        "target_profile_length": profile.source_length,
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
    target_needle = profile.target_zone.encode("ascii")

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
                        result = replace_zone_command_with_profile(
                            original,
                            profile=profile,
                            source_zone=args.source_zone,
                        )
                        if result is not None:
                            candidate, metadata = result
                            append_jsonl(
                                args.log,
                                {
                                    "event": (
                                        "dry_run_zone_profile_carrier_observed"
                                        if args.dry_run
                                        else "zone_profile_replaced"
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
