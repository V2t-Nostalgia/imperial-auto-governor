#!/usr/bin/env python3
"""Summarize passive UDP flow traces produced by iag_packet_interceptor.py."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def parse_endpoint(value: str) -> tuple[str, int]:
    host, port = value.rsplit(":", 1)
    return host, int(port)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    parser.add_argument(
        "--lan-prefix",
        default="",
        help="Prefix used to flag LAN endpoints in the summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    flows: Counter[tuple[str, int, str, int]] = Counter()
    by_remote_ip: dict[str, Counter[tuple[int, int]]] = defaultdict(Counter)
    reliable_flows: Counter[tuple[str, int, str, int]] = Counter()
    first_seen: dict[tuple[str, int, str, int], str] = {}
    last_seen: dict[tuple[str, int, str, int], str] = {}
    total_packets = 0

    with args.jsonl.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("event") != "flow_packet":
                continue
            total_packets += 1
            src_ip, src_port = parse_endpoint(event["src"])
            dst_ip, dst_port = parse_endpoint(event["dst"])
            flow = (src_ip, src_port, dst_ip, dst_port)
            flows[flow] += 1
            first_seen.setdefault(flow, event["timestamp"])
            last_seen[flow] = event["timestamp"]
            payload_hex = event.get("payload_hex", "")
            if payload_hex.startswith("010000000000"):
                reliable_flows[flow] += 1

            for endpoint_ip, endpoint_port, peer_port in (
                (src_ip, src_port, dst_port),
                (dst_ip, dst_port, src_port),
            ):
                if not endpoint_ip.startswith(args.lan_prefix):
                    by_remote_ip[endpoint_ip][(endpoint_port, peer_port)] += 1

    print(f"trace_file={args.jsonl}")
    print(f"total_flow_packets={total_packets}")
    print("")
    print("top_flows:")
    for flow, count in flows.most_common(20):
        src_ip, src_port, dst_ip, dst_port = flow
        reliable_count = reliable_flows.get(flow, 0)
        print(
            f"  {src_ip}:{src_port} -> {dst_ip}:{dst_port} "
            f"packets={count} reliable_like={reliable_count} "
            f"first={first_seen[flow]} last={last_seen[flow]}"
        )

    print("")
    print("non_lan_endpoint_port_sets:")
    for ip, ports in sorted(by_remote_ip.items()):
        pairs = ", ".join(
            f"local_or_remote_port={port}/peer_port={peer} count={count}"
            for (port, peer), count in ports.most_common(10)
        )
        print(f"  {ip}: {pairs}")

    print("")
    print("reliable_like_flows:")
    for flow, count in reliable_flows.most_common(20):
        src_ip, src_port, dst_ip, dst_port = flow
        print(f"  {src_ip}:{src_port} -> {dst_ip}:{dst_port} packets={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
