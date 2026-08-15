#!/usr/bin/env python3
"""One-shot Stellaris co-op construction command substitution.

This tool is intentionally narrow. It only changes one previously observed,
equal-length TEST1 construction record and then exits.
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


RESEARCH_LAB = b"building_research_lab_1"
ADMINISTRATIVE_OFFICE = b"building_bureaucratic_1"
FOUNDRY = b"building_foundry_1"

BUILD_HEAD = bytes.fromhex(
    "612c0100140000000000"
)
EA3F_TAG = bytes.fromhex("ea3f01001400")
BUILD_STRING_TAG = bytes.fromhex("413d01000300922b01000f00")
FC29_TAG = bytes.fromhex("fc2901001400")
PLACEMENT_TAG = bytes.fromhex("932b01001400")
BUILD_TAIL = bytes.fromhex("0400040004000000")
ZONE_STRING_HEAD = bytes.fromhex("174401000300932b01000f00")
ZONE_TARGET_TAG = bytes.fromhex("942b01001400")
ZONE_RULE_TAG = bytes.fromhex("194401000c0001000000")
COMMAND_ENVELOPE_DISTANCE = 70
COMMAND_ENVELOPE_TYPE = bytes.fromhex("04000000")


def build_planet_signature(
    building_id: bytes,
    ea3f: int,
    fc29: int,
    placement: int,
    boundary: bool = False,
) -> bytes:
    tail = BUILD_TAIL[:-2] if boundary else BUILD_TAIL
    return (
        BUILD_HEAD
        + EA3F_TAG
        + ea3f.to_bytes(4, "little")
        + BUILD_STRING_TAG
        + len(building_id).to_bytes(2, "little")
        + building_id
        + FC29_TAG
        + fc29.to_bytes(4, "little")
        + PLACEMENT_TAG
        + placement.to_bytes(4, "little")
        + tail
    )


def build_test1_signature(building_id: bytes, boundary: bool = False) -> bytes:
    return build_planet_signature(
        building_id,
        ea3f=616,
        fc29=308,
        placement=23,
        boundary=boundary,
    )


def build_zone_signature(
    zone_type: bytes,
    build_queue_id: int,
    planet_id: int,
    target_zone_id: int,
    boundary: bool = False,
) -> bytes:
    tail = BUILD_TAIL[:-2] if boundary else BUILD_TAIL
    return (
        BUILD_HEAD
        + EA3F_TAG
        + build_queue_id.to_bytes(4, "little")
        + ZONE_STRING_HEAD
        + len(zone_type).to_bytes(2, "little")
        + zone_type
        + FC29_TAG
        + planet_id.to_bytes(4, "little")
        + ZONE_TARGET_TAG
        + target_zone_id.to_bytes(4, "little")
        + ZONE_RULE_TAG
        + tail
    )


TEST1_RESEARCH_CORE = build_test1_signature(RESEARCH_LAB)
TEST1_RESEARCH_FRAGMENT = build_test1_signature(RESEARCH_LAB, boundary=True)


@dataclass(frozen=True)
class ReplacementResult:
    payload: bytes
    matched: bool
    offset: int | None
    reason: str


def replace_planet_building(
    payload: bytes,
    source_id: bytes,
    target_id: bytes,
    source_ea3f: int,
    source_fc29: int,
    source_placement: int,
    target_ea3f: int,
    target_fc29: int,
    target_placement: int,
    allow_variable_length: bool = False,
    pad_variable_length: bool = False,
) -> ReplacementResult:
    """Replace one exact construction record, including planet and building."""
    source_core = build_planet_signature(
        source_id,
        source_ea3f,
        source_fc29,
        source_placement,
    )
    source_fragment = build_planet_signature(
        source_id,
        source_ea3f,
        source_fc29,
        source_placement,
        boundary=True,
    )
    target_core = build_planet_signature(
        target_id,
        target_ea3f,
        target_fc29,
        target_placement,
    )
    target_fragment = build_planet_signature(
        target_id,
        target_ea3f,
        target_fc29,
        target_placement,
        boundary=True,
    )

    core_count = payload.count(source_core)
    fragment_count = (
        1
        if payload.endswith(source_fragment)
        and payload.count(source_fragment) == 1
        else 0
    )
    total_count = core_count + fragment_count
    if total_count != 1:
        return ReplacementResult(
            payload=payload,
            matched=False,
            offset=None,
            reason=(
                f"stable_core_count={core_count};"
                f"boundary_fragment_count={fragment_count}"
            ),
        )

    # Variable-length application messages require outer protocol metadata that
    # is not decoded yet. Match the exact command, but leave it byte-for-byte
    # unchanged so the multiplayer simulation remains synchronized.
    variable_length = len(source_id) != len(target_id)
    if variable_length and not allow_variable_length and not pad_variable_length:
        signature = source_core if core_count == 1 else source_fragment
        return ReplacementResult(
            payload=payload,
            matched=True,
            offset=payload.index(signature) + signature.index(source_id),
            reason=(
                "unsafe_variable_length_building_rewrite_blocked;"
                f"source_length={len(source_id)};"
                f"target_length={len(target_id)}"
            ),
        )

    signature = source_core if core_count == 1 else source_fragment
    replacement = target_core if core_count == 1 else target_fragment
    is_core_match = core_count == 1
    core_offset = payload.index(signature)
    building_offset_in_core = signature.index(source_id)
    building_offset = core_offset + building_offset_in_core

    expected_delta = len(target_id) - len(source_id)
    if variable_length:
        envelope_offset = core_offset - COMMAND_ENVELOPE_DISTANCE
        if envelope_offset < 0:
            return ReplacementResult(
                payload=payload,
                matched=True,
                offset=building_offset,
                reason="unsafe_missing_building_command_envelope_blocked",
            )
        if (
            payload[envelope_offset + 2 : envelope_offset + 6]
            != COMMAND_ENVELOPE_TYPE
        ):
            return ReplacementResult(
                payload=payload,
                matched=True,
                offset=building_offset,
                reason="unsafe_invalid_building_command_envelope_blocked",
            )
        declared_length = int.from_bytes(
            payload[envelope_offset : envelope_offset + 2],
            "little",
        )
        record_end = envelope_offset + 1 + declared_length
        source_end = core_offset + len(signature)
        if (
            record_end != source_end
            and payload.startswith(source_fragment, core_offset)
            and record_end == core_offset + len(source_fragment)
        ):
            # A following record may begin with the two zero bytes that make
            # the six-byte inbound tail look like the older eight-byte tail.
            # The declared envelope boundary is authoritative.
            signature = source_fragment
            replacement = target_fragment
            source_end = core_offset + len(signature)
            is_core_match = False
        if record_end != source_end:
            return ReplacementResult(
                payload=payload,
                matched=True,
                offset=building_offset,
                reason=(
                    "unsafe_unverified_building_command_boundary_blocked;"
                    f"declared_end={record_end};source_end={source_end}"
                ),
            )
        if pad_variable_length:
            if expected_delta >= 0:
                return ReplacementResult(
                    payload=payload,
                    matched=True,
                    offset=building_offset,
                    reason=(
                        "unsafe_padded_building_rewrite_requires_shorter_target;"
                        f"delta={expected_delta}"
                    ),
                )
            padding = bytes(-expected_delta)
            modified = (
                payload[:core_offset]
                + replacement
                + padding
                + payload[core_offset + len(signature) :]
            )
        else:
            target_declared_length = declared_length + expected_delta
            if not 0 <= target_declared_length <= 0xFFFF:
                raise RuntimeError("Target command envelope length is out of range.")
            modified = (
                payload[:envelope_offset]
                + target_declared_length.to_bytes(2, "little")
                + payload[envelope_offset + 2 : core_offset]
                + replacement
                + payload[core_offset + len(signature) :]
            )
    else:
        modified = (
            payload[:core_offset]
            + replacement
            + payload[core_offset + len(signature) :]
        )

    expected_payload_delta = 0 if pad_variable_length else expected_delta
    if len(modified) != len(payload) + expected_payload_delta:
        raise RuntimeError("Replacement produced an unexpected payload length.")
    if modified.count(target_id) != 1:
        raise RuntimeError("Post-replacement target building count is not one.")

    return ReplacementResult(
        payload=modified,
        matched=True,
        offset=building_offset,
        reason=(
            (
                (
                    "exact_planet_building_core_with_fixed_length_padding"
                    if is_core_match
                    else (
                        "exact_planet_building_boundary_fragment_"
                        "with_fixed_length_padding"
                    )
                )
                if pad_variable_length
                else (
                    "exact_planet_building_core_with_envelope_resize"
                    if is_core_match
                    else (
                        "exact_planet_building_boundary_fragment_"
                        "with_envelope_resize"
                    )
                )
            )
            if variable_length
            else (
                "exact_planet_building_core"
                if is_core_match
                else "exact_planet_building_boundary_fragment"
            )
        ),
    )


def replace_test1_building(
    payload: bytes,
    source_id: bytes,
    target_id: bytes,
    allow_variable_length: bool = False,
) -> ReplacementResult:
    """Replace a building while retaining the known TEST1 planet profile."""
    return replace_planet_building(
        payload,
        source_id,
        target_id,
        source_ea3f=616,
        source_fc29=308,
        source_placement=23,
        target_ea3f=616,
        target_fc29=308,
        target_placement=23,
        allow_variable_length=allow_variable_length,
    )


def replace_test1_research_with_admin(payload: bytes) -> ReplacementResult:
    """Backward-compatible wrapper used by existing tests and tooling."""
    return replace_test1_building(payload, RESEARCH_LAB, ADMINISTRATIVE_OFFICE)


def replace_planet_zone(
    payload: bytes,
    source_zone: bytes,
    target_zone: bytes,
    source_queue: int,
    source_planet: int,
    source_target_zone: int,
    target_queue: int,
    target_planet: int,
    target_target_zone: int,
) -> ReplacementResult:
    """Replace one exact zone construction record."""
    source_core = build_zone_signature(
        source_zone,
        source_queue,
        source_planet,
        source_target_zone,
    )
    source_fragment = build_zone_signature(
        source_zone,
        source_queue,
        source_planet,
        source_target_zone,
        boundary=True,
    )
    target_core = build_zone_signature(
        target_zone,
        target_queue,
        target_planet,
        target_target_zone,
    )
    target_fragment = build_zone_signature(
        target_zone,
        target_queue,
        target_planet,
        target_target_zone,
        boundary=True,
    )

    core_count = payload.count(source_core)
    fragment_count = (
        1
        if payload.endswith(source_fragment)
        and payload.count(source_fragment) == 1
        else 0
    )
    if core_count + fragment_count != 1:
        return ReplacementResult(
            payload=payload,
            matched=False,
            offset=None,
            reason=(
                f"zone_core_count={core_count};"
                f"zone_boundary_fragment_count={fragment_count}"
            ),
        )

    # The zone command is embedded in a larger multiplayer message. We have not
    # yet decoded every outer length/fragment field, so changing its byte count
    # can stall the synchronized simulation even when UDP checksums are valid.
    if len(source_zone) != len(target_zone):
        source = source_core if core_count else source_fragment
        return ReplacementResult(
            payload=payload,
            matched=True,
            offset=payload.index(source) + source.index(source_zone),
            reason=(
                "unsafe_variable_length_zone_rewrite_blocked;"
                f"source_length={len(source_zone)};"
                f"target_length={len(target_zone)}"
            ),
        )

    source = source_core if core_count else source_fragment
    target = target_core if core_count else target_fragment
    offset = payload.index(source)
    type_offset = offset + source.index(source_zone)
    modified = payload[:offset] + target + payload[offset + len(source) :]
    expected_delta = len(target_zone) - len(source_zone)
    if len(modified) != len(payload) + expected_delta:
        raise RuntimeError("Zone replacement produced an unexpected length.")
    if modified.count(target_zone) != 1:
        raise RuntimeError("Post-replacement target zone count is not one.")
    return ReplacementResult(
        payload=modified,
        matched=True,
        offset=type_offset,
        reason=(
            "exact_planet_zone_core"
            if core_count
            else "exact_planet_zone_boundary_fragment"
        ),
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now() -> str:
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
        raise RuntimeError(
            f"Missing vendored PyDivert runtime: {runtime}. "
            "Run the dependency preparation step first."
        )
    sys.path.insert(0, str(runtime))


def build_filter(args: argparse.Namespace) -> str:
    if getattr(args, "trace_flow", False):
        if getattr(args, "trace_all_udp", False):
            return "udp"
        if getattr(args, "trace_local_port", 0):
            return (
                "udp and "
                f"(udp.SrcPort == {args.trace_local_port} or "
                f"udp.DstPort == {args.trace_local_port})"
            )
        return (
            "udp and ("
            f"(ip.SrcAddr == {args.remote_ip} and ip.DstAddr == {args.local_ip}) "
            "or "
            f"(ip.SrcAddr == {args.local_ip} and ip.DstAddr == {args.remote_ip})"
            ")"
        )
    if args.discover:
        if args.discover_inbound:
            return (
                "inbound and udp and "
                f"ip.SrcAddr == {args.remote_ip} and "
                f"ip.DstAddr == {args.local_ip} and "
                f"udp.SrcPort == {args.remote_port} and "
                f"udp.DstPort == {args.local_port}"
            )
        return "udp"
    return (
        "inbound and udp and "
        f"ip.SrcAddr == {args.remote_ip} and "
        f"ip.DstAddr == {args.local_ip} and "
        f"udp.SrcPort == {args.remote_port} and "
        f"udp.DstPort == {args.local_port}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot replacement of a legal TEST1 research-lab command "
            "with an equal-length administrative-office command."
        )
    )
    parser.add_argument("--remote-ip", required=True)
    parser.add_argument("--remote-port", type=int, default=46377)
    parser.add_argument("--local-ip", required=True)
    parser.add_argument("--local-port", type=int, default=61552)
    parser.add_argument(
        "--log",
        type=Path,
        default=Path(__file__).resolve().parent / "logs" / "interceptor.jsonl",
    )
    parser.add_argument(
        "--ready-file",
        type=Path,
        default=Path(__file__).resolve().parent / "active_interceptor.json",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="Exit if no matching command is observed within this period.",
    )
    parser.add_argument(
        "--observe-only",
        action="store_true",
        help="Detect and log the exact command, but send it unchanged.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help=(
            "Passively sniff UDP and log packets containing the research-lab "
            "ID. This mode never blocks, modifies, or reinjects packets."
        ),
    )
    parser.add_argument(
        "--trace-flow",
        action="store_true",
        help=(
            "Passively log every UDP payload in both directions between the "
            "configured local and remote IPs until timeout."
        ),
    )
    parser.add_argument(
        "--trace-local-port",
        type=int,
        default=0,
        help="In trace mode, capture both directions for this local UDP port.",
    )
    parser.add_argument(
        "--trace-all-udp",
        action="store_true",
        help="In trace mode, passively capture all UDP traffic.",
    )
    parser.add_argument(
        "--discover-id",
        action="append",
        help=(
            "ASCII game object ID to locate in passive discovery mode. "
            "May be supplied multiple times."
        ),
    )
    parser.add_argument(
        "--discover-count",
        type=int,
        default=1,
        help="Number of discovery matches to collect before exiting.",
    )
    parser.add_argument(
        "--discover-inbound",
        action="store_true",
        help="Limit passive discovery to the configured remote-to-local flow.",
    )
    parser.add_argument(
        "--discover-unique",
        action="store_true",
        help="Count each discovered object ID only once.",
    )
    parser.add_argument("--source-id", default=RESEARCH_LAB.decode("ascii"))
    parser.add_argument(
        "--target-id",
        default=ADMINISTRATIVE_OFFICE.decode("ascii"),
    )
    parser.add_argument("--source-ea3f", type=int, default=616)
    parser.add_argument("--source-fc29", type=int, default=308)
    parser.add_argument("--source-placement", type=int, default=23)
    parser.add_argument("--target-ea3f", type=int, default=616)
    parser.add_argument("--target-fc29", type=int, default=308)
    parser.add_argument("--target-placement", type=int, default=23)
    parser.add_argument(
        "--record-kind",
        choices=("building", "zone"),
        default="building",
    )
    parser.add_argument(
        "--allow-variable-length",
        action="store_true",
        help=(
            "Experimentally resize a complete building command envelope. "
            "Incomplete or unverified records are still forwarded unchanged."
        ),
    )
    parser.add_argument(
        "--pad-variable-length",
        action="store_true",
        help=(
            "Experimental fixed-datagram mode for a shorter target ID: keep "
            "the command envelope and UDP payload lengths unchanged by adding "
            "zero padding at the verified command boundary."
        ),
    )
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    if args.allow_variable_length:
        raise SystemExit(
            "Live variable-length rewriting is disabled: resizing the command "
            "envelope still stalls multiplayer time synchronization. Use "
            "offline replay until the reliable transport/echo layer is decoded."
        )
    add_vendor_runtime()
    import pydivert  # type: ignore[import-not-found]

    packet_filter = build_filter(args)
    discover_ids = args.discover_id or [RESEARCH_LAB.decode("ascii")]
    discover_needles = [(value, value.encode("ascii")) for value in discover_ids]
    source_id = args.source_id.encode("ascii")
    target_id = args.target_id.encode("ascii")
    start_event = {
        "event": "started",
        "timestamp": utc_now(),
        "pid": os.getpid(),
        "filter": packet_filter,
        "observe_only": args.observe_only,
        "discover": args.discover,
        "trace_flow": args.trace_flow,
        "discover_ids": discover_ids,
        "discover_count": args.discover_count,
        "discover_inbound": args.discover_inbound,
        "discover_unique": args.discover_unique,
        "source_id": args.source_id,
        "target_id": args.target_id,
        "source_planet": {
            "ea3f": args.source_ea3f,
            "fc29": args.source_fc29,
            "placement": args.source_placement,
        },
        "target_planet": {
            "ea3f": args.target_ea3f,
            "fc29": args.target_fc29,
            "placement": args.target_placement,
        },
        "record_kind": args.record_kind,
        "allow_variable_length": args.allow_variable_length,
        "pad_variable_length": args.pad_variable_length,
        "timeout_seconds": args.timeout_seconds,
    }
    append_jsonl(args.log, start_event)
    args.ready_file.write_text(
        json.dumps(start_event, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("IAG one-shot packet interceptor is READY.", flush=True)
    print(f"Filter: {packet_filter}", flush=True)
    print(
        "Action: queue building_research_lab_1 on TEST1 from the LLM co-op player.",
        flush=True,
    )
    if args.trace_flow:
        print(
            "Mode: PASSIVE FLOW TRACE. Every matching packet is copied only.",
            flush=True,
        )
    elif args.discover:
        print(
            "Mode: PASSIVE DISCOVERY. Packets are copied only and never reinjected.",
            flush=True,
        )
        print(f"Discovery IDs: {', '.join(discover_ids)}", flush=True)
        print(f"Matches required: {args.discover_count}", flush=True)
    elif args.observe_only:
        print("Mode: OBSERVE ONLY. No packet will be modified.", flush=True)
    else:
        print("Mode: ARMED. Exactly one matching command will be modified.", flush=True)
        print(f"Replacement: {args.source_id} -> {args.target_id}", flush=True)
        print(
            "Planet profile: "
            f"({args.source_ea3f}, {args.source_fc29}, {args.source_placement}) -> "
            f"({args.target_ea3f}, {args.target_fc29}, {args.target_placement})",
            flush=True,
        )

    deadline = time.monotonic() + args.timeout_seconds
    matched = False
    discovery_matches = 0
    discovered_ids: set[str] = set()
    trace_packets = 0

    try:
        flags = (
            pydivert.Flag.SNIFF
            if args.discover or args.trace_flow
            else pydivert.Flag.DEFAULT
        )
        with pydivert.WinDivert(packet_filter, flags=flags) as divert:
            for packet in divert:
                original_payload = packet.payload or b""
                outgoing_payload = original_payload
                event: dict[str, object] | None = None

                try:
                    if args.trace_flow:
                        trace_packets += 1
                        event = {
                            "event": "flow_packet",
                            "timestamp": utc_now(),
                            "trace_index": trace_packets,
                            "payload_length": len(original_payload),
                            "payload_hex": original_payload.hex(),
                            "payload_sha256": sha256_hex(original_payload),
                            "src": f"{packet.src_addr}:{packet.src_port}",
                            "dst": f"{packet.dst_addr}:{packet.dst_port}",
                            "is_inbound": packet.is_inbound,
                            "is_outbound": packet.is_outbound,
                        }
                    else:
                        discovered = next(
                        (
                            (object_id, needle)
                            for object_id, needle in discover_needles
                            if needle in original_payload
                        ),
                        None,
                    )
                    if (
                        not args.trace_flow
                        and args.discover
                        and discovered is not None
                    ):
                        object_id, discover_needle = discovered
                        building_offset = original_payload.index(discover_needle)
                        context_start = max(0, building_offset - 96)
                        context_end = min(
                            len(original_payload),
                            building_offset + len(discover_needle) + 96,
                        )
                        event = {
                            "event": "object_id_discovered",
                            "timestamp": utc_now(),
                            "object_id": object_id,
                            "payload_length": len(original_payload),
                            # Full payload is required to identify application-
                            # layer length, sequence, and fragmentation fields.
                            "payload_hex": original_payload.hex(),
                            "building_offset": building_offset,
                            "object_id_count": original_payload.count(discover_needle),
                            "payload_sha256": sha256_hex(original_payload),
                            "context_start": context_start,
                            "context_hex": original_payload[
                                context_start:context_end
                            ].hex(),
                            "src": f"{packet.src_addr}:{packet.src_port}",
                            "dst": f"{packet.dst_addr}:{packet.dst_port}",
                            "is_inbound": packet.is_inbound,
                            "is_outbound": packet.is_outbound,
                        }
                        is_new_id = object_id not in discovered_ids
                        if is_new_id:
                            discovered_ids.add(object_id)
                        if not args.discover_unique or is_new_id:
                            discovery_matches += 1
                        event["counted_match"] = (
                            not args.discover_unique or is_new_id
                        )
                        event["discovery_matches"] = discovery_matches
                        matched = discovery_matches >= args.discover_count
                    elif not args.trace_flow:
                        if args.record_kind == "zone":
                            result = replace_planet_zone(
                                original_payload,
                                source_id,
                                target_id,
                                args.source_ea3f,
                                args.source_fc29,
                                args.source_placement,
                                args.target_ea3f,
                                args.target_fc29,
                                args.target_placement,
                            )
                        else:
                            result = replace_planet_building(
                                original_payload,
                                source_id,
                                target_id,
                                args.source_ea3f,
                                args.source_fc29,
                                args.source_placement,
                                args.target_ea3f,
                                args.target_fc29,
                                args.target_placement,
                                allow_variable_length=args.allow_variable_length,
                                pad_variable_length=args.pad_variable_length,
                            )
                    if (
                        not args.discover
                        and not args.trace_flow
                        and result.matched
                    ):
                        safety_blocked = result.reason.startswith("unsafe_")
                        outgoing_payload = (
                            original_payload
                            if args.observe_only or safety_blocked
                            else result.payload
                        )
                        if not args.observe_only and not safety_blocked:
                            packet.payload = outgoing_payload

                        event = {
                            "event": (
                                "safety_rewrite_blocked_original_forwarded"
                                if safety_blocked
                                else (
                                    "exact_match_observed"
                                    if args.observe_only
                                    else "packet_modified"
                                )
                            ),
                            "timestamp": utc_now(),
                            "payload_length": len(original_payload),
                            "output_payload_length": len(outgoing_payload),
                            "payload_length_delta": (
                                len(outgoing_payload) - len(original_payload)
                            ),
                            "building_offset": result.offset,
                            "before_sha256": sha256_hex(original_payload),
                            "after_sha256": sha256_hex(outgoing_payload),
                            "reason": result.reason,
                            "source_id": args.source_id,
                            "target_id": args.target_id,
                            "record_kind": args.record_kind,
                            "source_planet": {
                                "ea3f": args.source_ea3f,
                                "fc29": args.source_fc29,
                                "placement": args.source_placement,
                            },
                            "target_planet": {
                                "ea3f": args.target_ea3f,
                                "fc29": args.target_fc29,
                                "placement": args.target_placement,
                            },
                            "src": f"{packet.src_addr}:{packet.src_port}",
                            "dst": f"{packet.dst_addr}:{packet.dst_port}",
                        }
                        matched = True
                except Exception as error:
                    # Fail closed: any validation error sends the original packet.
                    packet.payload = original_payload
                    event = {
                        "event": "replacement_error_original_forwarded",
                        "timestamp": utc_now(),
                        "error": repr(error),
                        "payload_length": len(original_payload),
                        "sha256": sha256_hex(original_payload),
                    }

                if not args.discover and not args.trace_flow:
                    divert.send(packet, recalculate_checksum=True)

                if event is not None:
                    append_jsonl(args.log, event)
                    print(json.dumps(event, ensure_ascii=False, indent=2), flush=True)
                if matched:
                    break
                if time.monotonic() >= deadline:
                    break
    finally:
        stop_event = {
            "event": "stopped",
            "timestamp": utc_now(),
            "matched": matched,
            "observe_only": args.observe_only,
            "discover": args.discover,
            "trace_flow": args.trace_flow,
            "trace_packets": trace_packets,
            "discovery_matches": discovery_matches,
            "discovered_ids": sorted(discovered_ids),
        }
        append_jsonl(args.log, stop_event)
        args.ready_file.unlink(missing_ok=True)
        print(json.dumps(stop_event, ensure_ascii=False, indent=2), flush=True)

    return 0 if matched else 2


if __name__ == "__main__":
    raise SystemExit(run())
