from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from x11_fixed_click import (  # noqa: E402
    X11ControlError,
    find_window,
    parse_geometry_shell,
    validate_profile,
    validate_ratio,
)


def profile() -> dict:
    return {
        "schema": "iag.fixed_click_profile.v1",
        "created_at": "2026-07-28T00:00:00+08:00",
        "window": {
            "title_regex": "Stellaris",
            "width": 2048,
            "height": 1152,
        },
        "target": {"x": 1500, "y": 800, "button": 1},
        "guard": {
            "enabled": True,
            "template_path": "/tmp/carrier.guard.png",
            "template_size": 72,
            "search_margin": 8,
            "threshold": 0.88,
        },
        "source_capture": {
            "capture_id": "probe",
            "screenshot_path": "/tmp/probe.png",
        },
    }


class FixedClickTests(unittest.TestCase):
    def test_selects_real_stellaris_window_over_mutter_frame(self) -> None:
        def command_output(command: list[str], **_kwargs: object) -> str:
            if command[1] == "search":
                return "6291478\n117440574\n"
            if command[-1] == "6291478":
                return "8489\n"
            if command[-1] == "117440574":
                return "127638\n"
            self.fail(f"Unexpected command: {command}")

        with (
            patch("x11_fixed_click.run_checked", side_effect=command_output),
            patch(
                "x11_fixed_click.read_process_name",
                side_effect=lambda pid: {
                    8489: "mutter-x11-fram",
                    127638: "stellaris",
                }[pid],
            ),
        ):
            window_id = find_window("Stellaris", environment={})

        self.assertEqual(window_id, 117440574)

    def test_rejects_two_real_stellaris_windows(self) -> None:
        def command_output(command: list[str], **_kwargs: object) -> str:
            if command[1] == "search":
                return "101\n202\n"
            return f"{command[-1]}\n"

        with (
            patch("x11_fixed_click.run_checked", side_effect=command_output),
            patch("x11_fixed_click.read_process_name", return_value="stellaris"),
        ):
            with self.assertRaisesRegex(
                X11ControlError,
                "found 2 among 2 title matches",
            ):
                find_window("Stellaris", environment={})

    def test_parses_xdotool_geometry(self) -> None:
        geometry = parse_geometry_shell(
            "WINDOW=123\nX=5\nY=7\nWIDTH=2048\nHEIGHT=1152\nSCREEN=0\n",
            123,
            "Stellaris",
        )
        self.assertEqual((geometry.x, geometry.y), (5, 7))
        self.assertEqual((geometry.width, geometry.height), (2048, 1152))

    def test_accepts_valid_profile(self) -> None:
        validate_profile(profile())

    def test_rejects_click_outside_window(self) -> None:
        value = profile()
        value["target"]["x"] = 2048
        with self.assertRaises(X11ControlError):
            validate_profile(value)

    def test_rejects_invalid_ratio(self) -> None:
        with self.assertRaises(X11ControlError):
            validate_ratio(1.1, "x_ratio")


if __name__ == "__main__":
    unittest.main()
