from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from uuid import uuid4


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from host_executor_protocol import (  # noqa: E402
    HOST_EXECUTOR_CAPABILITY,
    REQUIRED_HOST_EXECUTOR_APP_VERSION,
    HostExecutorProtocolError,
    begin_host_execution,
    complete_host_execution,
    record_host_ready,
    record_host_result,
    request_for_host_client,
    wait_for_host_ready,
    wait_for_host_result,
)


class HostExecutorProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = RUNTIME / "tests" / "runtime_test_data" / uuid4().hex
        self.root.mkdir(parents=True)
        self.config = {
            "runtime_root": str(self.root),
            "save_client_heartbeat_timeout_seconds": 15,
            "interceptor_timeout_seconds": 120,
            "observe_seconds": 15,
        }
        self.client = {
            "schema": "iag.save_client_status.v1",
            "client_id": "client-12345678",
            "state": "running",
            "source_ip": "198.51.100.20",
            "last_seen_epoch": time.time(),
            "capabilities": [HOST_EXECUTOR_CAPABILITY],
            "app_version": REQUIRED_HOST_EXECUTOR_APP_VERSION,
        }
        state = self.root / "state"
        state.mkdir()
        (state / "save_client_status.json").write_text(
            json.dumps(self.client),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        self.root.rmdir()

    @staticmethod
    def manifest() -> dict:
        return {
            "carrier": {
                "command": "building_research_lab_1",
                "required_origin": "non-host co-op client outbound",
            },
            "action": {
                "type": "build_building",
                "planet_id": 3,
                "build_queue_id": 0,
                "zone_id": 0,
                "building_id": "building_holo_theatres",
            },
        }

    @staticmethod
    def district_manifest() -> dict:
        return {
            "carrier": {
                "command": "district_generator",
                "required_origin": "non-host co-op client outbound",
            },
            "action": {
                "type": "build_district",
                "build_queue_id": 901,
                "colony_id": 82,
                "district_type": "district_city",
            },
        }

    @staticmethod
    def zone_manifest() -> dict:
        return {
            "carrier": {
                "command": "zone_research_engineering",
                "required_origin": "non-host co-op client outbound",
            },
            "action": {
                "type": "build_zone",
                "build_queue_id": 901,
                "colony_id": 82,
                "district_id": 213,
                "slot_selector": 1,
                "zone_type": "zone_foundry",
            },
        }

    @staticmethod
    def upgrade_manifest() -> dict:
        return {
            "carrier": {
                "command": "building_research_lab_2",
                "required_origin": "non-host co-op client outbound",
            },
            "action": {
                "type": "upgrade_building",
                "planet_id": 280,
                "build_queue_id": 8555,
                "colony_id": 76,
                "zone_id": 229,
                "building_object_id": 452,
                "from_building_id": "building_research_lab_1",
                "to_building_id": "building_research_lab_2",
            },
        }

    @staticmethod
    def replacement_manifest() -> dict:
        return {
            "carrier": {
                "command": "building_upc_replacement_command_relay_target",
                "required_origin": "non-host co-op client outbound",
            },
            "action": {
                "type": "replace_building",
                "planet_id": 280,
                "build_queue_id": 8555,
                "colony_id": 76,
                "zone_id": 229,
                "building_position": 1,
                "building_object_id": 463,
                "from_building_id": "building_commercial_zone",
                "to_building_id": "building_holo_theatres",
            },
        }

    def test_complete_ready_result_round_trip(self) -> None:
        request = begin_host_execution(
            self.config,
            run_id="20260729_220319",
            manifest=self.manifest(),
            host_ip="198.51.100.20",
            peer_ip="192.0.2.10",
        )
        offered = request_for_host_client(
            self.config,
            client_id=self.client["client_id"],
            source_ip="198.51.100.20",
        )
        self.assertEqual(offered["request_id"], request["request_id"])
        self.assertEqual(
            offered["carrier"]["required_direction"],
            "inbound_to_host",
        )

        record_host_ready(
            self.config,
            {
                "request_id": request["request_id"],
                "elevated": True,
                "interceptor_open": True,
                "filter": "udp",
                "route_scope": "direct_peer_ip_and_stellaris_process_udp_ports",
                "stellaris_udp_ports": [61111, 61112],
                "process_id": 42,
            },
            client_id=self.client["client_id"],
            source_ip="198.51.100.20",
        )
        ready = wait_for_host_ready(
            self.config,
            request["request_id"],
            timeout_seconds=0.2,
        )
        self.assertTrue(ready["interceptor_open"])
        self.assertEqual(
            ready["route_scope"],
            "direct_peer_ip_and_stellaris_process_udp_ports",
        )
        self.assertEqual(ready["stellaris_udp_ports"], [61111, 61112])

        record_host_result(
            self.config,
            {
                "request_id": request["request_id"],
                "success": True,
                "carrier_seen": True,
                "rewritten": True,
                "authoritative_confirmation": True,
                "phase": "authoritative_host_command_confirmed",
            },
            client_id=self.client["client_id"],
            source_ip="198.51.100.20",
        )
        result = wait_for_host_result(
            self.config,
            request["request_id"],
            timeout_seconds=0.2,
        )
        self.assertTrue(result["success"])
        complete_host_execution(self.config, request["request_id"])
        self.assertIsNone(
            request_for_host_client(
                self.config,
                client_id=self.client["client_id"],
                source_ip="198.51.100.20",
            )
        )

    def test_district_request_uses_its_same_family_carrier(self) -> None:
        request = begin_host_execution(
            self.config,
            run_id="20260730_120000",
            manifest=self.district_manifest(),
            host_ip="198.51.100.20",
            peer_ip="192.0.2.10",
        )

        self.assertEqual(request["carrier"]["command"], "district_generator")
        self.assertEqual(request["action"]["type"], "build_district")
        complete_host_execution(self.config, request["request_id"])

    def test_zone_request_uses_the_longest_selected_carrier(self) -> None:
        request = begin_host_execution(
            self.config,
            run_id="20260730_193120",
            manifest=self.zone_manifest(),
            host_ip="198.51.100.20",
            peer_ip="192.0.2.10",
        )

        self.assertEqual(
            request["carrier"]["command"],
            "zone_research_engineering",
        )
        self.assertEqual(request["action"]["type"], "build_zone")
        complete_host_execution(self.config, request["request_id"])

    def test_upgrade_request_accepts_the_live_test_carrier(self) -> None:
        request = begin_host_execution(
            self.config,
            run_id="20260802_040000",
            manifest=self.upgrade_manifest(),
            host_ip="198.51.100.20",
            peer_ip="192.0.2.10",
        )

        self.assertEqual(
            request["carrier"]["command"],
            "building_research_lab_2",
        )
        self.assertEqual(request["action"]["type"], "upgrade_building")
        complete_host_execution(self.config, request["request_id"])

    def test_replacement_request_uses_the_dedicated_carrier(self) -> None:
        request = begin_host_execution(
            self.config,
            run_id="20260803_040000",
            manifest=self.replacement_manifest(),
            host_ip="198.51.100.20",
            peer_ip="192.0.2.10",
        )

        self.assertEqual(
            request["carrier"]["command"],
            "building_upc_replacement_command_relay_target",
        )
        self.assertEqual(request["action"]["type"], "replace_building")
        self.assertEqual(request["action"]["building_object_id"], 463)
        complete_host_execution(self.config, request["request_id"])

    def test_old_uploader_is_rejected_before_request_creation(self) -> None:
        self.client["capabilities"] = ["save_upload_v1"]
        (self.root / "state" / "save_client_status.json").write_text(
            json.dumps(self.client),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            HostExecutorProtocolError,
            "too old",
        ):
            begin_host_execution(
                self.config,
                run_id="20260729_220319",
                manifest=self.manifest(),
                host_ip="198.51.100.20",
                peer_ip="192.0.2.10",
            )
        self.assertFalse(
            (self.root / "state" / "host_executor" / "active.json").exists()
        )

    def test_hostbridge1_is_rejected_before_request_creation(self) -> None:
        self.client["app_version"] = "2026.07.29-hostbridge1"
        (self.root / "state" / "save_client_status.json").write_text(
            json.dumps(self.client),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            HostExecutorProtocolError,
            "same-family construction rewrites",
        ):
            begin_host_execution(
                self.config,
                run_id="20260729_220319",
                manifest=self.manifest(),
                host_ip="198.51.100.20",
                peer_ip="192.0.2.10",
            )
        self.assertFalse(
            (self.root / "state" / "host_executor" / "active.json").exists()
        )
    def test_ready_requires_open_elevated_interceptor(self) -> None:
        request = begin_host_execution(
            self.config,
            run_id="20260729_220319",
            manifest=self.manifest(),
            host_ip="198.51.100.20",
            peer_ip="192.0.2.10",
        )
        with self.assertRaisesRegex(
            HostExecutorProtocolError,
            "WinDivert handle is open",
        ):
            record_host_ready(
                self.config,
                {
                    "request_id": request["request_id"],
                    "elevated": False,
                    "interceptor_open": True,
                },
                client_id=self.client["client_id"],
                source_ip="198.51.100.20",
            )


if __name__ == "__main__":
    unittest.main()
