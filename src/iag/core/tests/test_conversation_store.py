#!/usr/bin/env python3
"""Persistence tests for provider-specific private protocol metadata."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iag.core.conversation_store import ConversationStore


class ConversationStoreTests(unittest.TestCase):
    def test_anthropic_replay_blocks_round_trip_without_public_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ConversationStore(Path(temporary) / "conversation.sqlite3")
            store.append(
                "assistant",
                "",
                reasoning_content="private",
                tool_calls=[
                    {
                        "id": "toolu_1",
                        "type": "function",
                        "function": {"name": "inspect", "arguments": "{}"},
                    }
                ],
                kind="tool_call",
                metadata={
                    "origin": "model",
                    "anthropic_content": [
                        {
                            "type": "thinking",
                            "thinking": "private",
                            "signature": "signed-thinking",
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "inspect",
                            "input": {},
                        },
                    ],
                },
            )
            store.append(
                "tool",
                "{}",
                tool_call_id="toolu_1",
                tool_name="inspect",
                kind="tool_result",
                metadata={"public_summary": "Inspection complete."},
            )

            messages, _usage = store.protocol_messages()
            public = store.public_messages()

        self.assertEqual(
            messages[0]["anthropic_content"][0]["signature"],
            "signed-thinking",
        )
        self.assertNotIn("anthropic_content", public[0]["metadata"])

    def test_orphan_tool_audit_is_retained_but_not_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ConversationStore(Path(temporary) / "conversation.sqlite3")
            store.append("user", "before")
            store.append(
                "tool",
                "{}",
                tool_call_id="orphan",
                tool_name="inspect",
                kind="tool_result",
                metadata={"public_summary": "Retained audit."},
            )
            store.append("assistant", "after")

            messages, usage = store.protocol_messages()
            public = store.public_messages()

        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
        self.assertEqual(public[1]["content"], "Retained audit.")
        self.assertEqual(usage["quarantined_messages"], 1)
        self.assertEqual(usage["quarantined_groups"], 1)

    def test_incomplete_tool_group_is_quarantined_as_one_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ConversationStore(Path(temporary) / "conversation.sqlite3")
            store.append(
                "assistant",
                "working",
                tool_calls=[
                    {
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "a", "arguments": "{}"},
                    },
                    {
                        "id": "call_b",
                        "type": "function",
                        "function": {"name": "b", "arguments": "{}"},
                    },
                ],
            )
            store.append(
                "tool",
                "{}",
                tool_call_id="call_a",
                tool_name="a",
            )

            messages, usage = store.protocol_messages()

        self.assertEqual(messages, [])
        self.assertEqual(usage["quarantined_messages"], 2)

    def test_complete_multi_tool_group_remains_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ConversationStore(Path(temporary) / "conversation.sqlite3")
            calls = [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": call_id, "arguments": "{}"},
                }
                for call_id in ("call_a", "call_b")
            ]
            store.append("assistant", "", tool_calls=calls)
            for call_id in ("call_b", "call_a"):
                store.append(
                    "tool",
                    "{}",
                    tool_call_id=call_id,
                    tool_name=call_id,
                )

            messages, usage = store.protocol_messages()

        self.assertEqual(
            [item["role"] for item in messages],
            ["assistant", "tool", "tool"],
        )
        self.assertEqual(usage["quarantined_messages"], 0)

    def test_application_tool_audit_uses_public_summary_without_becoming_tool(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ConversationStore(Path(temporary) / "conversation.sqlite3")
            store.append(
                "system",
                '{"private":"structured result"}',
                tool_name="inspect_fleets",
                kind="application_tool_audit",
                metadata={"public_summary": "已读取 3 支玩家舰队。"},
            )

            public = store.public_messages()
            protocol, usage = store.protocol_messages()

        self.assertEqual(public[0]["content"], "已读取 3 支玩家舰队。")
        self.assertEqual(public[0]["role"], "system")
        self.assertEqual(protocol[0]["role"], "system")
        self.assertEqual(usage["quarantined_messages"], 0)


if __name__ == "__main__":
    unittest.main()
