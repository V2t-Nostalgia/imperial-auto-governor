#!/usr/bin/env python3
"""Entrypoint for the standalone Imperial Auto Governor game overlay."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from apps.game_overlay.overlay_config import (
    OverlayConfiguration,
    OverlayConnection,
    OverlaySettings,
    bundled_font_file,
    load_overlay_configuration,
)
from apps.game_overlay.window import OverlayWindow


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--preview", action="store_true")
    return parser.parse_args()


def preview_configuration() -> OverlayConfiguration:
    root = resource_root()
    phrase_file = root / "resources" / "analysis_phrases_zh.txt"
    return OverlayConfiguration(
        config_path=Path.cwd() / ".tmp" / "overlay-preview.json",
        connection=OverlayConnection(
            "https://127.0.0.1:8765",
            "0" * 64,
            "preview",
        ),
        settings=OverlaySettings(
            analysis_phrases_file=phrase_file,
            font_file=bundled_font_file(root),
        ),
    )


def main() -> int:
    args = parse_args()
    application = QApplication(sys.argv[:1])
    application.setApplicationName("Imperial Auto Governor Overlay")
    try:
        if args.preview:
            configuration = preview_configuration()
        else:
            if args.config is None:
                raise ValueError("--config is required outside preview mode.")
            configuration = load_overlay_configuration(
                args.config,
                resource_root=resource_root(),
            )
        window = OverlayWindow(configuration, preview=args.preview)
        window.show()
        return int(application.exec())
    except Exception as error:  # noqa: BLE001 - GUI boundary must report startup failures.
        QMessageBox.critical(None, "IAG Game Overlay", str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
