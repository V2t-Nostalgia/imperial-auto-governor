#!/usr/bin/env python3
"""Guarded fixed-coordinate Stellaris capture and click implementation for Windows."""

from __future__ import annotations

import ctypes
import os
import re
import time
import uuid
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from x11_fixed_click import (
    atomic_write_json,
    commit_calibration,
    guard_score,
    now_iso,
    read_json,
    validate_profile,
)


class WindowsControlError(RuntimeError):
    """Raised when a guarded Windows action cannot be completed safely."""


@dataclass(frozen=True)
class WindowGeometry:
    window_id: int
    title: str
    x: int
    y: int
    width: int
    height: int
    screen: int = 0


if os.name == "nt":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # Keep imports testable on non-Windows hosts.
    user32 = gdi32 = kernel32 = None


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE = 9
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
INPUT_MOUSE = 0


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", INPUTUNION)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", RGBQUAD * 1),
    ]


def _configure_win32() -> None:
    """Declare pointer-sized Win32 signatures before any window work."""
    if os.name != "nt":
        return

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        wintypes.LPDWORD,
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClientRect.argtypes = [wintypes.HWND, wintypes.LPRECT]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, wintypes.LPPOINT]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.SetFocus.restype = wintypes.HWND
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.SendInput.argtypes = [
        wintypes.UINT,
        ctypes.POINTER(INPUT),
        ctypes.c_int,
    ]
    user32.SendInput.restype = wintypes.UINT
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL

    # Per-monitor DPI awareness keeps client coordinates identical to the
    # pixels captured by BitBlt on scaled displays. It is harmless if a GUI
    # host already selected a DPI mode before importing this module.
    try:
        setter = user32.SetProcessDpiAwarenessContext
        setter.argtypes = [wintypes.HANDLE]
        setter.restype = wintypes.BOOL
        setter(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


_configure_win32()


def _require_windows() -> None:
    if os.name != "nt" or user32 is None:
        raise WindowsControlError("Windows fixed-click control is only available on Windows.")


def _process_name(pid: int) -> str:
    _require_windows()
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32_768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle,
            0,
            buffer,
            ctypes.byref(size),
        ):
            return ""
        return Path(buffer.value).name
    finally:
        kernel32.CloseHandle(handle)


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def find_window(title_regex: str) -> int:
    _require_windows()
    try:
        pattern = re.compile(title_regex, re.IGNORECASE)
    except re.error as error:
        raise WindowsControlError(f"Invalid Stellaris window regex: {error}") from error
    matches: list[tuple[int, int, str, str]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        title = _window_title(hwnd)
        if not title or not pattern.search(title):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = _process_name(int(pid.value))
        matches.append((int(hwnd), int(pid.value), process_name, title))
        return True

    if not user32.EnumWindows(callback, 0):
        raise WindowsControlError("EnumWindows failed while locating Stellaris.")
    stellaris = [
        item for item in matches if item[2].casefold() == "stellaris.exe"
    ]
    selected = stellaris if stellaris else matches
    if len(selected) != 1:
        details = ", ".join(
            f"hwnd={item[0]} pid={item[1]} process={item[2] or '?'} title={item[3]!r}"
            for item in matches
        )
        raise WindowsControlError(
            f"Expected one visible Stellaris window, found {len(selected)}"
            + (f" ({details})" if details else "")
        )
    return selected[0][0]


def get_window_geometry(hwnd: int) -> WindowGeometry:
    _require_windows()
    client = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(client)):
        raise WindowsControlError("GetClientRect failed for Stellaris.")
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise WindowsControlError("ClientToScreen failed for Stellaris.")
    width = int(client.right - client.left)
    height = int(client.bottom - client.top)
    if width <= 0 or height <= 0:
        raise WindowsControlError("The Stellaris client area is empty or minimized.")
    return WindowGeometry(
        window_id=int(hwnd),
        title=_window_title(hwnd),
        x=int(origin.x),
        y=int(origin.y),
        width=width,
        height=height,
    )


def _activate_window(hwnd: int) -> None:
    _require_windows()
    user32.ShowWindow(hwnd, SW_RESTORE)
    foreground = user32.GetForegroundWindow()
    current_thread = kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    foreground_thread = (
        user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    )
    attached_target = bool(
        target_thread and target_thread != current_thread
        and user32.AttachThreadInput(current_thread, target_thread, True)
    )
    attached_foreground = bool(
        foreground_thread
        and foreground_thread != current_thread
        and foreground_thread != target_thread
        and user32.AttachThreadInput(current_thread, foreground_thread, True)
    )
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached_foreground:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
        if attached_target:
            user32.AttachThreadInput(current_thread, target_thread, False)
    if user32.GetForegroundWindow() != hwnd:
        # A short Alt tap satisfies the foreground-lock rule without typing text.
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)
    time.sleep(0.15)
    if user32.GetForegroundWindow() != hwnd:
        raise WindowsControlError("Windows refused to foreground the Stellaris window.")


def capture_geometry(geometry: WindowGeometry, output_path: Path) -> None:
    _require_windows()
    try:
        import cv2
        import numpy
    except ImportError as error:
        raise WindowsControlError("OpenCV and NumPy are required for Windows capture.") from error
    output_path.parent.mkdir(parents=True, exist_ok=True)
    screen_dc = user32.GetDC(0)
    if not screen_dc:
        raise WindowsControlError("GetDC failed while capturing Stellaris.")
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    if not memory_dc:
        user32.ReleaseDC(0, screen_dc)
        raise WindowsControlError("CreateCompatibleDC failed while capturing Stellaris.")
    bitmap = gdi32.CreateCompatibleBitmap(
        screen_dc,
        geometry.width,
        geometry.height,
    )
    if not bitmap:
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(0, screen_dc)
        raise WindowsControlError("CreateCompatibleBitmap failed while capturing Stellaris.")
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not gdi32.BitBlt(
            memory_dc,
            0,
            0,
            geometry.width,
            geometry.height,
            screen_dc,
            geometry.x,
            geometry.y,
            SRCCOPY,
        ):
            raise WindowsControlError("BitBlt failed while capturing Stellaris.")
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = geometry.width
        info.bmiHeader.biHeight = -geometry.height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        buffer = ctypes.create_string_buffer(geometry.width * geometry.height * 4)
        rows = gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            geometry.height,
            buffer,
            ctypes.byref(info),
            DIB_RGB_COLORS,
        )
        if rows != geometry.height:
            raise WindowsControlError("GetDIBits returned an incomplete screenshot.")
        image = numpy.frombuffer(buffer, dtype=numpy.uint8).reshape(
            geometry.height,
            geometry.width,
            4,
        )
        if not cv2.imwrite(str(output_path), image[:, :, :3]):
            raise WindowsControlError("Failed to save the Windows screenshot.")
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(0, screen_dc)


def capture_calibration(
    capture_root: Path,
    *,
    display: str | None = None,
    xauthority: str | None = None,
    title_regex: str,
    **_ignored: Any,
) -> dict[str, Any]:
    del display, xauthority
    hwnd = find_window(title_regex)
    _activate_window(hwnd)
    geometry = get_window_geometry(hwnd)
    capture_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
    screenshot = capture_root / f"{capture_id}.png"
    metadata_path = capture_root / f"{capture_id}.json"
    capture_geometry(geometry, screenshot)
    metadata = {
        "schema": "iag.calibration_capture.v1",
        "capture_id": capture_id,
        "created_at": now_iso(),
        "screenshot_path": str(screenshot.resolve()),
        "geometry": asdict(geometry),
        "platform": "windows",
        "title_regex": title_regex,
    }
    atomic_write_json(metadata_path, metadata)
    metadata["metadata_path"] = str(metadata_path.resolve())
    return metadata


def _click_flags(button: int) -> tuple[int, int]:
    try:
        return {
            1: (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            2: (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
            3: (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        }[button]
    except KeyError as error:
        raise WindowsControlError("Only standard mouse buttons are supported.") from error


def _send_mouse_input(flags: int) -> None:
    event = INPUT(type=INPUT_MOUSE)
    event.mi = MOUSEINPUT(
        dx=0,
        dy=0,
        mouseData=0,
        dwFlags=flags,
        time=0,
        dwExtraInfo=0,
    )
    if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
        error_code = getattr(ctypes, "get_last_error", lambda: 0)()
        raise WindowsControlError(
            f"SendInput failed while injecting a guarded click (WinError {error_code})."
        )


def execute_fixed_click(
    profile_path: Path,
    *,
    display: str | None = None,
    xauthority: str | None = None,
    artifact_root: Path,
    move_only: bool = False,
    pointer_settle_seconds: float = 0.20,
    click_hold_seconds: float = 0.08,
    post_click_settle_seconds: float = 0.25,
    **_ignored: Any,
) -> dict[str, Any]:
    del display, xauthority
    profile = read_json(profile_path)
    try:
        validate_profile(profile)
    except Exception as error:
        raise WindowsControlError(str(error)) from error
    hwnd = find_window(str(profile["window"]["title_regex"]))
    _activate_window(hwnd)
    geometry = get_window_geometry(hwnd)
    expected = (
        int(profile["window"]["width"]),
        int(profile["window"]["height"]),
    )
    if (geometry.width, geometry.height) != expected:
        raise WindowsControlError(
            "Stellaris window size changed after calibration: "
            f"expected {expected[0]}x{expected[1]}, found "
            f"{geometry.width}x{geometry.height}."
        )
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
    before_path = artifact_root / f"click_before_{run_id}.png"
    after_path = artifact_root / f"click_after_{run_id}.png"
    user32.SetCursorPos(geometry.x + 8, geometry.y + 8)
    time.sleep(0.12)
    capture_geometry(geometry, before_path)
    try:
        score = guard_score(profile, before_path)
    except Exception as error:
        raise WindowsControlError(str(error)) from error
    threshold = float(profile["guard"].get("threshold", 0.0))
    if score < threshold:
        raise WindowsControlError(
            f"Fixed click guard rejected the current UI ({score:.3f} < {threshold:.3f})."
        )
    target = profile["target"]
    x = int(target["x"])
    y = int(target["y"])
    if not user32.SetCursorPos(geometry.x + x, geometry.y + y):
        raise WindowsControlError("SetCursorPos failed for the calibrated target.")
    pointer_settle_seconds = max(float(pointer_settle_seconds), 0.0)
    click_hold_seconds = max(float(click_hold_seconds), 0.0)
    post_click_settle_seconds = max(float(post_click_settle_seconds), 0.0)
    if pointer_settle_seconds:
        # Clausewitz UI hit testing can lag behind an instantaneous cursor warp.
        # Let the game observe the hover before injecting the button transition.
        time.sleep(pointer_settle_seconds)
    if not move_only:
        down, up = _click_flags(int(target.get("button", 1)))
        _send_mouse_input(down)
        if click_hold_seconds:
            time.sleep(click_hold_seconds)
        _send_mouse_input(up)
        if post_click_settle_seconds:
            time.sleep(post_click_settle_seconds)
        capture_geometry(geometry, after_path)
    return {
        "schema": "iag.fixed_click_result.v1",
        "executed_at": now_iso(),
        "move_only": move_only,
        "window": asdict(geometry),
        "target": {"x": x, "y": y},
        "guard_score": round(score, 6),
        "guard_threshold": threshold,
        "before_screenshot": str(before_path.resolve()),
        "after_screenshot": (
            str(after_path.resolve()) if not move_only else None
        ),
        "input_method": "SendInput",
        "pointer_settle_seconds": pointer_settle_seconds,
        "click_hold_seconds": click_hold_seconds,
        "post_click_settle_seconds": post_click_settle_seconds,
        "platform": "windows",
    }
