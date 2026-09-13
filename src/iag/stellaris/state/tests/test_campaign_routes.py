from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from iag.stellaris.state.campaign_routes import CampaignRoutePlanner
from iag.stellaris.state.fleet_profiles import extract_fleet_profiles
from iag.stellaris.state.tests.test_fleet_profiles import WAR_ROUTE_FIXTURE

DETOUR_FIXTURE = (
    WAR_ROUTE_FIXTURE.replace(
        "terra_incognita={ systems={ 10 11 12 } }",
        "terra_incognita={ systems={ 10 11 12 13 14 } }",
    )
    .replace(
        "hyperlane={ { to=11 length=10 } }\n }\n 11=",
        "hyperlane={ { to=11 length=10 } { to=13 length=10 } }\n }\n 11=",
    )
    .replace(
        "hyperlane={ { to=11 length=10 } }\n }\n}",
        "hyperlane={ { to=11 length=10 } { to=14 length=10 } }\n }\n"
        ' 13=\n {\n  name={ key="DETOUR_A" }\n  planet=13\n'
        "  discovery={ 0 }\n  hyperlane=\n  {\n   { to=10 length=10 }\n"
        "   { to=14 length=10 }\n  }\n }\n"
        ' 14=\n {\n  name={ key="DETOUR_B" }\n  planet=14\n'
        "  discovery={ 0 }\n  hyperlane=\n  {\n   { to=13 length=10 }\n"
        "   { to=12 length=10 }\n  }\n }\n}",
    )
    .replace(
        '12={ planet_class="pc_g_star"',
        '13={ planet_class="pc_g_star" name={ key="DETOUR_A" } '
        "coordinate={ x=0 y=0 origin=13 } }\n"
        ' 14={ planet_class="pc_g_star" name={ key="DETOUR_B" } '
        "coordinate={ x=0 y=0 origin=14 } }\n"
        ' 12={ planet_class="pc_g_star"',
    )
)


MULTI_ROUTE_FIXTURE = (
    DETOUR_FIXTURE.replace(
        "terra_incognita={ systems={ 10 11 12 13 14 } }",
        "terra_incognita={ systems={ 10 11 12 13 14 15 16 } }",
    )
    .replace(
        "hyperlane={ { to=11 length=10 } { to=13 length=10 } }",
        "hyperlane={ { to=11 length=10 } { to=13 length=10 } { to=15 length=10 } }",
        1,
    )
    .replace(
        "hyperlane={ { to=11 length=10 } { to=14 length=10 } }",
        "hyperlane={ { to=11 length=10 } { to=14 length=10 } { to=16 length=10 } }",
        1,
    )
    .replace(
        "\n}\nplanets=\n{",
        '\n 15=\n {\n  name={ key="ALT_A" }\n  planet=15\n'
        "  discovery={ 0 }\n  hyperlane={ { to=10 length=10 } "
        "{ to=16 length=10 } }\n }\n"
        ' 16=\n {\n  name={ key="ALT_B" }\n  planet=16\n'
        "  discovery={ 0 }\n  hyperlane={ { to=15 length=10 } "
        "{ to=12 length=10 } }\n }\n}\nplanets=\n{",
        1,
    )
    .replace(
        ' 12={ planet_class="pc_g_star"',
        ' 15={ planet_class="pc_g_star" name={ key="ALT_A" } '
        "coordinate={ x=0 y=0 origin=15 } }\n"
        ' 16={ planet_class="pc_g_star" name={ key="ALT_B" } '
        "coordinate={ x=0 y=0 origin=16 } }\n"
        ' 12={ planet_class="pc_g_star"',
        1,
    )
)


class CampaignRoutePlannerTests(unittest.TestCase):
    def test_shared_planner_serves_parallel_route_reads(self) -> None:
        fleet_profile = extract_fleet_profiles(DETOUR_FIXTURE)
        planner = CampaignRoutePlanner(
            DETOUR_FIXTURE,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
        )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(
                    lambda _value: planner.plan(
                        fleet_id=1,
                        target_system_id=12,
                    ),
                    range(8),
                )
            )

        expected = results[0]["route_options"]
        self.assertTrue(all(result["route_options"] == expected for result in results))
        self.assertIn(11, planner._blocker_cache)

    def test_compares_safe_detour_with_shorter_breakthrough(self) -> None:
        fleet_profile = extract_fleet_profiles(DETOUR_FIXTURE)
        planner = CampaignRoutePlanner(
            DETOUR_FIXTURE,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
        )
        result = planner.plan(fleet_id=1, target_system_id=12)

        by_type = {item["route_type"]: item for item in result["route_options"]}
        self.assertEqual(by_type["safe_detour"]["path_system_ids"], [10, 13, 14, 12])
        assault = by_type["shortest_assault"]
        self.assertEqual(assault["path_system_ids"], [10, 11, 12])
        self.assertEqual(assault["breakthrough_count"], 1)
        blocker = assault["blockers"][0]
        self.assertEqual(blocker["system_id"], 11)
        self.assertEqual(blocker["starbase_sources"][0]["fleet_id"], 90)
        self.assertEqual(
            blocker["starbase_sources"][0]["inhibitor_source_authority"],
            "system_ftl_inhibitor_presence_exact_object",
        )

    def test_plan_rejects_a_system_without_war_objectives(self) -> None:
        fleet_profile = extract_fleet_profiles(DETOUR_FIXTURE)
        planner = CampaignRoutePlanner(
            DETOUR_FIXTURE,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
        )
        with self.assertRaisesRegex(ValueError, "no save-backed active-war objective"):
            planner.plan(fleet_id=1, target_system_id=14)

    def test_route_summarizes_planetary_inhibitor_ground_cost(self) -> None:
        fleet_profile = extract_fleet_profiles(DETOUR_FIXTURE)
        planner = CampaignRoutePlanner(
            DETOUR_FIXTURE,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [
                    {
                        "planet_id": 70,
                        "system_id": 11,
                        "bombardment_damage": 20.0,
                        "defending_armies": {
                            "army_count": 2,
                            "current_health_total": 175.0,
                        },
                        "planetary_ftl_inhibitor_sources": [
                            {"building_type": "building_fortress"}
                        ],
                    }
                ],
            },
        )

        result = planner.plan(fleet_id=1, target_system_id=12)
        assault = next(
            item
            for item in result["route_options"]
            if item["route_type"] == "shortest_assault"
        )

        self.assertEqual(assault["planetary_inhibitor_count"], 1)
        self.assertEqual(assault["known_planet_defender_army_count"], 2)
        self.assertEqual(assault["known_planet_defender_health"], 175.0)
        self.assertEqual(assault["current_bombardment_damage_total"], 20.0)

    def test_multiobjective_search_keeps_equal_cost_distinct_corridors(self) -> None:
        fleet_profile = extract_fleet_profiles(MULTI_ROUTE_FIXTURE)
        planner = CampaignRoutePlanner(
            MULTI_ROUTE_FIXTURE,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
        )

        result = planner.plan(fleet_id=1, target_system_id=12)
        paths = {tuple(item["path_system_ids"]) for item in result["route_options"]}

        self.assertIn((10, 13, 14, 12), paths)
        self.assertIn((10, 15, 16, 12), paths)
        self.assertEqual(
            result["route_search"]["algorithm"],
            "bounded_multiobjective_label_search",
        )
        self.assertGreaterEqual(result["raw_candidate_count"], 3)

    def test_exploration_pressure_prefers_an_unused_equal_cost_corridor(self) -> None:
        fleet_profile = extract_fleet_profiles(MULTI_ROUTE_FIXTURE)
        planner = CampaignRoutePlanner(
            MULTI_ROUTE_FIXTURE,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
            deployment_commitments=[
                {
                    "campaign_plan_id": "existing",
                    "fleet_id": 2,
                    "target_system_id": 12,
                    "path_system_ids": [10, 13, 14, 12],
                    "status": "active",
                }
            ],
        )

        result = planner.plan(fleet_id=1, target_system_id=12)
        unused = next(
            item
            for item in result["route_options"]
            if item["path_system_ids"] == [10, 15, 16, 12]
        )

        self.assertEqual(unused["corridor_visit_count"], 0.0)
        self.assertIn("unvisited_corridor", unused["strategy_tags"])
        self.assertEqual(unused["recommendation_rank"], 1)
        self.assertTrue(unused["recommended"])
        self.assertNotIn(
            (10, 13, 14, 12),
            {tuple(item["path_system_ids"]) for item in result["route_options"]},
        )
        self.assertEqual(result["deployment_context"]["target_assigned_fleet_ids"], [2])

    def test_pareto_search_avoids_a_non_inhibitor_enemy_concentration(self) -> None:
        fleet_profile = extract_fleet_profiles(MULTI_ROUTE_FIXTURE)
        fleet_profile["hostile_targets"].append(
            {
                "fleet_id": 93,
                "owner_country_id": 2,
                "ship_class": "shipclass_military",
                "military_power": 1000.0,
                "system_id": 13,
            }
        )
        planner = CampaignRoutePlanner(
            MULTI_ROUTE_FIXTURE,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
        )

        result = planner.plan(fleet_id=1, target_system_id=12)
        paths = {tuple(item["path_system_ids"]) for item in result["route_options"]}

        self.assertIn((10, 15, 16, 12), paths)
        self.assertNotIn((10, 13, 14, 12), paths)
        self.assertTrue(result["route_options"][0]["execution_recommended"])

    def test_force_gate_precedes_corridor_exploration(self) -> None:
        fixture = MULTI_ROUTE_FIXTURE.replace(
            "  military_power=500\n",
            "  military_power=50\n",
            1,
        )
        fleet_profile = extract_fleet_profiles(fixture)
        planner = CampaignRoutePlanner(
            fixture,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
        )

        understrength = planner.plan(fleet_id=1, target_system_id=12)

        self.assertEqual(understrength["status"], "reinforcement_required")
        self.assertFalse(understrength["route_options"][0]["execution_recommended"])
        self.assertGreater(
            understrength["recommended_force_assessment"][
                "maximum_additional_military_power_required"
            ],
            0,
        )

        support = dict(fleet_profile["fleets"][0])
        support.update({"fleet_id": 2, "military_power": 250.0})
        fleet_profile["fleets"].append(support)
        coordinated = CampaignRoutePlanner(
            fixture,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
            deployment_commitments=[
                {
                    "fleet_id": 2,
                    "target_system_id": 12,
                    "path_system_ids": [10, 15, 16, 12],
                    "status": "active",
                }
            ],
        ).plan(fleet_id=1, target_system_id=12)

        self.assertEqual(coordinated["status"], "routes_available")
        self.assertTrue(coordinated["route_options"][0]["execution_recommended"])
        target_engagement = next(
            item
            for item in coordinated["recommended_force_assessment"]["engagements"]
            if item["system_id"] == 12
        )
        self.assertEqual(target_engagement["supporting_fleet_ids"], [2])
        self.assertGreaterEqual(target_engagement["known_force_ratio"], 1.2)

        deployment = CampaignRoutePlanner(
            fixture,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
        ).deployment_summary(authorized_fleet_ids={1, 2})
        target_front = next(
            item for item in deployment["fronts"] if item["system_id"] == 12
        )
        self.assertEqual(
            target_front["reinforcement_package"]["fleet_ids"],
            [2],
        )
        self.assertTrue(
            target_front["reinforcement_package"]["closes_known_power_shortfall"]
        )

    def test_inferred_movement_route_is_not_hard_support_at_intermediate_blocker(
        self,
    ) -> None:
        fixture = MULTI_ROUTE_FIXTURE.replace(
            "  military_power=500\n",
            "  military_power=50\n",
            1,
        )
        fleet_profile = extract_fleet_profiles(fixture)
        support = dict(fleet_profile["fleets"][0])
        support.update(
            {
                "fleet_id": 2,
                "military_power": 1000.0,
                "movement": {
                    "current_system_id": 10,
                    "target_system_id": 12,
                },
            }
        )
        fleet_profile["fleets"].append(support)
        result = CampaignRoutePlanner(
            fixture,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
        ).plan(fleet_id=1, target_system_id=12)
        direct = next(
            item
            for item in result["route_options"]
            if item["path_system_ids"] == [10, 11, 12]
        )
        blocker = next(
            item
            for item in direct["force_assessment"]["engagements"]
            if item["system_id"] == 11
        )
        target = next(
            item
            for item in direct["force_assessment"]["engagements"]
            if item["system_id"] == 12
        )

        self.assertEqual(blocker["supporting_fleet_ids"], [])
        self.assertEqual(blocker["status"], "opposition_power_unknown")
        self.assertEqual(target["supporting_fleet_ids"], [2])
        self.assertEqual(target["status"], "adequate_known_force")

    def test_player_occupied_hostile_colony_becomes_a_garrison_candidate(self) -> None:
        fixture = MULTI_ROUTE_FIXTURE.replace(
            'name={ key="DETOUR_A" }\n  planet=13',
            'name={ key="DETOUR_A" }\n  planet=13\n  planet=70',
            1,
        ).replace(
            "planets=\n{",
            'planets=\n{\n 70={ planet_class="pc_continental" '
            'name={ key="OCCUPIED" } coordinate={ x=0 y=0 origin=13 } '
            "owner=2 controller=0 colony=5 }",
            1,
        )
        fleet_profile = extract_fleet_profiles(fixture)
        screen = dict(fleet_profile["fleets"][0])
        screen.update({"fleet_id": 2, "military_power": 50.0})
        fleet_profile["fleets"].append(screen)
        planner = CampaignRoutePlanner(
            fixture,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
        )

        summary = planner.deployment_summary()
        garrison = next(
            item for item in summary["garrison_candidates"] if item["system_id"] == 13
        )

        self.assertIn("player_occupied_hostile_colony", garrison["reasons"])
        self.assertEqual(garrison["coverage_state"], "uncovered")
        assignment = next(
            item
            for item in summary["deployment_portfolio"]["garrison_assignments"]
            if item["target_system_id"] == 13
        )
        self.assertEqual(assignment["fleet_id"], 2)

    def test_concentrated_front_receives_multiple_unique_fleets_until_safe(
        self,
    ) -> None:
        fixture = (
            MULTI_ROUTE_FIXTURE.replace(
                "  military_power=500\n",
                "  military_power=300\n",
                1,
            )
            .replace("military_power=100", "military_power=0")
            .replace("military_power=200", "military_power=600")
        )
        fleet_profile = extract_fleet_profiles(fixture)
        for fleet_id in (2, 3):
            support = dict(fleet_profile["fleets"][0])
            support.update({"fleet_id": fleet_id, "military_power": 300.0})
            fleet_profile["fleets"].append(support)
        summary = CampaignRoutePlanner(
            fixture,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
        ).deployment_summary(authorized_fleet_ids={1, 2, 3})

        target_front = next(
            item for item in summary["fronts"] if item["system_id"] == 12
        )
        assigned = target_front["reinforcement_package"]["fleet_ids"]
        self.assertEqual(set(assigned), {1, 2, 3})
        self.assertTrue(
            target_front["reinforcement_package"]["closes_known_power_shortfall"]
        )
        portfolio_fleet_ids = [
            item["fleet_id"]
            for item in summary["deployment_portfolio"]["assault_assignments"]
        ]
        self.assertEqual(len(portfolio_fleet_ids), len(set(portfolio_fleet_ids)))
        packages = summary["deployment_portfolio"]["task_force_packages"]
        self.assertEqual(len(packages), 1)
        self.assertEqual(set(packages[0]["reinforcement_fleet_ids"]), {1, 2, 3})
        self.assertTrue(packages[0]["hard_gate_satisfied"])
        self.assertTrue(
            all(
                assignment["requires_coordinated_dispatch"]
                for assignment in summary["deployment_portfolio"]["assault_assignments"]
            )
        )

    def test_congestion_never_splits_an_understrength_force_across_fronts(
        self,
    ) -> None:
        fixture = (
            MULTI_ROUTE_FIXTURE.replace(
                "  military_power=500\n",
                "  military_power=300\n",
                1,
            )
            .replace("military_power=100", "military_power=0")
            .replace("military_power=200", "military_power=600")
        )
        fleet_profile = extract_fleet_profiles(fixture)
        support = dict(fleet_profile["fleets"][0])
        support.update({"fleet_id": 2, "military_power": 300.0})
        fleet_profile["fleets"].append(support)
        summary = CampaignRoutePlanner(
            fixture,
            fleet_profile=fleet_profile,
            invasion_profile={
                "hostile_colonies": [],
                "blocked_hostile_colonies": [],
            },
            exploration_weight=2.0,
            congestion_weight=4.0,
        ).deployment_summary(authorized_fleet_ids={1, 2})

        portfolio = summary["deployment_portfolio"]
        self.assertEqual(portfolio["assault_assignments"], [])
        self.assertEqual(portfolio["task_force_packages"], [])
        target_front = next(
            item for item in summary["fronts"] if item["system_id"] == 12
        )
        self.assertFalse(target_front["reinforcement_package"]["dispatch_ready"])
        self.assertGreater(
            target_front["reinforcement_package"]["remaining_known_power_shortfall"],
            0,
        )

    def test_resume_route_prefers_the_previous_corridor_over_type_only(self) -> None:
        profile = {
            "source_system": {"system_id": 13},
            "route_options": [
                {
                    "route_id": "other",
                    "route_type": "safe_detour",
                    "path_system_ids": [13, 15, 12],
                    "strategic_score": 0.0,
                    "recommendation_rank": 1,
                },
                {
                    "route_id": "continued",
                    "route_type": "safe_detour",
                    "path_system_ids": [13, 14, 12],
                    "strategic_score": 1.0,
                    "recommendation_rank": 2,
                },
            ],
        }

        selected = CampaignRoutePlanner.resume_route(
            profile,
            previous_route_id="old-id",
            previous_path_system_ids=[10, 13, 14, 12],
            previous_route_type="safe_detour",
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected["route_id"], "continued")


if __name__ == "__main__":
    unittest.main()
