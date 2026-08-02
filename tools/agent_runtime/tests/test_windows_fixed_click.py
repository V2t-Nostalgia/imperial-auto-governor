from __future__ import annotations

import ctypes
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
