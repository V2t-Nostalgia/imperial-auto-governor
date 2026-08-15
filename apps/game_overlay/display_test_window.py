#!/usr/bin/env python3
"""Local-only overlay window used to test presentation without an Agent."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from PySide6.QtCore import QEvent, QTimer

from .overlay_config import OverlayConfiguration
from .window import OverlayWindow

MOCK_RESPONSES = (
    (
        "矿物安全余量正在收窄。我会先扩充采矿区划，随后重新评估"
        "消费品与研究岗位的承载能力。"
    ),
    (
        "殖民地的现有队列足以覆盖近期人口增长。这里暂时不需要新的结构，"
        "我会继续观察住房与岗位变化。"
    ),
    (
        "能源与矿物收入已经恢复稳定。下一阶段可以逐步增加合金产能，"
        "但我不会同时铺开过多高维护建筑。"
    ),
)


def chunk_visible_text(text: str, size: int = 2) -> tuple[str, ...]:
    """Split a mock reply into deterministic visible stream fragments."""
    if size < 1:
        raise ValueError("Mock stream chunk size must be positive.")
    return tuple(text[index : index + size] for index in range(0, len(text), size))


class DisplayTestWindow(OverlayWindow):
    """Exercise the complete overlay rendering flow with no network client."""

    def __init__(self, configuration: OverlayConfiguration) -> None:
        self._display_test_started = False
        self._message_id = 100
        self._response_index = 0
        self._final_response = ""
        self._chunks: Iterator[str] = iter(())
        super().__init__(
            configuration,
            preview=True,
            persist_settings=True,
        )
        self.setWindowTitle("IAG Overlay Display Test")
        self.drag_handle.setText("GREY TEMPEST // DISPLAY TEST")
        self.command_input.setPlaceholderText("TRANSMIT LOCAL TEST MESSAGE...")

        self.mock_delay_timer = QTimer(self)
        self.mock_delay_timer.setSingleShot(True)
        self.mock_delay_timer.timeout.connect(self._start_mock_stream)
        self.mock_stream_timer = QTimer(self)
        self.mock_stream_timer.timeout.connect(self._emit_mock_delta)

    def _preview_bootstrap(self) -> dict[str, Any]:
        return {
            "conversation": {
                "conversation_id": "display-test",
                "title": "本地显示测试 // 无 Agent 连接",
            },
            "messages": [
                {
                    "id": 1,
                    "role": "assistant",
                    "content": (
                        "显示链路已经就绪。输入任意内容即可测试分析文案、"
                        "流式回复、拖动、缩放与快捷键切换。"
                    ),
                }
            ],
            "stream": {"active": False, "draft": "", "latest_sequence": 1},
        }

    def showEvent(self, event: QEvent) -> None:
        first_show = not self._display_test_started
        super().showEvent(event)
        self.connection_label.setText("LOCAL TEST")
        self.connection_label.setToolTip(
            "This executable has no Agent connection or authentication path."
        )
        if first_show:
            self._display_test_started = True
            QTimer.singleShot(0, lambda: self.set_interactive(True))

    def _event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.apply_event(
            {
                "conversation_id": "display-test",
                "event_type": event_type,
                "payload": payload,
            }
        )

    def send_message(self, text: str) -> None:
        if self.active_turn:
            self.connection_label.setText("LOCAL BUSY")
            return
        self._message_id += 1
        self._event(
            "user_message",
            {"id": self._message_id, "role": "user", "content": text},
        )
        self._event("turn_started", {})
        self.connection_label.setText("LOCAL THINKING")
        self._final_response = MOCK_RESPONSES[
            self._response_index % len(MOCK_RESPONSES)
        ]
        self._response_index += 1
        self._chunks = iter(chunk_visible_text(self._final_response))
        self.mock_delay_timer.start(1600)

    def _start_mock_stream(self) -> None:
        self._event("assistant_started", {"round_index": 0})
        self.connection_label.setText("LOCAL STREAM")
        self.mock_stream_timer.start(45)

    def _emit_mock_delta(self) -> None:
        try:
            delta = next(self._chunks)
        except StopIteration:
            self.mock_stream_timer.stop()
            self._message_id += 1
            self._event(
                "assistant_final",
                {
                    "id": self._message_id,
                    "role": "assistant",
                    "content": self._final_response,
                    "round_index": 0,
                },
            )
            self._event("turn_finished", {})
            self.connection_label.setText("LOCAL TEST")
            return
        self._event("assistant_delta", {"round_index": 0, "delta": delta})
