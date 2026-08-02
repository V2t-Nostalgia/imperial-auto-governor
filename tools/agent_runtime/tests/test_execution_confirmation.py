from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from agent_tools import (  # noqa: E402
    AgentToolbox,
    construction_action_evidence,
    snapshot_is_new_enough_to_reject,
)
from iag_supervisor import host_result_awaits_save_confirmation  # noqa: E402


class MemoryStore:
    def __init__(self, values: dict | None = None) -> None:
        self.values = values or {}

    def get_state(self, key: str, default=None):
        return self.values.get(key, default)

    def set_state(self, key: str, value) -> None:
        self.values[key] = value


def snapshot(*, sha256: str = "new", modified_at: str = "2026-08-02T00:01:00+08:00") -> dict:
    return {
        "game_date": "2224.01.01",
        "source_save": {
            "sha256": sha256,
            "modified_at": modified_at,
        },
        "planets": [
            {
                "planet_id": 280,
                "construction": {
                    "pending_items": [
                        {
                            "kind": "building",
                            "building_id": "building_research_lab_1",
                            "zone_id": 282,
                        },
                        {
                            "kind": "district",
                            "district_type": "district_mining",
                        },
                        {
                            "kind": "zone",
                            "zone_type": "zone_research_engineering",
                            "district_id": 90,
                            "slot_selector": 2,
                        },
                    ]
                },
                "districts": [
                    {"district_id": 90, "type": "district_mining", "level": 3}
                ],
                "zones": [
                    {
                        "zone_id": 282,
                        "district_id": 90,
                        "slot_selector": 1,
                        "type": "zone_research_society",
                        "buildings": [
                            {
                                "object_id": 452,
                                "type": "building_research_lab_1",
                                "position": 0,
                            },
                            {
                                "object_id": 463,
                                "type": "building_research_lab_1",
                                "position": 1,
                            },
                        ],
                    },
                    {
                        "zone_id": 300,
                        "district_id": 90,
                        "slot_selector": 2,
                        "type": "zone_research_engineering",
                        "buildings": [],
                    },
                ],
            }
        ],
    }


class ConstructionEvidenceTests(unittest.TestCase):
    def test_building_evidence_combines_completed_and_pending(self) -> None:
        evidence = construction_action_evidence(
            snapshot(),
            {
                "type": "build_building",
                "planet_id": 280,
                "zone_id": 282,
                "building_id": "building_research_lab_1",
            },
        )
        self.assertEqual(evidence["completed_count"], 2)
        self.assertEqual(evidence["pending_count"], 1)
        self.assertEqual(evidence["total_count"], 3)

    def test_district_and_zone_evidence_use_exact_target(self) -> None:
        district = construction_action_evidence(
            snapshot(),
            {
                "type": "build_district",
                "planet_id": 280,
                "district_type": "district_mining",
            },
        )
        zone = construction_action_evidence(
            snapshot(),
            {
                "type": "build_zone",
                "planet_id": 280,
                "district_id": 90,
                "slot_selector": 2,
                "zone_type": "zone_research_engineering",
            },
        )
        self.assertEqual(district["total_count"], 4)
        self.assertEqual(zone["total_count"], 2)

    def test_upgrade_evidence_confirms_only_the_exact_saved_slot(self) -> None:
        action = {
            "type": "upgrade_building",
            "planet_id": 280,
            "zone_id": 282,
            "building_position": 0,
            "building_object_id": 452,
            "from_building_id": "building_research_lab_1",
            "to_building_id": "building_research_lab_2",
        }
        self.assertEqual(
            construction_action_evidence(snapshot(), action)["total_count"],
            0,
        )

        wrong_slot = copy.deepcopy(snapshot())
        wrong_slot["planets"][0]["zones"][0]["buildings"][1]["type"] = (
            "building_research_lab_2"
        )
        self.assertEqual(
            construction_action_evidence(wrong_slot, action)["total_count"],
            0,
        )

        exact_slot = copy.deepcopy(snapshot())
        exact_building = exact_slot["planets"][0]["zones"][0]["buildings"][0]
        exact_building["type"] = "building_research_lab_2"
        exact_building["object_id"] = 9001
        evidence = construction_action_evidence(exact_slot, action)
        self.assertEqual(evidence["completed_count"], 1)
        self.assertEqual(evidence["target_slot"]["position"], 0)
        self.assertFalse(
            evidence["target_slot"]["object_id_matches_source"]
        )

    def test_negative_reconciliation_requires_save_after_observation(self) -> None:
        pending = {
            "source_save_sha256": "old",
            "source_game_date": "2223.07.01",
            "observation_finished_at": "2026-08-02T00:00:30+08:00",
        }
        self.assertTrue(snapshot_is_new_enough_to_reject(snapshot(), pending))
        self.assertFalse(
            snapshot_is_new_enough_to_reject(
                snapshot(modified_at="2026-08-02T00:00:00+08:00"),
                pending,
            )
        )

    def test_rewritten_host_result_is_pending_instead_of_network_failure(self) -> None:
        host_result = {
            "phase": "rewritten_without_authoritative_confirmation",
            "error": "已改写载体，但未看到房主权威广播。",
        }
        telemetry = {
            "carrier_seen": True,
            "rewritten": True,
            "authoritative_confirmation": False,
        }
        self.assertTrue(
            host_result_awaits_save_confirmation(host_result, telemetry)
        )

    def test_pending_execution_is_reconciled_by_new_save(self) -> None:
        action = {
            "type": "build_building",
            "planet_id": 280,
            "zone_id": 282,
            "building_id": "building_research_lab_1",
        }
        pending = {
            "run_id": "run-1",
            "candidate_id": "candidate-1",
            "action": action,
            "source_game_date": "2223.07.01",
            "source_save_sha256": "old",
            "observation_finished_at": "2026-08-02T00:00:30+08:00",
            "baseline_evidence": {
                "planet_found": True,
                "completed_count": 0,
                "pending_count": 0,
                "total_count": 0,
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            toolbox = object.__new__(AgentToolbox)
            toolbox.snapshot = snapshot()
            toolbox.pending_execution_confirmation = pending
            toolbox.execution_reconciliation = None
            toolbox.execution_blocked = True
            toolbox.runs_root = Path(temporary)
            toolbox.store = MemoryStore(
                {
                    "pending_execution_confirmation": pending,
                    "last_execution": {"run_id": "run-1", "success": False},
                }
            )

            toolbox._reconcile_pending_execution()

            self.assertFalse(toolbox.execution_blocked)
            self.assertIsNone(toolbox.pending_execution_confirmation)
            self.assertEqual(
                toolbox.execution_reconciliation["state"],
                "confirmed_by_save",
            )
            self.assertTrue(toolbox.store.values["last_execution"]["success"])

    def test_multiple_provisional_actions_reconcile_independently(self) -> None:
        action = {
            "type": "build_building",
            "planet_id": 280,
            "zone_id": 282,
            "building_id": "building_research_lab_1",
        }
        pending = [
            {
                "run_id": "run-1",
                "candidate_id": "candidate-1",
                "action": action,
                "source_game_date": "2223.07.01",
                "source_save_sha256": "old",
                "observation_finished_at": "2026-08-02T00:00:30+08:00",
                "baseline_evidence": {"total_count": 0},
            },
            {
                "run_id": "run-2",
                "candidate_id": "candidate-2",
                "action": action,
                "source_game_date": "2223.07.01",
                "source_save_sha256": "old",
                "observation_finished_at": "2026-08-02T00:00:30+08:00",
                "baseline_evidence": {"total_count": 1},
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            toolbox = object.__new__(AgentToolbox)
            toolbox.snapshot = snapshot()
            toolbox.pending_execution_confirmations = pending
            toolbox.pending_execution_confirmation = pending[0]
            toolbox.inconclusive_rewrite_policy = "allow_serial_provisional"
            toolbox.execution_reconciliation = None
            toolbox.execution_blocked = False
            toolbox.runs_root = Path(temporary)
            toolbox.store = MemoryStore(
                {"pending_execution_confirmations": pending}
            )

            toolbox._reconcile_pending_execution()

            self.assertEqual(toolbox.pending_execution_confirmations, [])
            self.assertEqual(
                toolbox.execution_reconciliation["state"], "reconciled"
            )
            self.assertEqual(
                len(toolbox.store.values["execution_confirmation_history"]),
                2,
            )


if __name__ == "__main__":
    unittest.main()
