from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from agent_tools import game_month_index  # noqa: E402
from autonomy import coalesce_next_review_after_turn  # noqa: E402
from conversation_store import ConversationStore  # noqa: E402


class AutonomySchedulingTests(unittest.TestCase):
    def test_missed_review_is_coalesced_to_the_next_cycle_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ConversationStore(Path(temporary) / "campaign.sqlite3")
            store.bind_campaign("campaign", "a" * 32, campaign_label="Earth")
            source_index = game_month_index("2200.07.01")
            self.assertIsNotNone(source_index)
            previous = {
                "run_id": "old-run",
                "due_month_index": source_index,
            }
            store.set_state(
                "next_review",
                {
                    "run_id": "active-run",
                    "source_game_date": "2200.07.01",
                    "source_month_index": source_index,
                    "due_month_index": source_index + 6,
                    "next_review_months": 6,
                },
            )

            with (
                patch("autonomy.resolve_current_save", return_value=Path("current.sav")),
                patch(
                    "autonomy.load_save_metadata",
                    return_value={"date": "2201.04.01"},
                ),
            ):
                result = coalesce_next_review_after_turn({}, store, previous)

            updated = store.get_state("next_review")
            self.assertTrue(result["changed"])
            self.assertEqual(result["skipped_intervals"], 1)
            self.assertEqual(updated["due_month_index"], source_index + 12)
            self.assertEqual(
                updated["due_month_index"],
                game_month_index("2201.07.01"),
            )

    def test_unchanged_schedule_is_not_moved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ConversationStore(Path(temporary) / "campaign.sqlite3")
            schedule = {"run_id": "same", "due_month_index": 1}
            store.set_state("next_review", schedule)
            result = coalesce_next_review_after_turn({}, store, schedule)
            self.assertFalse(result["changed"])
            self.assertEqual(store.get_state("next_review"), schedule)


if __name__ == "__main__":
    unittest.main()
