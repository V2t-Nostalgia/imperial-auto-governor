from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from port_discovery import (  # noqa: E402
    ProcessInfo,
    decode_endpoint,
    discover_session,
    process_identity_matches,
    port_verification,
    select_socket,
)


TEST_ROOT = Path(__file__).resolve().parent
TEST_TEMP_ROOT = Path("C:/tmp") if os.name == "nt" else Path(tempfile.gettempdir())
UDP_HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def udp_line(
    *,
    local: str,
    remote: str,
    inode: int,
    uid: int = 1000,
) -> str:
    return (
        f"  8: {local} {remote} 01 00000000:00000000 "
        f"00:00000000 00000000  {uid} 0 {inode} 2 0000000000000000 0\n"
    )


class PortDiscoveryTests(unittest.TestCase):
    def test_decodes_proc_ipv4_endpoint(self) -> None:
        self.assertEqual(
            decode_endpoint("0A0200C0:C351", "ipv4"),
            ("192.0.2.10", 50001),
        )

    def test_process_matching_ignores_launch_wrappers(self) -> None:
        self.assertFalse(
            process_identity_matches(
                "reaper", "/steam/reaper /game/stellaris", ["stellaris"]
            )
        )
        self.assertFalse(
            process_identity_matches(
                "python3", "/runtime/probe.py --process-name stellaris", ["stellaris"]
            )
        )
        self.assertTrue(
            process_identity_matches("stellaris", "/game/stellaris", ["stellaris"])
        )

    def test_ambiguous_private_peers_are_not_guessed(self) -> None:
        from port_discovery import UdpSocket

        one = UdpSocket(
            "ipv4", "192.0.2.10", 50001, "198.51.100.21", 40000,
            "01", 0, 0, 1000, 10, (42,),
        )
        two = UdpSocket(
            "ipv4", "192.0.2.10", 50001, "203.0.113.30", 40000,
            "01", 0, 0, 1000, 11, (42,),
        )
        self.assertIsNone(select_socket([one, two], None))
        self.assertEqual(select_socket([one, two], "203.0.113.30"), two)

    def test_flags_telemetry_port_not_owned_by_stellaris(self) -> None:
        from port_discovery import UdpSocket

        socket = UdpSocket(
            "ipv4", "192.0.2.10", 50001, "198.51.100.21", 40000,
            "01", 0, 0, 1000, 10, (42,),
        )
        telemetry = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "local_udp_port": 55555,
            "outbound_packets": 8,
            "inbound_packets": 8,
        }
        result = port_verification(
            [ProcessInfo(42, "stellaris", "/game/stellaris")],
            [socket],
            socket,
            telemetry,
            telemetry_max_age_seconds=15,
        )
        self.assertEqual(result["state"], "conflict")
        self.assertFalse(result["verified"])

    @unittest.skipIf(os.name == "nt", "Synthetic fd symlinks require Unix semantics.")
    def test_discovers_stellaris_owned_socket_and_live_ports(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            proc = Path(directory)
            pid_root = proc / "42"
            fd_root = pid_root / "fd"
            net_root = proc / "net"
            fd_root.mkdir(parents=True)
            net_root.mkdir()
            (pid_root / "comm").write_text("stellaris\n", encoding="utf-8")
            (pid_root / "cmdline").write_bytes(b"/game/stellaris\x00")
            os.symlink("socket:[12345]", fd_root / "7")
            (net_root / "udp").write_text(
                UDP_HEADER
                + udp_line(
                    local="0901A8C0:C351",
                    remote="1401A8C0:9C40",
                    inode=12345,
                ),
                encoding="utf-8",
            )
            (net_root / "udp6").write_text(UDP_HEADER, encoding="utf-8")
            telemetry_path = proc / "telemetry.json"
            telemetry_path.write_text(
                json.dumps(
                    {
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "outbound_packets": 4,
                        "inbound_packets": 3,
                        "local_udp_port": 50001,
                        "host_destination_port": 40000,
                        "host_source_port": 40001,
                        "carrier_seen": False,
                        "authoritative_confirmation": False,
                    }
                ),
                encoding="utf-8",
            )

            result = discover_session(
                ["stellaris"],
                host_ip="198.51.100.21",
                telemetry_path=telemetry_path,
                proc_root=proc,
            )

            self.assertEqual(result["confidence"], "bidirectional_flow_observed")
            self.assertEqual(result["selected_endpoint"]["local_port"], 50001)
            self.assertEqual(result["selected_endpoint"]["remote_port"], 40000)
            self.assertEqual(result["live_ports"]["host_source_port"], 40001)

    def test_accepts_fresh_steam_brokered_flow_as_unverified_candidate(self) -> None:
        from port_discovery import UdpSocket

        game = ProcessInfo(5, "stellaris", "/game/stellaris")
        steam = ProcessInfo(7, "steam", "/steam/steam")
        steam_socket = UdpSocket(
            "ipv4",
            "192.0.2.254",
            53708,
            "0.0.0.0",
            0,
            "07",
            0,
            0,
            1000,
            99,
            (7,),
        )
        telemetry = {
            "schema": "iag.passive_flow_telemetry.v1",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "phase": "passive_read_only",
            "candidate_active": True,
            "transport_owner": "steam",
            "host_ip": "198.51.100.20",
            "local_udp_port": 53708,
            "host_destination_port": 58276,
            "host_source_port": 58276,
            "outbound_packets": 12,
            "inbound_packets": 10,
            "carrier_seen": False,
            "authoritative_confirmation": False,
        }
        with (
            patch(
                "port_discovery.discover_process_sockets",
                side_effect=[([game], []), ([steam], [steam_socket])],
            ),
            patch(
                "port_discovery.read_json_if_present",
                side_effect=[None, telemetry],
            ),
        ):
            result = discover_session(
                ["stellaris"],
                transport_process_names=["steam"],
                passive_telemetry_path=Path("/run/passive.json"),
            )

        self.assertEqual(result["confidence"], "brokered_flow_candidate")
        self.assertEqual(
            result["port_verification"]["state"],
            "brokered_flow_candidate",
        )
        self.assertFalse(result["port_verification"]["verified"])
        self.assertEqual(result["selected_endpoint"]["remote_ip"], "198.51.100.20")
        self.assertEqual([item["pid"] for item in result["processes"]], [5])
        self.assertEqual([item["pid"] for item in result["transport_processes"]], [7])

    def test_unowned_bidirectional_traffic_does_not_turn_status_green(self) -> None:
        telemetry = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "local_udp_port": 55555,
            "outbound_packets": 10,
            "inbound_packets": 10,
        }
        processes = [ProcessInfo(5, "stellaris", "/game/stellaris")]
        with (
            patch(
                "port_discovery.discover_process_sockets",
                return_value=(processes, []),
            ),
            patch("port_discovery.read_json_if_present", return_value=telemetry),
        ):
            result = discover_session(
                ["stellaris"],
                telemetry_path=Path("/run/iag-status.json"),
            )

        self.assertEqual(result["confidence"], "process_detected")
        self.assertEqual(
            result["port_verification"]["state"],
            "waiting_for_socket",
        )
    def test_fresh_authoritative_confirmation_wins(self) -> None:
        telemetry = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "outbound_packets": 1,
            "inbound_packets": 1,
            "carrier_seen": True,
            "authoritative_confirmation": True,
        }
        processes = [ProcessInfo(5, "stellaris", "/game/stellaris")]
        with (
            patch(
                "port_discovery.discover_process_sockets",
                return_value=(processes, []),
            ),
            patch("port_discovery.read_json_if_present", return_value=telemetry),
        ):
            result = discover_session(
                ["stellaris"],
                telemetry_path=Path("/run/iag-status.json"),
            )

            self.assertEqual(result["confidence"], "authoritative_confirmed")


if __name__ == "__main__":
    unittest.main()
