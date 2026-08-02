# SPDX-FileCopyrightText: 2026 Nostalgia and Imperial Auto Governor contributors
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
import zipfile
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from host_bridge_pairing import (  # noqa: E402
    HostBridgePairingError,
    build_paired_host_bridge_archive,
    pairing_server_url,
)


class HostBridgePairingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = RUNTIME / "tests" / "runtime_test_data" / (
            "host_bridge_pairing_" + uuid.uuid4().hex
        )
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def source_archive(self) -> Path:
        archive = self.root / "public.zip"
        package_root = "IAGHostBridge-0.5.5-windows-x64"
        example = {
            "server_url": "",
            "server_certificate_sha256": "REPLACE",
            "upload_token_file": "secrets/save_upload_token",
            "save_root": "",
        }
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
            target.writestr(f"{package_root}/IAGHostBridgeGUI.exe", b"test")
            target.writestr(
                f"{package_root}/iag_save_uploader.example.json",
                json.dumps(example),
            )
        return archive

    def test_builds_archive_with_runtime_pairing_data(self) -> None:
        source = self.source_archive()
        destination = self.root / "paired.zip"
        fingerprint = "ab" * 32
        build_paired_host_bridge_archive(
            source,
            destination,
            server_url="https://192.0.2.10:8765",
            certificate_fingerprint=fingerprint,
            upload_token="unit-test-token",
        )
        package_root = "IAGHostBridge-0.5.5-windows-x64"
        with zipfile.ZipFile(destination) as archive:
            config = json.loads(
                archive.read(
                    f"{package_root}/iag_save_uploader.json"
                ).decode("utf-8")
            )
            token = archive.read(
                f"{package_root}/secrets/save_upload_token"
            ).decode("utf-8").strip()
            self.assertIsNone(archive.testzip())
        self.assertEqual(config["server_url"], "https://192.0.2.10:8765")
        self.assertEqual(config["server_certificate_sha256"], fingerprint)
        self.assertEqual(token, "unit-test-token")
        with zipfile.ZipFile(source) as archive:
            self.assertNotIn(
                f"{package_root}/iag_save_uploader.json",
                archive.namelist(),
            )

    def test_rejects_invalid_fingerprint(self) -> None:
        with self.assertRaises(HostBridgePairingError):
            build_paired_host_bridge_archive(
                self.source_archive(),
                self.root / "paired.zip",
                server_url="https://192.0.2.10:8765",
                certificate_fingerprint="short",
                upload_token="unit-test-token",
            )

    def test_validates_host_header(self) -> None:
        self.assertEqual(
            pairing_server_url("192.0.2.10:8765"),
            "https://192.0.2.10:8765",
        )
        with self.assertRaises(HostBridgePairingError):
            pairing_server_url("example.test/path")


if __name__ == "__main__":
    unittest.main()
