#!/usr/bin/env python3
"""Pinned-HTTPS client and long-poll SSE reader for the game overlay."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import ssl
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from .overlay_config import OverlayConnection


class OverlayTransportError(RuntimeError):
    """The overlay could not exchange a trusted message with the Agent."""


def parse_sse_events(payload: bytes | str) -> list[dict[str, Any]]:
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    events: list[dict[str, Any]] = []
    event_name = "message"
    event_id: str | None = None
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal event_name, event_id, data_lines
        if not data_lines:
            event_name = "message"
            event_id = None
            return
        raw_data = "\n".join(data_lines)
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError as error:
            raise OverlayTransportError("Agent returned invalid SSE JSON.") from error
        events.append({"event": event_name, "id": event_id, "data": data})
        event_name = "message"
        event_id = None
        data_lines = []

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            flush()
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "id":
            event_id = value
        elif field == "data":
            data_lines.append(value)
    flush()
    return events


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        port: int,
        expected_fingerprint: str,
        *,
        timeout: float,
    ) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        super().__init__(host, port, timeout=timeout, context=context)
        self.expected_fingerprint = expected_fingerprint.lower()

    def connect(self) -> None:
        super().connect()
        if self.sock is None:
            raise OverlayTransportError("TLS socket was not established.")
        certificate = self.sock.getpeercert(binary_form=True)
        actual = hashlib.sha256(certificate).hexdigest().lower()
        if not hmac.compare_digest(actual, self.expected_fingerprint):
            self.close()
            raise OverlayTransportError(
                "Agent TLS certificate fingerprint does not match the paired config."
            )


class OverlayClient:
    def __init__(self, connection: OverlayConnection) -> None:
        parsed = urlparse(connection.server_url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise OverlayTransportError("Overlay Agent URL must use HTTPS.")
        self.host = parsed.hostname
        self.port = parsed.port or 443
        self.base_path = parsed.path.rstrip("/")
        self.fingerprint = connection.certificate_sha256
        self.token = connection.access_token

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float = 35,
    ) -> tuple[int, str, bytes]:
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "IAG-Game-Overlay/1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        connection = _PinnedHTTPSConnection(
            self.host,
            self.port,
            self.fingerprint,
            timeout=timeout,
        )
        try:
            connection.request(
                method, self.base_path + path, body=body, headers=headers
            )
            response = connection.getresponse()
            content = response.read()
            content_type = str(response.getheader("Content-Type") or "")
            if response.status < 200 or response.status >= 300:
                detail = content.decode("utf-8", errors="replace")[:800]
                raise OverlayTransportError(
                    f"Agent returned HTTP {response.status}: {detail}"
                )
            return response.status, content_type, content
        except (OSError, http.client.HTTPException, ssl.SSLError) as error:
            if isinstance(error, OverlayTransportError):
                raise
            raise OverlayTransportError(
                f"Agent connection failed: {type(error).__name__}: {error}"
            ) from error
        finally:
            connection.close()

    def bootstrap(self) -> dict[str, Any]:
        _status, _content_type, content = self._request(
            "GET",
            "/api/overlay/bootstrap",
            timeout=15,
        )
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OverlayTransportError("Agent bootstrap is not valid JSON.") from error
        if not isinstance(value, dict):
            raise OverlayTransportError("Agent bootstrap must be a JSON object.")
        return value

    def events(
        self,
        conversation_id: str,
        after_sequence: int,
    ) -> list[dict[str, Any]]:
        query = urlencode(
            {
                "conversation_id": conversation_id,
                "after": max(0, int(after_sequence)),
            },
            quote_via=quote,
        )
        _status, content_type, content = self._request(
            "GET",
            "/api/overlay/events?" + query,
            timeout=35,
        )
        if "text/event-stream" not in content_type.lower():
            raise OverlayTransportError("Agent overlay event endpoint is not SSE.")
        return parse_sse_events(content)

    def send_message(self, conversation_id: str, text: str) -> dict[str, Any]:
        _status, _content_type, content = self._request(
            "POST",
            "/api/overlay/message",
            payload={"conversation_id": conversation_id, "text": text},
            timeout=20,
        )
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OverlayTransportError(
                "Agent message response is not valid JSON."
            ) from error
        if not isinstance(value, dict):
            raise OverlayTransportError("Agent message response must be a JSON object.")
        return value


class OverlayNetworkWorker:
    """Run blocking HTTPS work outside Qt's UI thread."""

    def __init__(
        self,
        client: OverlayClient,
        *,
        on_bootstrap: Callable[[dict[str, Any]], None],
        on_event: Callable[[dict[str, Any]], None],
        on_connection: Callable[[bool, str], None],
        on_message_error: Callable[[str], None],
    ) -> None:
        self.client = client
        self.on_bootstrap = on_bootstrap
        self.on_event = on_event
        self.on_connection = on_connection
        self.on_message_error = on_message_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._conversation_id = ""
        self._after_sequence = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="iag-overlay-network",
            daemon=True,
        )
        self._thread.start()

    def _apply_bootstrap(self, value: dict[str, Any]) -> None:
        conversation = value.get("conversation")
        conversation = conversation if isinstance(conversation, dict) else {}
        selected = str(conversation.get("conversation_id") or "")
        stream = value.get("stream")
        stream = stream if isinstance(stream, dict) else {}
        changed = bool(selected and selected != self._conversation_id)
        if changed:
            self._conversation_id = selected
            self._after_sequence = max(
                0,
                int(stream.get("latest_sequence") or 0),
            )
        else:
            self._after_sequence = max(
                self._after_sequence,
                int(stream.get("latest_sequence") or 0),
            )
        self.on_bootstrap(value)

    def _run(self) -> None:
        retry_seconds = 1.0
        while not self._stop.is_set():
            try:
                self._apply_bootstrap(self.client.bootstrap())
                self.on_connection(True, "LINKED")
                retry_seconds = 1.0
                if not self._conversation_id:
                    raise OverlayTransportError("Agent has no active conversation.")
                events = self.client.events(
                    self._conversation_id,
                    self._after_sequence,
                )
                for item in events:
                    data = item.get("data")
                    if not isinstance(data, dict) or item.get("event") == "keepalive":
                        continue
                    sequence = data.get("sequence")
                    if sequence is not None:
                        self._after_sequence = max(
                            self._after_sequence,
                            int(sequence),
                        )
                    self.on_event(data)
            except Exception as error:  # noqa: BLE001 - reconnect on all transport faults.
                self.on_connection(False, str(error))
                if self._stop.wait(retry_seconds):
                    break
                retry_seconds = min(retry_seconds * 1.8, 10.0)

    def send_message(self, conversation_id: str, text: str) -> None:
        def worker() -> None:
            try:
                self.client.send_message(conversation_id, text)
            except Exception as error:  # noqa: BLE001 - report send failure to the UI.
                self.on_message_error(str(error))

        threading.Thread(
            target=worker,
            name="iag-overlay-send",
            daemon=True,
        ).start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
