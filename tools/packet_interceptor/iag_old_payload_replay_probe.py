#!/usr/bin/env python3
"""One-shot cross-session replay probe for an authorized private game."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


MAIN_HEADER = bytes.fromhex("010000000000")


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def append_jsonl(path: Path, event: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def load_trace_payload(log_path: Path, trace_index: int) -> bytes:
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            event = json.loads(line)
            if (
                event.get("event") == "flow_packet"
                and event.get("trace_index") == trace_index
            ):
                return bytes.fromhex(event["payload_hex"])
    raise RuntimeError(f"Trace index {trace_index} was not found in {log_path}.")


def add_vendor_runtime() -> None:
    runtime = Path(__file__).resolve().parent / "vendor_runtime"
    if not runtime.is_dir():
        raise RuntimeError(f"Missing vendored PyDivert runtime: {runtime}")
    sys.path.insert(0, str(runtime))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--local-port", required=True, type=int)
    parser.add_argument("--remote-ip", required=True)
    parser.add_argument("--source-log", required=True, type=Path)
    parser.add_argument("--trace-index", required=True, type=int)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--observe-seconds", type=float, default=5.0)
    parser.add_argument("--carrier-length", type=int, default=25)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    old_payload = load_trace_payload(args.source_log, args.trace_index)
    if len(old_payload) != 914 or not old_payload.startswith(MAIN_HEADER):
        raise RuntimeError("The selected replay payload is not a 914-byte main packet.")

    add_vendor_runtime()
    import pydivert  # type: ignore[import-not-found]

    packet_filter = (
        "udp and ("
        f"(outbound and ip.SrcAddr == {args.local_ip} and "
        f"ip.DstAddr == {args.remote_ip}) "
        "or "
        f"(inbound and ip.SrcAddr == {args.remote_ip} and "
        f"ip.DstAddr == {args.local_ip})"
        ")"
    )
    start_event = {
        "event": "started",
        "timestamp": utc_now(),
        "pid": os.getpid(),
        "filter": packet_filter,
        "source_log": str(args.source_log),
        "trace_index": args.trace_index,
        "replay_length": len(old_payload),
        "replay_sha256": sha256_hex(old_payload),
        "timeout_seconds": args.timeout_seconds,
        "observe_seconds": args.observe_seconds,
        "carrier_length": args.carrier_length,
    }
    append_jsonl(args.log, start_event)
    args.ready_file.write_text(
        json.dumps(start_event, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("IAG old-payload replay probe is READY.", flush=True)

    started = time.monotonic()
    replayed_at: float | None = None
    replayed = False
    packet_index = 0

    try:
        with pydivert.WinDivert(packet_filter) as divert:
            for packet in divert:
                packet_index += 1
                original = packet.payload or b""
                now = time.monotonic()
                event_name = "flow_observed"

                is_candidate = (
                    not replayed
                    and packet.is_outbound
                    and len(original) == args.carrier_length
                    and original.startswith(MAIN_HEADER)
                )
                if is_candidate:
                    packet.payload = old_payload
                    replayed = True
                    replayed_at = now
                    event_name = "old_payload_replayed"

                outgoing = packet.payload or b""
                divert.send(packet, recalculate_checksum=True)

                event = {
                    "event": event_name,
                    "timestamp": utc_now(),
                    "packet_index": packet_index,
                    "src": f"{packet.src_addr}:{packet.src_port}",
                    "dst": f"{packet.dst_addr}:{packet.dst_port}",
                    "is_inbound": packet.is_inbound,
                    "is_outbound": packet.is_outbound,
                    "payload_length": len(original),
                    "before_sha256": sha256_hex(original),
                    "after_sha256": sha256_hex(outgoing),
                    "header_hex": outgoing[:25].hex(),
                    "contains_research_lab": b"building_research_lab_1" in outgoing,
                }
                append_jsonl(args.log, event)
                if event_name == "old_payload_replayed":
                    print(json.dumps(event, ensure_ascii=False, indent=2), flush=True)

                if replayed_at is not None and now - replayed_at >= args.observe_seconds:
                    break
                if now - started >= args.timeout_seconds:
                    break
    finally:
        stop_event = {
            "event": "stopped",
            "timestamp": utc_now(),
            "replayed": replayed,
            "packets_observed": packet_index,
        }
        append_jsonl(args.log, stop_event)
        args.ready_file.unlink(missing_ok=True)
        print(json.dumps(stop_event, ensure_ascii=False, indent=2), flush=True)

    return 0 if replayed else 2


if __name__ == "__main__":
    raise SystemExit(run())
