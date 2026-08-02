from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from campaign_strategy import (  # noqa: E402
    activate_emergency,
    end_emergency,
    public_strategy_state,
    save_decade_plan,
)
from conversation_store import ConversationStore  # noqa: E402


class CampaignStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        root = RUNTIME / "tests" / "runtime_test_data"
        self.database_path = root / f"strategy_{uuid.uuid4().hex}.sqlite3"
        self.store = ConversationStore(self.database_path)

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            self.database_path.with_name(
                self.database_path.name + suffix
            ).unlink(missing_ok=True)

    def test_decade_period_and_next_draft_follow_game_date(self) -> None:
        state = save_decade_plan(
            self.store,
            text="优先殖民和科研基础设施。",
            game_date="2200.01.01",
            target="current",
        )
        self.assertEqual(state["current"]["start_year"], 2200)
        self.assertEqual(state["current"]["end_year"], 2209)
        self.assertFalse(state["renewal_open"])

        state = public_strategy_state(self.store, "2209.01.01")["decade"]
        self.assertTrue(state["renewal_open"])
        save_decade_plan(
            self.store,
            text="转入合金和舰队容量扩张。",
            game_date="2209.01.01",
            target="next",
        )
        state = public_strategy_state(self.store, "2210.01.01")["decade"]
        self.assertEqual(state["current"]["start_year"], 2210)
        self.assertIn("合金", state["current"]["text"])
        self.assertIsNone(state["next_draft"])

    def test_emergency_requires_explicit_end_and_suspends_plan(self) -> None:
        save_decade_plan(
            self.store,
            text="扩张。",
            game_date="2200.01.01",
            target="current",
        )
        activate_emergency(
            self.store,
            title="战争动员",
            directive="暂停科研扩张，优先合金和能源。",
            game_date="2203.04.01",
        )
        state = public_strategy_state(self.store, "2204.01.01")
        self.assertTrue(state["emergency"]["active"])
        self.assertTrue(state["decade_suspended"])

        ended = end_emergency(self.store, game_date="2204.01.01")
        self.assertFalse(ended["active"])
        state = public_strategy_state(self.store, "2204.01.01")
        self.assertFalse(state["decade_suspended"])


if __name__ == "__main__":
    unittest.main()
