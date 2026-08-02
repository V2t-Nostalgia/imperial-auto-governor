from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from iag_supervisor import (  # noqa: E402
    StaleSourceSaveError,
    SupervisorError,
    carrier_click_sequence_paths,
    carrier_click_profile_path,
    carrier_navigation_profile_path,
    execute_carrier_click_sequence,
    interceptor_command,
    resolve_host_ip,
    wait_until_ready,
    validate_manifest,
    validate_source_save,
)


def manifest() -> dict:
    return {
        "schema": "iag.execution.v1",
        "action": {"type": "build_building"},
        "safety": {
            "one_shot": True,
            "preserve_udp_payload_length": True,
            "preserve_command_count": True,
            "preserve_carrier_serial": True,
            "require_authoritative_host_confirmation": True,
        },
    }


class SupervisorTests(unittest.TestCase):
    def test_accepts_required_safety_invariants(self) -> None:
        validate_manifest(manifest())

    def test_accepts_district_manifest(self) -> None:
        value = manifest()
        value["action"] = {"type": "build_district"}
        validate_manifest(value)

    def test_accepts_upgrade_manifest(self) -> None:
        value = manifest()
        value["action"] = {"type": "upgrade_building"}
        validate_manifest(value)

    def test_action_specific_click_profiles_do_not_reuse_building_click(self) -> None:
        config = {
            "runtime_root": "/runtime",
            "carrier_click_profile": "calibration/carrier_click.json",
        }
        self.assertEqual(
            carrier_click_profile_path(config, "build_building"),
            Path("/runtime/calibration/carrier_click.json"),
        )
        self.assertEqual(
            carrier_click_profile_path(config, "build_district"),
            Path("/runtime/calibration/carrier_district_click.json"),
        )
        self.assertEqual(
            carrier_click_profile_path(config, "build_zone"),
            Path("/runtime/calibration/carrier_zone_click.json"),
        )
        self.assertEqual(
            carrier_click_profile_path(config, "upgrade_building"),
            Path("/runtime/calibration/carrier_upgrade_click.json"),
        )

    def test_navigation_profiles_create_two_step_building_and_zone_sequences(self) -> None:
        config = {
            "runtime_root": "/runtime",
            "carrier_navigation_enabled": True,
        }
        self.assertEqual(
            carrier_navigation_profile_path(config, "build_building"),
            Path("/runtime/calibration/carrier_building_open.json"),
        )
        self.assertEqual(
            carrier_click_sequence_paths(config, "build_building"),
            [
                Path("/runtime/calibration/carrier_building_open.json"),
                Path("/runtime/calibration/carrier_click.json"),
            ],
        )
        self.assertEqual(
            carrier_click_sequence_paths(config, "build_zone"),
            [
                Path("/runtime/calibration/carrier_zone_open.json"),
                Path("/runtime/calibration/carrier_zone_click.json"),
            ],
        )
        self.assertEqual(
            carrier_click_sequence_paths(config, "build_district"),
            [Path("/runtime/calibration/carrier_district_click.json")],
        )
        self.assertEqual(
            carrier_click_sequence_paths(config, "upgrade_building"),
            [
                Path("/runtime/calibration/carrier_upgrade_open.json"),
                Path("/runtime/calibration/carrier_upgrade_click.json"),
            ],
        )

    def test_navigation_can_be_disabled_for_legacy_manual_panel_state(self) -> None:
        config = {
            "runtime_root": "/runtime",
            "carrier_navigation_enabled": False,
        }
        self.assertEqual(
            carrier_click_sequence_paths(config, "build_building"),
            [Path("/runtime/calibration/carrier_click.json")],
        )

    @patch("iag_supervisor.time.sleep")
    @patch("iag_supervisor.execute_fixed_click")
    def test_executes_guarded_click_profiles_in_order(
        self,
        execute_click,
        sleep,
    ) -> None:
        execute_click.side_effect = [
            {"schema": "iag.fixed_click_result.v1", "target": {"x": 10, "y": 20}},
            {"schema": "iag.fixed_click_result.v1", "target": {"x": 30, "y": 40}},
        ]
        profiles = [Path("/run/open.json"), Path("/run/command.json")]
        result = execute_carrier_click_sequence(
            profiles,
            config={
                "display": ":1",
                "carrier_click_step_delay_seconds": 0.25,
                "carrier_pointer_settle_seconds": 0.30,
                "carrier_click_hold_seconds": 0.10,
                "carrier_post_click_settle_seconds": 0.40,
            },
            artifact_root=Path("/run"),
        )
        self.assertEqual(result["step_count"], 2)
        self.assertEqual(
            [call.args[0] for call in execute_click.call_args_list],
            profiles,
        )
        sleep.assert_called_once_with(0.25)
        for call in execute_click.call_args_list:
            self.assertEqual(call.kwargs["pointer_settle_seconds"], 0.30)
            self.assertEqual(call.kwargs["click_hold_seconds"], 0.10)
            self.assertEqual(call.kwargs["post_click_settle_seconds"], 0.40)

    def test_rejects_missing_host_confirmation(self) -> None:
        value = manifest()
        value["safety"]["require_authoritative_host_confirmation"] = False
        with self.assertRaises(SupervisorError):
            validate_manifest(value)

    def test_interceptor_uses_dynamic_port_filter(self) -> None:
        command = interceptor_command(
            {
                "interceptor_python": "/venv/bin/python",
                "interceptor_timeout_seconds": 120,
                "observe_seconds": 15,
                "nfqueue_number": 4242,
            },
            manifest_path=Path("/run/manifest.json"),
            host_ip="198.51.100.21",
            log_path=Path("/run/interceptor.jsonl"),
            ready_path=Path("/run/ready.json"),
            telemetry_path=Path("/run/status.json"),
            stop_path=Path("/run/emergency_stop"),
        )
        port_index = command.index("--host-port")
        self.assertEqual(command[port_index + 1], "0")
        self.assertIn("--status-file", command)
        self.assertIn("--stop-file", command)

    def test_capability_mode_starts_interceptor_without_sudo(self) -> None:
        config = {
            "interceptor_python": "/venv/bin/python",
            "interceptor_privilege_mode": "capability",
        }
        with patch.object(os, "geteuid", return_value=1000, create=True):
            command = interceptor_command(
                config,
                manifest_path=Path("/run/manifest.json"),
                host_ip="198.51.100.21",
                log_path=Path("/run/interceptor.jsonl"),
                ready_path=Path("/run/ready.json"),
                telemetry_path=Path("/run/status.json"),
                stop_path=Path("/run/emergency_stop"),
            )

        self.assertEqual(command[0], "/venv/bin/python")
        self.assertNotIn("-n", command[:3])

    def test_sudo_mode_remains_an_explicit_fallback(self) -> None:
        config = {
            "interceptor_python": "/venv/bin/python",
            "interceptor_privilege_mode": "sudo",
            "sudo_command": "sudo",
        }
        with patch.object(os, "geteuid", return_value=1000, create=True):
            command = interceptor_command(
                config,
                manifest_path=Path("/run/manifest.json"),
                host_ip="198.51.100.21",
                log_path=Path("/run/interceptor.jsonl"),
                ready_path=Path("/run/ready.json"),
                telemetry_path=Path("/run/status.json"),
                stop_path=Path("/run/emergency_stop"),
            )

        self.assertEqual(command[:3], ["sudo", "-n", "/venv/bin/python"])

    def test_ready_failure_includes_interceptor_stderr(self) -> None:
        root = RUNTIME / "tests" / "runtime_test_data" / uuid4().hex
        root.mkdir(parents=True)
        stderr_path = root / "stderr.log"
        stderr_path.write_text("sudo: a password is required\n", encoding="utf-8")

        class ExitedProcess:
            @staticmethod
            def poll() -> int:
                return 1

        try:
            with self.assertRaisesRegex(
                SupervisorError,
                "sudo: a password is required",
            ):
                wait_until_ready(
                    ExitedProcess(),
                    root / "ready.json",
                    timeout_seconds=1,
                    stderr_path=stderr_path,
                )
        finally:
            stderr_path.unlink(missing_ok=True)
            root.rmdir()

    def test_host_discovery_retries_until_passive_flow_recovers(self) -> None:
        missing = {
            "confidence": "process_detected",
            "port_verification": {
                "state": "passive_waiting",
                "telemetry_fresh": True,
            },
            "telemetry": {
                "source": "passive_observer",
                "candidate_active": False,
                "host_ip": None,
            },
            "selected_endpoint": None,
        }
        recovered = {
            "confidence": "brokered_flow_candidate",
            "port_verification": {
                "state": "brokered_flow_candidate",
                "telemetry_fresh": True,
            },
            "telemetry": {
                "source": "passive_observer",
                "candidate_active": True,
                "host_ip": "198.51.100.20",
            },
            "selected_endpoint": {
                "remote_ip": "198.51.100.20",
                "source": "passive_observer",
            },
        }
        config = {
            "runtime_root": "/runtime",
            "host_ip": "",
            "process_names": ["stellaris"],
            "transport_process_names": ["steam"],
            "host_discovery_timeout_seconds": 1,
            "host_discovery_poll_seconds": 0.001,
        }

        with (
            patch(
                "iag_supervisor.discover_session",
                side_effect=[missing, recovered],
            ) as discover,
            patch("iag_supervisor.time.sleep"),
        ):
            host_ip = resolve_host_ip(config, Path("/run/interceptor.json"))

        self.assertEqual(host_ip, "198.51.100.20")
        self.assertEqual(discover.call_count, 2)

    def test_connected_host_bridge_recovers_host_without_socket_discovery(self) -> None:
        root = RUNTIME / "tests" / "runtime_test_data" / uuid4().hex
        status_path = root / "state" / "save_client_status.json"
        status_path.parent.mkdir(parents=True)
        try:
            status_path.write_text(
                json.dumps(
                    {
                        "state": "running",
                        "source_ip": "198.51.100.21",
                        "last_seen_epoch": time.time(),
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "runtime_root": str(root),
                "save_client_status_path": "state/save_client_status.json",
                "save_client_heartbeat_timeout_seconds": 15,
            }
            with patch("iag_supervisor.discover_session") as discover:
                host_ip = resolve_host_ip(config, root / "interceptor.json")
            self.assertEqual(host_ip, "198.51.100.21")
            discover.assert_not_called()
        finally:
            status_path.unlink(missing_ok=True)
            status_path.parent.rmdir()
            root.rmdir()

    def test_newer_save_is_a_recoverable_preclick_error(self) -> None:
        root = RUNTIME / "tests" / "runtime_test_data" / uuid4().hex
        root.mkdir(parents=True)
        source = root / "source.sav"
        latest = root / "latest.sav"
        try:
            source.write_bytes(b"source-save")
            latest.write_bytes(b"latest-save")
            now = time.time()
            os.utime(source, (now - 2, now - 2))
            os.utime(latest, (now - 1, now - 1))
            manifest_value = {
                "source_save_path": str(source),
                "source_save_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
            with patch("iag_supervisor.resolve_current_save", return_value=latest):
                with self.assertRaises(StaleSourceSaveError):
                    validate_source_save(
                        manifest_value,
                        {"require_fresh_save_seconds": 900},
                    )
        finally:
            source.unlink(missing_ok=True)
            latest.unlink(missing_ok=True)
            root.rmdir()


if __name__ == "__main__":
    unittest.main()
