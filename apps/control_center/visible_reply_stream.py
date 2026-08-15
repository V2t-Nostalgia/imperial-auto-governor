#!/usr/bin/env python3
"""Thread-safe visible conversation events for the in-game overlay."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

ALLOWED_EVENT_TYPES = {
    "turn_started",
    "user_message",
    "assistant_started",
    "assistant_delta",
    "assistant_final",
    "turn_finished",
}
ALLOWED_PAYLOAD_KEYS = {
    "turn_started": {"trigger"},
    "user_message": {"id", "role", "kind", "content"},
    "assistant_started": {"round_index"},
    "assistant_delta": {"round_index", "delta"},
    "assistant_final": {
        "id",
        "role",
        "kind",
        "content",
        "round_index",
    },
    "turn_finished": {"trigger"},
}


@dataclass(frozen=True, slots=True)
class VisibleReplyEvent:
    sequence: int
    conversation_id: str
    event_type: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "conversation_id": self.conversation_id,
            "event_type": self.event_type,
            "payload": dict(self.payload),
        }


class VisibleReplyBroker:
    """Publish only player text and model-visible text to overlay clients."""

    def __init__(self, *, maximum_events_per_conversation: int = 2048) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._maximum_events = max(64, int(maximum_events_per_conversation))
        self._events: dict[str, deque[VisibleReplyEvent]] = defaultdict(
            lambda: deque(maxlen=self._maximum_events)
        )
        self._states: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"active": False, "draft": "", "latest_sequence": 0}
        )
        self._sequence = 0

    def publish(
        self,
        conversation_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> VisibleReplyEvent:
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"Unsupported visible event type: {event_type}")
        selected = str(conversation_id).strip()
        if not selected:
            raise ValueError("conversation_id cannot be empty.")
        safe_payload = dict(payload or {})
        unexpected = set(safe_payload) - ALLOWED_PAYLOAD_KEYS[event_type]
        if unexpected:
            raise ValueError(
                "Visible event contains unsupported payload fields: "
                + ", ".join(sorted(unexpected))
            )
        with self._condition:
            self._sequence += 1
            event = VisibleReplyEvent(
                self._sequence,
                selected,
                event_type,
                safe_payload,
            )
            self._events[selected].append(event)
            state = self._states[selected]
            state["latest_sequence"] = event.sequence
            if event_type == "turn_started":
                state["active"] = True
                state["draft"] = ""
            elif event_type == "assistant_started":
                state["draft"] = ""
            elif event_type == "assistant_delta":
                state["draft"] += str(safe_payload.get("delta") or "")
            elif event_type == "assistant_final":
                state["draft"] = ""
            elif event_type == "turn_finished":
                state["active"] = False
                state["draft"] = ""
            self._condition.notify_all()
            return event

    def events_after(
        self,
        conversation_id: str,
        after_sequence: int,
        *,
        limit: int = 256,
    ) -> list[VisibleReplyEvent]:
        selected = str(conversation_id).strip()
        safe_after = max(0, int(after_sequence))
        safe_limit = max(1, min(int(limit), 1024))
        with self._condition:
            return [
                event
                for event in self._events.get(selected, ())
                if event.sequence > safe_after
            ][:safe_limit]

    def wait_after(
        self,
        conversation_id: str,
        after_sequence: int,
        *,
        timeout_seconds: float = 25.0,
        limit: int = 256,
    ) -> list[VisibleReplyEvent]:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        with self._condition:
            while True:
                events = self.events_after(
                    conversation_id,
                    after_sequence,
                    limit=limit,
                )
                if events:
                    return events
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)

    def snapshot(self, conversation_id: str) -> dict[str, Any]:
        selected = str(conversation_id).strip()
        with self._condition:
            state = self._states.get(selected)
            if state is None:
                return {
                    "active": False,
                    "draft": "",
                    "latest_sequence": self._sequence,
                }
            return dict(state)
