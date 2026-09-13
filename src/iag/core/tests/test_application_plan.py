#!/usr/bin/env python3
"""Tests for model-authored plans and deterministic partial evaluation."""

from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from iag.core.application_plan import (
    ApplicationPlanBook,
    ApplicationPlanError,
    flatten_plan_facts,
)
from iag.core.conversation_store import ConversationStore


class FakeToolbox:
    def __init__(self, *, provisional: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.provisional = provisional
        self.snapshot_checks = 0

    def assert_world_snapshot_current(self) -> None:
        self.snapshot_checks += 1

    def dispatch(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> tuple[dict[str, object], str]:
        self.calls.append((name, arguments))
        if name == "prepare_action":
            return {"success": True, "run_id": "run-1"}, "prepared"
        return {
            "success": True,
            "run_id": arguments["run_id"],
            "awaiting_fresh_save_confirmation": self.provisional,
        }, "executed"


class ApplicationPlanBookTests(unittest.TestCase):
    def make_book(
        self,
        root: Path,
        facts: dict[str, object],
    ) -> ApplicationPlanBook:
        store = ConversationStore(root / "plan.sqlite3")
        return ApplicationPlanBook(store, "fleet_operations", lambda: facts)

    def test_flatten_uses_stable_object_identity_for_lists(self) -> None:
        facts = flatten_plan_facts({"fleets": [{"fleet_id": 42, "power": 9000}]})
        self.assertEqual(facts["/fleets/fleet_id=42/power"], 9000)

    def test_tolerance_and_dependency_scoped_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current = {"economy": {"minerals": 95, "energy": 20}}
            book = self.make_book(Path(temporary), current)
            book.create(
                {
                    "plan_id": "war-preparation",
                    "title": "War preparation",
                    "objective": "Prepare without collapsing the economy.",
                    "reason": "Player requested a sustained objective.",
                    "nodes": [
                        {
                            "node_id": "goal",
                            "title": "Strategic goal",
                            "objective": "Preserve the overall objective.",
                        },
                        {
                            "node_id": "alloys",
                            "parent_id": "goal",
                            "title": "Alloy branch",
                            "objective": "Maintain the mineral floor.",
                            "expectations": [
                                {
                                    "fact": "/economy/minerals",
                                    "operator": "gte",
                                    "value": 100,
                                    "tolerance": 10.0,
                                }
                            ],
                        },
                        {
                            "node_id": "research",
                            "parent_id": "goal",
                            "title": "Research branch",
                            "objective": "Keep research independent.",
                            "expectations": [
                                {
                                    "fact": "/economy/energy",
                                    "operator": "gte",
                                    "value": 10,
                                }
                            ],
                        },
                        {
                            "node_id": "alloy-followup",
                            "parent_id": "goal",
                            "depends_on": ["alloys"],
                            "title": "Follow-up",
                            "objective": "Continue after the alloy branch.",
                        },
                    ],
                }
            )

            self.assertEqual(book.evaluate()["decision"], "continue")
            current["economy"]["minerals"] = 80
            result = book.evaluate()

        self.assertEqual(result["decision"], "localized_anomaly")
        self.assertEqual(
            set(result["invalidated_node_ids"]),
            {"alloys", "alloy-followup"},
        )
        self.assertIn("goal", result["preserved_node_ids"])
        self.assertIn("research", result["preserved_node_ids"])

    def test_continue_waiver_lasts_only_for_same_observed_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current = {"fleet": {"power": 80}}
            book = self.make_book(Path(temporary), current)
            book.create(
                {
                    "plan_id": "front",
                    "title": "Front",
                    "objective": "Hold the front.",
                    "reason": "test",
                    "nodes": [
                        {
                            "node_id": "line",
                            "title": "Line",
                            "objective": "Keep enough power.",
                            "expectations": [
                                {
                                    "fact": "/fleet/power",
                                    "operator": "gte",
                                    "value": 100,
                                }
                            ],
                        }
                    ],
                }
            )
            anomaly = book.evaluate()
            book.accept_localized_anomaly(anomaly, reason="Still acceptable")
            self.assertEqual(book.evaluate()["decision"], "continue")
            current["fleet"]["power"] = 70
            self.assertEqual(book.evaluate()["decision"], "localized_anomaly")

    def test_deterministic_action_runs_prepare_then_execute_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            book = self.make_book(Path(temporary), {"ready": True})
            book.create(
                {
                    "plan_id": "research",
                    "title": "Research",
                    "objective": "Start the selected technology.",
                    "reason": "test",
                    "nodes": [
                        {
                            "node_id": "physics",
                            "title": "Physics",
                            "objective": "Start one selection.",
                            "action": {
                                "action_id": "physics-1",
                                "prepare_tool": "prepare_action",
                                "prepare_arguments": {"technology_id": 7},
                                "execute_tool": "execute_action",
                                "execute_arguments": {"run_id": "$prepare.run_id"},
                            },
                        }
                    ],
                }
            )
            evaluation = book.evaluate()
            toolbox = FakeToolbox()
            result = book.execute_due_action(
                evaluation,
                toolbox,
                allow_execute=True,
            )
            next_result = book.evaluate()

        self.assertTrue(result["executed"])
        self.assertEqual(
            toolbox.calls,
            [
                ("prepare_action", {"technology_id": 7}),
                ("execute_action", {"run_id": "run-1"}),
            ],
        )
        self.assertEqual(toolbox.snapshot_checks, 2)
        self.assertEqual(next_result["decision"], "continue")

    def test_concurrent_editors_cannot_overwrite_the_same_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.sqlite3"
            first_store = ConversationStore(path)
            second_store = ConversationStore(
                path,
                conversation_id=first_store.conversation_id,
            )
            first = ApplicationPlanBook(
                first_store,
                "fleet_operations",
                lambda: {},
            )
            second = ApplicationPlanBook(
                second_store,
                "fleet_operations",
                lambda: {},
            )
            first.create(
                {
                    "plan_id": "concurrent",
                    "title": "Concurrent edits",
                    "objective": "Preserve revision semantics.",
                    "reason": "test",
                    "nodes": [
                        {
                            "node_id": "left",
                            "title": "Left",
                            "objective": "Original left branch.",
                        },
                        {
                            "node_id": "right",
                            "title": "Right",
                            "objective": "Original right branch.",
                        },
                    ],
                }
            )
            barrier = threading.Barrier(2)

            def edit(book: ApplicationPlanBook, node_id: str) -> object:
                barrier.wait()
                try:
                    return book.edit(
                        {
                            "plan_id": "concurrent",
                            "expected_revision": 1,
                            "observed_change": f"{node_id} changed",
                            "reason": "Concurrent test",
                            "invalidate_node_ids": [],
                            "preserve_node_ids": [
                                "right" if node_id == "left" else "left"
                            ],
                            "remove_node_ids": [],
                            "upsert_nodes": [
                                {
                                    "node_id": node_id,
                                    "title": node_id.title(),
                                    "objective": f"Updated {node_id} branch.",
                                }
                            ],
                            "resume_from_node_id": None,
                        }
                    )
                except ApplicationPlanError as error:
                    return error

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        lambda item: edit(*item),
                        ((first, "left"), (second, "right")),
                    )
                )
            current = first.current()

        self.assertEqual(
            sum(isinstance(item, ApplicationPlanError) for item in results),
            1,
        )
        self.assertEqual(current["revision"], 2)
        objectives = {
            node["node_id"]: node["objective"] for node in current["nodes"]
        }
        self.assertEqual(
            sum(value.startswith("Updated") for value in objectives.values()),
            1,
        )

    def test_snapshot_change_between_prepare_and_execute_stops_mutation(self) -> None:
        class StaleAfterPrepareToolbox(FakeToolbox):
            def assert_world_snapshot_current(self) -> None:
                super().assert_world_snapshot_current()
                if self.snapshot_checks == 2:
                    raise RuntimeError("snapshot changed")

        with tempfile.TemporaryDirectory() as temporary:
            book = self.make_book(Path(temporary), {"ready": True})
            book.create(
                {
                    "plan_id": "stale-check",
                    "title": "Stale check",
                    "objective": "Never execute from mixed save state.",
                    "reason": "test",
                    "nodes": [
                        {
                            "node_id": "action",
                            "title": "Action",
                            "objective": "Prepare and execute once.",
                            "action": {
                                "action_id": "action-1",
                                "prepare_tool": "prepare_action",
                                "prepare_arguments": {},
                                "execute_tool": "execute_action",
                                "execute_arguments": {
                                    "run_id": "$prepare.run_id"
                                },
                            },
                        }
                    ],
                }
            )
            toolbox = StaleAfterPrepareToolbox()
            with self.assertRaisesRegex(RuntimeError, "snapshot changed"):
                book.execute_due_action(
                    book.evaluate(),
                    toolbox,
                    allow_execute=True,
                )

        self.assertEqual(toolbox.calls, [("prepare_action", {})])

    def test_provisional_action_is_not_marked_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current: dict[str, object] = {"ready": True}
            book = self.make_book(Path(temporary), current)
            book.create(
                {
                    "plan_id": "construction",
                    "title": "Construction",
                    "objective": "Build after validation.",
                    "reason": "test",
                    "nodes": [
                        {
                            "node_id": "building",
                            "title": "Building",
                            "objective": "Wait for save confirmation.",
                            "action": {
                                "action_id": "building-1",
                                "prepare_tool": "prepare_action",
                                "prepare_arguments": {},
                                "execute_tool": "execute_action",
                                "execute_arguments": {"run_id": "$prepare.run_id"},
                                "completion_mode": "after_success",
                            },
                        }
                    ],
                }
            )
            result = book.execute_due_action(
                book.evaluate(),
                FakeToolbox(provisional=True),
                allow_execute=True,
            )
            plan = book.current()
            current["batch"] = {
                "execution_reconciliation": {
                    "run_id": "run-1",
                    "state": "confirmed_by_save",
                }
            }
            confirmed = book.evaluate()
            confirmed_plan = book.current()

        self.assertTrue(result["provisional"])
        self.assertEqual(plan["nodes"][0]["status"], "active")
        self.assertIn("building", confirmed["completed_node_ids"])
        self.assertEqual(confirmed_plan["nodes"][0]["status"], "completed")

    def test_external_review_is_localized_and_requires_domain_acknowledgement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            book = self.make_book(Path(temporary), {"fleet": {"power": 100}})
            book.create(
                {
                    "plan_id": "front",
                    "title": "Front",
                    "objective": "Hold the front.",
                    "reason": "test",
                    "nodes": [
                        {
                            "node_id": "goal",
                            "title": "Goal",
                            "objective": "Hold territory.",
                        },
                        {
                            "node_id": "reserve",
                            "parent_id": "goal",
                            "title": "Reserve",
                            "objective": "Maintain a reserve.",
                        },
                    ],
                }
            )
            book.queue_external_review(
                {
                    "review_id": "annual-1",
                    "application_id": "fleet_operations",
                    "reason": "Economic support changed.",
                    "affected_node_ids": ["reserve"],
                    "instruction": "Recheck reserve size only.",
                }
            )
            evaluation = book.evaluate()
            book.complete_external_reviews(["annual-1"])
            pending = book.pending_external_reviews()

        self.assertEqual(evaluation["decision"], "localized_model_decision")
        self.assertEqual(
            [item["node_id"] for item in evaluation["affected_nodes"]],
            ["reserve"],
        )
        self.assertIn("goal", evaluation["preserved_node_ids"])
        self.assertEqual(pending, [])

    def test_future_stage_expectations_wait_for_dependency_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            book = self.make_book(Path(temporary), {"enemy": {"power": 9999}})
            book.create(
                {
                    "plan_id": "staged-war",
                    "title": "Staged war",
                    "objective": "Advance in stages.",
                    "reason": "test",
                    "nodes": [
                        {
                            "node_id": "mobilize",
                            "title": "Mobilize",
                            "objective": "Prepare first.",
                        },
                        {
                            "node_id": "advance",
                            "depends_on": ["mobilize"],
                            "title": "Advance",
                            "objective": "Fight only after mobilization.",
                            "expectations": [
                                {
                                    "fact": "/enemy/power",
                                    "operator": "lte",
                                    "value": 100,
                                }
                            ],
                        },
                    ],
                }
            )

            result = book.evaluate()

        self.assertEqual(result["decision"], "continue")

    def test_each_state_change_action_runs_again_after_watched_fact_changes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current = {"state": {"revision": 1}}
            book = self.make_book(Path(temporary), current)
            book.create(
                {
                    "plan_id": "monitor",
                    "title": "Monitor",
                    "objective": "Act once for each relevant state revision.",
                    "reason": "test",
                    "nodes": [
                        {
                            "node_id": "action",
                            "title": "Action",
                            "objective": "Repeat after a watched change.",
                            "expectations": [
                                {
                                    "fact": "/state/revision",
                                    "operator": "gte",
                                    "value": 0,
                                }
                            ],
                            "action": {
                                "action_id": "repeat",
                                "prepare_tool": "prepare_action",
                                "prepare_arguments": {},
                                "execute_tool": "execute_action",
                                "execute_arguments": {"run_id": "$prepare.run_id"},
                                "cadence": "each_state_change",
                                "completion_mode": "wait_for_conditions",
                            },
                        }
                    ],
                }
            )
            toolbox = FakeToolbox()
            book.execute_due_action(book.evaluate(), toolbox, allow_execute=True)
            self.assertEqual(book.evaluate()["decision"], "continue")
            current["state"]["revision"] = 2
            changed = book.evaluate()
            book.execute_due_action(changed, toolbox, allow_execute=True)

        self.assertEqual(changed["decision"], "execute")
        self.assertEqual(len(toolbox.calls), 4)

    def test_waiver_distinguishes_missing_fact_from_present_null(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current: dict[str, object] = {"state": {}}
            book = self.make_book(Path(temporary), current)
            book.create(
                {
                    "plan_id": "presence",
                    "title": "Presence",
                    "objective": "Track presence as well as value.",
                    "reason": "test",
                    "nodes": [
                        {
                            "node_id": "flag",
                            "title": "Flag",
                            "objective": "Require a truthy flag.",
                            "expectations": [
                                {
                                    "fact": "/state/flag",
                                    "operator": "truthy",
                                }
                            ],
                        }
                    ],
                }
            )
            anomaly = book.evaluate()
            book.accept_localized_anomaly(anomaly, reason="Temporarily absent")
            self.assertEqual(book.evaluate()["decision"], "continue")
            current["state"]["flag"] = None
            changed = book.evaluate()

        self.assertEqual(changed["decision"], "localized_anomaly")

    def test_new_plan_cannot_replace_active_plan_and_archives_completed_plan(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            book = self.make_book(Path(temporary), {})
            first = {
                "plan_id": "first",
                "title": "First",
                "objective": "First objective.",
                "reason": "test",
                "nodes": [
                    {
                        "node_id": "goal",
                        "title": "Goal",
                        "objective": "Complete first.",
                    }
                ],
            }
            book.create(first)
            with self.assertRaisesRegex(ApplicationPlanError, "active plan"):
                book.create({**first, "plan_id": "second", "title": "Second"})
            book.edit(
                {
                    "plan_id": "first",
                    "expected_revision": 1,
                    "observed_change": "The objective was completed.",
                    "reason": "Close the old plan before starting another.",
                    "invalidate_node_ids": [],
                    "preserve_node_ids": ["goal"],
                    "remove_node_ids": [],
                    "upsert_nodes": [],
                    "resume_from_node_id": None,
                    "plan_status": "completed",
                }
            )
            book.create({**first, "plan_id": "second", "title": "Second"})
            inspected = book.inspect({"include_archived": True})

        self.assertEqual(
            inspected["archived_plans"][0]["plan"]["plan_id"],
            "first",
        )


if __name__ == "__main__":
    unittest.main()
