from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from iag.stellaris.execution.session_proxy_controller import (
    SessionProxyController,
    SessionProxyError,
    session_proxy_worker_command,
)


class SessionProxyControllerTests(unittest.TestCase):
    def test_source_install_uses_module_worker(self) -> None:
        with (
            patch.object(sys, "frozen", False, create=True),
            patch.object(sys, "executable", r"C:\Python\python.exe"),
        ):
            command = session_proxy_worker_command(["--host-ip", "192.0.2.20"])
        self.assertEqual(
            command,
            [
                r"C:\Python\python.exe",
                "-m",
                "iag.stellaris.execution.session_proxy",
                "--host-ip",
                "192.0.2.20",
            ],
        )

    def test_frozen_agent_uses_internal_worker_mode(self) -> None:
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", r"C:\IAG\IAGWindowsAgent.exe"),
        ):
            command = session_proxy_worker_command(["--host-ip", "192.0.2.20"])
        self.assertEqual(
            command,
            [
                r"C:\IAG\IAGWindowsAgent.exe",
                "--session-proxy-worker",
                "--host-ip",
                "192.0.2.20",
            ],
        )

    def test_status_removes_stale_ready_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = SessionProxyController({"runtime_root": directory})
            controller.ready_path.parent.mkdir(parents=True, exist_ok=True)
            controller.ready_path.write_text(
                json.dumps({"pid": 2_147_483_647}),
                encoding="utf-8",
            )
            with patch("psutil.pid_exists", return_value=False):
                result = controller.status()
            self.assertFalse(result["running"])
            self.assertFalse(controller.ready_path.exists())

    def test_proxy_cannot_stop_after_insertion_until_room_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = SessionProxyController({"runtime_root": directory})
            controller.ready_path.parent.mkdir(parents=True, exist_ok=True)
            value = {"pid": os.getpid(), "insertion_count": 1}
            controller.ready_path.write_text(json.dumps(value), encoding="utf-8")
            controller.status_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(SessionProxyError, "先退出多人房间"):
                controller.stop(room_exited=False)

    def test_arm_request_is_correlated_by_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = SessionProxyController(
                {
                    "runtime_root": directory,
                    "session_proxy_source_actor": 2,
                    "session_proxy_host_actor": 1,
                }
            )
            initial = {
                "running": True,
                "ready": True,
                "flow": {"local_port": 50000, "host_port": 51000},
                "armed": False,
                "session_id": "session-1",
            }
            completed = {
                **initial,
                "last_request": {
                    "request_id": "fleet-run-1",
                    "outcome": "confirmed",
                },
            }
            with patch.object(
                controller,
                "status",
                side_effect=[initial, completed],
            ):
                result = controller.arm_and_wait(
                    action="move_fleet",
                    target={
                        "source_fleet_object": 7,
                        "destination_tag_hex": "0c3a01001400",
                        "destination_object": 11,
                    },
                    request_id="fleet-run-1",
                )
            request = json.loads(controller.arm_path.read_text(encoding="utf-8"))
            self.assertEqual(request["request_id"], "fleet-run-1")
            self.assertEqual(request["source_actor"], 2)
            self.assertEqual(result["outcome"], "confirmed")

    def test_player_confirmed_flow_lock_writes_session_scoped_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = SessionProxyController({"runtime_root": directory})
            candidate_id = "a" * 24
            status = {
                "running": True,
                "ready": True,
                "flow": None,
                "session_id": "session-1",
                "flow_candidates": [
                    {
                        "candidate_id": candidate_id,
                        "bidirectional": True,
                        "manual_lockable": True,
                    }
                ],
            }
            with patch.object(controller, "status", side_effect=[status, status]):
                result = controller.lock_flow(
                    candidate_id=candidate_id,
                    player_confirmed=True,
                )

            request = json.loads(
                controller.flow_lock_path.read_text(encoding="utf-8")
            )
            self.assertEqual(request["session_id"], "session-1")
            self.assertEqual(request["candidate_id"], candidate_id)
            self.assertTrue(request["player_confirmed"])
            self.assertTrue(result["manual_flow_lock_pending"])

    def test_player_confirmed_flow_lock_accepts_one_way_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = SessionProxyController({"runtime_root": directory})
            status = {
                "running": True,
                "ready": True,
                "flow": None,
                "session_id": "session-1",
                "flow_candidates": [
                    {
                        "candidate_id": "b" * 24,
                        "bidirectional": False,
                        "manual_lockable": False,
                    }
                ],
            }
            with patch.object(controller, "status", side_effect=[status, status]):
                result = controller.lock_flow(
                    candidate_id="b" * 24,
                    player_confirmed=True,
                )

            request = json.loads(
                controller.flow_lock_path.read_text(encoding="utf-8")
            )
            self.assertEqual(request["candidate_id"], "b" * 24)
            self.assertTrue(result["manual_flow_lock_pending"])

    def test_action_sequence_stops_after_first_unconfirmed_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = SessionProxyController({"runtime_root": directory})
            with patch.object(
                controller,
                "_arm_and_wait_locked",
                side_effect=[
                    {"outcome": "confirmed"},
                    {"outcome": "rejected", "error": "host rejected"},
                ],
            ) as submit:
                result = controller.arm_sequence_and_wait(
                    steps=[
                        {"action": "one", "target": {"value": 1}},
                        {"action": "two", "target": {"value": 2}},
                        {"action": "three", "target": {"value": 3}},
                    ],
                    request_id="batch",
                )

            self.assertEqual(submit.call_count, 2)
            self.assertEqual(result["outcome"], "partial")
            self.assertEqual(result["confirmed_steps"], 1)
            self.assertEqual(result["attempted_steps"], 2)


if __name__ == "__main__":
    unittest.main()
