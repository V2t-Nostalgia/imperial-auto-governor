from __future__ import annotations

import sys
import unittest
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from windows_agent_gui import run_health_check


class WindowsAgentHealthTests(unittest.TestCase):
    def test_source_tree_passes_bundled_import_health_check(self) -> None:
        self.assertEqual(run_health_check(), 0)

    def test_download_link_uses_packaged_host_bridge_name(self) -> None:
        html = (RUNTIME / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/downloads/IAGHostBridge-windows-x64.zip", html)
        self.assertNotIn("hostbridge8", html.casefold())


if __name__ == "__main__":
    unittest.main()
