#!/usr/bin/env python3
"""Load and persist the game overlay portion of the Host Bridge config."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
FUSION_PIXEL_FONT = (
    Path("resources")
    / "fonts"
    / "fusion_pixel"
    / "fusion-pixel-12px-monospaced-zh_hans.ttf"
)


@dataclass(frozen=True, slots=True)
class OverlayGeometry:
    x: int = 80
    y: int = 120
    width: int = 680
    height: int = 430


@dataclass(frozen=True, slots=True)
class OverlayConnection:
    server_url: str
    certificate_sha256: str
    access_token: str


@dataclass(frozen=True, slots=True)
class OverlaySettings:
    hotkey: str = "Ctrl+Shift+Space"
    opacity: float = 0.78
    font_size: int = 15
    analysis_phrase_interval_seconds: float = 2.8
    analysis_character_interval_ms: int = 65
    geometry: OverlayGeometry = OverlayGeometry()
    analysis_phrases_file: Path = Path("analysis_phrases_zh.txt")
    font_file: Path | None = None


@dataclass(frozen=True, slots=True)
class OverlayConfiguration:
    config_path: Path
    connection: OverlayConnection
    settings: OverlaySettings


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Host Bridge config does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Host Bridge config is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise TypeError("Host Bridge config must contain a JSON object.")
    return value


def _resolve_relative(config_path: Path, value: Any, default: Path) -> Path:
    text = str(value or "").strip()
    selected = Path(text).expanduser() if text else default
    if not selected.is_absolute():
        selected = config_path.parent / selected
    return selected.resolve()


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def ensure_analysis_phrase_file(
    config_path: Path,
    overlay: dict[str, Any],
    resource_root: Path,
) -> Path:
    default = config_path.parent / "overlay" / "analysis_phrases_zh.txt"
    destination = _resolve_relative(
        config_path,
        overlay.get("analysis_phrases_file"),
        default,
    )
    if not destination.is_file():
        source = resource_root / "resources" / "analysis_phrases_zh.txt"
        if not source.is_file():
            raise ValueError(f"Bundled analysis phrase file is missing: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return destination


def load_analysis_phrases(path: Path) -> tuple[str, ...]:
    phrases = tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    return phrases or ("正在分析...",)


def bundled_font_file(resource_root: Path) -> Path | None:
    font_file = resource_root / FUSION_PIXEL_FONT
    return font_file if font_file.is_file() else None


def _settings_from_config(
    selected_config: Path,
    value: dict[str, Any],
    resource_root: Path,
) -> OverlaySettings:
    overlay_raw = value.get("overlay")
    overlay = dict(overlay_raw) if isinstance(overlay_raw, dict) else {}
    geometry_raw = overlay.get("geometry")
    geometry_value = geometry_raw if isinstance(geometry_raw, dict) else {}
    geometry = OverlayGeometry(
        x=int(geometry_value.get("x", 80)),
        y=int(geometry_value.get("y", 120)),
        width=max(420, int(geometry_value.get("width", 680))),
        height=max(260, int(geometry_value.get("height", 430))),
    )
    phrase_path = ensure_analysis_phrase_file(
        selected_config,
        overlay,
        resource_root,
    )
    return OverlaySettings(
        hotkey=str(overlay.get("hotkey", "Ctrl+Shift+Space")).strip()
        or "Ctrl+Shift+Space",
        opacity=max(0.25, min(float(overlay.get("opacity", 0.78)), 0.98)),
        font_size=max(10, min(int(overlay.get("font_size", 15)), 28)),
        analysis_phrase_interval_seconds=max(
            0.8,
            min(
                float(overlay.get("analysis_phrase_interval_seconds", 2.8)),
                30.0,
            ),
        ),
        analysis_character_interval_ms=max(
            20,
            min(
                int(overlay.get("analysis_character_interval_ms", 65)),
                250,
            ),
        ),
        geometry=geometry,
        analysis_phrases_file=phrase_path,
        font_file=bundled_font_file(resource_root),
    )


def load_overlay_configuration(
    config_path: Path,
    *,
    resource_root: Path,
) -> OverlayConfiguration:
    selected_config = config_path.expanduser().resolve()
    value = _read_object(selected_config)
    overlay_raw = value.get("overlay")
    overlay = dict(overlay_raw) if isinstance(overlay_raw, dict) else {}

    server_url = str(value.get("server_url", "")).strip().rstrip("/")
    parsed = urlparse(server_url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("The overlay requires the paired HTTPS Agent URL.")
    fingerprint = str(value.get("server_certificate_sha256", ""))
    fingerprint = fingerprint.replace(":", "").strip().lower()
    if not FINGERPRINT_RE.fullmatch(fingerprint):
        raise ValueError("The Agent certificate SHA-256 must contain 64 hex digits.")
    token_path = _resolve_relative(
        selected_config,
        overlay.get("access_token_file"),
        selected_config.parent / "secrets" / "overlay_access_token",
    )
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as error:
        raise ValueError(
            f"The paired overlay token is missing: {token_path}"
        ) from error
    if not token:
        raise ValueError("The paired overlay token is empty.")

    settings = _settings_from_config(selected_config, value, resource_root)
    return OverlayConfiguration(
        config_path=selected_config,
        connection=OverlayConnection(server_url, fingerprint, token),
        settings=settings,
    )


def default_display_test_config_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / ".iag"
    return base / "Imperial Auto Governor" / "overlay-display-test.json"


def load_display_test_configuration(
    config_path: Path | None,
    *,
    resource_root: Path,
) -> OverlayConfiguration:
    """Load local-only display settings without any Agent connection secrets."""
    selected_config = (
        config_path.expanduser().resolve()
        if config_path is not None
        else default_display_test_config_path().resolve()
    )
    if not selected_config.is_file():
        _atomic_write_json(
            selected_config,
            {
                "display_test": True,
                "overlay": {
                    "hotkey": "Ctrl+Shift+Space",
                    "opacity": 0.78,
                    "font_size": 15,
                    "analysis_phrase_interval_seconds": 2.8,
                    "analysis_character_interval_ms": 65,
                },
            },
        )
    value = _read_object(selected_config)
    settings = _settings_from_config(selected_config, value, resource_root)
    return OverlayConfiguration(
        config_path=selected_config,
        connection=OverlayConnection("", "", ""),
        settings=settings,
    )


def update_overlay_settings(
    config_path: Path,
    settings: OverlaySettings,
) -> None:
    value = _read_object(config_path)
    overlay_raw = value.get("overlay")
    overlay = dict(overlay_raw) if isinstance(overlay_raw, dict) else {}
    try:
        phrase_value = str(
            settings.analysis_phrases_file.relative_to(config_path.parent)
        )
    except ValueError:
        phrase_value = str(settings.analysis_phrases_file)
    if "server_url" in value or "access_token_file" in overlay:
        overlay["access_token_file"] = str(
            overlay.get("access_token_file") or "secrets/overlay_access_token"
        )
    overlay.update(
        {
            "hotkey": settings.hotkey,
            "opacity": settings.opacity,
            "font_size": settings.font_size,
            "analysis_phrase_interval_seconds": (
                settings.analysis_phrase_interval_seconds
            ),
            "analysis_character_interval_ms": (
                settings.analysis_character_interval_ms
            ),
            "analysis_phrases_file": phrase_value,
            "geometry": {
                "x": settings.geometry.x,
                "y": settings.geometry.y,
                "width": settings.geometry.width,
                "height": settings.geometry.height,
            },
        }
    )
    value["overlay"] = overlay
    _atomic_write_json(config_path, value)
