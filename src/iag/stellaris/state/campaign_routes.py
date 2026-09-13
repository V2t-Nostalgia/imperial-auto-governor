"""Save-backed strategic routes that may include deliberate breakthroughs."""

from __future__ import annotations

import hashlib
import heapq
import math
import threading
from collections import Counter, deque
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

from iag.stellaris.state.fleet_profiles import (
    INVALID_OBJECT_ID,
    active_war_opponents,
    country_relation_profiles,
    extract_fleet_profiles,
    fleet_owner_map,
    hyperlane_neighbors,
    integer_scalar,
    integer_values,
    known_system_ids,
    movement_profile,
    name_hint,
    name_key,
    optional_section,
    parse_numeric_map,
    planet_map,
    player_countries,
    repeated_integer,
)
from iag.stellaris.state.invasion_profiles import extract_invasion_profiles
from iag.stellaris.state.planet_profiles import find_braced_section

if TYPE_CHECKING:
    from iag.stellaris.state.state_index import WorldStateIndex

SEARCH_OBJECTIVES = (
    "hyperlane_jumps",
    "breakthrough_count",
    "known_blocker_starbase_power",
    "planetary_inhibitor_count",
    "known_planet_defender_health",
    "unknown_blocker_risk_count",
    "known_path_hostile_military_power",
    "peak_known_hostile_military_power",
    "unknown_hostile_power_object_count",
    "corridor_visit_count",
    "peak_edge_visit_count",
)

ROUTE_OBJECTIVES = (
    *SEARCH_OBJECTIVES[:-2],
    "space_force_shortfall",
    *SEARCH_OBJECTIVES[-2:],
)


@dataclass(frozen=True)
class _RouteLabel:
    system_id: int
    path: tuple[int, ...]
    hyperlane_jumps: int
    breakthrough_count: int
    known_blocker_starbase_power: float
    planetary_inhibitor_count: int
    known_planet_defender_health: float
    unknown_blocker_risk_count: int
    known_path_hostile_military_power: float
    peak_known_hostile_military_power: float
    unknown_hostile_power_object_count: int
    corridor_visit_count: float
    peak_edge_visit_count: float

    def metrics(self) -> tuple[float, ...]:
        return (
            float(self.hyperlane_jumps),
            float(self.breakthrough_count),
            self.known_blocker_starbase_power,
            float(self.planetary_inhibitor_count),
            self.known_planet_defender_health,
            float(self.unknown_blocker_risk_count),
            self.known_path_hostile_military_power,
            self.peak_known_hostile_military_power,
            float(self.unknown_hostile_power_object_count),
            self.corridor_visit_count,
            self.peak_edge_visit_count,
        )


def _dominates(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return all(a <= b for a, b in zip(left, right)) and any(
        a < b for a, b in zip(left, right)
    )


def _edge_key(left: int, right: int) -> tuple[int, int]:
    return (left, right) if left <= right else (right, left)


def _path(
    parents: dict[int, int | None],
    target: int,
) -> list[int]:
    if target not in parents:
        return []
    output: list[int] = []
    current: int | None = target
    while current is not None:
        output.append(current)
        current = parents[current]
    return list(reversed(output))


def _bfs_path(
    graph: dict[int, list[int]],
    source: int,
    target: int,
    *,
    allowed: set[int],
    terminal: set[int] | None = None,
) -> list[int]:
    parents: dict[int, int | None] = {source: None}
    queue = deque([source])
    terminal = terminal or set()
    while queue:
        current = queue.popleft()
        if current == target:
            return _path(parents, target)
        if current in terminal:
            continue
        for neighbor in graph.get(current, []):
            if neighbor not in allowed or neighbor in parents:
                continue
            parents[neighbor] = current
            queue.append(neighbor)
    return []


def _least_breakthrough_path(
    graph: dict[int, list[int]],
    source: int,
    target: int,
    *,
    allowed: set[int],
    inhibitors: set[int],
) -> list[int]:
    best: dict[int, tuple[int, int]] = {source: (0, 0)}
    parents: dict[int, int | None] = {source: None}
    queue: list[tuple[int, int, int]] = [(0, 0, source)]
    while queue:
        blockers, hops, current = heapq.heappop(queue)
        if best.get(current) != (blockers, hops):
            continue
        if current == target:
            return _path(parents, target)
        for neighbor in graph.get(current, []):
            if neighbor not in allowed:
                continue
            next_cost = (
                blockers + int(neighbor in inhibitors and neighbor != target),
                hops + 1,
            )
            if next_cost >= best.get(neighbor, (10**9, 10**9)):
                continue
            best[neighbor] = next_cost
            parents[neighbor] = current
            heapq.heappush(queue, (*next_cost, neighbor))
    return []


def _bounded_labels(
    labels: list[_RouteLabel],
    maximum: int,
) -> list[_RouteLabel]:
    """Keep objective extremes plus the best normalized compromises."""
    if len(labels) <= maximum:
        return labels
    metrics = [label.metrics() for label in labels]
    objective_count = len(SEARCH_OBJECTIVES)
    minima = [
        min(values[index] for values in metrics) for index in range(objective_count)
    ]
    maxima = [
        max(values[index] for values in metrics) for index in range(objective_count)
    ]

    def normalized_score(index: int) -> tuple[float, tuple[int, ...]]:
        total = 0.0
        for metric_index, value in enumerate(metrics[index]):
            span = maxima[metric_index] - minima[metric_index]
            total += 0.0 if span <= 0 else (value - minima[metric_index]) / span
        return total, labels[index].path

    selected: list[int] = []
    for metric_index in range(objective_count):
        best = min(
            range(len(labels)),
            key=lambda index: (
                metrics[index][metric_index],
                normalized_score(index),
            ),
        )
        if best not in selected:
            selected.append(best)
        if len(selected) == maximum:
            break
    if len(selected) < maximum:
        for index in sorted(range(len(labels)), key=normalized_score):
            if index not in selected:
                selected.append(index)
            if len(selected) == maximum:
                break
    return [labels[index] for index in selected]


class CampaignRoutePlanner:
    """Compare legal detours with routes that intentionally clear inhibitors."""

    def __init__(
        self,
        text: str,
        *,
        owner: int | None = None,
        game_root: Path | None = None,
        fleet_profile: dict[str, Any] | None = None,
        invasion_profile: dict[str, Any] | None = None,
        maximum_recruitment_count: int = 5,
        deployment_commitments: list[dict[str, Any]] | None = None,
        maximum_route_options: int = 8,
        maximum_labels_per_system: int = 16,
        maximum_search_expansions: int = 25000,
        detour_hop_budget: int = 8,
        exploration_weight: float = 0.35,
        congestion_weight: float = 1.0,
        minimum_space_force_ratio: float = 1.20,
        state_index: WorldStateIndex | None = None,
    ) -> None:
        players = player_countries(text, state_index=state_index)
        if owner is None:
            if len(players) != 1:
                raise ValueError(
                    "Could not infer one player country; pass owner explicitly."
                )
            owner = players[0]
        self.text = text
        self.owner = owner
        self.game_root = game_root
        self.countries = (
            state_index.numeric_map("country")
            if state_index is not None
            else parse_numeric_map(find_braced_section(text, "country").strip())
        )
        self.country = self.countries.get(owner)
        if not self.country:
            raise ValueError(f"Country {owner} does not exist in this save.")
        self.fleets_raw = (
            state_index.numeric_map("fleet")
            if state_index is not None
            else parse_numeric_map(find_braced_section(text, "fleet").strip())
        )
        self.systems = (
            state_index.numeric_map("galactic_object")
            if state_index is not None
            else parse_numeric_map(
                find_braced_section(text, "galactic_object").strip()
            )
        )
        self.planets = planet_map(text, state_index=state_index)
        self.planet_systems = {
            int(planet_id): int(system_id)
            for system_id, block in self.systems.items()
            for planet_id in repeated_integer(block or "", "planet")
        }
        self.relations = country_relation_profiles(self.country)
        self.opponents, self.active_war_ids = active_war_opponents(
            text,
            owner,
            state_index=state_index,
        )
        self.known_systems = known_system_ids(
            self.country,
            owner=owner,
            systems=self.systems,
        )
        self.fleet_profile = fleet_profile or extract_fleet_profiles(
            text,
            owner=owner,
            state_index=state_index,
        )
        self.invasion_profile = invasion_profile or extract_invasion_profiles(
            text,
            owner=owner,
            game_root=game_root,
            maximum_recruitment_count=maximum_recruitment_count,
            fleet_profile=self.fleet_profile,
            state_index=state_index,
        )
        self.graph = {
            system_id: hyperlane_neighbors(block or "")
            for system_id, block in self.systems.items()
        }
        self.fleet_owners = fleet_owner_map(self.countries)
        self.controllers = self._system_controllers()
        self.hostile_country_ids = set(self.opponents)
        self.hostile_country_ids.update(
            country_id
            for country_id, relation in self.relations.items()
            if relation.get("hostile", False) and relation.get("truce_id") is None
        )
        self.inhibitor_systems = {
            system_id
            for system_id, block in self.systems.items()
            if block
            and self.hostile_country_ids.intersection(
                integer_values(block, "inhibitor_owners")
            )
        }
        restricted = set(integer_values(self.country, "restricted_systems"))
        self.allowed_systems = {
            system_id
            for system_id in self.known_systems
            if system_id not in restricted
            and all(
                self._country_accessible(controller)
                for controller in self.controllers.get(system_id, set())
            )
        }
        self.hostile_targets = [
            *self.fleet_profile.get("hostile_targets", []),
            *self.fleet_profile.get("blocked_hostile_targets", []),
        ]
        self.hostile_colonies = [
            *self.invasion_profile.get("hostile_colonies", []),
            *self.invasion_profile.get("blocked_hostile_colonies", []),
        ]
        self.deployment_commitments = [
            dict(item)
            for item in (deployment_commitments or [])
            if isinstance(item, dict)
        ]
        self.maximum_route_options = max(2, min(int(maximum_route_options), 16))
        self.maximum_labels_per_system = max(
            self.maximum_route_options,
            min(int(maximum_labels_per_system), 32),
        )
        self.maximum_search_expansions = max(
            1000,
            min(int(maximum_search_expansions), 100000),
        )
        self.detour_hop_budget = max(2, min(int(detour_hop_budget), 24))
        self.exploration_weight = max(0.0, min(float(exploration_weight), 2.0))
        self.congestion_weight = max(0.0, min(float(congestion_weight), 4.0))
        self.minimum_space_force_ratio = max(
            1.0,
            min(float(minimum_space_force_ratio), 5.0),
        )
        self._cache_lock = threading.RLock()
        self._blocker_cache: dict[int, dict[str, Any]] = {}
        self._opposition_cache: dict[int, dict[str, Any]] = {}

    def _system_controllers(self) -> dict[int, set[int]]:
        controllers: dict[int, set[int]] = {}
        for fleet_id, block in self.fleets_raw.items():
            if not block or "ship_class=shipclass_starbase" not in block:
                continue
            owner = self.fleet_owners.get(fleet_id)
            system_id = movement_profile(block).get("current_system_id")
            if owner is not None and system_id is not None:
                controllers.setdefault(int(system_id), set()).add(owner)
        return controllers

    def _country_accessible(self, country_id: int) -> bool:
        if country_id == self.owner or country_id in self.opponents:
            return True
        relation = self.relations.get(country_id)
        if relation is None:
            return False
        if relation.get("truce_id") is not None or relation.get(
            "forced_open_borders", False
        ):
            return True
        if relation.get("hostile", False) or relation.get("closed_borders", False):
            return False
        return bool(relation.get("contact") or relation.get("communications"))

    def _system_label(self, system_id: int) -> dict[str, Any]:
        block = self.systems.get(system_id) or ""
        return {
            "system_id": system_id,
            "name_key": name_key(block),
            "display_name_hint": name_hint(block),
        }

    def _objectives(self, system_id: int) -> dict[str, Any]:
        targets = [
            dict(target)
            for target in self.hostile_targets
            if int(target.get("system_id", -1)) == system_id
        ]
        colonies = [
            dict(target)
            for target in self.hostile_colonies
            if int(target.get("system_id", -1)) == system_id
        ]
        return {
            "hostile_starbases": [
                target
                for target in targets
                if target.get("ship_class") == "shipclass_starbase"
            ],
            "hostile_mobile_fleets": [
                target
                for target in targets
                if target.get("ship_class") != "shipclass_starbase"
            ],
            "hostile_colonies": colonies,
        }

    def _blocker(self, system_id: int) -> dict[str, Any]:
        with self._cache_lock:
            cached = self._blocker_cache.get(system_id)
        if cached is not None:
            return cached
        system_block = self.systems.get(system_id) or ""
        inhibitor_presence = set(integer_values(system_block, "ftl_inhibitor_presence"))
        objectives = self._objectives(system_id)
        starbases: list[dict[str, Any]] = []
        for target in objectives["hostile_starbases"]:
            fleet_id = int(target["fleet_id"])
            starbases.append(
                {
                    **target,
                    "inhibitor_source_authority": (
                        "system_ftl_inhibitor_presence_exact_object"
                        if fleet_id in inhibitor_presence
                        else "hostile_starbase_in_confirmed_inhibitor_system"
                    ),
                }
            )
        planets = [
            colony
            for colony in objectives["hostile_colonies"]
            if colony.get("planetary_ftl_inhibitor_sources")
        ]
        known_power = sum(
            float(target.get("military_power") or 0.0) for target in starbases
        )
        unknown_power_objects = sum(
            target.get("military_power") is None for target in starbases
        )
        unknown_source = not starbases and not planets
        result = {
            **self._system_label(system_id),
            "inhibitor_owner_country_ids": sorted(
                self.hostile_country_ids.intersection(
                    integer_values(system_block, "inhibitor_owners")
                )
            ),
            "starbase_sources": starbases,
            "planetary_sources": planets,
            "unknown_source": unknown_source,
            "known_starbase_power": known_power,
            "unknown_power_object_count": unknown_power_objects,
            "clearance_options": [
                option
                for option, available in (
                    ("attack_starbase", bool(starbases)),
                    ("orbital_bombardment", bool(planets)),
                    ("land_armies", bool(planets)),
                    ("manual_review", unknown_source),
                )
                if available
            ],
        }
        with self._cache_lock:
            return self._blocker_cache.setdefault(system_id, result)

    def _occupied_hostile_colonies(self) -> dict[int, list[dict[str, Any]]]:
        occupied: dict[int, list[dict[str, Any]]] = {}
        for planet_id, block in self.planets.items():
            if not block:
                continue
            planet_owner = integer_scalar(block, "owner")
            controller = integer_scalar(block, "controller")
            if (
                planet_owner not in self.opponents
                or controller != self.owner
                or controller == INVALID_OBJECT_ID
            ):
                continue
            coordinate = optional_section(block, "coordinate")
            system_id = integer_scalar(coordinate, "origin")
            if system_id in (None, INVALID_OBJECT_ID):
                system_id = self.planet_systems.get(int(planet_id))
            if system_id in (None, INVALID_OBJECT_ID):
                continue
            occupied.setdefault(int(system_id), []).append(
                {
                    "planet_id": int(planet_id),
                    "planet_name_key": name_key(block),
                    "planet_display_name_hint": name_hint(block),
                    "owner_country_id": int(planet_owner),
                    "controller_country_id": self.owner,
                }
            )
        return occupied

    @staticmethod
    def _add_path_pressure(
        pressure: dict[str, Any],
        *,
        fleet_id: int,
        path: list[int],
        target_system_id: int | None,
        power: float,
        weight: float,
        active: bool,
        authority: str,
    ) -> None:
        if not path:
            return
        for left, right in pairwise(path):
            edge = _edge_key(left, right)
            pressure["edge_visits"][edge] += weight
            pressure["edge_assigned_power"][edge] += power * weight
        for system_id in path[1:]:
            pressure["system_visits"][int(system_id)] += weight
            if active:
                pressure["active_system_assigned_power"][int(system_id)] += power
                pressure["active_system_fleet_ids"].setdefault(
                    int(system_id), set()
                ).add(fleet_id)
        if active and target_system_id is not None:
            pressure["target_visits"][int(target_system_id)] += weight
            pressure["target_assigned_power"][int(target_system_id)] += power * weight
            pressure["target_fleet_ids"].setdefault(int(target_system_id), set()).add(
                fleet_id
            )
        pressure["assignments"].append(
            {
                "fleet_id": fleet_id,
                "target_system_id": target_system_id,
                "path_system_ids": list(path),
                "weight": weight,
                "active": active,
                "authority": authority,
            }
        )

    def _deployment_pressure(self, excluded_fleet_id: int | None) -> dict[str, Any]:
        pressure: dict[str, Any] = {
            "edge_visits": Counter(),
            "edge_assigned_power": Counter(),
            "system_visits": Counter(),
            "active_system_assigned_power": Counter(),
            "active_system_fleet_ids": {},
            "target_visits": Counter(),
            "target_assigned_power": Counter(),
            "target_fleet_ids": {},
            "stationed_fleet_ids": {},
            "stationed_power": Counter(),
            "assignments": [],
        }
        active_commitment_fleets: set[int] = set()
        for commitment in self.deployment_commitments:
            try:
                fleet_id = int(commitment["fleet_id"])
                path = [int(value) for value in commitment["path_system_ids"]]
            except (KeyError, TypeError, ValueError):
                continue
            if fleet_id == excluded_fleet_id or len(path) < 1:
                continue
            status = str(commitment.get("status") or "active")
            if status in {"cancelled", "failed"}:
                continue
            active = status not in {"completed", "superseded"}
            weight = 1.0 if active else 0.25
            fleet = next(
                (
                    item
                    for item in self.fleet_profile.get("fleets", [])
                    if int(item["fleet_id"]) == fleet_id
                ),
                None,
            )
            power = float((fleet or {}).get("military_power") or 0.0)
            target = commitment.get("target_system_id")
            self._add_path_pressure(
                pressure,
                fleet_id=fleet_id,
                path=path,
                target_system_id=int(target) if target is not None else path[-1],
                power=power,
                weight=weight,
                active=active,
                authority=(
                    "active_campaign_commitment"
                    if active
                    else "completed_campaign_exploration_memory"
                ),
            )
            if active:
                active_commitment_fleets.add(fleet_id)

        for fleet in self.fleet_profile.get("fleets", []):
            if not fleet.get("attack_verified_family", False):
                continue
            fleet_id = int(fleet["fleet_id"])
            if fleet_id == excluded_fleet_id:
                continue
            movement = fleet.get("movement", {})
            current = movement.get("current_system_id")
            target = movement.get("target_system_id")
            if current is None:
                continue
            current = int(current)
            power = float(fleet.get("military_power") or 0.0)
            if target is None or int(target) == current:
                pressure["stationed_fleet_ids"].setdefault(current, []).append(fleet_id)
                pressure["stationed_power"][current] += power
            if (
                fleet_id in active_commitment_fleets
                or target is None
                or int(target) == current
            ):
                continue
            target = int(target)
            allowed = set(self.allowed_systems)
            allowed.update({current, target})
            path = _bfs_path(self.graph, current, target, allowed=allowed)
            if not path:
                continue
            self._add_path_pressure(
                pressure,
                fleet_id=fleet_id,
                path=path,
                target_system_id=target,
                power=power,
                weight=1.0,
                active=True,
                authority="save_movement_target_shortest_path_inference",
            )
        return pressure

    def _garrison_candidates(self, pressure: dict[str, Any]) -> list[dict[str, Any]]:
        objective_systems = {
            int(target["system_id"])
            for target in [*self.hostile_targets, *self.hostile_colonies]
            if target.get("system_id") is not None
        }
        occupied = self._occupied_hostile_colonies()
        reasons: dict[int, set[str]] = {
            system_id: {"player_occupied_hostile_colony"} for system_id in occupied
        }
        for system_id in self.allowed_systems:
            hostile_neighbors = objective_systems.intersection(
                self.graph.get(system_id, [])
            )
            if hostile_neighbors and self.owner in self.controllers.get(
                system_id, set()
            ):
                reasons.setdefault(system_id, set()).add(
                    "friendly_frontier_adjacent_to_war_objective"
                )

        output: list[dict[str, Any]] = []
        for system_id, system_reasons in reasons.items():
            stationed = sorted(
                int(value)
                for value in pressure["stationed_fleet_ids"].get(system_id, [])
            )
            hostile_neighbors = sorted(
                objective_systems.intersection(self.graph.get(system_id, []))
            )
            output.append(
                {
                    **self._system_label(system_id),
                    "reasons": sorted(system_reasons),
                    "occupied_hostile_colonies": occupied.get(system_id, []),
                    "adjacent_war_objective_system_ids": hostile_neighbors,
                    "stationed_military_fleet_ids": stationed,
                    "stationed_military_power": float(
                        pressure["stationed_power"].get(system_id, 0.0)
                    ),
                    "coverage_state": "covered" if stationed else "uncovered",
                    "priority": len(hostile_neighbors) + 2 * int(system_id in occupied),
                }
            )
        return sorted(
            output,
            key=lambda item: (
                item["coverage_state"] != "uncovered",
                -int(item["priority"]),
                int(item["system_id"]),
            ),
        )[:32]

    def _fleet_role(
        self,
        fleet: dict[str, Any],
        pressure: dict[str, Any],
        garrisons: list[dict[str, Any]],
    ) -> dict[str, Any]:
        military = [
            item
            for item in self.fleet_profile.get("fleets", [])
            if item.get("attack_verified_family", False)
            and item.get("military_power") is not None
        ]
        powers = sorted(float(item.get("military_power") or 0.0) for item in military)
        power = float(fleet.get("military_power") or 0.0)
        percentile = (
            sum(value <= power for value in powers) / len(powers) if powers else 0.0
        )
        current = fleet.get("movement", {}).get("current_system_id")
        current_garrison = next(
            (
                item
                for item in garrisons
                if current is not None and int(item["system_id"]) == int(current)
            ),
            None,
        )
        other_guards = []
        if current_garrison is not None:
            other_guards = [
                int(value)
                for value in current_garrison["stationed_military_fleet_ids"]
                if int(value) != int(fleet["fleet_id"])
            ]
        hold_recommended = bool(current_garrison is not None and not other_guards)
        if hold_recommended and percentile <= 0.5:
            role = "occupation_guard"
        elif percentile >= 0.75:
            role = "breakthrough_force"
        elif percentile <= 0.35:
            role = "frontline_screen_or_garrison"
        else:
            role = "line_fleet"
        return {
            "suggested_role": role,
            "relative_power_percentile": round(percentile, 4),
            "hold_current_system_recommended": hold_recommended,
            "departure_guard_risk": hold_recommended,
            "current_garrison_system": current_garrison,
            "other_stationed_guard_fleet_ids": other_guards,
            "authority": "relative_power_and_save_backed_front_coverage_heuristic",
        }

    def _deployment_portfolio(
        self,
        *,
        fronts: list[dict[str, Any]],
        roles: list[dict[str, Any]],
        garrisons: list[dict[str, Any]],
        pressure: dict[str, Any],
    ) -> dict[str, Any]:
        active_targets = {
            int(item["fleet_id"]): (
                int(item["target_system_id"])
                if item.get("target_system_id") is not None
                else None
            )
            for item in pressure["assignments"]
            if item.get("active", False) and item.get("fleet_id") is not None
        }
        available = [
            role
            for role in roles
            if role.get("move_callable_now", False)
            and role.get("attack_callable_now", False)
            and not role.get("departure_guard_risk", False)
            and int(role["fleet_id"]) not in active_targets
        ]
        assigned: set[int] = set()
        garrison_assignments: list[dict[str, Any]] = []
        for garrison in garrisons:
            if garrison.get("coverage_state") != "uncovered":
                continue
            system_id = int(garrison["system_id"])
            choices: list[tuple[int, float, int, dict[str, Any], list[int]]] = []
            for role in available:
                fleet_id = int(role["fleet_id"])
                if (
                    fleet_id in assigned
                    or float(role.get("relative_power_percentile") or 0.0) > 0.5
                ):
                    continue
                current = role.get("movement", {}).get("current_system_id")
                if current is None:
                    continue
                allowed = set(self.allowed_systems) | {int(current), system_id}
                path = _bfs_path(
                    self.graph,
                    int(current),
                    system_id,
                    allowed=allowed,
                )
                if not path:
                    continue
                choices.append(
                    (
                        len(path) - 1,
                        float(role.get("military_power") or 0.0),
                        fleet_id,
                        role,
                        path,
                    )
                )
            if not choices:
                continue
            hops, power, fleet_id, role, path = min(choices)
            assigned.add(fleet_id)
            garrison_assignments.append(
                {
                    "fleet_id": fleet_id,
                    "military_power": power,
                    "suggested_role": role["suggested_role"],
                    "target_system_id": system_id,
                    "target_reasons": list(garrison["reasons"]),
                    "path_system_ids": path,
                    "hyperlane_jumps": hops,
                    "requires_route_validation": True,
                }
            )

        front_state = {
            int(front["system_id"]): {
                "assigned_power": float(front["assigned_player_military_power"]),
                "assigned_count": len(front["assigned_player_fleet_ids"]),
                "recommended": [],
                "task_force_package": None,
            }
            for front in fronts
        }
        assault_assignments: list[dict[str, Any]] = []
        task_force_packages: list[dict[str, Any]] = []
        total_front_assignments = sum(
            int(state["assigned_count"]) for state in front_state.values()
        )
        while True:
            largest_shortfall = max(
                (
                    max(
                        float(front["minimum_recommended_friendly_power"])
                        - float(front_state[int(front["system_id"])]["assigned_power"]),
                        0.0,
                    )
                    for front in fronts
                    if int(front["unknown_hostile_power_object_count"]) == 0
                ),
                default=0.0,
            )
            best: tuple[float, float, int, int, dict[str, Any]] | None = None
            for front in fronts:
                if int(front["unknown_hostile_power_object_count"]) > 0:
                    continue
                system_id = int(front["system_id"])
                state = front_state[system_id]
                required_power = float(front["minimum_recommended_friendly_power"])
                shortfall = max(
                    required_power - float(state["assigned_power"]),
                    0.0,
                )
                if shortfall <= 0:
                    continue

                reachable: list[dict[str, Any]] = []
                for role in available:
                    fleet_id = int(role["fleet_id"])
                    if fleet_id in assigned:
                        continue
                    power = float(role.get("military_power") or 0.0)
                    current = role.get("movement", {}).get("current_system_id")
                    if current is None or power <= 0:
                        continue
                    allowed = set(self.allowed_systems) | {int(current), system_id}
                    path = _bfs_path(
                        self.graph,
                        int(current),
                        system_id,
                        allowed=allowed,
                    )
                    if path:
                        reachable.append(
                            {
                                "role": role,
                                "fleet_id": fleet_id,
                                "military_power": power,
                                "path": path,
                                "hyperlane_jumps": len(path) - 1,
                            }
                        )
                reachable.sort(
                    key=lambda item: (
                        -float(item["military_power"]),
                        int(item["hyperlane_jumps"]),
                        int(item["fleet_id"]),
                    )
                )
                package: list[dict[str, Any]] = []
                package_power = 0.0
                for choice in reachable:
                    package.append(choice)
                    package_power += float(choice["military_power"])
                    if package_power >= shortfall:
                        break
                if package_power < shortfall:
                    continue

                exploration = math.sqrt(
                    math.log(total_front_assignments + 2.0)
                    / (float(state["assigned_count"]) + 1.0)
                )
                overcommit_fraction = max(package_power - shortfall, 0.0) / max(
                    required_power, 1.0
                )
                mean_hops = sum(int(item["hyperlane_jumps"]) for item in package) / len(
                    package
                )
                score = (
                    shortfall / max(largest_shortfall, 1.0)
                    + self.exploration_weight * exploration
                    + 0.2 * int(front["has_hostile_inhibitor"])
                    - 0.35 * overcommit_fraction
                    - 0.03 * mean_hops
                )
                candidate_payload = {
                    "front": front,
                    "package": package,
                    "package_power": package_power,
                    "shortfall": shortfall,
                    "required_power": required_power,
                    "exploration": exploration,
                }
                candidate = (
                    score,
                    -overcommit_fraction,
                    -len(package),
                    -system_id,
                    candidate_payload,
                )
                if best is None or candidate[:4] > best[:4]:
                    best = candidate
            if best is None:
                break
            score, _overcommit, _negative_count, _negative_system, payload = best
            front = payload["front"]
            system_id = int(front["system_id"])
            state = front_state[system_id]
            package = list(payload["package"])
            package_ids = sorted(int(item["fleet_id"]) for item in package)
            projected_power = float(state["assigned_power"]) + float(
                payload["package_power"]
            )
            task_force_digest = hashlib.sha256(
                f"{system_id}:{','.join(map(str, package_ids))}".encode("ascii")
            ).hexdigest()[:12]
            task_force_id = f"task-force:{system_id}:{task_force_digest}"
            task_force = {
                "task_force_id": task_force_id,
                "target_system_id": system_id,
                "existing_fleet_ids": list(front["assigned_player_fleet_ids"]),
                "reinforcement_fleet_ids": package_ids,
                "all_fleet_ids": sorted(
                    {
                        *(int(value) for value in front["assigned_player_fleet_ids"]),
                        *package_ids,
                    }
                ),
                "known_hostile_military_power": float(
                    front["known_hostile_military_power"]
                ),
                "minimum_force_ratio": self.minimum_space_force_ratio,
                "minimum_recommended_friendly_power": float(payload["required_power"]),
                "projected_friendly_military_power": projected_power,
                "known_force_ratio": (
                    round(
                        projected_power / float(front["known_hostile_military_power"]),
                        4,
                    )
                    if float(front["known_hostile_military_power"]) > 0
                    else None
                ),
                "hard_gate_satisfied": projected_power
                >= float(payload["required_power"]),
                "dispatch_ready": projected_power >= float(payload["required_power"]),
                "dispatch_policy": "atomic_task_force",
                "allocation_score": round(score, 6),
                "exploration_credit": round(
                    self.exploration_weight * float(payload["exploration"]),
                    6,
                ),
            }
            state["task_force_package"] = task_force
            state["assigned_power"] = projected_power
            state["assigned_count"] = int(state["assigned_count"]) + len(package)
            state["recommended"].extend(package_ids)
            task_force_packages.append(task_force)
            total_front_assignments += len(package)
            for choice in package:
                role = choice["role"]
                fleet_id = int(choice["fleet_id"])
                power = float(choice["military_power"])
                assigned.add(fleet_id)
                independently_safe = float(
                    front["assigned_player_military_power"]
                ) + power >= float(payload["required_power"])
                assault_assignments.append(
                    {
                        "fleet_id": fleet_id,
                        "military_power": power,
                        "suggested_role": role["suggested_role"],
                        "target_system_id": system_id,
                        "allocation_score": round(score, 6),
                        "path_system_ids": list(choice["path"]),
                        "hyperlane_jumps": int(choice["hyperlane_jumps"]),
                        "task_force_id": task_force_id,
                        "task_force_fleet_ids": package_ids,
                        "task_force_projected_military_power": projected_power,
                        "task_force_hard_gate_satisfied": True,
                        "individual_dispatch_authorized": independently_safe,
                        "requires_coordinated_dispatch": not independently_safe,
                        "requires_campaign_route_inspection": True,
                    }
                )

        unmet_fronts: list[dict[str, Any]] = []
        for front in fronts:
            system_id = int(front["system_id"])
            state = front_state[system_id]
            projected_power = float(state["assigned_power"])
            required_power = float(front["minimum_recommended_friendly_power"])
            shortfall = max(required_power - projected_power, 0.0)
            task_force = state["task_force_package"]
            front["reinforcement_package"] = {
                "fleet_ids": list(state["recommended"]),
                "military_power": (
                    projected_power - float(front["assigned_player_military_power"])
                ),
                "projected_assigned_military_power": projected_power,
                "closes_known_power_shortfall": shortfall <= 0,
                "remaining_known_power_shortfall": shortfall,
                "hard_gate_satisfied": bool(
                    isinstance(task_force, dict)
                    and task_force.get("hard_gate_satisfied", False)
                ),
                "dispatch_ready": bool(
                    isinstance(task_force, dict)
                    and task_force.get("dispatch_ready", False)
                ),
                "task_force_id": (
                    task_force.get("task_force_id")
                    if isinstance(task_force, dict)
                    else None
                ),
                "requires_individual_route_validation": bool(state["recommended"]),
            }
            if shortfall > 0 or int(front["unknown_hostile_power_object_count"]) > 0:
                unmet_fronts.append(
                    {
                        "system_id": system_id,
                        "remaining_known_power_shortfall": shortfall,
                        "unknown_hostile_power_object_count": int(
                            front["unknown_hostile_power_object_count"]
                        ),
                    }
                )
        return {
            "schema": "iag.campaign_deployment_portfolio.v1",
            "garrison_assignments": garrison_assignments,
            "assault_assignments": assault_assignments,
            "task_force_packages": task_force_packages,
            "unassigned_reserve_fleet_ids": sorted(
                int(role["fleet_id"])
                for role in available
                if int(role["fleet_id"]) not in assigned
            ),
            "unmet_or_unknown_fronts": unmet_fronts,
            "allocation_policy": (
                "Small lower-half fleets cover reachable empty garrisons first. "
                "Assault fleets are allocated only as atomic packages whose combined "
                "save-backed power reaches the configured ratio. UCB-style exploration "
                "and congestion rank feasible packages and routes; they never split an "
                "understrength fleet toward a hostile concentration."
            ),
        }

    def deployment_summary(
        self,
        *,
        authorized_fleet_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        pressure = self._deployment_pressure(None)
        garrisons = self._garrison_candidates(pressure)
        fronts: list[dict[str, Any]] = []
        objective_systems = sorted(
            {
                int(target["system_id"])
                for target in [*self.hostile_targets, *self.hostile_colonies]
                if target.get("system_id") is not None
            }
        )
        for system_id in objective_systems:
            objectives = self._objectives(system_id)
            opposition = self._system_opposition(system_id)
            known_power = float(opposition["known_hostile_military_power"])
            unknown_power_count = int(opposition["unknown_hostile_power_object_count"])
            assigned_ids = sorted(
                {
                    int(value)
                    for value in pressure["target_fleet_ids"].get(system_id, set())
                }
                | {
                    int(value)
                    for value in pressure["stationed_fleet_ids"].get(system_id, [])
                }
            )
            assigned_power = sum(
                float(item.get("military_power") or 0.0)
                for item in self.fleet_profile.get("fleets", [])
                if int(item["fleet_id"]) in assigned_ids
            )
            minimum_power = known_power * self.minimum_space_force_ratio
            shortfall = max(minimum_power - assigned_power, 0.0)
            if shortfall > 0:
                force_status = "reinforcement_required"
            elif unknown_power_count > 0:
                force_status = "opposition_power_unknown"
            else:
                force_status = "adequate_known_force"
            fronts.append(
                {
                    **self._system_label(system_id),
                    "target_objectives": objectives,
                    "known_hostile_military_power": known_power,
                    "unknown_hostile_power_object_count": unknown_power_count,
                    "assigned_player_fleet_ids": assigned_ids,
                    "assigned_player_military_power": assigned_power,
                    "minimum_force_ratio": self.minimum_space_force_ratio,
                    "minimum_recommended_friendly_power": minimum_power,
                    "additional_military_power_required": shortfall,
                    "force_status": force_status,
                    "known_force_ratio": (
                        round(assigned_power / known_power, 4)
                        if known_power > 0
                        else None
                    ),
                    "has_hostile_inhibitor": system_id in self.inhibitor_systems,
                }
            )

        roles: list[dict[str, Any]] = []
        for fleet in self.fleet_profile.get("fleets", []):
            fleet_id = int(fleet["fleet_id"])
            if (
                not fleet.get("attack_verified_family", False)
                or fleet.get("movement", {}).get("current_system_id") is None
                or (
                    authorized_fleet_ids is not None
                    and fleet_id not in authorized_fleet_ids
                )
            ):
                continue
            roles.append(
                {
                    "fleet_id": fleet_id,
                    "display_name_hint": fleet.get("display_name_hint"),
                    "military_power": fleet.get("military_power"),
                    "availability": fleet.get("availability"),
                    "move_callable_now": fleet.get("move_callable_now", False),
                    "attack_callable_now": fleet.get("attack_callable_now", False),
                    "movement": fleet.get("movement"),
                    **self._fleet_role(fleet, pressure, garrisons),
                }
            )
        roles.sort(
            key=lambda item: (
                -float(item["military_power"] or 0.0),
                item["fleet_id"],
            )
        )
        portfolio = self._deployment_portfolio(
            fronts=fronts,
            roles=roles,
            garrisons=garrisons,
            pressure=pressure,
        )
        congested = sorted(
            (
                {
                    "edge_system_ids": list(edge),
                    "visit_count": round(float(visits), 4),
                    "assigned_military_power": round(
                        float(pressure["edge_assigned_power"].get(edge, 0.0)), 4
                    ),
                }
                for edge, visits in pressure["edge_visits"].items()
            ),
            key=lambda item: (-item["visit_count"], item["edge_system_ids"]),
        )
        return {
            "schema": "iag.campaign_deployment_state.v1",
            "game_date": self.fleet_profile.get("game_date"),
            "fronts": fronts,
            "garrison_candidates": garrisons,
            "fleet_role_recommendations": roles[:64],
            "deployment_portfolio": portfolio,
            "active_or_remembered_assignments": pressure["assignments"][-64:],
            "most_congested_corridors": congested[:32],
            "planning_rule": (
                "Use relative roles and coverage as advice; every actual order remains "
                "save-backed, permission-checked, and serially executed."
            ),
        }

    def _blocker_cost(self, system_id: int) -> tuple[int, float, int, float, int]:
        if system_id not in self.inhibitor_systems:
            return (0, 0.0, 0, 0.0, 0)
        blocker = self._blocker(system_id)
        planets = list(blocker["planetary_sources"])
        defender_health = sum(
            float(
                (
                    planet.get("defending_armies", {})
                    if isinstance(planet.get("defending_armies"), dict)
                    else {}
                ).get("current_health_total")
                or 0.0
            )
            for planet in planets
        )
        unknown = int(blocker["unknown_power_object_count"]) + int(
            blocker["unknown_source"]
        )
        return (
            1,
            float(blocker["known_starbase_power"]),
            len(planets),
            defender_health,
            unknown,
        )

    def _system_opposition(self, system_id: int) -> dict[str, Any]:
        with self._cache_lock:
            cached = self._opposition_cache.get(system_id)
        if cached is not None:
            return cached
        objectives = self._objectives(system_id)
        combatants: list[dict[str, Any]] = []
        seen: set[int] = set()
        for target in [
            *objectives["hostile_starbases"],
            *objectives["hostile_mobile_fleets"],
        ]:
            fleet_id = int(target["fleet_id"])
            if fleet_id in seen:
                continue
            seen.add(fleet_id)
            combatants.append(target)
        known_power = sum(
            float(target.get("military_power") or 0.0)
            for target in combatants
            if target.get("military_power") is not None
        )
        unknown_count = sum(
            target.get("military_power") is None for target in combatants
        )
        blocker = (
            self._blocker(system_id) if system_id in self.inhibitor_systems else None
        )
        if blocker is not None and blocker.get("unknown_source", False):
            unknown_count += 1
        result = {
            **self._system_label(system_id),
            "known_hostile_military_power": known_power,
            "unknown_hostile_power_object_count": int(unknown_count),
            "hostile_combatant_fleet_ids": sorted(seen),
            "authority": ("save_backed_visible_or_strategic_war_objectives_in_system"),
        }
        with self._cache_lock:
            return self._opposition_cache.setdefault(system_id, result)

    def _route_force_assessment(
        self,
        *,
        fleet: dict[str, Any],
        path: list[int],
        pressure: dict[str, Any],
    ) -> dict[str, Any]:
        source_fleet_id = int(fleet["fleet_id"])
        source_power = float(fleet.get("military_power") or 0.0)
        engagement_system_ids: list[int] = []
        for system_id in path[1:-1]:
            opposition = self._system_opposition(int(system_id))
            if (
                system_id in self.inhibitor_systems
                or float(opposition["known_hostile_military_power"]) > 0
                or int(opposition["unknown_hostile_power_object_count"]) > 0
            ):
                engagement_system_ids.append(int(system_id))
        if path:
            engagement_system_ids.append(int(path[-1]))
        engagement_system_ids = list(dict.fromkeys(engagement_system_ids))

        engagements: list[dict[str, Any]] = []
        for system_id in engagement_system_ids:
            opposition = self._system_opposition(system_id)
            routed_support_ids = {
                int(item["fleet_id"])
                for item in pressure["assignments"]
                if item.get("active", False)
                and item.get("fleet_id") is not None
                and system_id in item.get("path_system_ids", [])[1:]
                and (
                    item.get("authority") == "active_campaign_commitment"
                    or (
                        item.get("target_system_id") is not None
                        and int(item["target_system_id"]) == system_id
                    )
                )
            }
            support_ids = sorted(
                (
                    routed_support_ids
                    | {
                        int(value)
                        for value in pressure["stationed_fleet_ids"].get(system_id, [])
                    }
                )
                - {source_fleet_id}
            )
            support_power = sum(
                float(item.get("military_power") or 0.0)
                for item in self.fleet_profile.get("fleets", [])
                if int(item["fleet_id"]) in support_ids
            )
            known_power = float(opposition["known_hostile_military_power"])
            required_power = known_power * self.minimum_space_force_ratio
            projected_power = source_power + support_power
            shortfall = max(required_power - projected_power, 0.0)
            unknown_count = int(opposition["unknown_hostile_power_object_count"])
            if shortfall > 0:
                status = "reinforcement_required"
            elif unknown_count > 0:
                status = "opposition_power_unknown"
            else:
                status = "adequate_known_force"
            engagements.append(
                {
                    **opposition,
                    "source_fleet_military_power": source_power,
                    "supporting_fleet_ids": support_ids,
                    "supporting_military_power": support_power,
                    "projected_friendly_military_power": projected_power,
                    "minimum_force_ratio": self.minimum_space_force_ratio,
                    "minimum_recommended_friendly_power": required_power,
                    "known_force_ratio": (
                        round(projected_power / known_power, 4)
                        if known_power > 0
                        else None
                    ),
                    "additional_military_power_required": shortfall,
                    "status": status,
                    "hard_gate_satisfied": status == "adequate_known_force",
                }
            )

        shortfall = max(
            (float(item["additional_military_power_required"]) for item in engagements),
            default=0.0,
        )
        unknown_count = sum(
            int(item["unknown_hostile_power_object_count"]) for item in engagements
        )
        if shortfall > 0:
            status = "reinforcement_required"
        elif unknown_count > 0:
            status = "opposition_power_unknown"
        else:
            status = "adequate_known_force"
        limiting = max(
            engagements,
            key=lambda item: (
                float(item["additional_military_power_required"]),
                int(item["unknown_hostile_power_object_count"]),
                float(item["known_hostile_military_power"]),
            ),
            default=None,
        )
        return {
            "status": status,
            "hard_gate_satisfied": status == "adequate_known_force",
            "minimum_force_ratio": self.minimum_space_force_ratio,
            "source_fleet_id": source_fleet_id,
            "source_fleet_military_power": source_power,
            "maximum_additional_military_power_required": shortfall,
            "unknown_hostile_power_object_count": unknown_count,
            "limiting_engagement_system_id": (
                int(limiting["system_id"]) if limiting is not None else None
            ),
            "engagements": engagements,
            "coordination_rule": (
                "Congestion and exploration never authorize an engagement below "
                "the configured known-force ratio. Active support is counted only "
                "from save movement or campaign commitments crossing that system."
            ),
        }

    def _label_priority(self, label: _RouteLabel) -> tuple[float, ...]:
        return (
            label.hyperlane_jumps
            + 2.0 * label.breakthrough_count
            + math.log1p(label.known_blocker_starbase_power) / 8.0
            + math.log1p(label.peak_known_hostile_military_power) / 8.0
            + label.unknown_blocker_risk_count
            + label.unknown_hostile_power_object_count
            + self.congestion_weight * label.corridor_visit_count / 2.0,
            float(label.breakthrough_count),
            float(label.unknown_blocker_risk_count),
            label.corridor_visit_count,
            float(label.hyperlane_jumps),
        )

    def _pareto_paths(
        self,
        *,
        source: int,
        target: int,
        allowed: set[int],
        edge_visits: Counter[tuple[int, int]],
    ) -> tuple[list[list[int]], dict[str, Any]]:
        shortest = _bfs_path(self.graph, source, target, allowed=allowed)
        if not shortest:
            return [], {
                "algorithm": "bounded_multiobjective_label_search",
                "expanded_labels": 0,
                "truncated": False,
                "maximum_hops": 0,
            }
        shortest_hops = max(len(shortest) - 1, 0)
        maximum_hops = min(
            max(len(allowed) - 1, 0),
            shortest_hops
            + max(self.detour_hop_budget, math.ceil(shortest_hops * 0.75)),
        )
        source_cost = (
            self._blocker_cost(source) if source != target else (0, 0.0, 0, 0.0, 0)
        )
        initial = _RouteLabel(
            system_id=source,
            path=(source,),
            hyperlane_jumps=0,
            breakthrough_count=source_cost[0],
            known_blocker_starbase_power=source_cost[1],
            planetary_inhibitor_count=source_cost[2],
            known_planet_defender_health=source_cost[3],
            unknown_blocker_risk_count=source_cost[4],
            known_path_hostile_military_power=0.0,
            peak_known_hostile_military_power=0.0,
            unknown_hostile_power_object_count=0,
            corridor_visit_count=0.0,
            peak_edge_visit_count=0.0,
        )
        labels_by_system: dict[int, list[_RouteLabel]] = {source: [initial]}
        queue: list[tuple[tuple[float, ...], int, _RouteLabel]] = []
        sequence = 0
        heapq.heappush(queue, (self._label_priority(initial), sequence, initial))
        targets: list[_RouteLabel] = []
        expansions = 0
        truncated = False
        while queue:
            _priority, _sequence, label = heapq.heappop(queue)
            if label not in labels_by_system.get(label.system_id, []):
                continue
            if label.system_id == target:
                targets.append(label)
                targets = _bounded_labels(
                    targets,
                    self.maximum_route_options * 4,
                )
                continue
            if label.hyperlane_jumps >= maximum_hops:
                continue
            expansions += 1
            if expansions > self.maximum_search_expansions:
                truncated = True
                break
            for neighbor in sorted(self.graph.get(label.system_id, [])):
                if neighbor not in allowed or neighbor in label.path:
                    continue
                blocker_cost = (
                    self._blocker_cost(neighbor)
                    if neighbor != target
                    else (0, 0.0, 0, 0.0, 0)
                )
                edge_load = float(
                    edge_visits.get(_edge_key(label.system_id, neighbor), 0.0)
                )
                opposition = self._system_opposition(neighbor)
                hostile_power = float(opposition["known_hostile_military_power"])
                unknown_hostiles = int(opposition["unknown_hostile_power_object_count"])
                candidate = _RouteLabel(
                    system_id=neighbor,
                    path=(*label.path, neighbor),
                    hyperlane_jumps=label.hyperlane_jumps + 1,
                    breakthrough_count=label.breakthrough_count + blocker_cost[0],
                    known_blocker_starbase_power=(
                        label.known_blocker_starbase_power + blocker_cost[1]
                    ),
                    planetary_inhibitor_count=(
                        label.planetary_inhibitor_count + blocker_cost[2]
                    ),
                    known_planet_defender_health=(
                        label.known_planet_defender_health + blocker_cost[3]
                    ),
                    unknown_blocker_risk_count=(
                        label.unknown_blocker_risk_count + blocker_cost[4]
                    ),
                    known_path_hostile_military_power=(
                        label.known_path_hostile_military_power + hostile_power
                    ),
                    peak_known_hostile_military_power=max(
                        label.peak_known_hostile_military_power,
                        hostile_power,
                    ),
                    unknown_hostile_power_object_count=(
                        label.unknown_hostile_power_object_count + unknown_hostiles
                    ),
                    corridor_visit_count=label.corridor_visit_count + edge_load,
                    peak_edge_visit_count=max(label.peak_edge_visit_count, edge_load),
                )
                existing = labels_by_system.setdefault(neighbor, [])
                if candidate in existing or any(
                    _dominates(item.metrics(), candidate.metrics()) for item in existing
                ):
                    continue
                frontier = [
                    item
                    for item in existing
                    if not _dominates(candidate.metrics(), item.metrics())
                ]
                frontier.append(candidate)
                frontier = _bounded_labels(frontier, self.maximum_labels_per_system)
                labels_by_system[neighbor] = frontier
                if candidate not in frontier:
                    continue
                sequence += 1
                heapq.heappush(
                    queue,
                    (self._label_priority(candidate), sequence, candidate),
                )
        unique = sorted(
            {label.path for label in targets},
            key=lambda path: (len(path), path),
        )
        return [list(path) for path in unique], {
            "algorithm": "bounded_multiobjective_label_search",
            "objectives": list(SEARCH_OBJECTIVES),
            "expanded_labels": expansions,
            "truncated": truncated,
            "shortest_hops": shortest_hops,
            "maximum_hops": maximum_hops,
            "maximum_labels_per_system": self.maximum_labels_per_system,
            "maximum_route_options": self.maximum_route_options,
        }

    def _route_option(
        self,
        *,
        fleet_id: int,
        target_system_id: int,
        route_type: str,
        path: list[int],
        pressure: dict[str, Any],
        strategy_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        blockers = [
            self._blocker(system_id)
            for system_id in path[:-1]
            if system_id in self.inhibitor_systems
        ]
        known_power = sum(
            float(blocker["known_starbase_power"]) for blocker in blockers
        )
        unknown_count = sum(
            int(blocker["unknown_power_object_count"]) + int(blocker["unknown_source"])
            for blocker in blockers
        )
        planetary_sources = [
            planet for blocker in blockers for planet in blocker["planetary_sources"]
        ]
        defender_health = sum(
            float(
                (
                    planet.get("defending_armies", {})
                    if isinstance(planet.get("defending_armies"), dict)
                    else {}
                ).get("current_health_total")
                or 0.0
            )
            for planet in planetary_sources
        )
        defender_armies = sum(
            int(
                (
                    planet.get("defending_armies", {})
                    if isinstance(planet.get("defending_armies"), dict)
                    else {}
                ).get("army_count")
                or 0
            )
            for planet in planetary_sources
        )
        digest = hashlib.sha256(
            (
                f"{fleet_id}:{target_system_id}:{route_type}:"
                + ",".join(str(system_id) for system_id in path)
            ).encode("ascii")
        ).hexdigest()[:12]
        edge_loads = [
            float(pressure["edge_visits"].get(_edge_key(left, right), 0.0))
            for left, right in pairwise(path)
        ]
        edge_power = [
            float(pressure["edge_assigned_power"].get(_edge_key(left, right), 0.0))
            for left, right in pairwise(path)
        ]
        total_visits = sum(float(value) for value in pressure["edge_visits"].values())
        corridor_visits = sum(edge_loads)
        exploration_bonus = math.sqrt(
            math.log(total_visits + 2.0) / (corridor_visits + 1.0)
        )
        source_fleet = next(
            item
            for item in self.fleet_profile.get("fleets", [])
            if int(item["fleet_id"]) == int(fleet_id)
        )
        force_assessment = self._route_force_assessment(
            fleet=source_fleet,
            path=path,
            pressure=pressure,
        )
        force_engagements = force_assessment["engagements"]
        return {
            "route_id": f"route:{fleet_id}:{target_system_id}:{digest}",
            "route_type": route_type,
            "strategy_tags": list(strategy_tags or []),
            "source_fleet_id": fleet_id,
            "target_system_id": target_system_id,
            "path_system_ids": path,
            "path_systems": [self._system_label(system_id) for system_id in path],
            "hyperlane_jumps": max(len(path) - 1, 0),
            "breakthrough_count": len(blockers),
            "blockers": blockers,
            "known_blocker_starbase_power": known_power,
            "planetary_inhibitor_count": len(planetary_sources),
            "known_planet_defender_army_count": defender_armies,
            "known_planet_defender_health": defender_health,
            "current_bombardment_damage_total": sum(
                float(planet.get("bombardment_damage") or 0.0)
                for planet in planetary_sources
            ),
            "unknown_blocker_risk_count": unknown_count,
            "known_path_hostile_military_power": sum(
                float(item["known_hostile_military_power"])
                for item in force_engagements
            ),
            "peak_known_hostile_military_power": max(
                (
                    float(item["known_hostile_military_power"])
                    for item in force_engagements
                ),
                default=0.0,
            ),
            "unknown_hostile_power_object_count": sum(
                int(item["unknown_hostile_power_object_count"])
                for item in force_engagements
            ),
            "space_force_shortfall": float(
                force_assessment["maximum_additional_military_power_required"]
            ),
            "requires_breakthrough": bool(blockers),
            "corridor_visit_count": round(corridor_visits, 4),
            "peak_edge_visit_count": round(max(edge_loads, default=0.0), 4),
            "first_edge_visit_count": round(edge_loads[0] if edge_loads else 0.0, 4),
            "corridor_assigned_military_power": round(sum(edge_power), 4),
            "novel_edge_count": sum(value <= 0 for value in edge_loads),
            "target_assignment_count": round(
                float(pressure["target_visits"].get(target_system_id, 0.0)), 4
            ),
            "target_assigned_military_power": round(
                float(pressure["target_assigned_power"].get(target_system_id, 0.0)),
                4,
            ),
            "mcts_exploration_bonus": round(exploration_bonus, 6),
            "force_assessment": force_assessment,
            "exploration_authority": (
                "deterministic_ucb_style_bonus_from_current_and_remembered_corridor_visits"
            ),
        }

    @staticmethod
    def _objective_metrics(candidate: dict[str, Any]) -> tuple[float, ...]:
        return tuple(float(candidate[name]) for name in ROUTE_OBJECTIVES)

    @staticmethod
    def _pareto(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for candidate in options:
            metrics = CampaignRoutePlanner._objective_metrics(candidate)
            dominated = False
            for other in options:
                if other is candidate:
                    continue
                other_metrics = CampaignRoutePlanner._objective_metrics(other)
                if _dominates(other_metrics, metrics):
                    dominated = True
                    break
            if not dominated:
                output.append(candidate)
        return output

    def _score_options(
        self,
        options: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not options:
            return []
        transformed = [
            (
                float(option["hyperlane_jumps"]),
                float(option["breakthrough_count"]),
                math.log1p(float(option["known_blocker_starbase_power"])),
                float(option["planetary_inhibitor_count"]),
                math.log1p(float(option["known_planet_defender_health"])),
                float(option["unknown_blocker_risk_count"]),
                math.log1p(float(option["known_path_hostile_military_power"])),
                math.log1p(float(option["peak_known_hostile_military_power"])),
                float(option["unknown_hostile_power_object_count"]),
                math.log1p(float(option["space_force_shortfall"])),
                float(option["corridor_visit_count"]),
                float(option["peak_edge_visit_count"]),
            )
            for option in options
        ]
        objective_count = len(ROUTE_OBJECTIVES)
        minima = [
            min(values[index] for values in transformed)
            for index in range(objective_count)
        ]
        maxima = [
            max(values[index] for values in transformed)
            for index in range(objective_count)
        ]
        weights = (
            0.85,
            1.3,
            1.0,
            0.8,
            0.8,
            1.25,
            1.2,
            1.5,
            1.5,
            3.0,
            0.7,
            0.5,
        )
        ranked: list[dict[str, Any]] = []
        for option, values in zip(options, transformed):
            normalized: list[float] = []
            for index, value in enumerate(values):
                span = maxima[index] - minima[index]
                normalized.append(0.0 if span <= 0 else (value - minima[index]) / span)
            exploitation_cost = sum(
                normalized[index] * weights[index]
                for index in range(objective_count - 2)
            )
            congestion_penalty = self.congestion_weight * sum(
                normalized[index] * weights[index]
                for index in range(objective_count - 2, objective_count)
            )
            exploration_credit = self.exploration_weight * float(
                option["mcts_exploration_bonus"]
            )
            strategic_score = (
                exploitation_cost + congestion_penalty - exploration_credit
            )
            ranked.append(
                {
                    **option,
                    "normalized_objective_costs": {
                        name: round(value, 6)
                        for name, value in zip(ROUTE_OBJECTIVES, normalized)
                    },
                    "exploitation_cost": round(exploitation_cost, 6),
                    "congestion_penalty": round(congestion_penalty, 6),
                    "exploration_credit": round(exploration_credit, 6),
                    "strategic_score": round(strategic_score, 6),
                }
            )
        ranked.sort(
            key=lambda item: (
                not bool(
                    item.get("force_assessment", {}).get("hard_gate_satisfied", False)
                ),
                item.get("force_assessment", {}).get("status")
                == "opposition_power_unknown",
                float(item["strategic_score"]),
                int(item["breakthrough_count"]),
                int(item["hyperlane_jumps"]),
                str(item["route_id"]),
            )
        )
        ranked = ranked[: self.maximum_route_options]
        for index, option in enumerate(ranked, start=1):
            option["recommendation_rank"] = index
            option["recommended"] = index == 1
            option["execution_recommended"] = bool(
                index == 1
                and option.get("force_assessment", {}).get("hard_gate_satisfied", False)
            )
        return ranked

    def plan(self, *, fleet_id: int, target_system_id: int) -> dict[str, Any]:
        fleet = next(
            (
                item
                for item in self.fleet_profile.get("fleets", [])
                if int(item["fleet_id"]) == int(fleet_id)
            ),
            None,
        )
        if fleet is None or not fleet.get("attack_verified_family", False):
            raise ValueError(f"Fleet {fleet_id} is not a player military fleet.")
        source_system = fleet.get("movement", {}).get("current_system_id")
        if source_system is None:
            raise ValueError(f"Fleet {fleet_id} has no current save-backed system.")
        source_system = int(source_system)
        target_system_id = int(target_system_id)
        if target_system_id not in self.known_systems:
            raise ValueError(
                f"System {target_system_id} is not on the player strategic map."
            )
        target_objectives = self._objectives(target_system_id)
        if not any(target_objectives.values()):
            raise ValueError(
                f"System {target_system_id} has no save-backed active-war objective."
            )

        allowed = set(self.allowed_systems)
        allowed.update({source_system, target_system_id})
        pressure = self._deployment_pressure(int(fleet_id))
        safe = _bfs_path(
            self.graph,
            source_system,
            target_system_id,
            allowed=allowed,
            terminal=self.inhibitor_systems,
        )
        direct = _bfs_path(
            self.graph,
            source_system,
            target_system_id,
            allowed=allowed,
        )
        least = _least_breakthrough_path(
            self.graph,
            source_system,
            target_system_id,
            allowed=allowed,
            inhibitors=self.inhibitor_systems,
        )
        searched_paths, search_diagnostics = self._pareto_paths(
            source=source_system,
            target=target_system_id,
            allowed=allowed,
            edge_visits=pressure["edge_visits"],
        )
        candidates: list[dict[str, Any]] = []
        seen_paths: set[tuple[int, ...]] = set()
        canonical = (
            ("safe_detour", safe),
            ("shortest_assault", direct),
            ("least_breakthrough", least),
        )
        ordered_paths = [
            *(route_path for _route_type, route_path in canonical),
            *searched_paths,
        ]
        shortest_hops = max(len(direct) - 1, 0) if direct else None
        route_costs = [
            sum(
                int(system_id in self.inhibitor_systems)
                for system_id in route_path[:-1]
            )
            for route_path in ordered_paths
            if route_path
        ]
        minimum_breakthroughs = min(route_costs, default=0)
        for route_path in ordered_paths:
            key = tuple(route_path)
            if not route_path or key in seen_paths:
                continue
            seen_paths.add(key)
            breakthrough_count = sum(
                int(system_id in self.inhibitor_systems)
                for system_id in route_path[:-1]
            )
            tags: list[str] = []
            if shortest_hops is not None and len(route_path) - 1 == shortest_hops:
                tags.append("shortest")
            if breakthrough_count == 0:
                tags.append("no_breakthrough")
            if breakthrough_count == minimum_breakthroughs:
                tags.append("minimum_breakthrough")
            if not any(
                pressure["edge_visits"].get(_edge_key(left, right), 0.0) > 0
                for left, right in pairwise(route_path)
            ):
                tags.append("unvisited_corridor")
            if key == tuple(direct):
                effective_route_type = (
                    "direct_safe" if breakthrough_count == 0 else "shortest_assault"
                )
            elif key == tuple(safe):
                effective_route_type = "safe_detour"
            elif key == tuple(least):
                effective_route_type = "least_breakthrough"
            elif breakthrough_count == 0:
                effective_route_type = "safe_detour"
            elif breakthrough_count == minimum_breakthroughs:
                effective_route_type = "least_breakthrough"
            else:
                effective_route_type = "pareto_alternative"
            candidates.append(
                self._route_option(
                    fleet_id=fleet_id,
                    target_system_id=target_system_id,
                    route_type=effective_route_type,
                    path=route_path,
                    pressure=pressure,
                    strategy_tags=tags,
                )
            )

        known_path = _bfs_path(
            self.graph,
            source_system,
            target_system_id,
            allowed=self.known_systems | {source_system, target_system_id},
        )
        border_blockers = [
            {
                **self._system_label(system_id),
                "controller_country_ids": sorted(
                    self.controllers.get(system_id, set())
                ),
            }
            for system_id in known_path[1:]
            if system_id not in allowed
        ]
        pareto = self._pareto(candidates)
        options = self._score_options(pareto)
        recommended_force = options[0].get("force_assessment", {}) if options else {}
        garrisons = self._garrison_candidates(pressure)
        fleet_role = self._fleet_role(fleet, pressure, garrisons)
        return {
            "schema": "iag.campaign_route_options.v2",
            "game_date": self.fleet_profile.get("game_date"),
            "owner_country_id": self.owner,
            "active_war_ids": self.active_war_ids,
            "source_fleet": {
                key: value
                for key, value in fleet.items()
                if key not in {"ship_ids", "fleet_composition"}
            },
            "source_system": self._system_label(source_system),
            "target_system": self._system_label(target_system_id),
            "target_objectives": target_objectives,
            "route_options": options,
            "route_count": len(options),
            "raw_candidate_count": len(candidates),
            "pareto_candidate_count": len(pareto),
            "route_search": search_diagnostics,
            "recommended_force_assessment": recommended_force,
            "deployment_context": {
                "source_fleet_role": fleet_role,
                "target_assignment_count": round(
                    float(pressure["target_visits"].get(target_system_id, 0.0)),
                    4,
                ),
                "target_assigned_military_power": round(
                    float(pressure["target_assigned_power"].get(target_system_id, 0.0)),
                    4,
                ),
                "target_assigned_fleet_ids": sorted(
                    pressure["target_fleet_ids"].get(target_system_id, set())
                ),
                "garrison_candidates": garrisons,
                "active_or_remembered_assignments": pressure["assignments"][-64:],
            },
            "border_access_blockers": border_blockers,
            "status": (
                (
                    "routes_available"
                    if recommended_force.get("hard_gate_satisfied", False)
                    else str(
                        recommended_force.get("status")
                        or "force_assessment_unavailable"
                    )
                )
                if options
                else (
                    "blocked_by_border_access"
                    if border_blockers
                    else "no_known_hyperlane_path"
                )
            ),
            "non_hyperlane_routes_considered": False,
            "selection_policy": (
                "Pareto feasibility first; deterministic UCB-style exploration and "
                "corridor congestion only rank the surviving save-backed routes."
            ),
        }

    @staticmethod
    def select_route(
        profile: dict[str, Any],
        route_id: str,
    ) -> dict[str, Any]:
        route = next(
            (
                item
                for item in profile.get("route_options", [])
                if item.get("route_id") == route_id
            ),
            None,
        )
        if route is None:
            raise ValueError(
                "The route is absent from the current save-backed options."
            )
        return dict(route)

    @staticmethod
    def resume_route(
        profile: dict[str, Any],
        *,
        previous_route_id: str | None,
        previous_path_system_ids: list[int],
        previous_route_type: str | None,
    ) -> dict[str, Any] | None:
        """Keep the chosen corridor after a new save changes the route origin."""
        routes = [
            dict(item)
            for item in profile.get("route_options", [])
            if isinstance(item, dict)
        ]
        if not routes:
            return None
        exact = next(
            (
                route
                for route in routes
                if previous_route_id and route.get("route_id") == previous_route_id
            ),
            None,
        )
        if exact is not None:
            return exact

        source = profile.get("source_system", {}).get("system_id")
        previous = [int(value) for value in previous_path_system_ids]
        if source is not None and int(source) in previous:
            previous = previous[previous.index(int(source)) :]
        previous_edges = {_edge_key(left, right) for left, right in pairwise(previous)}

        def continuity(route: dict[str, Any]) -> tuple[Any, ...]:
            path = [int(value) for value in route.get("path_system_ids", [])]
            prefix = 0
            for left, right in zip(path, previous):
                if left != right:
                    break
                prefix += 1
            edges = {_edge_key(left, right) for left, right in pairwise(path)}
            overlap = len(edges.intersection(previous_edges))
            return (
                -prefix,
                -overlap,
                route.get("route_type") != previous_route_type,
                float(route.get("strategic_score") or 0.0),
                int(route.get("recommendation_rank") or 10**6),
                str(route.get("route_id") or ""),
            )

        return min(routes, key=continuity)
