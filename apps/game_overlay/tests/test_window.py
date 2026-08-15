#!/usr/bin/env python3
"""Rendering-contract tests for the transparent game overlay."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from apps.game_overlay.overlay_config import (  # noqa: E402
    OverlayConfiguration,
    OverlayConnection,
    OverlaySettings,
)
from apps.game_overlay.window import OverlayWindow  # noqa: E402


class OverlayWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        app_root = Path(__file__).resolve().parents[1]
        settings = OverlaySettings(
            analysis_phrases_file=app_root
            / "resources"
            / "analysis_phrases_zh.txt",
            font_file=app_root
            / "resources"
            / "fonts"
            / "fusion_pixel"
            / "fusion-pixel-12px-monospaced-zh_hans.ttf",
        )
        configuration = OverlayConfiguration(
            config_path=app_root / "tests" / "unused-overlay-config.json",
            connection=OverlayConnection("", "", ""),
            settings=settings,
        )
        listener_patch = patch(
            "apps.game_overlay.window.WindowsHotkeyListener",
            autospec=True,
        )
        self.addCleanup(listener_patch.stop)
        listener_patch.start()
        self.window = OverlayWindow(
            configuration,
            preview=True,
            persist_settings=False,
        )
        self.addCleanup(self.window.close)

    def test_every_text_surface_uses_the_bundled_font(self) -> None:
        family = self.window.font().family()
        self.assertEqual(family, "Fusion Pixel 12px Mono zh_hans")
        self.assertEqual(self.window.transcript.font().family(), family)
        self.assertEqual(
            self.window.transcript.document().defaultFont().family(),
            family,
        )
        self.assertEqual(self.window.analysis_label.font().family(), family)
        self.assertEqual(self.window.command_input.font().family(), family)
        self.assertEqual(
            self.window.command_input.document().defaultFont().family(),
            family,
        )

    def test_assistant_history_keeps_the_streaming_italic_style(self) -> None:
        self.window.apply_bootstrap(
            {
                "conversation": {"conversation_id": "test"},
                "messages": [
                    {"id": 1, "role": "user", "content": "玩家消息"},
                    {"id": 2, "role": "assistant", "content": "灰风回复"},
                ],
                "stream": {"active": False, "draft": ""},
            }
        )
        user_cursor = self.window.transcript.document().find("玩家消息")
        assistant_cursor = self.window.transcript.document().find("灰风回复")
        self.assertFalse(user_cursor.charFormat().fontItalic())
        self.assertTrue(assistant_cursor.charFormat().fontItalic())

    def test_local_analysis_phrase_advances_one_character_at_a_time(self) -> None:
        self.window.active_turn = True
        self.window.draft = ""
        first_phrase = self.window.phrases[0]
        self.window._refresh_analysis_label()
        self.window.phrase_timer.stop()
        self.assertEqual(self.window.analysis_label.text(), first_phrase[:1])
        self.window._advance_analysis_phrase()
        self.window.phrase_timer.stop()
        self.assertEqual(self.window.analysis_label.text(), first_phrase[:2])

    def test_input_replaces_the_native_caret_with_a_block_cursor(self) -> None:
        self.assertEqual(self.window.command_input.cursorWidth(), 0)
        cursor = self.window.command_input.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.window.command_input.setTextCursor(cursor)


if __name__ == "__main__":
    unittest.main()
