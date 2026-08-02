from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from save_ingest import (  # noqa: E402
    SaveIngestError,
    bearer_token_matches,
    read_campaign_manifest,
    receive_uploaded_save,
    resolve_current_save,
    review_interval_months,
)


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


def stellaris_save(date: str = "2201.04.01") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "meta",
            (
                'version="Pegasus v4.4.6"\n'
                'name="Upload Test"\n'
                f'date="{date}"\n'
                "version_control_revision=123\n"
            ),
        )
        archive.writestr("gamestate", f'date="{date}"\nplayer=0\n')
    return output.getvalue()


class SaveIngestTests(unittest.TestCase):
    def config(self, root: Path) -> dict[str, object]:
        return {
            "runtime_root": str(root),
            "save_source_mode": "host_upload",
            "uploaded_save_root": "state/uploaded_saves",
            "save_manifest_path": "state/current_save.json",
            "save_review_interval_months": 3,
            "save_upload_max_bytes": 1024 * 1024,
        }

    def headers(self, payload: bytes) -> dict[str, str]:
        return {
            "X-IAG-Filename": quote("autosave_2201.04.01.sav"),
            "X-IAG-Campaign-ID": "a" * 32,
            "X-IAG-Campaign-Label": quote("United Nations of Earth"),
            "X-IAG-Source-ID": quote("WINDOWS-HOST"),
            "X-IAG-Source-Mtime-Ns": "123456789",
            "X-IAG-SHA256": hashlib.sha256(payload).hexdigest(),
        }

    def test_receives_verified_save_and_resolves_manifest(self) -> None:
        with writable_test_directory() as root:
            config = self.config(root)
            payload = stellaris_save()

            manifest = receive_uploaded_save(
                config,
                io.BytesIO(payload),
                content_length=len(payload),
                headers=self.headers(payload),
            )

            self.assertEqual(manifest["metadata"]["date"], "2201.04.01")
            self.assertEqual(manifest["campaign_id"], "a" * 32)
            selected = resolve_current_save(config)
            self.assertTrue(selected.is_file())
            self.assertEqual(selected.read_bytes(), payload)
            current = json.loads(
                (root / "state" / "current_save.json").read_text(encoding="utf-8")
            )
            self.assertEqual(current["sha256"], hashlib.sha256(payload).hexdigest())
            campaign = read_campaign_manifest(config, "a" * 32)
            self.assertIsNotNone(campaign)
            self.assertEqual(campaign["metadata"]["date"], "2201.04.01")
            self.assertEqual(
                resolve_current_save(
                    config,
                    expected_campaign_id="a" * 32,
                ),
                selected,
            )
            with self.assertRaises(SaveIngestError):
                resolve_current_save(
                    config,
                    expected_campaign_id="b" * 32,
                )

    def test_rejects_hash_mismatch_without_publishing_manifest(self) -> None:
        with writable_test_directory() as root:
            config = self.config(root)
            payload = stellaris_save()
            headers = self.headers(payload)
            headers["X-IAG-SHA256"] = "0" * 64

            with self.assertRaises(SaveIngestError):
                receive_uploaded_save(
                    config,
                    io.BytesIO(payload),
                    content_length=len(payload),
                    headers=headers,
                )

            self.assertFalse((root / "state" / "current_save.json").exists())

    def test_manifest_cannot_escape_upload_root(self) -> None:
        with writable_test_directory() as root:
            config = self.config(root)
            outside = root / "outside.sav"
            outside.write_bytes(stellaris_save())
            manifest = root / "state" / "current_save.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "stored_path": str(outside),
                        "size": outside.stat().st_size,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(PermissionError):
                resolve_current_save(config)

    def test_upload_token_and_review_interval_validation(self) -> None:
        self.assertTrue(bearer_token_matches("Bearer secret", "secret"))
        self.assertFalse(bearer_token_matches("Basic secret", "secret"))
        self.assertEqual(
            review_interval_months({"save_review_interval_months": 12}),
            12,
        )
        with self.assertRaises(SaveIngestError):
            review_interval_months({"save_review_interval_months": 2})


if __name__ == "__main__":
    unittest.main()
