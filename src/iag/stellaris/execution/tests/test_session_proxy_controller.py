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


if __name__ == "__main__":
    unittest.main()
