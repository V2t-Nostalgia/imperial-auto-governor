#!/usr/bin/env python3
"""Replace one naturally emitted Stellaris command with a build command.

Unlike stream insertion, this keeps all synchronization invariants stable:

- UDP payload length is unchanged.
- Reliable byte-stream offsets are unchanged.
- The number of application commands is unchanged.
- The real carrier command's local serial is retained.

The replacement only uses a complete nonzero command record at least as large
as the verified 153-byte construction record. Remaining bytes become zero
padding at the verified command boundary.
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

from iag_stream_command_injector import (
    RELIABLE_HEADER_LENGTH,
    build_construction_command,
    find_command_records,
    is_reliable_packet,
)

BUILD_COMMAND_TYPE = bytes.fromhex("433d01000300")
EA3F_TAG = bytes.fromhex("ea3f01001400")
BUILD_STRING_TAG = bytes.fromhex("413d01000300922b01000f00")
FC29_TAG = bytes.fromhex("fc2901001400")
PLACEMENT_TAG = bytes.fromhex("932b01001400")


def is_building_command(record_data: bytes) -> bool:
    """Accept only the verified planet building command family."""
    return (
        len(record_data) >= 100
        and record_data[6:12] == BUILD_COMMAND_TYPE
        and EA3F_TAG in record_data
        and BUILD_STRING_TAG in record_data
        and FC29_TAG in record_data
        and PLACEMENT_TAG in record_data
    )


def replace_one_command(
    payload: bytes,
    *,
    build_queue_id: int,
    building_id: str,
    planet_id: int,
    placement: int,
    allow_any_command: bool = False,
) -> tuple[bytes, dict[str, int]] | None:
    """Replace the first complete, nonzero, sufficiently large command."""
    if not is_reliable_packet(payload):
        return None
    application_data = payload[RELIABLE_HEADER_LENGTH:]
    for record in find_command_records(application_data):
        if record.serial == 0:
            continue
        record_data = application_data[
            record.offset : record.offset + record.length
        ]
        if not allow_any_command and not is_building_command(record_data):
            continue
        command = build_construction_command(
            command_serial=record.serial,
            build_queue_id=build_queue_id,
            building_id=building_id,
            planet_id=planet_id,
            placement=placement,
        )
        if record.length < len(command):
            continue

        record_end = record.offset + record.length
        padding = bytes(record.length - len(command))
        modified_application = (
            application_data[: record.offset]
            + command
            + padding
            + application_data[record_end:]
        )
        modified = payload[:RELIABLE_HEADER_LENGTH] + modified_application
        if len(modified) != len(payload):
            raise RuntimeError("Command replacement changed payload length.")
        if modified.count(building_id.encode("ascii")) != 1:
            raise RuntimeError("Replacement building ID count is not one.")
        return modified, {
            "carrier_offset": record.offset,
            "carrier_length": record.length,
            "carrier_serial": record.serial,
            "padding_length": len(padding),
        }
    return None


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
    parser.add_argument("--planet-id", type=int, default=3)
    parser.add_argument("--build-queue-id", type=int, default=6)
    parser.add_argument("--placement", type=int, default=0)
    parser.add_argument("--building-id", default="building_research_lab_1")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--observe-seconds", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-any-command", action="store_true")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    add_vendor_runtime()
    import pydivert  # type: ignore[import-not-found]

    packet_filter = (
        "udp and ("
        f"(ip.SrcAddr == {args.local_ip} and ip.DstAddr == {args.remote_ip} "
        f"and udp.SrcPort == {args.local_port} "
        f"and udp.DstPort == {args.remote_outbound_port}) or "
        f"(ip.SrcAddr == {args.remote_ip} and ip.DstAddr == {args.local_ip} "
        f"and udp.SrcPort == {args.remote_inbound_port} "
        f"and udp.DstPort == {args.local_port}))"
    )
    start_event = {
        "event": "started",
        "timestamp": now_iso(),
        "pid": os.getpid(),
        "filter": packet_filter,
        "planet_id": args.planet_id,
        "build_queue_id": args.build_queue_id,
        "placement": args.placement,
        "building_id": args.building_id,
        "dry_run": args.dry_run,
        "allow_any_command": args.allow_any_command,
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
    building_needle = args.building_id.encode("ascii")

    try:
        with pydivert.WinDivert(packet_filter) as divert:
            for packet in divert:
                original = packet.payload or b""
                outgoing = original
                is_outbound = packet.src_addr == args.local_ip

                try:
                    if not replaced and is_outbound:
                        result = replace_one_command(
                            original,
                            build_queue_id=args.build_queue_id,
                            building_id=args.building_id,
                            planet_id=args.planet_id,
                            placement=args.placement,
                            allow_any_command=args.allow_any_command,
                        )
                        if result is not None:
                            candidate, metadata = result
                            append_jsonl(
                                args.log,
                                {
                                    "event": (
                                        "dry_run_carrier_observed"
                                        if args.dry_run
                                        else "command_replaced"
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
                            outgoing = candidate
                            packet.payload = outgoing
                            replaced = True
                            replacement_time = time.monotonic()
                    elif (
                        replaced
                        and not is_outbound
                        and building_needle in original
                    ):
                        host_broadcast_seen = True
                        append_jsonl(
                            args.log,
                            {
                                "event": "host_building_broadcast_seen",
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
                    and time.monotonic() - replacement_time
                    >= args.observe_seconds
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
            },
        )
        args.ready_file.unlink(missing_ok=True)

    return 0 if replaced and host_broadcast_seen else 2


if __name__ == "__main__":
    raise SystemExit(run())
