#!/usr/bin/env python3
"""Pure hotkey parser tests; no global registration is performed."""

from __future__ import annotations

import unittest

from apps.game_overlay.hotkey import MOD_CONTROL, MOD_SHIFT, parse_hotkey


class HotkeyParserTests(unittest.TestCase):
    def test_normalizes_configurable_toggle_hotkey(self) -> None:
        value = parse_hotkey("control + shift + space")
        self.assertEqual(value.text, "Ctrl+Shift+Space")
        self.assertTrue(value.modifiers & MOD_CONTROL)
        self.assertTrue(value.modifiers & MOD_SHIFT)
        self.assertEqual(value.virtual_key, 0x20)

    def test_requires_modifier(self) -> None:
        with self.assertRaises(ValueError):
            parse_hotkey("Space")


if __name__ == "__main__":
    unittest.main()
