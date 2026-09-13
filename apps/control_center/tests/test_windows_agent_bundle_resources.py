from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iag.core.paths import fleet_operations_root, research_strategy_root
from scripts.release.build_release import ReleaseError, windows_agent_health

ROOT = Path(__file__).resolve().parents[3]


class WindowsAgentBundleResourceTests(unittest.TestCase):
    def test_specialist_prompts_exist_and_are_declared_in_pyinstaller_spec(
        self,
    ) -> None:
        expected = (
            (
                fleet_operations_root() / "prompts" / "fleet_operator_zh.md",
                "iag/applications/fleet_operations/prompts",
            ),
            (
                research_strategy_root() / "prompts" / "research_director_zh.md",
                "iag/applications/research_strategy/prompts",
            ),
        )
        spec = (ROOT / "apps/control_center/IAGWindowsAgent.spec").read_text(
            encoding="utf-8"
        )
        for source, destination in expected:
            self.assertTrue(source.is_file(), source)
            self.assertIn(destination, spec)

    def test_release_health_rejects_agent_without_specialist_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / "IAGWindowsAgent.exe").touch()
            with self.assertRaisesRegex(
                ReleaseError,
                "fleet_operator_zh.md.*research_director_zh.md",
            ):
                windows_agent_health(stage)


if __name__ == "__main__":
    unittest.main()
