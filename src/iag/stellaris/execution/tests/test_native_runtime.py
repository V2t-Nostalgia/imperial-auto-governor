from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iag.stellaris.execution.native_runtime import (
    NATIVE_RUNTIME_BUILD_ID,
    WINDOWS_NATIVE_RUNTIME_BUILD_ID,
    NativeRuntimeClient,
    NativeRuntimeError,
    NativeRuntimeManifest,
    NativeRuntimeOutcome,
)


class NativeRuntimeClientTests(unittest.TestCase):
    def test_parses_correlated_verified_response(self) -> None:
        response = NativeRuntimeClient._parse_response(
            f"IAG1\trequest-1\tstop_research\tconfirmed\t"
            f"{NATIVE_RUNTIME_BUILD_ID}\t7\ttech_shields_2\t"
            "native_postcondition_not_researching",
            request_id="request-1",
            action="stop_research",
            country_id=7,
            target_echo="tech_shields_2",
        )

        self.assertEqual(response.outcome, NativeRuntimeOutcome.CONFIRMED)
        self.assertEqual(response.country_id, 7)
        self.assertEqual(response.technology_id, "tech_shields_2")

    def test_rejects_wrong_build_or_correlation(self) -> None:
        base = "IAG1\trequest-1\tstop_research\tconfirmed\t{}\t7\ttech_shields_2\tok"
        with self.assertRaisesRegex(NativeRuntimeError, "build identity"):
            NativeRuntimeClient._parse_response(
                base.format("0" * 40),
                request_id="request-1",
                action="stop_research",
                country_id=7,
                target_echo="tech_shields_2",
            )
        with self.assertRaisesRegex(NativeRuntimeError, "correlation"):
            NativeRuntimeClient._parse_response(
                base.format(NATIVE_RUNTIME_BUILD_ID),
                request_id="different-request",
                action="stop_research",
                country_id=7,
                target_echo="tech_shields_2",
            )

    def test_parses_correlated_move_response(self) -> None:
        response = NativeRuntimeClient._parse_response(
            f"IAG1\tmove-1\tmove_fleet\tconfirmed\t"
            f"{NATIVE_RUNTIME_BUILD_ID}\t0\t888:47\t"
            "native_postcondition_player_movement_order_present",
            request_id="move-1",
            action="move_fleet",
            country_id=0,
            target_echo="888:47",
        )

        self.assertEqual(response.outcome, NativeRuntimeOutcome.CONFIRMED)
        self.assertEqual(response.target_echo, "888:47")
        self.assertIsNone(response.technology_id)

    def test_parses_correlated_attack_response(self) -> None:
        response = NativeRuntimeClient._parse_response(
            f"IAG1\tattack-1\tattack_fleet\tconfirmed\t"
            f"{NATIVE_RUNTIME_BUILD_ID}\t0\t888:220\t"
            "native_postcondition_attack_order_matches_target",
            request_id="attack-1",
            action="attack_fleet",
            country_id=0,
            target_echo="888:220",
        )

        self.assertEqual(response.outcome, NativeRuntimeOutcome.CONFIRMED)
        self.assertEqual(response.target_echo, "888:220")
        self.assertIsNone(response.technology_id)

    def test_regular_file_is_not_a_runtime_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.sock"
            path.write_text("not a socket", encoding="utf-8")
            self.assertFalse(NativeRuntimeClient(path).available())

    def test_runtime_manifest_drives_generic_argument_order(self) -> None:
        manifest = json.dumps(
            {
                "schema_version": "iag.native_tool_manifest.v1",
                "platform": "windows-x64",
                "game_version": "Stellaris 4.4.6",
                "build_id": WINDOWS_NATIVE_RUNTIME_BUILD_ID,
                "tools": [
                    {
                        "action_type": "move_fleet",
                        "action_version": 1,
                        "application_id": "fleet_operations",
                        "description": "Move one fleet.",
                        "risk_class": "state_change",
                        "verification_state": "live_verified",
                        "parameters": [
                            {
                                "name": "fleet_id",
                                "type": "uint32",
                                "required": True,
                                "description": "Fleet id.",
                                "minimum": 0,
                                "maximum": 0xFFFFFFFF,
                            },
                            {
                                "name": "destination_system_id",
                                "type": "uint32",
                                "required": True,
                                "description": "System id.",
                                "minimum": 0,
                                "maximum": 0xFFFFFFFF,
                            },
                        ],
                    }
                ],
            },
            separators=(",", ":"),
        )
        requests: list[bytes] = []

        def exchange(request: bytes) -> str:
            requests.append(request)
            if b"\tdescribe_tools\t" in request:
                return (
                    "IAG1\truntime-tool-manifest\tdescribe_tools\tconfirmed\t"
                    f"{WINDOWS_NATIVE_RUNTIME_BUILD_ID}\t0\t{manifest}\t"
                    "runtime_tool_manifest"
                )
            return (
                "IAG1\tmove-1\tmove_fleet\tconfirmed\t"
                f"{WINDOWS_NATIVE_RUNTIME_BUILD_ID}\t7\t888:47\t"
                "native_postcondition_move_order_type_present"
            )

        client = NativeRuntimeClient("ignored")
        with patch.object(client, "_exchange", side_effect=exchange):
            catalog = client.describe_tools()
            response = client.execute(
                request_id="move-1",
                action_type="move_fleet",
                action_version=1,
                country_id=7,
                target={"destination_system_id": 47, "fleet_id": 888},
            )

        self.assertEqual(catalog.tools[0].application_id, "fleet_operations")
        self.assertEqual(
            tuple(item.name for item in catalog.tools[0].parameters),
            ("fleet_id", "destination_system_id"),
        )
        self.assertEqual(response.outcome, NativeRuntimeOutcome.CONFIRMED)
        self.assertEqual(
            requests[1],
            b"IAG1\tmove-1\tmove_fleet\t7\t888\t47\n",
        )

    def test_optional_runtime_parameter_encodes_as_empty_field(self) -> None:
        manifest = json.dumps(
            {
                "schema_version": "iag.native_tool_manifest.v1",
                "platform": "linux-x86_64",
                "game_version": "Stellaris 4.4.6",
                "build_id": NATIVE_RUNTIME_BUILD_ID,
                "tools": [
                    {
                        "action_type": "example_action",
                        "action_version": 1,
                        "application_id": "etc",
                        "description": "Test optional parameter encoding.",
                        "risk_class": "state_change",
                        "verification_state": "live_verified",
                        "parameters": [
                            {
                                "name": "object_id",
                                "type": "uint32",
                                "required": True,
                                "description": "Object id.",
                                "minimum": 0,
                                "maximum": 0xFFFFFFFF,
                            },
                            {
                                "name": "label",
                                "type": "string",
                                "required": False,
                                "description": "Optional label.",
                                "maximum_length": 40,
                            },
                        ],
                    }
                ],
            },
            separators=(",", ":"),
        )
        requests: list[bytes] = []

        def exchange(request: bytes) -> str:
            requests.append(request)
            if b"\tdescribe_tools\t" in request:
                return (
                    "IAG1\truntime-tool-manifest\tdescribe_tools\tconfirmed\t"
                    f"{NATIVE_RUNTIME_BUILD_ID}\t0\t{manifest}\t"
                    "runtime_tool_manifest"
                )
            return (
                "IAG1\toptional-1\texample_action\tconfirmed\t"
                f"{NATIVE_RUNTIME_BUILD_ID}\t4\t9:\tok"
            )

        client = NativeRuntimeClient("ignored")

        with patch.object(client, "_exchange", side_effect=exchange):
            client.execute(
                request_id="optional-1",
                action_type="example_action",
                action_version=1,
                country_id=4,
                target={"object_id": 9, "label": None},
            )

        self.assertEqual(
            requests[1],
            b"IAG1\toptional-1\texample_action\t4\t9\t\n",
        )

    def test_manifest_accepts_bounded_capture_record_parameter(self) -> None:
        manifest = NativeRuntimeManifest.model_validate(
            {
                "schema_version": "iag.native_tool_manifest.v1",
                "platform": "linux-x86_64",
                "game_version": "Stellaris 4.4.6",
                "build_id": NATIVE_RUNTIME_BUILD_ID,
                "tools": (
                    {
                        "action_type": "create_ship_design",
                        "action_version": 1,
                        "application_id": "fleet_operations",
                        "description": "Capture-backed ship design.",
                        "risk_class": "state_change",
                        "verification_state": "paired_capture",
                        "parameters": (
                            {
                                "name": "verified_record_hex",
                                "type": "string",
                                "required": True,
                                "description": "Verified record body.",
                                "maximum_length": 60_000,
                            },
                        ),
                    },
                ),
            }
        )

        self.assertEqual(
            manifest.tools[0].parameters[0].maximum_length,
            60_000,
        )


if __name__ == "__main__":
    unittest.main()
