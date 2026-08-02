from __future__ import annotations

import sys
import uuid
import unittest
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from conversation_store import ConversationStore  # noqa: E402


class ConversationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        root = RUNTIME / "tests" / "runtime_test_data"
        self.database_path = root / f"conversation_{uuid.uuid4().hex}.sqlite3"
        self.store = ConversationStore(self.database_path)

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            self.database_path.with_name(
                self.database_path.name + suffix
            ).unlink(missing_ok=True)

    def test_replays_reasoning_only_with_tool_call_group(self) -> None:
        self.store.append("user", "检查帝国")
        self.store.append(
            "assistant",
            "",
            reasoning_content="private reasoning",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "inspect_empire_state",
                        "arguments": "{}",
                    },
                }
            ],
            visible=False,
        )
        self.store.append(
            "tool",
            '{"secret_state":"large"}',
            tool_call_id="call_1",
            tool_name="inspect_empire_state",
            metadata={"public_summary": "已读取存档。", "success": True},
        )
        self.store.append(
            "assistant",
            "状态稳定。",
            reasoning_content="final private reasoning",
        )

        messages, stats = self.store.protocol_messages(max_chars=10_000)

        self.assertEqual(stats["omitted_messages"], 0)
        self.assertEqual(messages[1]["reasoning_content"], "private reasoning")
        self.assertEqual(messages[2]["tool_call_id"], "call_1")
        self.assertNotIn("reasoning_content", messages[3])

        public = self.store.public_messages()
        rendered = "\n".join(item["content"] for item in public)
        self.assertIn("已读取存档。", rendered)
        self.assertNotIn("private reasoning", rendered)
        self.assertNotIn("secret_state", rendered)

    def test_context_trimming_never_splits_latest_tool_group(self) -> None:
        self.store.append("user", "x" * 5000)
        self.store.append(
            "assistant",
            "",
            reasoning_content="reason",
            tool_calls=[
                {
                    "id": "call_latest",
                    "type": "function",
                    "function": {
                        "name": "inspect_empire_state",
                        "arguments": "{}",
                    },
                }
            ],
            visible=False,
        )
        self.store.append(
            "tool",
            "{}",
            tool_call_id="call_latest",
            tool_name="inspect_empire_state",
        )

        messages, stats = self.store.protocol_messages(max_chars=800)

        self.assertEqual([item["role"] for item in messages], ["assistant", "tool"])
        self.assertEqual(messages[1]["tool_call_id"], "call_latest")
        self.assertEqual(stats["omitted_messages"], 1)

    def test_repairs_interrupted_tool_call_after_restart(self) -> None:
        self.store.append(
            "assistant",
            "",
            reasoning_content="unfinished",
            tool_calls=[
                {
                    "id": "call_interrupted",
                    "type": "function",
                    "function": {
                        "name": "inspect_empire_state",
                        "arguments": "{}",
                    },
                }
            ],
            visible=False,
        )

        restarted = ConversationStore(self.database_path)
        messages, _stats = restarted.protocol_messages(max_chars=10_000)

        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-1]["tool_call_id"], "call_interrupted")
        self.assertIn("ended before", messages[-1]["content"])

    def test_state_round_trip(self) -> None:
        value = {"mode": "advisory", "months": 2}
        self.store.set_state("next_review", value)
        self.assertEqual(self.store.get_state("next_review"), value)

    def test_manages_isolated_campaign_conversations(self) -> None:
        legacy = self.store.conversation_metadata()
        self.assertEqual(legacy["conversation_id"], "campaign")
        self.assertEqual(legacy["title"], "当前战役")

        self.store.bind_campaign(
            "campaign",
            "a" * 32,
            campaign_label="Earth",
        )
        second = self.store.create_conversation(
            "Second Campaign",
            campaign_id="b" * 32,
            campaign_label="Mars",
        )
        second_store = ConversationStore(
            self.database_path,
            conversation_id=second["conversation_id"],
        )
        self.store.append("user", "legacy-only")
        second_store.append("user", "second-only")

        first_messages = self.store.public_messages()
        second_messages = second_store.public_messages()
        self.assertEqual(
            [item["content"] for item in first_messages],
            ["legacy-only"],
        )
        self.assertEqual(
            [item["content"] for item in second_messages],
            ["second-only"],
        )

        second_store.set_active_conversation(second["conversation_id"])
        self.assertEqual(
            self.store.active_conversation_id(),
            second["conversation_id"],
        )
        self.assertEqual(
            self.store.conversation_for_campaign("b" * 32)["conversation_id"],
            second["conversation_id"],
        )

    def test_campaign_binding_is_unique_and_archive_is_reversible(self) -> None:
        self.store.bind_campaign("campaign", "a" * 32)
        second = self.store.create_conversation("Unbound")
        with self.assertRaises(ValueError):
            self.store.bind_campaign(
                second["conversation_id"],
                "a" * 32,
            )

        archived = self.store.set_conversation_archived(
            second["conversation_id"],
            True,
        )
        self.assertTrue(archived["archived"])
        self.assertEqual(len(self.store.list_conversations()), 1)
        restored = self.store.set_conversation_archived(
            second["conversation_id"],
            False,
        )
        self.assertFalse(restored["archived"])

    def test_explicit_assignment_does_not_switch_active_conversation(self) -> None:
        self.store.bind_campaign(
            "campaign",
            "a" * 32,
            campaign_label="First",
        )
        second = self.store.create_conversation(
            "Second",
            campaign_id="b" * 32,
            campaign_label="Second",
        )

        update = self.store.assign_campaign_to_conversation(
            "a" * 32,
            second["conversation_id"],
            campaign_label="First Reassigned",
        )

        self.assertEqual(self.store.active_conversation_id(), "campaign")
        self.assertIsNone(
            self.store.conversation_metadata("campaign")["campaign_id"]
        )
        reassigned = self.store.conversation_metadata(
            second["conversation_id"]
        )
        self.assertEqual(reassigned["campaign_id"], "a" * 32)
        self.assertEqual(reassigned["campaign_label"], "First Reassigned")
        self.assertIsNone(self.store.conversation_for_campaign("b" * 32))
        self.assertEqual(
            update["displaced_conversation_id"],
            "campaign",
        )
        self.assertEqual(update["previous_target_campaign_id"], "b" * 32)

        unbound = self.store.assign_campaign_to_conversation("a" * 32, None)

        self.assertEqual(self.store.active_conversation_id(), "campaign")
        self.assertIsNone(
            self.store.conversation_metadata(
                second["conversation_id"]
            )["campaign_id"]
        )
        self.assertEqual(
            unbound["displaced_conversation_id"],
            second["conversation_id"],
        )


if __name__ == "__main__":
    unittest.main()
