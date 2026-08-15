#!/usr/bin/env python3
"""Configurable Windows global hotkey parsing and registration."""

from __future__ import annotations

import ctypes
import os
import threading
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MODIFIER_NAMES = {
    "ALT": MOD_ALT,
    "CTRL": MOD_CONTROL,
    "CONTROL": MOD_CONTROL,
    "SHIFT": MOD_SHIFT,
    "WIN": MOD_WIN,
    "WINDOWS": MOD_WIN,
}

VIRTUAL_KEYS = {
    "BACKSPACE": 0x08,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "SPACE": 0x20,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "END": 0x23,
    "HOME": 0x24,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
}
VIRTUAL_KEYS.update({f"F{index}": 0x6F + index for index in range(1, 25)})


@dataclass(frozen=True, slots=True)
class HotkeySpec:
    text: str
    modifiers: int
    virtual_key: int


def parse_hotkey(value: str) -> HotkeySpec:
    parts = [part.strip().upper() for part in str(value).split("+") if part.strip()]
    if len(parts) < 2:
        raise ValueError(
            "Global hotkey must include at least one modifier and one key."
        )
    modifiers = MOD_NOREPEAT
    normalized_modifiers: list[str] = []
    key_name: str | None = None
    for part in parts:
        if part in MODIFIER_NAMES:
            canonical = "Ctrl" if part in {"CTRL", "CONTROL"} else part.title()
            if canonical not in normalized_modifiers:
                normalized_modifiers.append(canonical)
                modifiers |= MODIFIER_NAMES[part]
            continue
        if key_name is not None:
            raise ValueError("Global hotkey can contain only one non-modifier key.")
        key_name = part
    if key_name is None:
        raise ValueError("Global hotkey is missing its primary key.")
    if len(key_name) == 1 and (key_name.isalpha() or key_name.isdigit()):
        virtual_key = ord(key_name)
    else:
        try:
            virtual_key = VIRTUAL_KEYS[key_name]
        except KeyError as error:
            raise ValueError(f"Unsupported global hotkey key: {key_name}") from error
    display_key = "Esc" if key_name in {"ESC", "ESCAPE"} else key_name.title()
    return HotkeySpec(
        "+".join([*normalized_modifiers, display_key]),
        modifiers,
        virtual_key,
    )


class WindowsHotkeyListener:
    """Own a RegisterHotKey message loop without coupling it to Qt."""

    def __init__(
        self,
        hotkey: str,
        callback: Callable[[], None],
        error_callback: Callable[[str], None],
    ) -> None:
        self.spec = parse_hotkey(hotkey)
        self.callback = callback
        self.error_callback = error_callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()

    def start(self) -> None:
        if os.name != "nt":
            raise RuntimeError("The global overlay hotkey currently requires Windows.")
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="iag-overlay-hotkey",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(2)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        hotkey_id = 0x4941
        registered = bool(
            user32.RegisterHotKey(
                None,
                hotkey_id,
                self.spec.modifiers,
                self.spec.virtual_key,
            )
        )
        self._ready.set()
        if not registered:
            self.error_callback(
                f"Global hotkey {self.spec.text} is already in use or unavailable."
            )
            return
        try:
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and message.wParam == hotkey_id:
                    self.callback()
        finally:
            user32.UnregisterHotKey(None, hotkey_id)
            self._thread_id = 0

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id,
                WM_QUIT,
                0,
                0,
            )
        thread.join(timeout=2)
        self._thread = None
