#!/usr/bin/env python3
"""Tests for the redacted game-overlay event boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apps.control_center.visible_reply_stream import VisibleReplyBroker
from iag.core.conversation_store import ConversationStore


class VisibleReplyBrokerTests(unittest.TestCase):
    def test_draft_and_turn_state_follow_visible_events(self) -> None:
        broker = VisibleReplyBroker()
        broker.publish("campaign", "turn_started")
        broker.publish("campaign", "assistant_started")
        broker.publish("campaign", "assistant_delta", {"delta": "alpha"})
        broker.publish("campaign", "assistant_delta", {"delta": " beta"})
        self.assertEqual(
            broker.snapshot("campaign"),
            {"active": True, "draft": "alpha beta", "latest_sequence": 4},
        )
        broker.publish(
            "campaign",
            "assistant_final",
            {"id": 7, "content": "alpha beta"},
        )
        broker.publish("campaign", "turn_finished")
        self.assertFalse(broker.snapshot("campaign")["active"])
        self.assertEqual(broker.snapshot("campaign")["draft"], "")

    def test_private_payload_types_are_rejected(self) -> None:
        broker = VisibleReplyBroker()
        with self.assertRaises(ValueError):
            broker.publish("campaign", "tool_result", {})
        with self.assertRaises(ValueError):
            broker.publish(
                "campaign",
                "assistant_delta",
                {"reasoning_content": "secret"},
            )


class OverlayTranscriptTests(unittest.TestCase):
    def test_overlay_query_excludes_tools_system_and_autonomy_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ConversationStore(Path(temporary) / "conversation.sqlite3")
            store.append("user", "visible player", kind="operator_message")
            store.append("user", "hidden trigger", kind="autonomy_trigger")
            store.append("assistant", "visible model", kind="assistant_message")
            store.append("assistant", "local fallback", kind="local_status")
            store.append("tool", "tool payload", kind="tool_result")
            store.append("system", "system audit", kind="execution_fact_ledger")

            messages = store.overlay_messages()

        self.assertEqual(
            [(item["role"], item["content"]) for item in messages],
            [
                ("user", "visible player"),
                ("assistant", "visible model"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
