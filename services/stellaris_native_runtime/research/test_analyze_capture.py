from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("analyze_capture.py")
SPEC = importlib.util.spec_from_file_location("iag_capture_analyzer", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot import {MODULE_PATH}")
capture = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = capture
SPEC.loader.exec_module(capture)


class CaptureAnalyzerTests(unittest.TestCase):
    def test_infers_game_thread_and_ai_origin(self) -> None:
        commands = [
            {
                "phase": "execute_sync",
                "command_type": "CTurnTickCommand",
                "thread_id": 41,
            },
            {
                "phase": "session_post",
                "command_type": "CResearchTechnologyCommand",
                "thread_id": 41,
                "first_seen_stack": [capture.DEFAULT_AI_STACK_MARKER],
            },
        ]

        self.assertEqual(capture.infer_game_thread(commands), 41)
        self.assertEqual(
            capture.ai_origin_types(commands, capture.DEFAULT_AI_STACK_MARKER),
            {"CResearchTechnologyCommand"},
        )

    def test_correlates_only_game_thread_final_payloads(self) -> None:
        commands = [
            {
                "phase": "session_post",
                "command_type": "CQueueFleetOrderCommand",
                "thread_id": 41,
                "monotonic_ns": 100,
            },
            {
                "phase": "session_post",
                "command_type": "CAddBuildableToQueueCommand",
                "thread_id": 41,
                "monotonic_ns": 200,
            },
            {
                "phase": "session_post",
                "command_type": "CResearchTechnologyCommand",
                "thread_id": 41,
                "monotonic_ns": 300,
            },
        ]
        payloads = [
            {
                "kind": "persistent_mutable",
                "object_type": "CAggressiveStanceFleetOrder",
                "thread_id": 99,
                "monotonic_ns": 105,
            },
            {
                "kind": "persistent_mutable",
                "object_type": "CQueueFleetOrderCommand",
                "thread_id": 41,
                "monotonic_ns": 110,
            },
            {
                "kind": "persistent_const",
                "object_type": "CScriptedFleetOrder",
                "thread_id": 41,
                "monotonic_ns": 120,
            },
            {
                "kind": "persistent_const",
                "object_type": "CAttackFleetAction",
                "thread_id": 41,
                "monotonic_ns": 130,
            },
            {
                "kind": "buildable",
                "object_type": "CBuildableShip",
                "thread_id": 41,
                "monotonic_ns": 220,
            },
        ]

        correlations, missing, scripted = capture.correlate_payloads(
            commands, payloads, 41
        )

        self.assertEqual(
            correlations[
                ("CQueueFleetOrderCommand", "CScriptedFleetOrder")
            ],
            1,
        )
        self.assertEqual(
            correlations[
                ("CAddBuildableToQueueCommand", "CBuildableShip")
            ],
            1,
        )
        self.assertFalse(missing)
        self.assertEqual(
            scripted[("CScriptedFleetOrder", "CAttackFleetAction")], 1
        )

    def test_sums_dropped_event_counters(self) -> None:
        self.assertEqual(
            capture.dropped_event_count(
                [{"dropped_before": 2}, {}, {"dropped_before": 3}]
            ),
            5,
        )


if __name__ == "__main__":
    unittest.main()
