from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from iag.stellaris.game_knowledge import technology_rule


class TechnologyRuleTests(unittest.TestCase):
    def test_reads_source_backed_technology_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            technology = root / "common" / "technology" / "00_test.txt"
            technology.parent.mkdir(parents=True)
            technology.write_text(
                """
@tier1cost3 = 2500
tech_shields_2 = {
    area = physics
    cost = @tier1cost3
    tier = 1
    category = { field_manipulation }
    prerequisites = { "tech_shields_1" }
    is_rare = yes
    modifier = { army_health = 0.05 }
}
""",
                encoding="utf-8",
            )
            result = technology_rule(root, "tech_shields_2")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["area"], "physics")
        self.assertEqual(result["cost"], 2500)
        self.assertEqual(result["category"], ["field_manipulation"])
        self.assertEqual(result["prerequisites"], ["tech_shields_1"])
        self.assertTrue(result["is_rare"])
        self.assertEqual(result["modifiers"][0]["path"], "army_health")


if __name__ == "__main__":
    unittest.main()
