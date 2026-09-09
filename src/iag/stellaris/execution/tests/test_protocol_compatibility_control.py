from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iag.stellaris.execution.protocol_compatibility import (
    OFFLINE_FIXTURE_TARGETS,
    SUPPORTED_SESSION_PROXY_ACTIONS,
)
from iag.stellaris.execution.protocol_compatibility_control import (
    ProtocolCompatibilityControl,
    ProtocolCompatibilityError,
)


GAME_INSTALL = {
    "version": "Stellaris 4.4.6",
    "raw_version": "4.4.6",
    "normalized_version": "4.4.6",
    "mod_compatibility_version": "4.4.*",
    "distribution": "steam",
    "launcher_settings_sha256": "a" * 64,
}


class FakeSessionProxyController:
    running = False
    flow_locked = False
    insertion_count = 0
    arm_result = {
        "outcome": "confirmed",
        "host_acknowledged_inserted_bytes": True,
        "response_retagged": True,
        "response": {"carrier_length": 80},
    }

    def __init__(self, config: dict[str, object]) -> None:
        self.config = config

    @classmethod
    def reset(cls) -> None:
        cls.running = False
        cls.flow_locked = False
        cls.insertion_count = 0
        cls.arm_result = {
            "outcome": "confirmed",
            "host_acknowledged_inserted_bytes": True,
            "response_retagged": True,
            "response": {"carrier_length": 80},
        }

    def status(self) -> dict[str, object]:
        return {
            "running": self.running,
            "ready": self.running,
            "flow": {"route": "test"} if self.flow_locked else None,
            "source_actor": 2 if self.flow_locked else None,
            "candidate_source_actors": [2] if self.flow_locked else [],
            "armed": False,
            "insertion_count": self.insertion_count,
            "synthetic_serial_count": self.insertion_count,
        }

    def start(self) -> dict[str, object]:
        type(self).running = True
        return self.status()

    def stop(
        self,
        *,
        room_exited: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        del room_exited, force
        type(self).running = False
        type(self).flow_locked = False
        return self.status()

    def arm_and_wait(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        type(self).insertion_count += 1
        return dict(self.arm_result)


class ProtocolCompatibilityControlTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = {
            "runtime_root": str(self.root / "runtime"),
            "execution_mode": "session_proxy",
            "session_proxy_acknowledged": True,
            "session_proxy_source_actor": 2,
            "session_proxy_host_actor": 1,
            "session_proxy_response_timeout_seconds": 1,
        }
        self.control = ProtocolCompatibilityControl(
            Path(self.config["runtime_root"]),
            lambda: self.config,
        )
        FakeSessionProxyController.reset()

    def prepared_plan(self) -> dict[str, object]:
        self.control.new_plan(game_version="4.4.6")
        plan = self.control.payload()["plan"]
        first = plan["scenarios"][0]
        first["enabled"] = True
        first["acknowledge_side_effects"] = True
        first["target"] = OFFLINE_FIXTURE_TARGETS[first["action"]]
        self.control.save_plan(plan)
        return plan

    def test_offline_check_exercises_every_shipped_command_builder(self) -> None:
        self.prepared_plan()
        result = self.control.offline_check()
        report_path = self.control.report_path("json")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        fixtures = report["offline_tests"]["fixtures"]
        self.assertTrue(result["offline_current"])
        self.assertEqual(
            [item["action"] for item in fixtures],
            list(SUPPORTED_SESSION_PROXY_ACTIONS),
        )
        self.assertTrue(all(item["status"] == "passed" for item in fixtures))

    def test_default_placeholders_do_not_impersonate_builder_failures(self) -> None:
        self.control.new_plan(game_version="4.4.6")
        plan = self.control.payload()["plan"]
        for scenario in plan["scenarios"]:
            scenario["enabled"] = True
            scenario["acknowledge_side_effects"] = True
        self.control.save_plan(plan)

        result = self.control.offline_check()

        self.assertTrue(result["offline_current"])
        self.assertFalse(result["state"]["live_targets_ready"])
        self.assertFalse(result["actions"]["can_start_live"])
        statuses = {
            item["offline_status"]
            for item in result["report"]["scenarios"]
            if item["enabled"]
        }
        self.assertEqual(statuses, {"unconfigured"})
        with self.assertRaisesRegex(
            ProtocolCompatibilityError,
            "仍含示例占位符",
        ):
            self.control.start_live(disposable_authorized=True)

    def test_live_workflow_advances_one_action_then_requires_room_exit(self) -> None:
        self.prepared_plan()
        self.control.offline_check()
        with (
            patch(
                "iag.stellaris.execution.protocol_compatibility_control."
                "SessionProxyController",
                FakeSessionProxyController,
            ),
            patch(
                "iag.stellaris.execution.protocol_compatibility_control."
                "_game_install",
                return_value=GAME_INSTALL,
            ),
        ):
            started = self.control.start_live(disposable_authorized=True)
            self.assertEqual(started["state"]["phase"], "waiting_for_room")
            FakeSessionProxyController.flow_locked = True
            ready = self.control.confirm_room()
            self.assertEqual(ready["state"]["phase"], "ready_for_action")
            executed = self.control.execute_current()
            self.assertEqual(
                executed["state"]["phase"],
                "awaiting_operator_verdict",
            )
            completed = self.control.record_verdict("passed")
            self.assertEqual(completed["state"]["phase"], "completed")
            self.assertTrue(FakeSessionProxyController.running)
            finished = self.control.finish(room_exited=True)
            self.assertEqual(finished["state"]["phase"], "finished")
            self.assertEqual(finished["report"]["status"], "passed")
            self.assertFalse(FakeSessionProxyController.running)

    def test_live_start_requires_disposable_authorization(self) -> None:
        self.prepared_plan()
        self.control.offline_check()
        with self.assertRaisesRegex(ProtocolCompatibilityError, "可丢弃"):
            self.control.start_live(disposable_authorized=False)

    def test_failed_operator_verdict_halts_later_actions(self) -> None:
        self.control.new_plan(game_version="4.4.6")
        plan = self.control.payload()["plan"]
        for scenario in plan["scenarios"][:2]:
            scenario["enabled"] = True
            scenario["acknowledge_side_effects"] = True
            scenario["target"] = OFFLINE_FIXTURE_TARGETS[scenario["action"]]
        self.control.save_plan(plan)
        self.control.offline_check()
        with (
            patch(
                "iag.stellaris.execution.protocol_compatibility_control."
                "SessionProxyController",
                FakeSessionProxyController,
            ),
            patch(
                "iag.stellaris.execution.protocol_compatibility_control."
                "_game_install",
                return_value=GAME_INSTALL,
            ),
        ):
            self.control.start_live(disposable_authorized=True)
            FakeSessionProxyController.flow_locked = True
            self.control.confirm_room()
            self.control.execute_current()
            halted = self.control.record_verdict("unexpected_result")
            self.assertEqual(halted["state"]["phase"], "halted")
            self.assertFalse(halted["actions"]["can_execute"])
            self.control.finish(room_exited=True)

    def test_selected_fleet_reinforcement_runs_as_one_scenario(self) -> None:
        self.control.new_plan(game_version="4.4.6")
        plan = self.control.payload()["plan"]
        for scenario in plan["scenarios"]:
            if scenario["action"] != "reinforce_selected_fleet":
                continue
            scenario["enabled"] = True
            scenario["acknowledge_side_effects"] = True
            scenario["target"] = OFFLINE_FIXTURE_TARGETS[scenario["action"]]
        self.control.save_plan(plan)
        self.control.offline_check()
        with (
            patch(
                "iag.stellaris.execution.protocol_compatibility_control."
                "SessionProxyController",
                FakeSessionProxyController,
            ),
            patch(
                "iag.stellaris.execution.protocol_compatibility_control."
                "_game_install",
                return_value=GAME_INSTALL,
            ),
        ):
            self.control.start_live(disposable_authorized=True)
            FakeSessionProxyController.flow_locked = True
            self.control.confirm_room()
            executed = self.control.execute_current()
            self.assertEqual(
                executed["state"]["phase"],
                "awaiting_operator_verdict",
            )
            completed = self.control.record_verdict("passed")
            self.assertEqual(completed["state"]["phase"], "completed")
            self.control.finish(room_exited=True)


if __name__ == "__main__":
    unittest.main()
