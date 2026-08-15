#!/usr/bin/env python3
"""PySide6 implementation of the transparent Stellaris conversation overlay."""

from __future__ import annotations

import ctypes
import itertools
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QFont,
    QFontDatabase,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizeGrip,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .hotkey import WindowsHotkeyListener, parse_hotkey
from .overlay_config import (
    OverlayConfiguration,
    OverlayGeometry,
    OverlaySettings,
    load_analysis_phrases,
    update_overlay_settings,
)
from .transport import OverlayClient, OverlayNetworkWorker

CRT_GREEN = "#39ff14"
CRT_DIM_GREEN = "#1cae46"
PANEL_BACKGROUND = "rgba(2, 14, 12, 205)"
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
CURSOR_BLINK_INTERVAL_MS = 520


class OverlaySignals(QObject):
    bootstrap = Signal(object)
    event = Signal(object)
    connection = Signal(bool, str)
    message_error = Signal(str)
    hotkey = Signal()
    hotkey_error = Signal(str)


class CommandInput(QPlainTextEdit):
    submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._block_cursor_visible = False
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(CURSOR_BLINK_INTERVAL_MS)
        self._cursor_timer.timeout.connect(self._toggle_block_cursor)
        self.cursorPositionChanged.connect(self._wake_block_cursor)
        # The overlay paints a phosphor-style block cursor over the native caret.
        self.setCursorWidth(0)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        self._wake_block_cursor()
        if event.key() in {Qt.Key_Return, Qt.Key_Enter} and not (
            event.modifiers() & Qt.ShiftModifier
        ):
            text = self.toPlainText().strip()
            if text:
                self.submitted.emit(text)
                self.clear()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        super().mousePressEvent(event)
        self._wake_block_cursor()

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        self._wake_block_cursor()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self._cursor_timer.stop()
        self._block_cursor_visible = False
        self.viewport().update()
        super().focusOutEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self.hasFocus() or not self._block_cursor_visible:
            return
        cursor_rectangle = self.cursorRect()
        cursor_rectangle.setWidth(max(7, self.fontMetrics().horizontalAdvance("M")))
        painter = QPainter(self.viewport())
        painter.fillRect(cursor_rectangle, QColor(CRT_GREEN))
        painter.end()

    def _wake_block_cursor(self) -> None:
        if not self.hasFocus():
            return
        self._block_cursor_visible = True
        self._cursor_timer.start()
        self.viewport().update()

    def _toggle_block_cursor(self) -> None:
        if not self.hasFocus():
            self._cursor_timer.stop()
            self._block_cursor_visible = False
        else:
            self._block_cursor_visible = not self._block_cursor_visible
        self.viewport().update()


class DragHandle(QLabel):
    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            window_handle = self.window().windowHandle()
            if window_handle is not None and window_handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)


class OverlaySettingsDialog(QDialog):
    def __init__(self, settings: OverlaySettings, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("IAG Overlay Configuration")
        self.setModal(True)
        self.hotkey = QLineEdit(settings.hotkey)
        self.opacity = QDoubleSpinBox()
        self.opacity.setRange(0.25, 0.98)
        self.opacity.setSingleStep(0.05)
        self.opacity.setDecimals(2)
        self.opacity.setValue(settings.opacity)
        self.font_size = QSpinBox()
        self.font_size.setRange(10, 28)
        self.font_size.setValue(settings.font_size)
        self.phrase_interval = QDoubleSpinBox()
        self.phrase_interval.setRange(0.8, 30.0)
        self.phrase_interval.setSingleStep(0.2)
        self.phrase_interval.setDecimals(1)
        self.phrase_interval.setValue(settings.analysis_phrase_interval_seconds)
        self.character_interval = QSpinBox()
        self.character_interval.setRange(20, 250)
        self.character_interval.setSingleStep(5)
        self.character_interval.setValue(settings.analysis_character_interval_ms)

        form = QFormLayout(self)
        form.addRow("Global hotkey", self.hotkey)
        form.addRow("Opacity", self.opacity)
        form.addRow("Font size", self.font_size)
        form.addRow("Analysis phrase hold seconds", self.phrase_interval)
        form.addRow("Analysis character milliseconds", self.character_interval)
        phrase_path = QLineEdit(str(settings.analysis_phrases_file))
        phrase_path.setReadOnly(True)
        form.addRow("Phrase file", phrase_path)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _validate_and_accept(self) -> None:
        try:
            parse_hotkey(self.hotkey.text())
        except ValueError as error:
            QMessageBox.warning(self, "Invalid global hotkey", str(error))
            return
        self.accept()

    def apply_to(self, settings: OverlaySettings) -> OverlaySettings:
        return replace(
            settings,
            hotkey=parse_hotkey(self.hotkey.text()).text,
            opacity=float(self.opacity.value()),
            font_size=int(self.font_size.value()),
            analysis_phrase_interval_seconds=float(self.phrase_interval.value()),
            analysis_character_interval_ms=int(self.character_interval.value()),
        )


class OverlayWindow(QWidget):
    def __init__(
        self,
        configuration: OverlayConfiguration,
        *,
        preview: bool = False,
        persist_settings: bool | None = None,
    ) -> None:
        super().__init__()
        self.configuration = configuration
        self.settings = configuration.settings
        self.preview = preview
        self.persist_settings = (
            not preview if persist_settings is None else bool(persist_settings)
        )
        self.signals = OverlaySignals()
        self.signals.bootstrap.connect(self.apply_bootstrap)
        self.signals.event.connect(self.apply_event)
        self.signals.connection.connect(self.set_connection_state)
        self.signals.message_error.connect(self.show_message_error)
        self.signals.hotkey.connect(self.toggle_interaction)
        self.signals.hotkey_error.connect(self.show_hotkey_error)
        self.network: OverlayNetworkWorker | None = None
        self.hotkey_listener: WindowsHotkeyListener | None = None
        self.interactive = False
        self.previous_foreground_window = 0
        self.conversation_id = ""
        self.seen_message_ids: set[int] = set()
        self.active_turn = False
        self.draft = ""
        self.phrases = load_analysis_phrases(self.settings.analysis_phrases_file)
        self.phrase_cycle = itertools.cycle(self.phrases)
        self._local_analysis_active = False
        self._analysis_phrase = ""
        self._analysis_character_index = 0
        self._analysis_holding = False
        self._body_font = QFont()

        self.setWindowTitle("IAG Game Overlay")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._build_ui()
        self._apply_font()
        self._apply_settings_geometry()
        self._apply_style()

        self.phrase_timer = QTimer(self)
        self.phrase_timer.setSingleShot(True)
        self.phrase_timer.timeout.connect(self._advance_analysis_phrase)
        self.geometry_timer = QTimer(self)
        self.geometry_timer.setSingleShot(True)
        self.geometry_timer.timeout.connect(self._persist_geometry)

        if not preview:
            self.network = OverlayNetworkWorker(
                OverlayClient(configuration.connection),
                on_bootstrap=self.signals.bootstrap.emit,
                on_event=self.signals.event.emit,
                on_connection=self.signals.connection.emit,
                on_message_error=self.signals.message_error.emit,
            )
        self._restart_hotkey_listener()

    def _build_ui(self) -> None:
        self.panel = QFrame(self)
        self.panel.setObjectName("panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.panel)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(7)

        header = QHBoxLayout()
        self.drag_handle = DragHandle("GREY TEMPEST // CONVERSATION LINK")
        self.drag_handle.setObjectName("title")
        header.addWidget(self.drag_handle, 1)
        self.connection_label = QLabel("OFFLINE")
        self.connection_label.setObjectName("connection")
        header.addWidget(self.connection_label)
        self.config_button = QPushButton("CFG")
        self.config_button.setObjectName("headerButton")
        self.config_button.clicked.connect(self.open_settings)
        header.addWidget(self.config_button)
        self.close_button = QPushButton("X")
        self.close_button.setObjectName("headerButton")
        self.close_button.clicked.connect(self.close)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        self.campaign_label = QLabel("NO ACTIVE CAMPAIGN")
        self.campaign_label.setObjectName("campaign")
        layout.addWidget(self.campaign_label)

        self.transcript = QPlainTextEdit()
        self.transcript.setObjectName("transcript")
        self.transcript.setReadOnly(True)
        self.transcript.setFrameShape(QFrame.NoFrame)
        self.transcript.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(self.transcript, 1)

        self.analysis_label = QLabel("")
        self.analysis_label.setObjectName("analysis")
        self.analysis_label.setWordWrap(True)
        self.analysis_label.hide()
        layout.addWidget(self.analysis_label)

        self.input_row = QWidget()
        input_layout = QHBoxLayout(self.input_row)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(7)
        prompt = QLabel(">")
        prompt.setObjectName("prompt")
        input_layout.addWidget(prompt, 0, Qt.AlignTop)
        self.command_input = CommandInput()
        self.command_input.setObjectName("commandInput")
        self.command_input.setPlaceholderText("TRANSMIT DIRECTIVE...")
        self.command_input.setMaximumHeight(78)
        self.command_input.submitted.connect(self.send_message)
        input_layout.addWidget(self.command_input, 1)
        layout.addWidget(self.input_row)

        grip_row = QHBoxLayout()
        self.hotkey_hint = QLabel(f"{self.settings.hotkey}  //  TOGGLE INPUT")
        self.hotkey_hint.setObjectName("hint")
        grip_row.addWidget(self.hotkey_hint)
        grip_row.addStretch(1)
        self.size_grip = QSizeGrip(self.panel)
        grip_row.addWidget(self.size_grip)
        layout.addLayout(grip_row)

    def _font_family(self) -> str:
        path = self.settings.font_file
        if path is not None and path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    return families[0]
        return "Consolas"

    def _apply_font(self) -> None:
        family = self._font_family()
        body = QFont(family, self.settings.font_size)
        body.setLetterSpacing(QFont.PercentageSpacing, 100)
        body.setItalic(False)
        body.setWeight(QFont.Normal)
        self._body_font = body
        self.setFont(body)
        for widget in (
            self.panel,
            self.drag_handle,
            self.connection_label,
            self.config_button,
            self.close_button,
            self.campaign_label,
            self.transcript,
            self.analysis_label,
            self.command_input,
            self.hotkey_hint,
        ):
            widget.setFont(body)
        self.transcript.document().setDefaultFont(body)
        self.command_input.document().setDefaultFont(body)

    def _apply_style(self) -> None:
        self.setWindowOpacity(self.settings.opacity)
        self.setStyleSheet(
            f"""
            QFrame#panel {{
                background: {PANEL_BACKGROUND};
                border: 1px solid {CRT_DIM_GREEN};
            }}
            QLabel#title {{
                color: {CRT_GREEN};
                letter-spacing: 2px;
            }}
            QLabel#campaign, QLabel#connection, QLabel#hint {{
                color: {CRT_DIM_GREEN}; font-size: 10px;
            }}
            QPlainTextEdit#transcript, QPlainTextEdit#commandInput {{
                color: {CRT_GREEN};
                background: transparent;
                border: none;
                selection-background-color: #176b34;
            }}
            QPlainTextEdit#commandInput {{
                border-top: 1px solid {CRT_DIM_GREEN};
                padding-top: 5px;
            }}
            QLabel#prompt {{ color: {CRT_GREEN}; font-size: 19px; }}
            QLabel#analysis {{ font-style: italic; }}
            QPushButton#headerButton {{
                color: {CRT_DIM_GREEN};
                background: transparent;
                border: 1px solid {CRT_DIM_GREEN};
                padding: 1px 5px;
                min-width: 42px;
            }}
            QPushButton#headerButton:hover {{
                color: {CRT_GREEN};
                border-color: {CRT_GREEN};
            }}
            QScrollBar:vertical {{ background: transparent; width: 7px; }}
            QScrollBar::handle:vertical {{
                background: {CRT_DIM_GREEN};
                min-height: 18px;
            }}
            """
        )

    def _apply_settings_geometry(self) -> None:
        geometry = self.settings.geometry
        screen = QApplication.screenAt(QPoint(geometry.x, geometry.y))
        if screen is None:
            screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        width = geometry.width
        height = geometry.height
        x = geometry.x
        y = geometry.y
        if available is not None:
            width = min(width, available.width())
            height = min(height, available.height())
            x = max(available.left(), min(x, available.right() - width + 1))
            y = max(available.top(), min(y, available.bottom() - height + 1))
        self.setGeometry(x, y, width, height)

    def showEvent(self, event: QEvent) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self.set_interactive(False))
        if self.network is not None:
            self.network.start()
        elif self.preview:
            self.apply_bootstrap(self._preview_bootstrap())

    def _preview_bootstrap(self) -> dict[str, Any]:
        return {
            "conversation": {
                "conversation_id": "preview",
                "title": "地球联合国 // 2204.07.01",
            },
            "messages": [
                {
                    "id": 1,
                    "role": "user",
                    "content": "优先解决矿物赤字，但不要牺牲科研区的长期规划。",
                },
                {
                    "id": 2,
                    "role": "assistant",
                    "content": (
                        "矿物安全余量正在收窄。"
                        "我会先扩充特拉比斯特-I 的采矿区划，"
                        "随后重新评估消费品与研究岗位的承载能力。"
                    ),
                },
            ],
            "stream": {"active": True, "draft": "", "latest_sequence": 2},
        }

    def _restart_hotkey_listener(self) -> None:
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        self.hotkey_listener = WindowsHotkeyListener(
            self.settings.hotkey,
            self.signals.hotkey.emit,
            self.signals.hotkey_error.emit,
        )
        self.hotkey_listener.start()
        self.hotkey_hint.setText(f"{self.settings.hotkey}  //  TOGGLE INPUT")

    def toggle_interaction(self) -> None:
        self.set_interactive(not self.interactive)

    def set_interactive(self, enabled: bool) -> None:
        self.interactive = bool(enabled)
        if self.interactive:
            self.previous_foreground_window = int(
                ctypes.windll.user32.GetForegroundWindow()
            )
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not self.interactive)
        self.input_row.setVisible(self.interactive)
        self.config_button.setVisible(self.interactive)
        self.close_button.setVisible(self.interactive)
        self.size_grip.setVisible(self.interactive)
        self._set_windows_click_through(not self.interactive)
        if self.interactive:
            self.show()
            self.raise_()
            self.activateWindow()
            self.command_input.setFocus(Qt.ShortcutFocusReason)
        elif self.previous_foreground_window:
            ctypes.windll.user32.SetForegroundWindow(self.previous_foreground_window)

    def _set_windows_click_through(self, enabled: bool) -> None:
        if not hasattr(ctypes, "windll"):
            return
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        style = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
        if enabled:
            style |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        else:
            style &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )

    def _append_message(self, message: dict[str, Any]) -> None:
        try:
            message_id = int(message.get("id"))
        except (TypeError, ValueError):
            message_id = 0
        if message_id and message_id in self.seen_message_ids:
            return
        content = str(message.get("content") or "").strip()
        if not content:
            return
        if message_id:
            self.seen_message_ids.add(message_id)
        is_assistant = message.get("role") == "assistant"
        prefix = "" if is_assistant else "> "
        cursor = self.transcript.textCursor()
        cursor.movePosition(QTextCursor.End)
        if self.transcript.toPlainText():
            cursor.insertBlock()
        message_format = QTextCharFormat()
        message_format.setFont(self._body_font)
        message_format.setFontItalic(is_assistant)
        message_format.setForeground(QColor(CRT_GREEN))
        cursor.insertText(prefix + content, message_format)
        self.transcript.setTextCursor(cursor)
        self.transcript.ensureCursorVisible()

    def apply_bootstrap(self, value: object) -> None:
        if not isinstance(value, dict):
            return
        conversation = value.get("conversation")
        conversation = conversation if isinstance(conversation, dict) else {}
        selected = str(conversation.get("conversation_id") or "")
        if selected != self.conversation_id:
            self.conversation_id = selected
            self.seen_message_ids.clear()
            self.transcript.clear()
        title = str(
            conversation.get("campaign_label")
            or conversation.get("title")
            or selected
            or "NO ACTIVE CAMPAIGN"
        )
        self.campaign_label.setText(title.upper())
        messages = value.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict):
                    self._append_message(message)
        stream = value.get("stream")
        stream = stream if isinstance(stream, dict) else {}
        self.active_turn = bool(stream.get("active", False))
        self.draft = str(stream.get("draft") or "")
        self._refresh_analysis_label()

    def apply_event(self, value: object) -> None:
        if not isinstance(value, dict):
            return
        if str(value.get("conversation_id") or "") != self.conversation_id:
            return
        event_type = str(value.get("event_type") or "")
        payload = value.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if event_type == "turn_started":
            self.active_turn = True
            self.draft = ""
        elif event_type == "user_message":
            self._append_message(payload)
        elif event_type == "assistant_started":
            self.draft = ""
        elif event_type == "assistant_delta":
            self.draft += str(payload.get("delta") or "")
        elif event_type == "assistant_final":
            self.draft = ""
            self._append_message(payload)
        elif event_type == "turn_finished":
            self.active_turn = False
            self.draft = ""
        self._refresh_analysis_label()

    def _refresh_analysis_label(self) -> None:
        if self.draft:
            self._stop_local_analysis_animation()
            self._set_analysis_response_style(True)
            self.analysis_label.setText(self.draft)
            self.analysis_label.show()
        elif self.active_turn:
            if not self._local_analysis_active:
                self._start_local_analysis_animation()
        else:
            self._stop_local_analysis_animation()
            self.analysis_label.clear()
            self.analysis_label.hide()

    def _set_analysis_response_style(self, response: bool) -> None:
        color = CRT_GREEN if response else CRT_DIM_GREEN
        self.analysis_label.setStyleSheet(
            f"color: {color}; font-style: italic;"
        )

    def _start_local_analysis_animation(self) -> None:
        self._local_analysis_active = True
        self._analysis_phrase = next(self.phrase_cycle)
        self._analysis_character_index = 0
        self._analysis_holding = False
        self._set_analysis_response_style(False)
        self.analysis_label.clear()
        self.analysis_label.show()
        self._advance_analysis_phrase()

    def _stop_local_analysis_animation(self) -> None:
        self.phrase_timer.stop()
        self._local_analysis_active = False
        self._analysis_phrase = ""
        self._analysis_character_index = 0
        self._analysis_holding = False

    def _advance_analysis_phrase(self) -> None:
        if not self.active_turn or self.draft or not self._local_analysis_active:
            self._stop_local_analysis_animation()
            return
        if self._analysis_holding:
            self._analysis_phrase = next(self.phrase_cycle)
            self._analysis_character_index = 0
            self._analysis_holding = False
            self.analysis_label.clear()
        if self._analysis_character_index < len(self._analysis_phrase):
            self._analysis_character_index += 1
            self.analysis_label.setText(
                self._analysis_phrase[: self._analysis_character_index]
            )
            self.phrase_timer.start(self.settings.analysis_character_interval_ms)
            return
        self._analysis_holding = True
        hold_milliseconds = int(
            self.settings.analysis_phrase_interval_seconds * 1000
        )
        self.phrase_timer.start(max(500, hold_milliseconds))

    def send_message(self, text: str) -> None:
        if self.network is None or not self.conversation_id:
            self.show_message_error("Overlay is not connected to an active campaign.")
            return
        self.network.send_message(self.conversation_id, text)

    def set_connection_state(self, connected: bool, detail: str) -> None:
        self.connection_label.setText("LINKED" if connected else "RETRYING")
        self.connection_label.setToolTip(detail)

    def show_message_error(self, detail: str) -> None:
        self.connection_label.setText("MESSAGE REJECTED")
        self.connection_label.setToolTip(detail)

    def show_hotkey_error(self, detail: str) -> None:
        QMessageBox.critical(self, "IAG Overlay hotkey", detail)

    def open_settings(self) -> None:
        dialog = OverlaySettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.Accepted:
            return
        previous_hotkey = self.settings.hotkey
        self.settings = dialog.apply_to(self.settings)
        self.phrases = load_analysis_phrases(self.settings.analysis_phrases_file)
        self.phrase_cycle = itertools.cycle(self.phrases)
        self._stop_local_analysis_animation()
        self._apply_font()
        self._apply_style()
        if self.persist_settings:
            update_overlay_settings(self.configuration.config_path, self.settings)
        if self.settings.hotkey != previous_hotkey:
            self._restart_hotkey_listener()
        self._refresh_analysis_label()

    def moveEvent(self, event: QEvent) -> None:
        super().moveEvent(event)
        if hasattr(self, "geometry_timer") and self.interactive:
            self.geometry_timer.start(500)

    def resizeEvent(self, event: QEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "geometry_timer") and self.interactive:
            self.geometry_timer.start(500)

    def _persist_geometry(self) -> None:
        current = self.geometry()
        self.settings = replace(
            self.settings,
            geometry=OverlayGeometry(
                x=current.x(),
                y=current.y(),
                width=current.width(),
                height=current.height(),
            ),
        )
        if self.persist_settings:
            update_overlay_settings(self.configuration.config_path, self.settings)

    def closeEvent(self, event: QEvent) -> None:
        self._persist_geometry()
        if self.network is not None:
            self.network.stop()
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        super().closeEvent(event)
