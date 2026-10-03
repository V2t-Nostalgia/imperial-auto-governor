#!/usr/bin/env python3
"""Control the exact-build, one-shot observer takeover research probe."""

from __future__ import annotations

import argparse
import os
import socket
import sys


DEFAULT_SOCKET = "/tmp/iag-stellaris-observer-takeover.sock"
PROTOCOL = "IAGTAKE1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or arm the Stellaris observer takeover probe."
    )
    parser.add_argument(
        "--socket",
        default=os.environ.get("IAG_TAKEOVER_SOCKET", DEFAULT_SOCKET),
        help="probe Unix socket path",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show current probe state")
    commands.add_parser("observe", help="return to read-only observation")
    commands.add_parser("disarm", help="disarm an unconsumed request")
    arm = commands.add_parser(
        "arm-move", help="replace one AI batch with one move_fleet command"
    )
    arm.add_argument("country_id", type=int)
    arm.add_argument("fleet_id", type=int)
    arm.add_argument("destination_system_id", type=int)
    return parser


def request(path: str, fields: list[str]) -> str:
    payload = "\t".join([PROTOCOL, *fields]) + "\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3.0)
        client.connect(path)
        client.sendall(payload.encode("ascii"))
        chunks: list[bytes] = []
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    return b"".join(chunks).decode("utf-8", errors="replace").strip()


def main() -> int:
    args = build_parser().parse_args()
    fields = [args.command.replace("-", "_")]
    if args.command == "arm-move":
        values = (
            args.country_id,
            args.fleet_id,
            args.destination_system_id,
        )
        if any(value < 0 or value > 0xFFFFFFFF for value in values):
            raise SystemExit("IDs must be unsigned 32-bit integers")
        fields.extend(str(value) for value in values)
    try:
        response = request(args.socket, fields)
    except (OSError, TimeoutError) as error:
        print(f"probe request failed: {error}", file=sys.stderr)
        return 2
    print(response)
    return 0 if response.startswith(f"{PROTOCOL}\tok\t") else 1


if __name__ == "__main__":
    raise SystemExit(main())
