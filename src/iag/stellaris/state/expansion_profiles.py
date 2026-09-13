"""Extract save-backed colonization and starbase operation candidates.

The packet serializers accept object IDs because that is what Stellaris puts
on the wire.  This module is the authority boundary that turns those IDs into
player-visible candidates.  Callers select candidates from this output rather
than supplying arbitrary protocol fields.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from iag.stellaris.game_knowledge import (
    planet_class_rule,
    species_habitability_rule,
    starbase_component_rule,
    starbase_level_rule,
)
from iag.stellaris.state.extract_game_state import (
    construction_queues,
    resource_values,
)
from iag.stellaris.state.fleet_profiles import (
    COLONY_SHIP_CLASS,
    bitset_contains,
    integer_scalar,
    integer_values,
    name_hint,
    name_key,
    optional_section,
    owned_fleet_ids,
    planet_map,
    player_countries,
    quoted_value,
    repeated_integer,
    starbase_map,
)
from iag.stellaris.state.planet_profiles import (
    find_braced_section,
    parse_numeric_map,
)
from iag.stellaris.state.research_profiles import extract_research_profile
from iag.stellaris.state.ship_profiles import extract_ship_profiles

if TYPE_CHECKING:
    from iag.stellaris.state.state_index import WorldStateIndex

INVALID_OBJECT_ID = 0xFFFFFFFF
VERIFIED_COLONY_DESIGNATIONS = ("col_city", "col_mining")
VERIFIED_STARBASE_COMPONENTS = {
    "module": ("shipyard", "anchorage"),
    "building": ("crew_quarters", "hydroponics_bay"),
}
STARBASE_LEVEL_TECHNOLOGIES = {
    "starbase_level_starport": "tech_starbase_1",
    "starbase_level_starhold": "tech_starbase_2",
    "starbase_level_starfortress": "tech_starbase_3",
    "starbase_level_citadel": "tech_starbase_4",
}
SCRIPT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _repeated_quoted(block: str, key: str) -> list[str]:
    return re.findall(
        rf'(?m)^\s*{re.escape(key)}="([^"]*)"\s*$',
        block,
    )


def _indexed_script_ids(block: str) -> dict[int, str]:
    return {
        int(index): value
        for index, value in re.findall(
            r"\b(\d+)=([A-Za-z0-9_.-]+)",
            block,
        )
    }


def _country_stockpile(country_block: str) -> dict[str, float]:
    modules = optional_section(country_block, "modules")
    economy = optional_section(modules, "standard_economy_module")
    return resource_values(optional_section(economy, "resources"))


def _queue_profile(
    queues: dict[int, str | None],
    queue_id: int | None,
    owner: int,
) -> dict[str, Any] | None:
    if queue_id in (None, INVALID_OBJECT_ID):
        return None
    block = queues.get(int(queue_id))
    if not block or integer_scalar(block, "owner") != owner:
        return None
    handles = integer_values(block, "items")
    queue_types = re.findall(
        r"(?m)^\s*type=([A-Za-z0-9_.-]+)\s*$",
        block,
    )
    return {
        "build_queue_id": int(queue_id),
        "queue_type": queue_types[-1] if queue_types else None,
        "queue_item_handles": handles,
        "queue_length": len(handles),
    }


def _planet_system_index(
    systems: dict[int, str | None],
) -> tuple[dict[int, int], dict[int, dict[str, Any]]]:
    planet_to_system: dict[int, int] = {}
    system_profiles: dict[int, dict[str, Any]] = {}
    for system_id, block in systems.items():
        if not block:
            continue
        planet_ids = repeated_integer(block, "planet")
        for planet_id in planet_ids:
            planet_to_system[planet_id] = system_id
        system_profiles[system_id] = {
            "system_id": system_id,
            "name_key": name_key(block),
            "display_name_hint": name_hint(block),
            "planet_ids": planet_ids,
            "starbase_indices": [
                value
                for value in integer_values(block, "starbases")
                if value != INVALID_OBJECT_ID
            ],
        }
    return planet_to_system, system_profiles


def _owned_starbase_profiles(
    *,
    owner: int,
    country_block: str,
    starbases: dict[int, str | None],
    systems: dict[int, dict[str, Any]],
    queues: dict[int, str | None],
    known_technologies: set[str],
    game_root: Path,
) -> list[dict[str, Any]]:
    owned_fleets = set(owned_fleet_ids(country_block))
    starbase_to_system = {
        starbase_index: int(system_id)
        for system_id, system in systems.items()
        for starbase_index in system["starbase_indices"]
    }
    output: list[dict[str, Any]] = []
    for starbase_index, block in sorted(starbases.items()):
        if not block:
            continue
        station_object = integer_scalar(block, "station")
        queue = _queue_profile(
            queues,
            integer_scalar(block, "build_queue"),
            owner,
        )
        shipyard_queue = _queue_profile(
            queues,
            integer_scalar(block, "shipyard_build_queue"),
            owner,
        )
        if (
            station_object not in owned_fleets
            and queue is None
            and shipyard_queue is None
        ):
            continue
        level_id = quoted_value(block, "level")
        if not level_id:
            continue
        level_rule = starbase_level_rule(game_root, level_id)
        modules = _indexed_script_ids(optional_section(block, "modules"))
        buildings = _indexed_script_ids(optional_section(block, "buildings"))
        system_id = starbase_to_system.get(starbase_index)
        profile: dict[str, Any] = {
            "starbase_index": starbase_index,
            "station_object": station_object,
            "system_id": system_id,
            "system_name_key": (
                systems.get(system_id, {}).get("name_key")
                if system_id is not None
                else None
            ),
            "level_id": level_id,
            "modules": modules,
            "buildings": buildings,
            "build_queue": queue,
            "shipyard_build_queue": shipyard_queue,
            "level_rule": level_rule,
            "operation_candidates": [],
        }
        candidates = profile["operation_candidates"]
        queue_idle = bool(queue) and queue["queue_length"] == 0
        next_level = level_rule.get("next_level")
        required_upgrade_tech = STARBASE_LEVEL_TECHNOLOGIES.get(
            str(next_level)
        )
        if (
            queue_idle
            and next_level
            and required_upgrade_tech in known_technologies
        ):
            candidates.append(
                {
                    "candidate_id": (
                        f"starbase:{starbase_index}:upgrade:{next_level}"
                    ),
                    "action": "upgrade_starbase",
                    "target_level": next_level,
                    "rule_authority": "installed_next_level_and_owned_technology",
                    "target": {
                        "context_822c": owner,
                        "build_queue_id": int(queue["build_queue_id"]),
                        "target_level": next_level,
                        "starbase_object": starbase_index,
                    },
                }
            )

        for kind, components in VERIFIED_STARBASE_COMPONENTS.items():
            slot_count = int(level_rule.get(f"{kind}_slot_count") or 0)
            occupied = modules if kind == "module" else buildings
            for slot_index in range(slot_count):
                current_component = occupied.get(slot_index)
                for component_id in components:
                    if current_component == component_id or not queue_idle:
                        continue
                    rule = starbase_component_rule(
                        game_root,
                        kind,
                        component_id,
                    )
                    if rule.get("status") != "available":
                        continue
                    required = set(rule.get("required_technologies", []))
                    if not required.issubset(known_technologies):
                        continue
                    replacing = current_component is not None
                    candidates.append(
                        {
                            "candidate_id": (
                                f"starbase:{starbase_index}:{kind}:"
                                f"{slot_index}:{component_id}"
                            ),
                            "action": f"set_starbase_{kind}",
                            "kind": kind,
                            "slot_index": slot_index,
                            "component_id": component_id,
                            "current_component_id": current_component,
                            "replaces_existing_component": replacing,
                            "requires_replacement_permission": replacing,
                            "rule": rule,
                            "target": {
                                "context_822c": owner,
                                "build_queue_id": int(queue["build_queue_id"]),
                                "component_id": component_id,
                                "slot_index": slot_index,
                                "starbase_object": starbase_index,
                            },
                        }
                    )
        output.append(profile)
    return output


def extract_expansion_profiles(
    text: str,
    owner: int | None = None,
    *,
    game_root: Path | None,
    minimum_habitability: float = 0.30,
    research_profile: dict[str, Any] | None = None,
    ship_profile: dict[str, Any] | None = None,
    state_index: WorldStateIndex | None = None,
) -> dict[str, Any]:
    """Return deterministic colonization and starbase candidates."""
    if game_root is None:
        raise ValueError("Installed Stellaris rules are required for expansion tools.")
    if not 0 <= minimum_habitability <= 1:
        raise ValueError("minimum_habitability must be between 0 and 1.")
    players = player_countries(text, state_index=state_index)
    if owner is None:
        if len(players) != 1:
            raise ValueError(
                "Could not infer one player country; pass owner explicitly."
            )
        owner = players[0]

    countries = (
        state_index.numeric_map("country")
        if state_index is not None
        else parse_numeric_map(find_braced_section(text, "country").strip())
    )
    country = countries.get(owner)
    if not country:
        raise ValueError(f"Country {owner} does not exist in this save.")
    planets = planet_map(text, state_index=state_index)
    systems_raw = (
        state_index.numeric_map("galactic_object")
        if state_index is not None
        else parse_numeric_map(
            find_braced_section(text, "galactic_object").strip()
        )
    )
    planet_to_system, systems = _planet_system_index(systems_raw)
    starbases = starbase_map(text, state_index=state_index)
    queues = construction_queues(text, state_index=state_index)
    research = research_profile or extract_research_profile(
        text,
        owner=owner,
        state_index=state_index,
    )
    known_technologies = set(research["known_technologies"])
    ship_profile = ship_profile or extract_ship_profiles(
        text,
        owner=owner,
        game_root=game_root,
        research_profile=research,
        state_index=state_index,
    )

    starbase_profiles = _owned_starbase_profiles(
        owner=owner,
        country_block=country,
        starbases=starbases,
        systems=systems,
        queues=queues,
        known_technologies=known_technologies,
        game_root=game_root,
    )
    owned_starbase_systems = {
        int(item["system_id"]): item
        for item in starbase_profiles
        if item.get("system_id") is not None
    }

    species_id = integer_scalar(country, "founder_species_ref")
    species_blocks = (
        state_index.numeric_map("species_db")
        if state_index is not None
        else parse_numeric_map(find_braced_section(text, "species_db").strip())
    )
    species_block = species_blocks.get(species_id) if species_id is not None else None
    species_traits = tuple(
        _repeated_quoted(optional_section(species_block or "", "traits"), "trait")
    )
    colonizer_designs = [
        design
        for design in ship_profile["designs"]
        if COLONY_SHIP_CLASS.removeprefix("shipclass_")
        in set(design.get("ship_sizes", []))
        and not design.get("obsolete", False)
    ]

    source_shipyards = [
        {
            "starbase_index": int(starbase["starbase_index"]),
            "station_object": starbase.get("station_object"),
            "system_id": starbase.get("system_id"),
            "system_name_key": starbase.get("system_name_key"),
            "shipyard_build_queue": dict(starbase["shipyard_build_queue"]),
        }
        for starbase in starbase_profiles
        if "shipyard" in starbase["modules"].values()
        and starbase.get("shipyard_build_queue") is not None
    ]

    colonizable_planets: list[dict[str, Any]] = []
    for planet_id, block in sorted(planets.items()):
        if not block:
            continue
        planet_class = quoted_value(block, "planet_class")
        if not planet_class:
            continue
        system_id = planet_to_system.get(planet_id)
        system = systems.get(int(system_id or -1))
        if system is None or system_id not in owned_starbase_systems:
            continue
        if (
            integer_scalar(block, "owner") is not None
            or integer_scalar(block, "controller") is not None
            or integer_scalar(block, "colony") is not None
            or not bitset_contains(integer_scalar(block, "surveyed_by"), owner)
        ):
            continue
        class_rule = planet_class_rule(game_root, planet_class)
        if not class_rule.get("colonizable", False):
            continue
        system_name_key = str(system.get("name_key") or "")
        if not SCRIPT_ID_RE.fullmatch(system_name_key):
            continue
        habitability = species_habitability_rule(
            game_root,
            species_traits,
            planet_class,
        )
        value = habitability.get("habitability")
        meets_policy = isinstance(value, (int, float)) and float(value) >= (
            minimum_habitability
        )
        colonizable_planets.append(
            {
                "planet_id": planet_id,
                "name_key": name_key(block),
                "display_name_hint": name_hint(block),
                "planet_class": planet_class,
                "planet_size": integer_scalar(block, "planet_size"),
                "system_id": system_id,
                "system_name_key": system_name_key,
                "habitability": habitability,
                "meets_habitability_policy": meets_policy,
                "minimum_habitability": minimum_habitability,
                "planet_class_rule": class_rule,
            }
        )

    colonization_candidates: list[dict[str, Any]] = []
    if species_id is not None and species_block:
        for planet in colonizable_planets:
            if not planet["meets_habitability_policy"]:
                continue
            for source in source_shipyards:
                for design in colonizer_designs:
                    design_id = int(design["design_id"])
                    target_planet_id = int(planet["planet_id"])
                    source_queue_id = int(
                        source["shipyard_build_queue"]["build_queue_id"]
                    )
                    colonization_candidates.append(
                        {
                            "candidate_id": (
                                f"colonize:{target_planet_id}:from:"
                                f"shipyard-queue:{source_queue_id}:design:{design_id}"
                            ),
                            "action": "order_colony_ship_and_colonize",
                            "target_planet": planet,
                            "source_shipyard": source,
                            "species_id": species_id,
                            "colonizer_design_id": design_id,
                            "verified_designations": list(
                                VERIFIED_COLONY_DESIGNATIONS
                            ),
                            "target_template": {
                                "context_822c": owner,
                                "species_id": species_id,
                                "design_id": design_id,
                                "upgrade_id": INVALID_OBJECT_ID,
                                "growth_stage": 0,
                                "target_planet_id": target_planet_id,
                                "source_shipyard_build_queue_id": source_queue_id,
                                "system_name_key": planet["system_name_key"],
                            },
                        }
                    )

    return {
        "schema": "iag.stellaris_expansion_state.v1",
        "schema_version": 1,
        "game_date": research.get("game_date"),
        "owner_country_id": owner,
        "country_stockpile": _country_stockpile(country),
        "known_technologies": sorted(known_technologies),
        "founder_species": {
            "species_id": species_id,
            "traits": list(species_traits),
            "resolved": bool(species_block),
        },
        "colonizer_designs": colonizer_designs,
        "source_shipyards": source_shipyards,
        "colonizable_planets": colonizable_planets,
        "colonization_candidates": colonization_candidates,
        "starbases": starbase_profiles,
        "starbase_operation_candidates": [
            candidate
            for starbase in starbase_profiles
            for candidate in starbase["operation_candidates"]
        ],
        "existing_colony_ship_protocol_state": (
            "paired_but_designation_persistence_unresolved"
        ),
    }


def selected_colonization(
    profile: dict[str, Any],
    *,
    candidate_id: str,
    designation: str,
) -> dict[str, Any]:
    candidate = next(
        (
            item
            for item in profile.get("colonization_candidates", [])
            if item.get("candidate_id") == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise ValueError("The colonization candidate is absent from current state.")
    if designation not in candidate["verified_designations"]:
        raise ValueError("The colony designation is not in the verified allowlist.")
    return {
        **candidate,
        "designation": designation,
        "target": {
            **candidate["target_template"],
            "colony_designation": designation,
        },
    }


def selected_starbase_operation(
    profile: dict[str, Any],
    *,
    candidate_id: str,
    allow_replacement: bool,
) -> dict[str, Any]:
    candidate = next(
        (
            item
            for item in profile.get("starbase_operation_candidates", [])
            if item.get("candidate_id") == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise ValueError("The starbase candidate is absent from current state.")
    if candidate.get("requires_replacement_permission") and not allow_replacement:
        raise ValueError("Player has not enabled starbase component replacement.")
    return dict(candidate)
