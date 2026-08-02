# SPDX-FileCopyrightText: 2026 Nostalgia and Imperial Auto Governor contributors
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


RELEASE = Path(__file__).resolve().parent
if str(RELEASE) not in sys.path:
    sys.path.insert(0, str(RELEASE))

from release_common import scan_tree  # noqa: E402


class ReleasePrivacyScannerTests(unittest.TestCase):
    def test_allows_only_known_crawl4ai_container_home_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compose = root / "research_services" / "compose.yaml"
            compose.parent.mkdir(parents=True)
            compose.write_bytes(
                b"tmpfs:\n  - " + b"/home/" + b"appuser/.crawl4ai:size=128m\n"
            )
            self.assertFalse(
                any("personal Linux path" in item for item in scan_tree(root, "windows-agent"))
            )

    def test_rejects_other_home_paths_even_in_research_compose(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compose = root / "research_services" / "compose.yaml"
            compose.parent.mkdir(parents=True)
            compose.write_bytes(b"volume: " + b"/home/" + b"private-user/data\n")
            self.assertTrue(
                any("personal Linux path" in item for item in scan_tree(root, "windows-agent"))
            )

    def test_rejects_appuser_path_outside_the_pinned_compose_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = root / "docs" / "example.md"
            document.parent.mkdir(parents=True)
            document.write_bytes(b"Path: " + b"/home/" + b"appuser/.crawl4ai\n")
            self.assertTrue(
                any("personal Linux path" in item for item in scan_tree(root, "source"))
            )

    def test_rejects_lookalike_container_home_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compose = root / "research_services" / "compose.yaml"
            compose.parent.mkdir(parents=True)
            compose.write_bytes(
                b"volume: " + b"/home/" + b"appuser/.config-private/data\n"
            )
            self.assertTrue(
                any(
                    "personal Linux path" in item
                    for item in scan_tree(root, "windows-agent")
                )
            )

    def test_rejects_runtime_environment_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "compose.env").write_text("TOKEN=not-a-real-token\n", encoding="utf-8")
            self.assertTrue(
                any(
                    "forbidden runtime artifact" in item
                    for item in scan_tree(root, "source")
                )
            )


if __name__ == "__main__":
    unittest.main()
