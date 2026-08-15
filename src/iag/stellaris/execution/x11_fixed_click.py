#!/usr/bin/env python3
"""Calibrate and execute one fixed, guarded click in an X11 Stellaris window."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class X11ControlError(RuntimeError):
    """Raised when a guarded X11 action cannot be completed safely."""


@dataclass(frozen=True)
class WindowGeometry:
    window_id: int
    title: str
    x: int
    y: int
    width: int
    height: int
    screen: int


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise X11ControlError(f"{path} must contain a JSON object.")
    return value


def x11_environment(display: str, xauthority: str | None) -> dict[str, str]:
    environment = os.environ.copy()
    environment["DISPLAY"] = display
    if xauthority:
        environment["XAUTHORITY"] = xauthority
    return environment


def run_checked(
    command: list[str],
    *,
    environment: dict[str, str],
    timeout: int = 15,
) -> str:
    try:
        completed = subprocess.run(
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise X11ControlError(f"Required executable is missing: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise X11ControlError(f"Command timed out: {command[0]}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise X11ControlError(
            f"Command failed ({completed.returncode}): {' '.join(command)}: {detail}"
        )
    return completed.stdout


def read_process_name(pid: int) -> str:
    """Return the executable name for a local Linux process when available."""
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def get_window_pid(
    window_id: int,
    *,
    environment: dict[str, str],
    xdotool: str = "xdotool",
) -> int | None:
    try:
        output = run_checked(
            [xdotool, "getwindowpid", str(window_id)],
            environment=environment,
        ).strip()
    except X11ControlError:
        return None
    return int(output) if output.isdigit() else None


def find_window(
    title_regex: str,
    *,
    environment: dict[str, str],
    xdotool: str = "xdotool",
) -> int:
    output = run_checked(
        [xdotool, "search", "--onlyvisible", "--name", title_regex],
        environment=environment,
    )
    ids = [int(line.strip()) for line in output.splitlines() if line.strip().isdigit()]
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise X11ControlError("No visible Stellaris window matched the calibration profile.")
    if len(ids) == 1:
        return ids[0]

    # GNOME/Mutter creates a visible frame window with the same title as the
    # X11 client. Select by owning process so the guard still fails closed when
    # more than one real Stellaris client is visible.
    candidates: list[tuple[int, int | None, str]] = []
    stellaris_windows: list[int] = []
    for window_id in ids:
        pid = get_window_pid(window_id, environment=environment, xdotool=xdotool)
        process_name = read_process_name(pid) if pid is not None else ""
        candidates.append((window_id, pid, process_name))
        if process_name.casefold() in {"stellaris", "stellaris.exe"}:
            stellaris_windows.append(window_id)
    if len(stellaris_windows) == 1:
        return stellaris_windows[0]

    detail = ", ".join(
        f"id={window_id} pid={pid if pid is not None else '?'} "
        f"process={process_name or '?'}"
        for window_id, pid, process_name in candidates
    )
    raise X11ControlError(
        "Expected one real Stellaris window after process filtering, "
        f"found {len(stellaris_windows)} among {len(ids)} title matches "
        f"({detail})."
    )


def parse_geometry_shell(output: str, window_id: int, title: str) -> WindowGeometry:
    values: dict[str, int] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        raw = raw.strip()
        if raw.lstrip("-").isdigit():
            values[key.strip().upper()] = int(raw)
    required = {"X", "Y", "WIDTH", "HEIGHT", "SCREEN"}
    missing = required - values.keys()
    if missing:
        raise X11ControlError(f"xdotool geometry output is missing: {sorted(missing)}")
    return WindowGeometry(
        window_id=window_id,
        title=title,
        x=values["X"],
        y=values["Y"],
        width=values["WIDTH"],
        height=values["HEIGHT"],
        screen=values["SCREEN"],
    )


def get_window_geometry(
    window_id: int,
    *,
    environment: dict[str, str],
    xdotool: str = "xdotool",
) -> WindowGeometry:
    title = run_checked(
        [xdotool, "getwindowname", str(window_id)],
        environment=environment,
    ).strip()
    output = run_checked(
        [xdotool, "getwindowgeometry", "--shell", str(window_id)],
        environment=environment,
    )
    return parse_geometry_shell(output, window_id, title)


def capture_geometry(
    geometry: WindowGeometry,
    output_path: Path,
    *,
    environment: dict[str, str],
    ffmpeg: str = "ffmpeg",
) -> None:
    if geometry.x < 0 or geometry.y < 0:
        raise X11ControlError("The Stellaris window must be fully visible on screen.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_checked(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "x11grab",
            "-draw_mouse",
            "0",
            "-video_size",
            f"{geometry.width}x{geometry.height}",
            "-i",
            f"{environment['DISPLAY']}+{geometry.x},{geometry.y}",
            "-frames:v",
            "1",
            "-y",
            str(output_path),
        ],
        environment=environment,
        timeout=30,
    )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise X11ControlError("ffmpeg did not create a calibration screenshot.")


def capture_calibration(
    capture_root: Path,
    *,
    display: str,
    xauthority: str | None,
    title_regex: str,
    xdotool: str = "xdotool",
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    environment = x11_environment(display, xauthority)
    window_id = find_window(title_regex, environment=environment, xdotool=xdotool)
    geometry = get_window_geometry(window_id, environment=environment, xdotool=xdotool)
    capture_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
    screenshot = capture_root / f"{capture_id}.png"
    metadata_path = capture_root / f"{capture_id}.json"
    capture_geometry(geometry, screenshot, environment=environment, ffmpeg=ffmpeg)
    metadata = {
        "schema": "iag.calibration_capture.v1",
        "capture_id": capture_id,
        "created_at": now_iso(),
        "screenshot_path": str(screenshot.resolve()),
        "geometry": asdict(geometry),
        "display": display,
        "title_regex": title_regex,
    }
    atomic_write_json(metadata_path, metadata)
    metadata["metadata_path"] = str(metadata_path.resolve())
    return metadata


def validate_ratio(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise X11ControlError(f"{name} must be between 0 and 1.")
    return number


def import_cv2() -> Any:
    try:
        import cv2
    except ImportError as error:
        raise X11ControlError("OpenCV is required for deterministic click guards.") from error
    return cv2


def commit_calibration(
    capture_metadata_path: Path,
    profile_path: Path,
    *,
    x_ratio: float,
    y_ratio: float,
    template_size: int = 72,
    threshold: float = 0.88,
    search_margin: int = 8,
) -> dict[str, Any]:
    metadata = read_json(capture_metadata_path)
    if metadata.get("schema") != "iag.calibration_capture.v1":
        raise X11ControlError("Unsupported calibration capture schema.")
    x_ratio = validate_ratio(x_ratio, "x_ratio")
    y_ratio = validate_ratio(y_ratio, "y_ratio")
    geometry = metadata["geometry"]
    width = int(geometry["width"])
    height = int(geometry["height"])
    x = min(round(x_ratio * width), width - 1)
    y = min(round(y_ratio * height), height - 1)
    if template_size < 24 or template_size % 2:
        raise X11ControlError("template_size must be an even integer of at least 24.")
    half = template_size // 2
    if x - half < 0 or y - half < 0 or x + half > width or y + half > height:
        raise X11ControlError("The calibration point is too close to a window edge.")

    cv2 = import_cv2()
    screenshot_path = Path(metadata["screenshot_path"])
    image = cv2.imread(str(screenshot_path), cv2.IMREAD_COLOR)
    if image is None:
        raise X11ControlError("The calibration screenshot cannot be decoded.")
    template = image[y - half : y + half, x - half : x + half]
    if template.shape[:2] != (template_size, template_size):
        raise X11ControlError("The click guard template has an unexpected size.")

    template_path = profile_path.with_suffix(".guard.png")
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(template_path), template):
        raise X11ControlError("Failed to save the click guard template.")
    profile = {
        "schema": "iag.fixed_click_profile.v1",
        "created_at": now_iso(),
        "window": {
            "title_regex": metadata["title_regex"],
            "width": width,
            "height": height,
        },
        "target": {"x": x, "y": y, "button": 1},
        "guard": {
            "enabled": True,
            "template_path": str(template_path.resolve()),
            "template_size": template_size,
            "search_margin": int(search_margin),
            "threshold": float(threshold),
        },
        "source_capture": {
            "capture_id": metadata["capture_id"],
            "screenshot_path": str(screenshot_path.resolve()),
        },
    }
    validate_profile(profile)
    atomic_write_json(profile_path, profile)
    return profile


def validate_profile(profile: dict[str, Any]) -> None:
    if profile.get("schema") != "iag.fixed_click_profile.v1":
        raise X11ControlError("Unsupported fixed click profile schema.")
    window = profile.get("window")
    target = profile.get("target")
    guard = profile.get("guard")
    if not all(isinstance(item, dict) for item in (window, target, guard)):
        raise X11ControlError("The click profile is incomplete.")
    width = int(window["width"])
    height = int(window["height"])
    x = int(target["x"])
    y = int(target["y"])
    if width <= 0 or height <= 0:
        raise X11ControlError("The calibrated window dimensions are invalid.")
    if not 0 <= x < width or not 0 <= y < height:
        raise X11ControlError("The calibrated click is outside the window.")
    if int(target.get("button", 1)) not in {1, 2, 3}:
        raise X11ControlError("Only standard mouse buttons are supported.")
    if guard.get("enabled"):
        threshold = float(guard["threshold"])
        if not 0.0 < threshold <= 1.0:
            raise X11ControlError("The guard threshold must be between 0 and 1.")


def guard_score(profile: dict[str, Any], screenshot_path: Path) -> float:
    guard = profile["guard"]
    if not guard.get("enabled"):
        return 1.0
    cv2 = import_cv2()
    image = cv2.imread(str(screenshot_path), cv2.IMREAD_GRAYSCALE)
    template = cv2.imread(str(Path(guard["template_path"])), cv2.IMREAD_GRAYSCALE)
    if image is None or template is None:
        raise X11ControlError("The live screenshot or guard template cannot be decoded.")
    target = profile["target"]
    margin = int(guard.get("search_margin", 8))
    half_width = template.shape[1] // 2
    half_height = template.shape[0] // 2
    x = int(target["x"])
    y = int(target["y"])
    left = max(x - half_width - margin, 0)
    top = max(y - half_height - margin, 0)
    right = min(x + half_width + margin, image.shape[1])
    bottom = min(y + half_height + margin, image.shape[0])
    haystack = image[top:bottom, left:right]
    if haystack.shape[0] < template.shape[0] or haystack.shape[1] < template.shape[1]:
        raise X11ControlError("The live guard region is smaller than its template.")
    result = cv2.matchTemplate(haystack, template, cv2.TM_CCOEFF_NORMED)
    _minimum, maximum, _minimum_location, _maximum_location = cv2.minMaxLoc(result)
    if not math.isfinite(maximum):
        raise X11ControlError("The click guard produced a non-finite score.")
    return float(maximum)


def execute_fixed_click(
    profile_path: Path,
    *,
    display: str,
    xauthority: str | None,
    artifact_root: Path,
    move_only: bool = False,
    guard_enabled: bool = True,
    xdotool: str = "xdotool",
    ffmpeg: str = "ffmpeg",
    **_ignored: Any,
) -> dict[str, Any]:
    profile = read_json(profile_path)
    validate_profile(profile)
    environment = x11_environment(display, xauthority)
    window_id = find_window(
        str(profile["window"]["title_regex"]),
        environment=environment,
        xdotool=xdotool,
    )
    geometry = get_window_geometry(window_id, environment=environment, xdotool=xdotool)
    expected_width = int(profile["window"]["width"])
    expected_height = int(profile["window"]["height"])
    if (geometry.width, geometry.height) != (expected_width, expected_height):
        raise X11ControlError(
            "Stellaris window size changed after calibration: "
            f"expected {expected_width}x{expected_height}, "
            f"found {geometry.width}x{geometry.height}."
        )

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
    before_path = artifact_root / f"click_before_{run_id}.png"
    run_checked(
        [xdotool, "windowactivate", "--sync", str(window_id)],
        environment=environment,
    )
    run_checked(
        [xdotool, "mousemove", "--window", str(window_id), "8", "8"],
        environment=environment,
    )
    time.sleep(0.12)
    capture_geometry(geometry, before_path, environment=environment, ffmpeg=ffmpeg)
    effective_guard_enabled = bool(guard_enabled) and bool(
        profile["guard"].get("enabled", True)
    )
    guard_error: str | None = None
    try:
        score: float | None = guard_score(profile, before_path)
    except Exception as error:
        if effective_guard_enabled:
            raise
        score = None
        guard_error = str(error)
    threshold = float(profile["guard"].get("threshold", 0.0))
    if effective_guard_enabled and score is not None and score < threshold:
        raise X11ControlError(
            f"Fixed click guard rejected the current UI ({score:.3f} < {threshold:.3f})."
        )

    target = profile["target"]
    x = int(target["x"])
    y = int(target["y"])
    run_checked(
        [xdotool, "mousemove", "--window", str(window_id), str(x), str(y)],
        environment=environment,
    )
    if not move_only:
        run_checked(
            [xdotool, "click", "--window", str(window_id), str(int(target.get("button", 1)))],
            environment=environment,
        )
    result = {
        "schema": "iag.fixed_click_result.v1",
        "executed_at": now_iso(),
        "move_only": move_only,
        "window": asdict(geometry),
        "target": {"x": x, "y": y},
        "guard_enabled": effective_guard_enabled,
        "guard_bypassed": not effective_guard_enabled,
        "guard_score": round(score, 6) if score is not None else None,
        "guard_threshold": threshold,
        "guard_passed": score is not None and score >= threshold,
        "guard_error": guard_error,
        "before_screenshot": str(before_path.resolve()),
    }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture")
    capture.add_argument("--capture-root", type=Path, required=True)
    capture.add_argument("--display", required=True)
    capture.add_argument("--xauthority")
    capture.add_argument("--title-regex", default="Stellaris")

    commit = subparsers.add_parser("commit")
    commit.add_argument("--capture", type=Path, required=True)
    commit.add_argument("--profile", type=Path, required=True)
    commit.add_argument("--x-ratio", type=float, required=True)
    commit.add_argument("--y-ratio", type=float, required=True)

    click = subparsers.add_parser("click")
    click.add_argument("--profile", type=Path, required=True)
    click.add_argument("--display", required=True)
    click.add_argument("--xauthority")
    click.add_argument("--artifact-root", type=Path, required=True)
    click.add_argument("--move-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "capture":
        result = capture_calibration(
            args.capture_root,
            display=args.display,
            xauthority=args.xauthority,
            title_regex=args.title_regex,
        )
    elif args.command == "commit":
        result = commit_calibration(
            args.capture,
            args.profile,
            x_ratio=args.x_ratio,
            y_ratio=args.y_ratio,
        )
    else:
        result = execute_fixed_click(
            args.profile,
            display=args.display,
            xauthority=args.xauthority,
            artifact_root=args.artifact_root,
            move_only=args.move_only,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
