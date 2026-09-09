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


if __name__ == "__main__":
    unittest.main()
