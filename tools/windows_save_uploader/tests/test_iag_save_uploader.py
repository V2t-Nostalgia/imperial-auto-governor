from __future__ import annotations

import json
import shutil
import socket
import threading
import sys
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


UPLOADER_ROOT = Path(__file__).resolve().parents[1]
if str(UPLOADER_ROOT) not in sys.path:
    sys.path.insert(0, str(UPLOADER_ROOT))

from iag_save_uploader import (  # noqa: E402
    PinnedHTTPSUploader,
    SaveUploader,
    StableSaveDiscovery,
    UploaderError,
    campaign_identity,
    log_event,
    parse_save_metadata,
)
from iag_save_uploader_gui import normalize_server_url  # noqa: E402


@contextmanager
def writable_test_directory():
    root = Path(__file__).resolve().parent / ".test_work"
    path = root / uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)
        try:
            root.rmdir()
        except OSError:
            pass


def write_save(path: Path, date: str = "2201.04.01") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "meta",
            f'name="Test Campaign"\ndate="{date}"\n',
        )
        archive.writestr("gamestate", f'date="{date}"\n')


class WindowsSaveUploaderTests(unittest.TestCase):
    def test_log_event_accepts_save_path_field(self) -> None:
        with writable_test_directory() as root:
            log_path = root / "uploader.jsonl"
            save_path = root / "campaign" / "autosave.sav"

            log_event(log_path, "upload_started", path=str(save_path))

            record = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(record["event"], "upload_started")
            self.assertEqual(record["path"], str(save_path))

    def test_waits_for_stable_file_before_returning_candidate(self) -> None:
        with writable_test_directory() as root:
            save_path = root / "campaign" / "autosave_2201.04.01.sav"
            write_save(save_path)
            discovery = StableSaveDiscovery(root, stability_seconds=5)

            self.assertEqual(discovery.scan(now_mono=10), [])
            self.assertEqual(discovery.scan(now_mono=14), [])
            candidates = discovery.scan(now_mono=15)

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].path, save_path.resolve())

    def test_parses_normal_stellaris_save_and_rejects_incomplete_zip(self) -> None:
        with writable_test_directory() as root:
            valid = root / "valid.sav"
            write_save(valid)
            self.assertEqual(parse_save_metadata(valid)["date"], "2201.04.01")

            invalid = root / "invalid.sav"
            with zipfile.ZipFile(invalid, "w") as archive:
                archive.writestr("meta", 'date="2201.04.01"\n')
            with self.assertRaises(UploaderError):
                parse_save_metadata(invalid)

    def test_campaign_identity_is_stable_per_host_and_directory(self) -> None:
        with writable_test_directory() as root:
            campaign = root / "UNE_-123"
            campaign.mkdir()
            with patch.object(socket, "gethostname", return_value="HOST-A"):
                first = campaign_identity(root, campaign)
                second = campaign_identity(root, campaign)
            self.assertEqual(first, second)
            self.assertEqual(len(first), 32)

    def test_requires_https_and_full_certificate_fingerprint(self) -> None:
        with self.assertRaises(UploaderError):
            PinnedHTTPSUploader(
                "http://192.0.2.10:8765",
                "0" * 64,
                "token",
                30,
            )
        with self.assertRaises(UploaderError):
            PinnedHTTPSUploader(
                "https://192.0.2.10:8765",
                "short",
                "token",
                30,
            )


    def test_normalizes_editable_llm_server_address(self) -> None:
        self.assertEqual(
            normalize_server_url("192.0.2.10"),
            "https://192.0.2.10:8765",
        )
        self.assertEqual(
            normalize_server_url("https://llm.local:9000"),
            "https://llm.local:9000",
        )

    def test_stop_event_ends_manual_uploader_without_autostart(self) -> None:
        with writable_test_directory() as root:
            saves = root / "save games"
            saves.mkdir()
            token = root / "token"
            token.write_text("test-token\n", encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "server_url": "https://192.0.2.10:8765",
                        "server_certificate_sha256": "0" * 64,
                        "upload_token_file": str(token),
                        "save_root": str(saves),
                        "state_file": str(root / "state.json"),
                        "log_file": str(root / "uploader.jsonl"),
                    }
                ),
                encoding="utf-8",
            )
            uploader = SaveUploader(config_path)
            heartbeat_states = []

            class FakeTransport:
                host = "192.0.2.10"
                port = 8765

                def heartbeat(self, payload):
                    heartbeat_states.append(payload["state"])
                    return {"source_ip": "198.51.100.20"}

            uploader.transport = FakeTransport()
            stop = threading.Event()
            stop.set()
            events = []
            result = uploader.run(stop_event=stop, status_callback=events.append)

            self.assertEqual(result, 0)
            self.assertEqual(heartbeat_states, ["stopped"])
            self.assertEqual(events[-1]["event"], "stopped")


if __name__ == "__main__":
    unittest.main()
