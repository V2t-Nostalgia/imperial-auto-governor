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

    def test_download_link_uses_paired_host_bridge_name(self) -> None:
        html = (RUNTIME / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/downloads/IAGHostBridge-paired-windows-x64.zip", html)
        self.assertNotIn("hostbridge8", html.casefold())

    def test_web_research_switch_is_visible_and_wired(self) -> None:
        html = (RUNTIME / "web" / "index.html").read_text(encoding="utf-8")
        script = (RUNTIME / "web" / "app_v2.js").read_text(encoding="utf-8")
        self.assertEqual(html.count('id="web-research-enabled"'), 1)
        self.assertIn("允许灰风联网检索", html)
        self.assertIn(
            '$("web-research-enabled").checked = Boolean(model.web_research_enabled)',
            script,
        )
        self.assertIn(
            'web_research_enabled: $("web-research-enabled").checked',
            script,
        )


if __name__ == "__main__":
    unittest.main()
