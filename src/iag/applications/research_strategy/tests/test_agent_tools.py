from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iag.applications.research_strategy.agent_tools import (
    TechnologyToolbox,
    TechnologyToolError,
)
from iag.core.conversation_store import ConversationStore
from iag.stellaris.execution.action_registry import BackendId, ResearchTarget
from iag.stellaris.execution.broker import (
    BackendExecutionResult,
    BackendSequenceResult,
    ExecutionBroker,
    OrderedActionSequence,
    SourceSnapshot,
)


class RecordingResearchBackend:
    backend_id = BackendId.SESSION_PROXY

    def __init__(self) -> None:
        self.sequences: list[OrderedActionSequence] = []
        self.next_results: list[BackendExecutionResult] | None = None

    def capabilities(self) -> tuple[str, ...]:
        return (
            "stellaris.execution.v1",
            "stellaris.action.start_research.v1",
            "stellaris.action.stop_research.v1",
        )

    def supports(self, spec: object) -> bool:
        return getattr(spec, "action_type", None) in {
            "start_research",
            "stop_research",
        }

    def execute(self, action: object) -> BackendExecutionResult:
        raise AssertionError("Research toolbox should use an ordered sequence.")

    def execute_ordered_sequence(
        self,
        sequence: OrderedActionSequence,
    ) -> BackendSequenceResult:
        self.sequences.append(sequence)
        results = self.next_results
        if results is None:
            results = [
                BackendExecutionResult(
                    backend_id=self.backend_id,
                    status="confirmed_by_packet",
                    evidence={"raw_transport": "hidden"},
                )
                for _action in sequence.actions
            ]
        return BackendSequenceResult(
            backend_id=self.backend_id,
            requested_steps=len(sequence.actions),
            results=tuple(results),
        )


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
        self.backend = RecordingResearchBackend()
        self.broker = ExecutionBroker(
            backends=(self.backend,),
            application_action_types={
                "research_strategy": frozenset({"start_research", "stop_research"})
            },
            snapshot_validator=lambda _snapshot: None,
            candidate_validator=lambda _action: None,
        )

    def toolbox(self, current: str | None = None) -> TechnologyToolbox:
        value = TechnologyToolbox(
            self.config,
            self.store,
            allow_execute=True,
            execution_broker=self.broker,
        )
        value._profile = lambda: (  # type: ignore[method-assign]
            self.save,
            research_profile(current),
        )
        value._save_path = lambda: self.save  # type: ignore[method-assign]
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
        result = toolbox.execute({"run_id": prepared["run_id"]})
        self.assertTrue(result["success"])
        self.assertNotIn("raw_transport", str(result["confirmations"]))
        diagnostics = self.store.get_state(
            "last_research_execution_diagnostics",
            {},
        )
        self.assertIn("raw_transport", str(diagnostics))
        self.assertEqual(len(self.backend.sequences), 1)
        actions = self.backend.sequences[0].actions
        self.assertEqual(len(actions), 1)
        self.assertEqual(
            actions[0].action_type,
            "start_research",
        )
        self.assertIsInstance(actions[0].target, ResearchTarget)
        self.assertEqual(actions[0].target.technology_id, "tech_shields_2")

    def test_prepare_binds_the_source_save_snapshot(self) -> None:
        toolbox = self.toolbox()
        prepared = toolbox.prepare(
            {
                "area": "physics",
                "technology_id": "tech_shields_2",
                "reason": "Bind the candidate to this save.",
            }
        )
        snapshot = SourceSnapshot.model_validate(prepared["source_snapshot"])
        self.assertEqual(snapshot.save_sha256, prepared["source_save_sha256"])

        self.save.write_bytes(b"new save")
        with self.assertRaisesRegex(TechnologyToolError, "已经变化"):
            toolbox._validate_source_snapshot(snapshot)

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
        result = toolbox.execute({"run_id": prepared["run_id"]})
        actions = [action.action_type for action in self.backend.sequences[-1].actions]
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
        self.backend.next_results = [
            BackendExecutionResult(
                backend_id=BackendId.SESSION_PROXY,
                status="confirmed_by_packet",
            ),
            BackendExecutionResult(
                backend_id=BackendId.SESSION_PROXY,
                status="failed",
                error="start rejected",
            ),
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
