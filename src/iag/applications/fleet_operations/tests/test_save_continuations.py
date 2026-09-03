import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iag.applications.fleet_operations.agent_tools import (
    PENDING_NEW_FLEET_KEY,
)
from iag.applications.save_continuations import (
    ATTEMPTS_STATE_KEY,
    probe_save_continuations,
    run_save_continuations,
)
from iag.core.conversation_store import ConversationStore


class SaveContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ConversationStore(self.root / "conversation.sqlite3")
        self.store.bind_campaign(
            "campaign",
            "a" * 16,
            campaign_label="test-campaign",
        )
        self.store.set_state("autonomy_mode", "execute")
        self.config = {
            "runtime_root": str(self.root),
            "execution_mode": "session_proxy",
            "experimental_new_fleet_tools_enabled": True,
        }
        self.pending = {
            "schema": "iag.pending_new_fleet.v1",
            "phase": "awaiting_template_save",
            "creation_run_id": "new_fleet_1",
            "design_id": 100,
            "target_count": 3,
            "baseline_template_ids": [17],
            "last_action_save_sha256": "old-save",
        }
        self.store.set_state(PENDING_NEW_FLEET_KEY, self.pending)

    @patch(
        "iag.applications.save_continuations._current_save_reference",
        return_value={
            "path": "test.sav",
            "sha256": "fresh-save",
            "game_date": "2201.02.01",
        },
    )
    def test_fresh_save_runs_fixed_continuation_once(self, _reference) -> None:
        probe = probe_save_continuations(self.config, self.store)
        self.assertTrue(probe["due"])
        self.assertTrue(probe["blocks_autonomy"])

        with patch(
            "iag.applications.save_continuations."
            "FleetToolbox.continue_pending_new_fleet_from_save",
            return_value={
                "state": "executed",
                "mutated_game": True,
                "fleet_template_id": 177,
            },
        ) as continuation:
            result = run_save_continuations(self.config, self.store)

        continuation.assert_called_once_with()
        self.assertTrue(result["mutated_game"])
        self.assertEqual(result["state"], "executed")
        self.assertEqual(
            self.store.get_state(ATTEMPTS_STATE_KEY)[
                "new_fleet_creation:new_fleet_1"
            ],
            "fresh-save",
        )
        repeated = probe_save_continuations(self.config, self.store)
        self.assertFalse(repeated["due"])
        self.assertEqual(
            repeated["handlers"][0]["reason"],
            "already_attempted_for_save",
        )

    @patch(
        "iag.applications.save_continuations._current_save_reference",
        return_value={
            "path": "test.sav",
            "sha256": "old-save",
            "game_date": "2201.01.01",
        },
    )
    def test_stale_save_blocks_another_model_audit(self, _reference) -> None:
        probe = probe_save_continuations(self.config, self.store)
        self.assertFalse(probe["due"])
        self.assertTrue(probe["blocks_autonomy"])
        self.assertEqual(
            probe["handlers"][0]["reason"],
            "waiting_for_fresh_save",
        )


if __name__ == "__main__":
    unittest.main()
