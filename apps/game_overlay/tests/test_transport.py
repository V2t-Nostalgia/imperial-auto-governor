#!/usr/bin/env python3
"""SSE parser tests for the standalone overlay transport."""

from __future__ import annotations

import unittest

from apps.game_overlay.transport import parse_sse_events


class SSEParserTests(unittest.TestCase):
    def test_parses_unicode_visible_event(self) -> None:
        events = parse_sse_events(
            "id: 9\n"
            "event: assistant_delta\n"
            'data: {"sequence":9,"payload":{"delta":"正在分析"}}\n\n'
        )
        self.assertEqual(events[0]["event"], "assistant_delta")
        self.assertEqual(events[0]["data"]["payload"]["delta"], "正在分析")


if __name__ == "__main__":
    unittest.main()
