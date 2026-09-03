from __future__ import annotations

import unittest

from iag.stellaris.state.research_profiles import extract_research_profile


FIXTURE = r'''
date="2204.09.15"
player=
{
 { name="Vertin" country=0 }
 { name="LLM" country=0 }
}
country=
{
 0=
 {
  tech_status=
  {
   technology="tech_shields_1"
   level=1
   technology="tech_power_plant_1"
   level=1
   physics_queue=
   {
    {
     progress=753.12514
     technology="tech_power_plant_2"
     date= "2202.01.05"
    }
   }
   stored_techpoints_for_tech=
   {
    tech_hyper_drive_2=400
   }
   stored_techpoints=
   {
    0 1003.12514 929.28514
   }
   alternatives=
   {
    physics=
    {
     "tech_physics_1"
     "tech_power_plant_2"
     "tech_hyper_drive_2"
    }
    society=
    {
     "tech_planetary_unification"
    }
    engineering=
    {
     "tech_ship_armor_2"
    }
   }
   auto_researching_physics=no
   auto_researching_society=no
   auto_researching_engineering=no
   always_available_tech="tech_planetary_unification"
   always_available_tech="tech_hyper_drive_2"
  }
 }
}
'''


class ResearchProfileTests(unittest.TestCase):
    def test_separates_completed_current_and_candidates(self) -> None:
        result = extract_research_profile(FIXTURE)
        physics = result["fields"]["physics"]

        self.assertEqual(
            result["known_technologies"],
            ["tech_power_plant_1", "tech_shields_1"],
        )
        self.assertNotIn("tech_power_plant_2", result["known_technologies"])
        self.assertEqual(
            physics["current"]["technology_id"],
            "tech_power_plant_2",
        )
        self.assertEqual(physics["stored_research_points"], 0.0)
        self.assertIn("tech_hyper_drive_2", physics["always_available"])
        self.assertEqual(
            result["fields"]["society"]["always_available"],
            ["tech_planetary_unification"],
        )

    def test_area_resolver_classifies_unrolled_always_available(self) -> None:
        text = FIXTURE.replace(
            'always_available_tech="tech_hyper_drive_2"',
            'always_available_tech="tech_unrolled_engineering"',
        )
        result = extract_research_profile(
            text,
            technology_area=lambda technology_id: (
                "engineering"
                if technology_id == "tech_unrolled_engineering"
                else None
            ),
        )
        self.assertIn(
            "tech_unrolled_engineering",
            result["fields"]["engineering"]["legal_candidate_ids"],
        )


if __name__ == "__main__":
    unittest.main()
