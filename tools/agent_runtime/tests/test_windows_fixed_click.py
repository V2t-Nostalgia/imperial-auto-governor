from __future__ import annotations

import ctypes
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import windows_fixed_click


class FakeUser32:
    def __init__(self, result: int = 1) -> None:
        self.result = result
        self.flags: list[int] = []

    def SendInput(self, count, pointer, structure_size):  # noqa: N802
        self.asserted_count = count
        self.asserted_size = structure_size
        event = ctypes.cast(
            pointer,
            ctypes.POINTER(windows_fixed_click.INPUT),
        ).contents
        self.flags.append(int(event.mi.dwFlags))
        return self.result


class WindowsFixedClickTests(unittest.TestCase):
    def _profile(self) -> dict:
        return {
            "schema": "iag.fixed_click_profile.v1",
            "window": {
                "title_regex": "Stellaris",
                "width": 2048,
                "height": 1152,
            },
            "target": {"x": 1200, "y": 700, "button": 1},
            "guard": {"enabled": True, "threshold": 0.88},
        }

    def test_send_input_uses_mouse_event_structure(self) -> None:
        fake = FakeUser32()
        with patch.object(windows_fixed_click, "user32", fake):
            windows_fixed_click._send_mouse_input(
                windows_fixed_click.MOUSEEVENTF_LEFTDOWN
            )
        self.assertEqual(fake.asserted_count, 1)
        self.assertEqual(fake.asserted_size, ctypes.sizeof(windows_fixed_click.INPUT))
        self.assertEqual(fake.flags, [windows_fixed_click.MOUSEEVENTF_LEFTDOWN])

    def test_send_input_failure_is_not_reported_as_a_click(self) -> None:
        fake = FakeUser32(result=0)
        with patch.object(windows_fixed_click, "user32", fake):
            with self.assertRaises(windows_fixed_click.WindowsControlError):
                windows_fixed_click._send_mouse_input(
                    windows_fixed_click.MOUSEEVENTF_LEFTUP
                )

    def test_low_guard_score_can_be_explicitly_bypassed(self) -> None:
        geometry = windows_fixed_click.WindowGeometry(
            window_id=1,
            title="Stellaris",
            x=0,
            y=0,
            width=2048,
            height=1152,
        )
        fake_user32 = MagicMock()
        fake_user32.SetCursorPos.return_value = 1
        with (
            patch.object(windows_fixed_click, "user32", fake_user32),
            patch.object(windows_fixed_click, "read_json", return_value=self._profile()),
            patch.object(windows_fixed_click, "find_window", return_value=1),
            patch.object(windows_fixed_click, "_activate_window"),
            patch.object(windows_fixed_click, "get_window_geometry", return_value=geometry),
            patch.object(windows_fixed_click, "capture_geometry"),
            patch.object(windows_fixed_click, "guard_score", return_value=0.846),
            patch.object(windows_fixed_click.time, "sleep"),
        ):
            result = windows_fixed_click.execute_fixed_click(
                Path("profile.json"),
                artifact_root=Path("."),
                move_only=True,
                guard_enabled=False,
            )
        self.assertTrue(result["guard_bypassed"])
        self.assertFalse(result["guard_enabled"])
        self.assertEqual(result["guard_score"], 0.846)


if __name__ == "__main__":
    unittest.main()
