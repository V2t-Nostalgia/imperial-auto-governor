#!/usr/bin/env python3
"""Entrypoint for the local-only IAG overlay display test executable."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from apps.game_overlay.display_test_window import DisplayTestWindow
from apps.game_overlay.main import resource_root
from apps.game_overlay.overlay_config import load_display_test_configuration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    application = QApplication(sys.argv[:1])
    application.setApplicationName("Imperial Auto Governor Overlay Display Test")
    try:
        configuration = load_display_test_configuration(
            args.config,
            resource_root=resource_root(),
        )
        window = DisplayTestWindow(configuration)
        window.show()
        return int(application.exec())
    except Exception as error:  # noqa: BLE001 - GUI startup boundary.
        QMessageBox.critical(None, "IAG Overlay Display Test", str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
