#!/usr/bin/env python3
"""Tiny external peer for the exact-build native UI research bridge."""

from __future__ import annotations

import argparse
from collections import deque
import os
import socket
from pathlib import Path


PROTOCOL = "IAGUI1"
APPLICATIONS = {
    "fleet_operations",
    "economy_governance",
    "research_strategy",
    "etc",
}


def escape_field(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def unescape_field(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "\\" or index + 1 >= len(value):
            output.append(value[index])
            index += 1
            continue
        index += 1
        escaped = value[index]
        output.append({"n": "\n", "r": "\r", "t": "\t"}.get(escaped, escaped))
        index += 1
    return "".join(output)


def serve(socket_path: Path) -> None:
    if socket_path.exists():
        socket_path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(os.fspath(socket_path))
    socket_path.chmod(0o600)
    server.listen(16)
    pending_messages: deque[tuple[str, str]] = deque()
    print(f"IAG native UI echo peer listening on {socket_path}", flush=True)
    try:
        while True:
            connection, _ = server.accept()
            with connection:
                payload = bytearray()
                while b"\n" not in payload and len(payload) < 65536:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    payload.extend(chunk)
                line = bytes(payload).split(b"\n", 1)[0].decode("utf-8", "replace")
                fields = line.split("\t", 3)
                if len(fields) != 4 or fields[0] != PROTOCOL:
                    response = f"{PROTOCOL}\tconversation.response\tetc\tinvalid request\n"
                else:
                    operation = fields[1]
                    application_id = unescape_field(fields[2])
                    message = unescape_field(fields[3])
                    if operation == "conversation.submit":
                        reply = f"Native bridge received: {message}"
                        response = (
                            f"{PROTOCOL}\tconversation.response\t"
                            f"{escape_field(application_id)}\t"
                            f"{escape_field(reply)}\n"
                        )
                    elif operation == "conversation.push":
                        if application_id not in APPLICATIONS:
                            application_id = "etc"
                        pending_messages.append((application_id, message))
                        response = (
                            f"{PROTOCOL}\tconversation.accepted\t"
                            f"{escape_field(application_id)}\t\n"
                        )
                    elif operation == "conversation.poll":
                        if pending_messages:
                            target, queued_message = pending_messages.popleft()
                            response = (
                                f"{PROTOCOL}\tconversation.push\t"
                                f"{escape_field(target)}\t"
                                f"{escape_field(queued_message)}\n"
                            )
                        else:
                            response = f"{PROTOCOL}\tconversation.none\t*\t\n"
                    else:
                        response = (
                            f"{PROTOCOL}\tconversation.response\tetc\t"
                            f"unsupported operation\n"
                        )
                connection.sendall(response.encode("utf-8"))
    finally:
        server.close()
        socket_path.unlink(missing_ok=True)


def push_message(socket_path: Path, application_id: str, message: str) -> None:
    request = (
        f"{PROTOCOL}\tconversation.push\t"
        f"{escape_field(application_id)}\t{escape_field(message)}\n"
    )
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(3)
    try:
        client.connect(os.fspath(socket_path))
        client.sendall(request.encode("utf-8"))
        client.shutdown(socket.SHUT_WR)
        response = bytearray()
        while b"\n" not in response and len(response) < 65536:
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
    finally:
        client.close()
    line = bytes(response).split(b"\n", 1)[0].decode("utf-8", "replace")
    fields = line.split("\t", 3)
    if len(fields) != 4 or fields[:2] != [PROTOCOL, "conversation.accepted"]:
        raise RuntimeError(f"IAG native UI peer rejected push: {line!r}")
    print(
        f"queued native UI message for {unescape_field(fields[2])}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--socket",
        type=Path,
        default=Path("/tmp/iag-stellaris-ui-agent.sock"),
    )
    parser.add_argument("--push-application", choices=sorted(APPLICATIONS))
    parser.add_argument("--push-message")
    args = parser.parse_args()
    if (args.push_application is None) != (args.push_message is None):
        parser.error("--push-application and --push-message must be used together")
    if args.push_application is not None:
        push_message(args.socket, args.push_application, args.push_message)
    else:
        serve(args.socket)


if __name__ == "__main__":
    main()
