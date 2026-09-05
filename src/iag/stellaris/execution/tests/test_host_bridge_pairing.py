#!/usr/bin/env python3
"""Tests for runtime pairing of Host Bridge and game-overlay credentials."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from iag.stellaris.execution.host_bridge_pairing import (
    build_paired_host_bridge_archive,
)


class HostBridgePairingTests(unittest.TestCase):
    def test_pairing_injects_separate_upload_and_overlay_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.zip"
            destination = root / "paired.zip"
            package_root = "IAGHostBridge-test"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr(
                    f"{package_root}/iag_save_uploader.example.json",
                    json.dumps({"server_url": "", "overlay": {}}),
                )
                archive.writestr(f"{package_root}/IAGHostBridgeGUI.exe", b"test")

            build_paired_host_bridge_archive(
                source,
                destination,
                server_url="https://192.0.2.10:8765",
                certificate_fingerprint="a" * 64,
                upload_token="upload-secret",
                overlay_access_token="overlay-secret",
            )

            with zipfile.ZipFile(destination) as archive:
                config = json.loads(
                    archive.read(f"{package_root}/iag_save_uploader.json").decode(
                        "utf-8"
                    )
                )
                upload = (
                    archive.read(f"{package_root}/secrets/save_upload_token")
                    .decode("utf-8")
                    .strip()
                )
                overlay = (
                    archive.read(f"{package_root}/secrets/overlay_access_token")
                    .decode("utf-8")
                    .strip()
                )

        self.assertEqual(upload, "upload-secret")
        self.assertEqual(overlay, "overlay-secret")
        self.assertEqual(
            config["overlay"]["access_token_file"],
            "secrets/overlay_access_token",
        )

    def test_pairing_replaces_existing_runtime_members_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.zip"
            destination = root / "paired.zip"
            package_root = "IAGHostBridge-test"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr(
                    f"{package_root}/iag_save_uploader.example.json",
                    json.dumps({"server_url": "", "overlay": {}}),
                )
                archive.writestr(
                    f"{package_root}/iag_save_uploader.json",
                    json.dumps({"server_url": "https://old.example"}),
                )
                archive.writestr(
                    f"{package_root}/secrets/save_upload_token",
                    "old-upload-secret",
                )
                archive.writestr(
                    f"{package_root}/secrets/overlay_access_token",
                    "old-overlay-secret",
                )

            build_paired_host_bridge_archive(
                source,
                destination,
                server_url="https://192.0.2.20:8765",
                certificate_fingerprint="b" * 64,
                upload_token="new-upload-secret",
                overlay_access_token="new-overlay-secret",
            )

            with zipfile.ZipFile(destination) as archive:
                names = archive.namelist()
                self.assertIsNone(archive.testzip())
                self.assertEqual(
                    names.count(f"{package_root}/iag_save_uploader.json"),
                    1,
                )
                self.assertEqual(
                    archive.read(
                        f"{package_root}/secrets/save_upload_token"
                    ).decode("utf-8").strip(),
                    "new-upload-secret",
                )


if __name__ == "__main__":
    unittest.main()
