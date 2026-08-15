#!/usr/bin/env python3
"""Replace one legal building construction command with a zone command.

This is a narrow experiment for the authorized Stellaris co-op test session.
It does not insert bytes into the reliable stream. Instead it waits for a real
building command, keeps that command's transport envelope and local serial, and
rewrites only the application command body to a zone construction body when the
new record fits inside the original command record.

Why this exists separately from iag_command_replacement_injector.py:

- building -> building has already been verified.
- arbitrary command -> building caused OOS.
- building -> zone is a different command family and needs its own evidence.
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

from iag.stellaris.execution.packet.iag_stream_command_injector import (
    COMMAND_SERIAL_OFFSET,
    find_command_records,
)

ZONE_822C_TAG = bytes.fromhex("822c01001400")
ZONE_5B40_TAG = bytes.fromhex("5b4001001400")
ZONE_STRING_HEAD_CURRENT = bytes.fromhex("9a4401000300b32b01000f00")
ZONE_132A_TAG = bytes.fromhex("132a01001400")
ZONE_B42B_TAG = bytes.fromhex("b42b01001400")
ZONE_9C44_TAG = bytes.fromhex("9c4401000c00")
ZONE_TAIL_CURRENT = bytes.fromhex("040004000400")

COMMAND_ENVELOPE_LENGTH = 70
COMMAND_ENVELOPE_TYPE = bytes.fromhex("04000000")


def is_building_carrier_command(record_data: bytes) -> bool:
    """Accept complete construction commands carrying a building ID string.

    Stellaris 4.x can emit planet building records with different command type
    ids across sessions/actions. The stable part for this experiment is the
    complete command envelope plus the ASCII building id field.
    """
    return (
        len(record_data) >= 100
        and record_data[2:6] == COMMAND_ENVELOPE_TYPE
        and b"building_" in record_data
        and b"zone_" not in record_data
    )


def build_zone_core(
    *,
    zone_type: str,
    build_queue_id: int,
    colony_id: int,
    district_id: int,
    slot_selector: int,
    context_822c: int = 0,
) -> bytes:
    encoded_zone = zone_type.encode("ascii")
    return (
        ZONE_822C_TAG
        + context_822c.to_bytes(4, "little")
        + ZONE_5B40_TAG
        + build_queue_id.to_bytes(4, "little")
        + ZONE_STRING_HEAD_CURRENT
        + len(encoded_zone).to_bytes(2, "little")
        + encoded_zone
        + ZONE_132A_TAG
        + colony_id.to_bytes(4, "little")
        + ZONE_B42B_TAG
        + district_id.to_bytes(4, "little")
        + ZONE_9C44_TAG
        + slot_selector.to_bytes(4, "little")
        + ZONE_TAIL_CURRENT
    )


def build_zone_record_from_building_carrier(
    carrier_record: bytes,
    *,
    zone_type: str,
    build_queue_id: int,
    colony_id: int,
    district_id: int,
    slot_selector: int,
    context_822c: int = 0,
    preserve_carrier_record_length: bool = True,
) -> tuple[bytes, dict[str, int]]:
    """Create a zone command inside a previously emitted building record."""
    if len(carrier_record) < COMMAND_ENVELOPE_LENGTH:
        raise ValueError("Carrier command is shorter than its command envelope.")
    if carrier_record[2:6] != COMMAND_ENVELOPE_TYPE:
        raise ValueError("Carrier command envelope type is not a command record.")

    prefix = bytearray(carrier_record[:COMMAND_ENVELOPE_LENGTH])
    zone_core = build_zone_core(
        zone_type=zone_type,
        build_queue_id=build_queue_id,
        colony_id=colony_id,
        district_id=district_id,
        slot_selector=slot_selector,
        context_822c=context_822c,
    )
    zone_record_length = COMMAND_ENVELOPE_LENGTH + len(zone_core)
    if zone_record_length > len(carrier_record):
        raise ValueError(
            "Zone command does not fit in the building carrier: "
            f"zone_record_length={zone_record_length};"
            f"carrier_length={len(carrier_record)}"
        )

    declared_record_length = (
        len(carrier_record)
        if preserve_carrier_record_length
        else zone_record_length
    )

    # The command record length field is one less than the record byte count.
    # Live multiplayer replacement should preserve the carrier's declared
    # command length as well as the UDP payload length. The earlier
    # boundary-padding variant shortened this field by one byte and caused a
    # time stall in live testing.
    prefix[:2] = (declared_record_length - 1).to_bytes(2, "little")
    record = bytes(prefix) + zone_core
    padding = bytes(len(carrier_record) - len(record))
    return record + padding, {
        "carrier_length": len(carrier_record),
        "zone_record_length": zone_record_length,
        "declared_record_length": declared_record_length,
        "padding_length": len(padding),
        "carrier_serial": int.from_bytes(
            carrier_record[
                COMMAND_SERIAL_OFFSET : COMMAND_SERIAL_OFFSET + 2
            ],
            "little",
        ),
        "context_822c": context_822c,
        "context_5b40": build_queue_id,
        "context_132a": colony_id,
        "context_b42b": district_id,
        "context_9c44": slot_selector,
    }


def replace_building_command_with_zone(
    payload: bytes,
    *,
    zone_type: str,
    build_queue_id: int,
    colony_id: int,
    district_id: int,
    slot_selector: int,
    context_822c: int = 0,
) -> tuple[bytes, dict[str, int]] | None:
    for record in find_command_records(payload):
        if record.serial == 0:
            continue
        carrier_record = payload[record.offset : record.offset + record.length]
        if not is_building_carrier_command(carrier_record):
            continue
        try:
            zone_record, metadata = build_zone_record_from_building_carrier(
                carrier_record,
                zone_type=zone_type,
                build_queue_id=build_queue_id,
                colony_id=colony_id,
                district_id=district_id,
                slot_selector=slot_selector,
                context_822c=context_822c,
            )
        except ValueError:
            continue

        record_end = record.offset + record.length
        modified = (
            payload[: record.offset]
            + zone_record
            + payload[record_end:]
        )
        if len(modified) != len(payload):
            raise RuntimeError("Building-to-zone rewrite changed payload length.")
        if zone_type.encode("ascii") not in modified:
            raise RuntimeError("Rewritten payload does not contain target zone type.")
        return modified, {
            **metadata,
            "carrier_offset": record.offset,
        }
    return None


def inspect_payload(
    payload: bytes,
    *,
    zone_type: str,
    build_queue_id: int,
    colony_id: int,
    district_id: int,
    slot_selector: int,
    context_822c: int = 0,
) -> dict[str, int]:
    """Return lightweight diagnostics for a packet without modifying it."""
    command_records = find_command_records(payload)
    building_commands = 0
    fitting_building_commands = 0
    for record in command_records:
        if record.serial == 0:
            continue
        carrier_record = payload[record.offset : record.offset + record.length]
        if not is_building_carrier_command(carrier_record):
            continue
        building_commands += 1
        try:
            build_zone_record_from_building_carrier(
                carrier_record,
                zone_type=zone_type,
                build_queue_id=build_queue_id,
                colony_id=colony_id,
                district_id=district_id,
                slot_selector=slot_selector,
                context_822c=context_822c,
            )
        except ValueError:
            continue
        fitting_building_commands += 1
    return {
        "reliable_packets": 1 if command_records else 0,
        "command_records": len(command_records),
        "building_commands": building_commands,
        "fitting_building_commands": fitting_building_commands,
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
    parser.add_argument(
        "--remote-outbound-port",
        type=int,
        required=True,
        help="Use 0 to accept any remote outbound destination port.",
    )
    parser.add_argument(
        "--remote-inbound-port",
        type=int,
        required=True,
        help="Use 0 to accept any remote inbound source port.",
    )
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--local-port", type=int, required=True)
    parser.add_argument("--context-822c", type=int, default=0)
    parser.add_argument("--build-queue-id", type=int, default=6)
    parser.add_argument("--colony-id", type=int, default=0)
    parser.add_argument("--district-id", type=int, default=0)
    parser.add_argument("--slot-selector", type=int, default=1)
    parser.add_argument("--zone-type", default="zone_trade")
    parser.add_argument(
        "--rewrite-direction",
        choices=["inbound", "outbound", "both"],
        default="inbound",
        help=(
            "Which side may be rewritten. Use inbound on the host machine to "
            "rewrite the co-op player's command before the host executes it."
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--observe-seconds", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    return parser.parse_args()


def build_packet_filter(args: argparse.Namespace) -> str:
    outbound_terms = [
        f"ip.SrcAddr == {args.local_ip}",
        f"ip.DstAddr == {args.remote_ip}",
        f"udp.SrcPort == {args.local_port}",
    ]
    if args.remote_outbound_port:
        outbound_terms.append(f"udp.DstPort == {args.remote_outbound_port}")

    inbound_terms = [
        f"ip.SrcAddr == {args.remote_ip}",
        f"ip.DstAddr == {args.local_ip}",
        f"udp.DstPort == {args.local_port}",
    ]
    if args.remote_inbound_port:
        inbound_terms.append(f"udp.SrcPort == {args.remote_inbound_port}")

    return (
        "udp and (("
        + " and ".join(outbound_terms)
        + ") or ("
        + " and ".join(inbound_terms)
        + "))"
    )


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
        "context_822c": args.context_822c,
        "build_queue_id": args.build_queue_id,
        "colony_id": args.colony_id,
        "district_id": args.district_id,
        "slot_selector": args.slot_selector,
        "zone_type": args.zone_type,
        "rewrite_direction": args.rewrite_direction,
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
    zone_needle = args.zone_type.encode("ascii")
    packets_seen = 0
    outbound_packets_seen = 0
    inbound_packets_seen = 0
    reliable_packets_seen = 0
    command_records_seen = 0
    building_commands_seen = 0
    fitting_building_commands_seen = 0
    first_diagnostic_logged = False

    try:
        with pydivert.WinDivert(packet_filter) as divert:
            for packet in divert:
                original = packet.payload or b""
                is_outbound = packet.src_addr == args.local_ip
                packets_seen += 1
                if is_outbound:
                    outbound_packets_seen += 1
                else:
                    inbound_packets_seen += 1

                diagnostic_allowed = (
                    args.rewrite_direction == "both"
                    or (args.rewrite_direction == "outbound" and is_outbound)
                    or (args.rewrite_direction == "inbound" and not is_outbound)
                )

                if diagnostic_allowed:
                    diagnostic = inspect_payload(
                        original,
                        zone_type=args.zone_type,
                        build_queue_id=args.build_queue_id,
                        colony_id=args.colony_id,
                        district_id=args.district_id,
                        slot_selector=args.slot_selector,
                        context_822c=args.context_822c,
                    )
                    reliable_packets_seen += diagnostic["reliable_packets"]
                    command_records_seen += diagnostic["command_records"]
                    building_commands_seen += diagnostic["building_commands"]
                    fitting_building_commands_seen += diagnostic[
                        "fitting_building_commands"
                    ]
                    if (
                        not first_diagnostic_logged
                        and (
                            diagnostic["reliable_packets"]
                            or diagnostic["command_records"]
                            or diagnostic["building_commands"]
                        )
                    ):
                        first_diagnostic_logged = True
                        append_jsonl(
                            args.log,
                            {
                                "event": "first_outbound_diagnostic",
                                "timestamp": now_iso(),
                                **diagnostic,
                                "direction": (
                                    "outbound" if is_outbound else "inbound"
                                ),
                                "src": f"{packet.src_addr}:{packet.src_port}",
                                "dst": f"{packet.dst_addr}:{packet.dst_port}",
                                "payload_length": len(original),
                            },
                        )

                try:
                    if not replaced and diagnostic_allowed:
                        result = replace_building_command_with_zone(
                            original,
                            zone_type=args.zone_type,
                            build_queue_id=args.build_queue_id,
                            colony_id=args.colony_id,
                            district_id=args.district_id,
                            slot_selector=args.slot_selector,
                            context_822c=args.context_822c,
                        )
                        if result is not None:
                            candidate, metadata = result
                            append_jsonl(
                                args.log,
                                {
                                    "event": (
                                        "dry_run_carrier_observed"
                                        if args.dry_run
                                        else "building_to_zone_replaced"
                                    ),
                                    "timestamp": now_iso(),
                                    **metadata,
                                    "direction": (
                                        "outbound" if is_outbound else "inbound"
                                    ),
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
                    broadcast_direction_matches = (
                        (args.rewrite_direction == "inbound" and is_outbound)
                        or (args.rewrite_direction == "outbound" and not is_outbound)
                        or args.rewrite_direction == "both"
                    )
                    if replaced and broadcast_direction_matches and zone_needle in original:
                        host_broadcast_seen = True
                        append_jsonl(
                            args.log,
                            {
                                "event": "host_zone_broadcast_seen",
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
                "reliable_packets_seen": reliable_packets_seen,
                "command_records_seen": command_records_seen,
                "building_commands_seen": building_commands_seen,
                "fitting_building_commands_seen": fitting_building_commands_seen,
            },
        )
        args.ready_file.unlink(missing_ok=True)

    return 0 if replaced and host_broadcast_seen else 2


if __name__ == "__main__":
    raise SystemExit(run())
