from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iag.applications.research_strategy.agent_tools import (
    TechnologyToolbox,
    TechnologyToolError,
)
from iag.core.conversation_store import ConversationStore


def research_profile(current: str | None = None) -> dict[str, object]:
    fields = {
        area: {
            "current": None,
            "alternatives": [],
            "always_available": [],
            "legal_candidate_ids": [],
            "stored_research_points": 0.0,
            "auto_researching": False,
        }
        for area in ("physics", "society", "engineering")
    }
    fields["physics"] = {
        **fields["physics"],
        "current": (
            {"technology_id": current, "progress": 10.0}
            if current is not None
            else None
        ),
        "alternatives": ["tech_shields_2", "tech_lasers_2"],
        "legal_candidate_ids": ["tech_shields_2", "tech_lasers_2"],
    }
    return {
        "schema": "iag.stellaris_research_state.v1",
        "schema_version": 1,
        "game_date": "2204.09.15",
        "owner_country_id": 0,
        "player_country_ids": [0],
        "known_technologies": ["tech_shields_1"],
        "known_technology_levels": {"tech_shields_1": 1},
        "fields": fields,
        "always_available_unclassified": [],
        "stored_points_by_technology": {},
    }


class TechnologyToolboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.save = self.root / "test.sav"
        self.save.write_bytes(b"test")
        self.store = ConversationStore(self.root / "conversation.sqlite3")
        self.config = {
            "runtime_root": str(self.root),
            "execution_mode": "session_proxy",
            "experimental_research_tools_enabled": True,
            "experimental_research_reselection_enabled": False,
        }

    def toolbox(self, current: str | None = None) -> TechnologyToolbox:
        value = TechnologyToolbox(self.config, self.store, allow_execute=True)
        value._profile = lambda: (  # type: ignore[method-assign]
            self.save,
            research_profile(current),
        )
        return value

    def test_rejects_technology_not_in_save_candidates(self) -> None:
        with self.assertRaisesRegex(TechnologyToolError, "合法候选"):
            self.toolbox().prepare(
                {
                    "area": "physics",
                    "technology_id": "tech_dark_matter_power_core",
                    "reason": "Not rolled.",
                }
            )

    def test_empty_field_executes_one_start_command(self) -> None:
        toolbox = self.toolbox()
        prepared = toolbox.prepare(
            {
                "area": "physics",
                "technology_id": "tech_shields_2",
                "reason": "Defensive baseline.",
            }
        )
        with patch(
            "iag.applications.research_strategy.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.return_value = {
                "outcome": "confirmed"
            }
            result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertTrue(result["success"])
        controller_type.return_value.arm_and_wait.assert_called_once()
        self.assertEqual(
            controller_type.return_value.arm_and_wait.call_args.kwargs["action"],
            "start_research",
        )

    def test_reselection_is_default_denied_and_then_strictly_ordered(self) -> None:
        with self.assertRaisesRegex(TechnologyToolError, "尚未启用"):
            self.toolbox("tech_shields_2").prepare(
                {
                    "area": "physics",
                    "technology_id": "tech_lasers_2",
                    "reason": "Switch focus.",
                }
            )

        self.config["experimental_research_reselection_enabled"] = True
        toolbox = self.toolbox("tech_shields_2")
        prepared = toolbox.prepare(
            {
                "area": "physics",
                "technology_id": "tech_lasers_2",
                "reason": "Switch focus.",
            }
        )
        with patch(
            "iag.applications.research_strategy.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.side_effect = [
                {"outcome": "confirmed"},
                {"outcome": "confirmed"},
            ]
            result = toolbox.execute({"run_id": prepared["run_id"]})
        actions = [
            call.kwargs["action"]
            for call in controller_type.return_value.arm_and_wait.call_args_list
        ]
        self.assertEqual(actions, ["stop_research", "start_research"])
        self.assertEqual(result["replaced_technology_id"], "tech_shields_2")

    def test_partial_reselection_records_only_confirmed_steps(self) -> None:
        self.config["experimental_research_reselection_enabled"] = True
        toolbox = self.toolbox("tech_shields_2")
        prepared = toolbox.prepare(
            {
                "area": "physics",
                "technology_id": "tech_lasers_2",
                "reason": "Switch focus.",
            }
        )
        with patch(
            "iag.applications.research_strategy.agent_tools.SessionProxyController"
        ) as controller_type:
            controller_type.return_value.arm_and_wait.side_effect = [
                {"outcome": "confirmed"},
                {"outcome": "failed", "error": "start rejected"},
            ]
            with self.assertRaisesRegex(TechnologyToolError, "start rejected"):
                toolbox.execute({"run_id": prepared["run_id"]})

        audit = self.store.get_state("last_research_execution", {})
        self.assertFalse(audit["success"])
        self.assertEqual(audit["completed_steps"], 1)
        self.assertEqual(len(audit["confirmations"]), 2)
        self.assertIsNone(toolbox.prepared)


if __name__ == "__main__":
    unittest.main()
