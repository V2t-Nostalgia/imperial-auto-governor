#!/usr/bin/env python3
"""Tests for tightly scoped fast-adviser plan repair."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from iag.applications.plan_advisor import PlanExceptionAdvisor


class PlanExceptionAdvisorTests(unittest.TestCase):
    def evaluation(self) -> dict[str, object]:
        return {
            "plan_id": "war",
            "revision": 3,
            "invalidated_node_ids": ["route", "advance"],
            "preserved_node_ids": ["goal", "research"],
            "affected_nodes": [
                {
                    "node_id": "route",
                    "parent_id": "goal",
                    "depends_on": [],
                },
                {
                    "node_id": "advance",
                    "parent_id": "goal",
                    "depends_on": ["route"],
                },
            ],
        }

    def test_local_replacement_may_attach_to_preserved_parent(self) -> None:
        patch = PlanExceptionAdvisor._scoped_patch(
            {
                "observed_change": "route blocked",
                "reason": "replace only the route",
                "remove_node_ids": ["route", "advance"],
                "upsert_nodes": [
                    {
                        "node_id": "route-b",
                        "parent_id": "goal",
                        "title": "Alternate route",
                        "objective": "Advance by the local alternate route.",
                    }
                ],
                "resume_from_node_id": "route-b",
            },
            self.evaluation(),
        )

        self.assertEqual(patch["resume_from_node_id"], "route-b")

    def test_preserved_branch_cannot_be_modified(self) -> None:
        with self.assertRaisesRegex(ValueError, "unaffected node"):
            PlanExceptionAdvisor._scoped_patch(
                {
                    "observed_change": "route blocked",
                    "reason": "unrelated rewrite",
                    "upsert_nodes": [
                        {
                            "node_id": "research",
                            "title": "Rewrite research",
                            "objective": "Not locally related.",
                        }
                    ],
                },
                self.evaluation(),
            )

    def test_new_top_level_branch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "affected branch"):
            PlanExceptionAdvisor._scoped_patch(
                {
                    "observed_change": "route blocked",
                    "reason": "global rewrite",
                    "upsert_nodes": [
                        {
                            "node_id": "new-war-goal",
                            "title": "New strategy",
                            "objective": "Replace the entire strategy.",
                        }
                    ],
                },
                self.evaluation(),
            )

    def test_unconfigured_adviser_escalates_without_model_call(self) -> None:
        calls: list[object] = []

        class FakeRuntimeConfig:
            def snapshot(self) -> Any:
                return SimpleNamespace(settings={})

        adviser = PlanExceptionAdvisor(
            FakeRuntimeConfig(),  # type: ignore[arg-type]
            completion_fn=lambda *_args, **_kwargs: calls.append(object()),
        )
        result = adviser.handle(
            self.evaluation(),
            plan_book=object(),  # type: ignore[arg-type]
        )

        self.assertEqual(result["decision"], "escalate")
        self.assertFalse(result["configured"])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
