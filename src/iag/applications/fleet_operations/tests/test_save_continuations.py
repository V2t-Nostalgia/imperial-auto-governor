import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iag.applications.fleet_operations.agent_tools import (
    PENDING_ARMY_RECRUITMENT_KEY,
    PENDING_CAMPAIGN_KEY,
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
            "experimental_fleet_tools_enabled": True,
            "experimental_fleet_attack_enabled": True,
            "experimental_invasion_tools_enabled": True,
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

    @patch(
        "iag.applications.save_continuations._current_save_reference",
        return_value={
            "path": "test.sav",
            "sha256": "fresh-save",
            "game_date": "2201.02.01",
        },
    )
    def test_army_recruitment_reconciliation_is_dispatched_once(
        self,
        _reference,
    ) -> None:
        self.store.set_state(PENDING_NEW_FLEET_KEY, None)
        self.store.set_state(
            PENDING_ARMY_RECRUITMENT_KEY,
            {
                "recruitment_run_id": "army-1",
                "phase": "awaiting_transport_save",
                "last_action_save_sha256": "old-save",
            },
        )
        with patch(
            "iag.applications.save_continuations."
            "FleetToolbox.reconcile_pending_army_recruitment_from_save",
            return_value={
                "state": "confirmed_in_save",
                "mutated_game": False,
                "transport_fleet_id": 19,
            },
        ) as continuation:
            result = run_save_continuations(self.config, self.store)

        continuation.assert_called_once_with()
        self.assertEqual(result["handler"], "army_recruitment")
        self.assertFalse(result["mutated_game"])
        self.assertFalse(probe_save_continuations(self.config, self.store)["due"])

    @patch(
        "iag.applications.save_continuations._current_save_reference",
        return_value={
            "path": "test.sav",
            "sha256": "fresh-save",
            "game_date": "2201.02.01",
        },
    )
    def test_campaign_continuation_advances_one_action_per_save(
        self,
        _reference,
    ) -> None:
        self.store.set_state(PENDING_NEW_FLEET_KEY, None)
        self.store.set_state(
            PENDING_CAMPAIGN_KEY,
            {
                "campaign_plan_id": "campaign-1",
                "status": "awaiting_fresh_save",
                "last_action_save_sha256": "old-save",
            },
        )
        with patch(
            "iag.applications.save_continuations."
            "FleetToolbox.continue_pending_campaign_from_save",
            return_value={
                "state": "executed",
                "mutated_game": True,
                "action": "attack_fleet",
            },
        ) as continuation:
            result = run_save_continuations(self.config, self.store)

        continuation.assert_called_once_with()
        self.assertEqual(result["handler"], "campaign_route")
        self.assertTrue(result["mutated_game"])
        repeated = probe_save_continuations(self.config, self.store)
        self.assertFalse(repeated["due"])
        self.assertTrue(repeated["blocks_autonomy"])
        self.assertEqual(
            repeated["reason"],
            "save_already_consumed_by_mutating_continuation",
        )
        campaign_probe = next(
            item
            for item in repeated["handlers"]
            if item["handler"] == "campaign_route"
        )
        self.assertEqual(campaign_probe["reason"], "already_attempted_for_save")

    @patch(
        "iag.applications.save_continuations._current_save_reference",
        return_value={
            "path": "test.sav",
            "sha256": "fresh-save",
            "game_date": "2201.02.01",
        },
    )
    def test_campaign_waiting_for_transport_allows_a_model_turn(
        self,
        _reference,
    ) -> None:
        self.store.set_state(PENDING_NEW_FLEET_KEY, None)
        self.store.set_state(
            PENDING_CAMPAIGN_KEY,
            {
                "campaign_plan_id": "campaign-1",
                "status": "waiting_for_transport_fleet",
                "last_action_save_sha256": "old-save",
                "last_evaluated_save_sha256": "fresh-save",
            },
        )

        probe = probe_save_continuations(self.config, self.store)

        self.assertFalse(probe["due"])
        self.assertFalse(probe["blocks_autonomy"])
        campaign_probe = next(
            item
            for item in probe["handlers"]
            if item["handler"] == "campaign_route"
        )
        self.assertEqual(campaign_probe["reason"], "already_evaluated_for_save")


if __name__ == "__main__":
    unittest.main()
