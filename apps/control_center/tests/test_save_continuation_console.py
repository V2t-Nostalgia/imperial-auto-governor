import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.control_center.web_console import ConsoleService
from iag.core.conversation_store import ConversationStore


class SaveContinuationConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "conversation.sqlite3"
        catalog = ConversationStore(self.database)
        self.bound = catalog.create_conversation(
            "Bound Campaign",
            campaign_id="a" * 16,
            campaign_label="test-campaign",
        )
        service = ConsoleService.__new__(ConsoleService)
        service.config = {"runtime_root": str(self.root)}
        service.conversation_db_path = self.database
        service.conversation_store = catalog
        self.service = service

    def test_latest_save_resolves_only_exact_campaign_binding(self) -> None:
        manifest = {
            "campaign_id": "a" * 16,
            "campaign_label": "test-campaign",
        }
        with patch(
            "apps.control_center.web_console.read_manifest",
            return_value=manifest,
        ):
            selected = self.service._current_continuation_store()
        assert selected is not None
        self.assertEqual(
            selected.conversation_id,
            self.bound["conversation_id"],
        )

        manifest["campaign_label"] = "different-campaign"
        with patch(
            "apps.control_center.web_console.read_manifest",
            return_value=manifest,
        ):
            self.assertIsNone(self.service._current_continuation_store())

    def test_mutating_continuation_consumes_stale_autonomy_source(self) -> None:
        store = ConversationStore(
            self.database,
            conversation_id=str(self.bound["conversation_id"]),
        )
        source_identity = {
            "path": "test.sav",
            "modified_ns": 10,
            "size": 20,
        }
        with patch(
            "apps.control_center.web_console.run_save_continuations",
            return_value={
                "schema": "iag.save_continuation_run.v1",
                "state": "executed",
                "mutated_game": True,
                "outcome": {},
            },
        ):
            result = self.service._run_save_continuation_job(
                store,
                source_identity,
            )
        self.assertTrue(result["mutated_game"])
        self.assertEqual(
            store.get_state("last_autonomy_source"),
            source_identity,
        )
        messages = store.public_messages(limit=20)
        self.assertEqual(messages[-1]["kind"], "save_continuation")


if __name__ == "__main__":
    unittest.main()
