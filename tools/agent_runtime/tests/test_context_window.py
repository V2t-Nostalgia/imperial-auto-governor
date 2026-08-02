from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from context_window import build_context_messages  # noqa: E402
from conversation_store import ConversationStore  # noqa: E402


class ContextWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        root = RUNTIME / "tests" / "runtime_test_data"
        self.database_path = root / f"context_{uuid.uuid4().hex}.sqlite3"
        self.store = ConversationStore(self.database_path)

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            self.database_path.with_name(
                self.database_path.name + suffix
            ).unlink(missing_ok=True)

    def test_rolls_up_api_context_without_deleting_local_history(self) -> None:
        for index in range(18):
            self.store.append("user", f"长期要求 {index}：" + "发展科研。" * 160)
            self.store.append("assistant", f"已记录第 {index} 项要求。")
        stored_before = self.store.public_state()["stored_messages"]
        summary_calls: list[list[dict]] = []

        def completion(_config, messages, *, tools=None):
            self.assertIsNone(tools)
            summary_calls.append(messages)
            return {"content": "玩家要求持续发展科研；没有任何未确认建设。"}

        messages, stats = build_context_messages(
            self.store,
            {
                "model_context_window_tokens": 8192,
                "context_output_reserve_tokens": 1024,
                "context_compression_enabled": True,
                "context_compression_trigger_percent": 30,
                "context_compression_target_percent": 20,
                "context_compression_min_recent_segments": 3,
            },
            system_prompt="系统提示",
            tool_schemas=[],
            completion_fn=completion,
        )

        self.assertTrue(summary_calls)
        self.assertTrue(stats["compacted_now"])
        self.assertIn("概况版较早会话", messages[1]["content"])
        self.assertEqual(
            self.store.public_state()["stored_messages"], stored_before
        )
        self.assertIsNotNone(self.store.get_state("context_summary", None))


if __name__ == "__main__":
    unittest.main()
